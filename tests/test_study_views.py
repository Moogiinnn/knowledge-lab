import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.curriculum.models import ConsentLog, Enrollment, Question
from apps.curriculum.services import create_version
from apps.events.models import SubmissionEvent
from apps.events.services import revise_grade

from .conftest import make_short_question, make_student

PASSWORD = "x-test-pass-123"


@pytest.fixture
def client_for(client):
    def _login(user):
        assert client.login(username=user.username, password=PASSWORD)
        return client

    return _login


def form_key(response) -> str:
    return re.search(r'name="idempotency_key" value="([0-9a-f-]+)"', response.content.decode()).group(1)


def post_answer(client, question, answer, key=None, **extra):
    data = {"answer": answer, "idempotency_key": key or str(uuid.uuid4()), "response_time_sec": "42"}
    data.update(extra)
    return client.post(reverse("submit", args=[question.pk]), data)


def test_login_required(client, short_question):
    response = client.get(reverse("question", args=[short_question.pk]))
    assert response.status_code == 302 and reverse("login") in response["Location"]


def test_login_starts_study_session(client, student):
    client.post(reverse("login"), {"username": "S001", "password": PASSWORD})
    first = client.session["study_session_id"]
    client.post(reverse("logout"))
    client.post(reverse("login"), {"username": "S001", "password": PASSWORD})
    assert client.session["study_session_id"] != first


def test_home_lists_published_questions_only(client_for, course, student, short_question):
    Question.objects.create(course=course, code="draft-only", title="Ноорог", kind="SHORT")
    page = client_for(student).get(reverse("home")).content.decode()
    assert short_question.title in page and "Ноорог" not in page


def test_undecided_consent_redirects_to_consent_page(client_for, course, short_question):
    undecided = make_student(course, "S200", consent=False)
    client = client_for(undecided)
    response = client.get(reverse("question", args=[short_question.pk]))
    assert response["Location"] == reverse("consent", args=[course.pk])
    assert post_answer(client, short_question, "10")["Location"] == reverse("consent", args=[course.pk])
    assert not SubmissionEvent.objects.exists()


def test_consent_decisions_are_logged(client_for, course):
    user = make_student(course, "S201", consent=False)
    client = client_for(user)
    url = reverse("consent", args=[course.pk])
    client.post(url, {"decision": "DECLINED"})
    enrollment = Enrollment.objects.get(user=user)
    assert enrollment.consent_decided and not enrollment.has_research_consent
    client.post(url, {"decision": "GIVEN"})
    client.post(url, {"decision": "WITHDRAWN"})
    assert list(ConsentLog.objects.order_by("id").values_list("decision", flat=True)) == [
        "DECLINED", "GIVEN", "WITHDRAWN",
    ]
    client.post(url, {"decision": "BOGUS"})
    assert ConsentLog.objects.count() == 3


def test_submit_records_event_and_redirects(client_for, student, short_question):
    client = client_for(student)
    page = client.get(reverse("question", args=[short_question.pk]))
    response = post_answer(client, short_question, "10", key=form_key(page), assistance_flag="no")
    assert response.status_code == 302 and response["Location"].endswith("?saved=1")
    event = SubmissionEvent.objects.get()
    assert event.correct is True and event.response_time_sec == 42 and event.assistance_flag is False
    assert str(event.study_session_id) == client.session["study_session_id"]
    assert event.answer.text == "10"


def test_double_submit_of_same_form_creates_one_event(client_for, student, short_question):
    client = client_for(student)
    key = str(uuid.uuid4())
    post_answer(client, short_question, "10", key=key)
    post_answer(client, short_question, "10", key=key)
    assert SubmissionEvent.objects.count() == 1


def test_conflict_rerenders_with_answer_and_fresh_key(client_for, student, short_question):
    client = client_for(student)
    key = str(uuid.uuid4())
    post_answer(client, short_question, "10", key=key)
    response = post_answer(client, short_question, "11", key=key)
    assert response.status_code == 400
    body = response.content.decode()
    assert "11</textarea>" in body and form_key(response) != key
    # The fresh key from the re-rendered form works: the student is not stuck.
    assert post_answer(client, short_question, "11", key=form_key(response)).status_code == 302
    assert SubmissionEvent.objects.count() == 2


def test_bad_timing_is_kept_as_invalid_not_rejected(client_for, student, short_question):
    client = client_for(student)
    assert post_answer(client, short_question, "10", response_time_sec="abc").status_code == 302
    event = SubmissionEvent.objects.get()
    assert (event.response_time_sec, event.timing_source) == (None, "client_invalid")


def test_assistance_errors_rerender(client_for, student, short_question):
    client = client_for(student)
    response = post_answer(client, short_question, "10", assistance_flag="no", assistance_types=["AI"])
    assert response.status_code == 400 and not SubmissionEvent.objects.exists()
    response = post_answer(client, short_question, "10", assistance_flag="yes", assistance_types=["AI", "peer"])
    assert SubmissionEvent.objects.get().assistance_types == ["AI", "peer"]


def test_other_course_and_draft_questions_are_404(client_for, course, student):
    from apps.curriculum.models import Concept, Course

    other = Course.objects.create(code="other", name="Other")
    Concept.objects.create(course=other, code="array", name="a")
    Concept.objects.create(course=other, code="stack", name="s")
    foreign = make_short_question(other, code="foreign")
    draft = Question.objects.create(course=course, code="d", title="d", kind="SHORT")
    create_version(draft, prompt="p", accepted_answers=["1"], concept_weights={"array": 1})
    client = client_for(student)
    for q in (foreign, draft):
        assert client.get(reverse("question", args=[q.pk])).status_code == 404
        assert post_answer(client, q, "1").status_code == 404
    assert not SubmissionEvent.objects.exists()


def test_hints_open_in_order_and_count(client_for, course, student):
    q = make_short_question(course, code="hinted", hints=["эхний", "хоёр дахь"])
    client = client_for(student)
    page = client.get(reverse("question", args=[q.pk])).content.decode()
    assert "эхний" not in page and "Hint харах (1/2)" in page
    client.post(reverse("hint", args=[q.pk]), {"hint_index": 0})
    page = client.get(reverse("question", args=[q.pk])).content.decode()
    assert "эхний" in page and "Hint харах (2/2)" in page
    client.post(reverse("hint", args=[q.pk]), {"hint_index": "x"})  # ignored safely
    post_answer(client, q, "10")
    assert SubmissionEvent.objects.get().hint_count == 1


def test_history_shows_teacher_revision(client_for, student, short_question):
    client = client_for(student)
    post_answer(client, short_question, "арав")
    revise_grade(event=SubmissionEvent.objects.get(), correct=True, score=0.9, source="teacher", feedback="Зөв санаа")
    page = client.get(reverse("question", args=[short_question.pk])).content.decode()
    assert "90%" in page and "Зөв санаа" in page


def test_staff_home_goes_to_admin(db, client):
    staff = get_user_model().objects.create_user(username="teacher", password=PASSWORD, is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("home"))["Location"] == reverse("admin:index")


def test_server_elapsed_runs_from_first_open_to_submit(client_for, student, short_question, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone as tz

    client = client_for(student)
    start = tz.now()
    monkeypatch.setattr(tz, "now", lambda: start)
    client.get(reverse("question", args=[short_question.pk]))
    monkeypatch.setattr(tz, "now", lambda: start + timedelta(seconds=30))
    client.get(reverse("question", args=[short_question.pk]))  # reopening keeps the first time
    monkeypatch.setattr(tz, "now", lambda: start + timedelta(seconds=95))
    post_answer(client, short_question, "4")
    # The next attempt is timed from the next opening, not from the first one.
    monkeypatch.setattr(tz, "now", lambda: start + timedelta(seconds=100))
    client.get(reverse("question", args=[short_question.pk]))
    monkeypatch.setattr(tz, "now", lambda: start + timedelta(seconds=110))
    post_answer(client, short_question, "10")
    assert list(SubmissionEvent.objects.order_by("attempt_index").values_list("server_elapsed_sec", flat=True)) == [
        95, 10,
    ]


def test_submit_without_opening_has_no_server_elapsed(client_for, student, short_question):
    post_answer(client_for(student), short_question, "10")
    assert SubmissionEvent.objects.get().server_elapsed_sec is None


def test_question_kind_is_shown_in_mongolian(client_for, student, short_question):
    assert "Богино хариулт" in client_for(student).get(reverse("home")).content.decode()
