import csv
import io

from django.core.management import call_command
from django.utils import timezone

from apps.curriculum.models import Course, Enrollment
from apps.events.export import COLUMNS, export_rows
from apps.events.services import revise_grade

from .conftest import make_short_question, make_student

REQUIRED = [
    "event_id", "student_id", "timestamp", "question_id", "attempt_index", "correct", "score",
    "tests_passed", "tests_total", "response_time_sec", "hint_count",
]


def test_export_has_required_fields_and_no_identifiers(course, student, short_question, submit):
    submit(student, short_question, "10", response_time_sec=42)
    (row,) = list(export_rows(course))
    assert set(REQUIRED) <= set(row) and list(row) == COLUMNS
    assert row["student_id"].startswith("p_")
    assert student.username not in "".join(str(v) for v in row.values())
    assert row["timestamp"].endswith("Z")
    assert (row["correct"], row["score"], row["hint_count"], row["response_time_sec"]) == (1, 1.0, 0, 42)
    assert row["tests_passed"] == "" and row["tests_total"] == ""


def test_export_respects_consent_and_withdrawal(course, short_question, submit):
    yes = make_student(course, "S100")
    no = make_student(course, "S101", consent=False)
    later_withdrawn = make_student(course, "S102")
    for user in (yes, no, later_withdrawn):
        submit(user, short_question, "10")
    Enrollment.objects.filter(user=later_withdrawn).update(consent_withdrawn_at=timezone.now())
    rows = list(export_rows(course))
    assert len(rows) == 1


def test_synthetic_course_is_excluded_by_default(db, submit):
    demo = Course.objects.create(code="demo", name="demo", is_synthetic=True)
    from apps.curriculum.models import Concept

    Concept.objects.create(course=demo, code="array", name="array")
    Concept.objects.create(course=demo, code="stack", name="stack")
    q = make_short_question(demo)
    submit(make_student(demo, "D1"), q, "10")
    assert list(export_rows(demo)) == []
    assert len(list(export_rows(demo, include_synthetic=True))) == 1


def test_teacher_revision_is_final_grade_and_auto_grade_kept(course, student, short_question, submit):
    event, _ = submit(student, short_question, "ten")
    revise_grade(event=event, correct=True, score=0.8, source="teacher", reason="accept words")
    revise_grade(event=event, correct=True, score=0.9, source="teacher", reason="second look")
    (row,) = list(export_rows(course))
    assert (row["correct"], row["score"]) == (1, 0.9)
    assert (row["auto_correct"], row["auto_score"]) == (0, 0.0)
    assert row["grading_status"] == "REVISED:teacher" and row["grade_revisions"] == 2


def test_rows_are_in_replay_order_and_command_writes_csv(course, student, short_question, submit):
    for answer in ["1", "2", "10"]:
        submit(student, short_question, answer)
    out = io.StringIO()
    call_command("export_events", course.code, stdout=out, stderr=io.StringIO())
    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert [r["attempt_index"] for r in rows] == ["1", "2", "3"]
    assert [r["timestamp"] for r in rows] == sorted(r["timestamp"] for r in rows)


def test_one_students_timestamps_strictly_increase_at_export_resolution(course, student, short_question, submit,
                                                                         monkeypatch):  # fmt: skip
    from django.utils import timezone

    frozen = timezone.now()
    monkeypatch.setattr("apps.events.services.timezone.now", lambda: frozen)  # a clock that does not advance
    for answer in ["1", "2", "3", "10"]:
        submit(student, short_question, answer)
    rows = list(export_rows(course))
    stamps = [r["timestamp"] for r in rows]
    assert [r["attempt_index"] for r in rows] == [1, 2, 3, 4]
    assert stamps == sorted(stamps) and len(set(stamps)) == 4
