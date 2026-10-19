import csv
import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.roles import ensure_teacher_group
from apps.curriculum.models import Concept, ConceptEdge, Course, Enrollment, Question, QuestionVersion
from apps.curriculum.services import create_version
from apps.events.models import GradeRevision, SubmissionEvent

from .conftest import make_short_question

PASSWORD = "x-test-pass-123"


@pytest.fixture
def teacher(db):
    user = get_user_model().objects.create_user(username="teacher1", password=PASSWORD, is_staff=True)
    user.groups.add(ensure_teacher_group())
    return user


@pytest.fixture
def tclient(client, teacher):
    client.force_login(teacher)
    return client


def management(prefix, total, initial=0):
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def version_post(question, weights, answers="2"):
    data = {
        "question": question.pk,
        "prompt": "Стекийн бодлого",
        "difficulty": "2",
        "accepted_answers_text": answers,
        "hints_text": "LIFO\nЭхний pop",
        **management("concept_links", len(weights)),
        **management("test_cases", 0),
    }
    for i, (concept, weight) in enumerate(weights):
        data[f"concept_links-{i}-concept"] = concept.pk
        data[f"concept_links-{i}-weight"] = str(weight)
    return data


def action(tclient, model, name, pks):
    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
    return tclient.post(url, {"action": name, "_selected_action": [str(pk) for pk in pks]}, follow=True)


@pytest.fixture
def question(course):
    return Question.objects.create(course=course, code="q-new", title="Шинэ бодлого", kind="SHORT")


def concept(course, code):
    return Concept.objects.get(course=course, code=code)


def test_teacher_creates_version_with_weights(tclient, course, question):
    url = reverse("admin:curriculum_questionversion_add")
    response = tclient.post(url, version_post(question, [(concept(course, "stack"), 0.7), (concept(course, "array"), 0.3)]))
    assert response.status_code == 302, response.content.decode()[:2000]
    version = QuestionVersion.objects.get(question=question)
    assert (version.version, version.status) == (1, "DRAFT")
    assert version.accepted_answers == ["2"] and version.hints == ["LIFO", "Эхний pop"]
    assert sorted(version.concept_links.values_list("weight", flat=True)) == [0.3, 0.7]


def test_weights_not_summing_to_one_show_form_error(tclient, course, question):
    url = reverse("admin:curriculum_questionversion_add")
    response = tclient.post(url, version_post(question, [(concept(course, "stack"), 0.7), (concept(course, "array"), 0.5)]))
    assert response.status_code == 200
    assert "нийлбэр 1 байх ёстой" in response.content.decode()
    assert not QuestionVersion.objects.exists()


def test_concept_from_other_course_shows_form_error(tclient, course, question):
    other = Course.objects.create(code="other", name="Other")
    foreign = Concept.objects.create(course=other, code="stack", name="stack")
    url = reverse("admin:curriculum_questionversion_add")
    response = tclient.post(url, version_post(question, [(foreign, 1)]))
    assert response.status_code == 200 and "хичээлд хамаарахгүй" in response.content.decode()


def test_short_version_needs_an_answer(tclient, course, question):
    url = reverse("admin:curriculum_questionversion_add")
    response = tclient.post(url, version_post(question, [(concept(course, "stack"), 1)], answers=""))
    assert response.status_code == 200 and not QuestionVersion.objects.exists()


def test_publish_action(tclient, course, question):
    good = create_version(question, prompt="p", accepted_answers=["1"], concept_weights={"stack": 1})
    other = Question.objects.create(course=course, code="q-empty", title="t", kind="SHORT")
    unmapped = create_version(other, prompt="p", accepted_answers=["1"], concept_weights={})
    page = action(tclient, QuestionVersion, "publish", [good.pk, unmapped.pk]).content.decode()
    question.refresh_from_db()
    other.refresh_from_db()
    assert question.current_version_id == good.pk
    assert other.current_version_id is None
    unmapped.refresh_from_db()
    assert unmapped.status == "DRAFT"
    assert "Concept-ийн жин оруулаагүй" in page


def test_published_version_is_read_only_in_admin(tclient, course):
    q = make_short_question(course)
    version = q.current_version
    url = reverse("admin:curriculum_questionversion_change", args=[version.pk])
    page = tclient.get(url).content.decode()
    assert 'name="prompt"' not in page
    assert 'name="concept_links-0-weight"' not in page
    assert re.search(r'name="concept_links-MAX_NUM_FORMS" value="0"', page)  # no "add another" row
    tclient.post(url, {"prompt": "өөрчилсөн", **management("concept_links", 0, 0)})
    version.refresh_from_db()
    assert version.prompt != "өөрчилсөн"
    assert tclient.post(reverse("admin:curriculum_questionversion_delete", args=[version.pk])).status_code == 403


def test_copy_as_draft_keeps_weights(tclient, course):
    q = make_short_question(course)
    action(tclient, QuestionVersion, "copy_draft", [q.current_version_id])
    draft = QuestionVersion.objects.get(question=q, version=2)
    assert draft.status == "DRAFT" and draft.accepted_answers == ["10"]
    assert dict(draft.concept_links.values_list("concept__code", "weight")) == {"array": 0.6, "stack": 0.4}


def test_retire_action_hides_question(tclient, course):
    q = make_short_question(course)
    action(tclient, QuestionVersion, "retire", [q.current_version_id])
    q.refresh_from_db()
    assert q.current_version is None
    assert QuestionVersion.objects.get(question=q).status == "RETIRED"


def test_prerequisite_cycle_is_rejected(tclient, course):
    for src, dst in [("array", "stack"), ("stack", "queue")]:
        ConceptEdge.objects.create(source=concept(course, src), target=concept(course, dst))
    url = reverse("admin:curriculum_conceptedge_add")
    data = {"source": concept(course, "queue").pk, "target": concept(course, "array").pk,
            "relation": "PREREQUISITE", "initial_weight": "1", "note": ""}
    response = tclient.post(url, data)
    assert response.status_code == 200 and "цикл" in response.content.decode()
    data["relation"] = "RELATED"  # a non-prerequisite link may point backwards
    assert tclient.post(url, data).status_code == 302


def test_approving_edge_sets_timestamp(tclient, course):
    edge = ConceptEdge.objects.create(source=concept(course, "queue"), target=concept(course, "bfs"))
    action(tclient, ConceptEdge, "approve", [edge.pk])
    edge.refresh_from_db()
    assert edge.teacher_approved and edge.approved_at is not None


def test_teacher_grades_with_revision(tclient, teacher, student, short_question, submit):
    event, _ = submit(student, short_question, "арав")
    url = reverse("admin:events_submissionevent_change", args=[event.pk])
    page = tclient.get(url).content.decode()
    assert "арав" in page and student.username not in page
    data = {
        **management("answer", 1, 1),
        "answer-0-event": str(event.pk),
        **management("revisions", 1, 0),
        "revisions-0-correct": "on",
        "revisions-0-score": "0.9",
        "revisions-0-feedback": "Үгээр бичсэн ч зөв",
        "revisions-0-reason": "",
        "revisions-0-tests_passed": "",
        "revisions-0-tests_total": "",
        "revisions-0-event": str(event.pk),
    }
    response = tclient.post(url, data)
    assert response.status_code == 302, response.content.decode()[:3000]
    revision = GradeRevision.objects.get()
    assert (revision.correct, revision.score, revision.source, revision.revised_by) == (True, 0.9, "teacher", teacher)
    event.refresh_from_db()
    assert (event.correct, event.score) == (False, 0.0)  # the original auto grade is untouched


def test_score_out_of_range_is_rejected(tclient, student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    url = reverse("admin:events_submissionevent_change", args=[event.pk])
    data = {
        **management("answer", 1, 1),
        "answer-0-event": str(event.pk),
        **management("revisions", 1, 0),
        "revisions-0-score": "1.5",
        "revisions-0-event": str(event.pk),
    }
    assert tclient.post(url, data).status_code == 200
    assert not GradeRevision.objects.exists()


def test_events_cannot_be_added_or_deleted(tclient, student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    assert tclient.get(reverse("admin:events_submissionevent_add")).status_code == 403
    assert tclient.post(reverse("admin:events_submissionevent_delete", args=[event.pk])).status_code == 403
    assert SubmissionEvent.objects.count() == 1


def test_needs_grading_filter(tclient, course, student, submit):
    from apps.curriculum.services import publish_version

    code_q = Question.objects.create(course=course, code="code-q", title="Код", kind="CODE")
    publish_version(create_version(code_q, prompt="p", concept_weights={"array": 1}))
    code_q.refresh_from_db()
    event, _ = submit(student, code_q, "print(1)")
    url = reverse("admin:events_submissionevent_changelist") + "?needs_grading=yes"
    assert str(event.pk) in tclient.get(url).content.decode()
    GradeRevision.objects.create(event=event, correct=True, score=1, source="teacher")
    assert str(event.pk) not in tclient.get(url).content.decode()


def test_export_action_returns_csv(tclient, course, student, short_question, submit):
    submit(student, short_question, "10")
    url = reverse("admin:curriculum_course_changelist")
    response = tclient.post(url, {"action": "export_research_csv", "_selected_action": [course.pk]})
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert len(rows) == 1 and rows[0]["student_id"].startswith("p_")


def test_teacher_cannot_set_consent_or_edit_users(tclient, course, student):
    enrollment = Enrollment.objects.get(user=student)
    enrollment.consent_given_at = None
    enrollment.save()
    url = reverse("admin:curriculum_enrollment_change", args=[enrollment.pk])
    tclient.post(url, {"user": student.pk, "course": course.pk, "status": "ACTIVE",
                       "consent_given_at_0": "2026-01-01", "consent_given_at_1": "00:00:00",
                       **management("consent_log", 0, 0)})
    enrollment.refresh_from_db()
    assert enrollment.consent_given_at is None
    assert tclient.get(reverse("admin:auth_user_change", args=[student.pk])).status_code in (200, 403)
    assert tclient.post(reverse("admin:auth_user_change", args=[student.pk]), {"is_superuser": "on"}).status_code == 403
    assert tclient.get(reverse("admin:auth_user_add")).status_code == 403


def test_import_students(tmp_path, course):
    src = tmp_path / "students.csv"
    src.write_text("username\nB2026001\nB2026002\n", encoding="utf-8")
    out = tmp_path / "creds.csv"
    call_command("import_students", course.code, str(src), out=str(out), stdout=io.StringIO())
    creds = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [c["username"] for c in creds] == ["B2026001", "B2026002"]
    user = get_user_model().objects.get(username="B2026001")
    assert user.check_password(creds[0]["password"])
    assert Enrollment.objects.filter(course=course, user=user, consent_given_at=None).exists()
    # Re-running enrolls only; existing passwords are not reset.
    call_command("import_students", course.code, str(src), out=str(out), stdout=io.StringIO())
    assert list(csv.DictReader(out.open(encoding="utf-8"))) == []
    assert get_user_model().objects.get(username="B2026001").check_password(creds[0]["password"])


def test_import_rejects_bad_usernames(tmp_path, course):
    from django.core.management.base import CommandError

    src = tmp_path / "students.csv"
    src.write_text("username\nБат Болд\n", encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("import_students", course.code, str(src), stdout=io.StringIO())
