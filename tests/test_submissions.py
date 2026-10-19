import threading
import uuid
from hashlib import sha256

import pytest
from django.db import close_old_connections

from apps.accounts.pseudonym import pseudonymous_id
from apps.curriculum.models import Question
from apps.curriculum.services import create_version, publish_version
from apps.events.models import SubmissionEvent
from apps.events.services import (
    IdempotencyConflict,
    InvalidSubmission,
    NotEnrolled,
    QuestionNotAvailable,
    show_hint,
    submit_answer,
)
from apps.grading.short_answer import grade_short

from .conftest import make_short_question, make_student


def test_pseudonym_is_keyed_not_plain_hash(settings):
    p = pseudonymous_id(1)
    assert p == pseudonymous_id(1)
    assert p != pseudonymous_id(2)
    assert sha256(b"1").hexdigest()[:24] not in p
    settings.PSEUDONYM_KEY = "k" * 32
    assert pseudonymous_id(1) != p
    settings.PSEUDONYM_KEY = "short"
    with pytest.raises(RuntimeError):
        pseudonymous_id(1)


def test_short_grading_normalises_whitespace_and_case():
    assert grade_short(" 10 ", ["10"]).correct is True
    assert grade_short("O(N log N)", ["o(n log n)"]).score == 1.0
    assert grade_short("9", ["10"]).correct is False
    with pytest.raises(ValueError):
        grade_short("10", [])


def test_attempt_indices_and_scores(student, short_question, submit):
    # The regression from v1: a correct answer must not be graded 0.
    results = [submit(student, short_question, a)[0] for a in ["4", "7", "10"]]
    assert [e.attempt_index for e in results] == [1, 2, 3]
    assert [e.score for e in results] == [0.0, 0.0, 1.0]
    assert all(e.grading_status == "AUTO" and e.grader_version for e in results)


def test_event_stores_pseudonym_snapshot_and_version(student, short_question, submit):
    event, _ = submit(student, short_question, "10")
    assert event.student_pseudonym == pseudonymous_id(student.pk)
    assert student.username not in event.student_pseudonym
    assert event.question_version_id == short_question.current_version_id
    assert event.concept_snapshot == [{"concept": "array", "weight": 0.6}, {"concept": "stack", "weight": 0.4}]
    assert event.submitted_at.utcoffset().total_seconds() == 0


def test_retry_with_same_key_returns_same_event(student, short_question, submit):
    key = uuid.uuid4()
    first, created = submit(student, short_question, "10", key=key)
    again, created_again = submit(student, short_question, "10", key=key)
    assert created and not created_again
    assert again.event_id == first.event_id
    assert SubmissionEvent.objects.count() == 1
    with pytest.raises(IdempotencyConflict):
        submit(student, short_question, "11", key=key)


def test_same_key_from_two_students_is_two_events(course, short_question, submit):
    key = uuid.uuid4()
    a = make_student(course, "S010")
    b = make_student(course, "S011")
    submit(a, short_question, "10", key=key)
    submit(b, short_question, "10", key=key)
    assert SubmissionEvent.objects.count() == 2


def test_unenrolled_and_unpublished_are_rejected(course, short_question, submit):
    from django.contrib.auth import get_user_model

    outsider = get_user_model().objects.create_user(username="X1", password="x-test-pass-123")
    with pytest.raises(NotEnrolled):
        submit(outsider, short_question, "10")

    student = make_student(course, "S002")
    draft_only = Question.objects.create(course=course, code="draft", title="d", kind="SHORT")
    create_version(draft_only, prompt="p", accepted_answers=["1"], concept_weights={"array": 1})
    with pytest.raises(QuestionNotAvailable):
        submit(student, draft_only, "1")


def test_new_version_is_used_and_old_events_keep_theirs(student, short_question, submit):
    old_event, _ = submit(student, short_question, "10")
    v2 = create_version(
        short_question, prompt="new", accepted_answers=["11"], concept_weights={"queue": 0.5, "bfs": 0.5}
    )
    publish_version(v2)
    short_question.refresh_from_db()
    new_event, _ = submit(student, short_question, "11")
    old_event.refresh_from_db()
    assert old_event.question_version.version == 1 and old_event.question_version.status == "RETIRED"
    assert new_event.question_version.version == 2 and new_event.correct is True
    assert {c["concept"] for c in new_event.concept_snapshot} == {"queue", "bfs"}
    assert new_event.attempt_index == 2


@pytest.mark.parametrize(
    "value,seconds,source",
    [
        (185, 185, "client_active_visible"),
        (12.6, 13, "client_active_visible"),
        (0, 0, "client_active_visible"),
        (None, None, "client_missing"),
        (-5, None, "client_invalid"),
        (10**7, None, "client_invalid"),
        ("60", None, "client_invalid"),
        (True, None, "client_invalid"),
    ],
)
def test_response_time_cleaning(student, short_question, submit, value, seconds, source):
    event, _ = submit(student, short_question, "10", response_time_sec=value)
    assert (event.response_time_sec, event.timing_source) == (seconds, source)


def test_assistance_validation(student, short_question, submit):
    event, _ = submit(student, short_question, "10", assistance_types=["AI"])
    assert event.assistance_flag is True and event.assistance_types == ["AI"]
    event, _ = submit(student, short_question, "10")
    assert event.assistance_flag is None
    event, _ = submit(student, short_question, "10", assistance_flag=False)
    assert event.assistance_flag is False
    with pytest.raises(InvalidSubmission):
        submit(student, short_question, "10", assistance_flag=False, assistance_types=["peer"])
    with pytest.raises(InvalidSubmission):
        submit(student, short_question, "10", assistance_types=["chatgpt"])


def test_hint_count_is_counted_on_server(course, student, submit):
    q = make_short_question(course, code="q-hints", hints=["h0", "h1", "h2"])
    assert submit(student, q, "1")[0].hint_count == 0
    assert show_hint(user=student, question=q, hint_index=0) == "h0"
    show_hint(user=student, question=q, hint_index=0)  # reopening is not counted twice
    show_hint(user=student, question=q, hint_index=1)
    assert submit(student, q, "2")[0].hint_count == 2
    with pytest.raises(InvalidSubmission):
        show_hint(user=student, question=q, hint_index=2 + 1)


def test_code_question_is_pending_with_null_grades(course, student, submit):
    q = Question.objects.create(course=course, code="q-code", title="c", kind="CODE")
    publish_version(create_version(q, prompt="write sum()", concept_weights={"array": 1}))
    q.refresh_from_db()
    event, _ = submit(student, q, "def f(a): return sum(a)")
    assert event.grading_status == "PENDING"
    assert (event.correct, event.score, event.tests_passed, event.tests_total) == (None, None, None, None)


@pytest.mark.django_db(transaction=True)
def test_concurrent_submits_get_distinct_indices(course):
    student = make_student(course, "S050")
    question = make_short_question(course, code="q-race")
    session = uuid.uuid4()
    errors = []

    def worker(answer):
        try:
            submit_answer(
                user=student, question=question, idempotency_key=uuid.uuid4(), answer=answer, study_session_id=session
            )
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    indices = sorted(SubmissionEvent.objects.filter(question=question).values_list("attempt_index", flat=True))
    assert indices == list(range(1, 9))
