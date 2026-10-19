"""Writing to the research log. Views call these functions; nothing else writes events."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.pseudonym import pseudonymous_id
from apps.curriculum.models import Enrollment, Question
from apps.grading.short_answer import GRADER_VERSION as SHORT_GRADER_VERSION
from apps.grading.short_answer import grade_short

from .models import GradeRevision, HintView, SubmissionAnswer, SubmissionEvent

MAX_ANSWER_CHARS = 50_000
MAX_RESPONSE_TIME_SEC = 86_400
ASSISTANCE_TYPES = {"AI", "peer", "teacher", "material", "other"}
CODE_PENDING_GRADER_VERSION = "code-pending"


class SubmissionError(Exception):
    """Base class; `code` is stable and shown to API clients."""

    code = "invalid"


class NotEnrolled(SubmissionError):
    code = "not_enrolled"


class QuestionNotAvailable(SubmissionError):
    code = "question_not_available"


class IdempotencyConflict(SubmissionError):
    code = "idempotency_conflict"


class InvalidSubmission(SubmissionError):
    code = "invalid_submission"


@dataclass(frozen=True)
class Timing:
    seconds: int | None
    source: str


def clean_timing(value) -> Timing:
    """Client-measured active time. Bad values are kept as null with a marked source,
    never rejected: losing the answer would be worse than losing the timing."""
    if value is None:
        return Timing(None, "client_missing")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Timing(None, "client_invalid")
    if not (0 <= value <= MAX_RESPONSE_TIME_SEC):
        return Timing(None, "client_invalid")
    return Timing(round(value), "client_active_visible")


def clean_assistance(flag, types) -> tuple[bool | None, list[str]]:
    types = list(types or [])
    if any(t not in ASSISTANCE_TYPES for t in types) or len(set(types)) != len(types):
        raise InvalidSubmission(f"assistance types must be distinct values from {sorted(ASSISTANCE_TYPES)}")
    if flag not in (None, True, False):
        raise InvalidSubmission("assistance flag must be true, false or null")
    if flag is False and types:
        raise InvalidSubmission("assistance types given but assistance flag is false")
    if types:
        flag = True
    return flag, sorted(types)


def _active_enrollment(user, course, *, lock: bool) -> Enrollment:
    qs = Enrollment.objects.select_related("course")
    if lock:
        qs = qs.select_for_update()
    try:
        return qs.get(user=user, course=course, status=Enrollment.Status.ACTIVE)
    except Enrollment.DoesNotExist:
        raise NotEnrolled("student is not actively enrolled in this course") from None


def submit_answer(
    *,
    user,
    question: Question,
    idempotency_key: uuid.UUID,
    answer: str,
    study_session_id: uuid.UUID,
    response_time_sec=None,
    server_elapsed_sec: int | None = None,
    assistance_flag=None,
    assistance_types=None,
) -> tuple[SubmissionEvent, bool]:
    """Record one submit. Returns (event, created). A retry with the same idempotency key
    and the same answer returns the original event; a different answer is a conflict."""
    if not isinstance(answer, str) or not answer.strip():
        raise InvalidSubmission("answer is empty")
    if len(answer) > MAX_ANSWER_CHARS:
        raise InvalidSubmission("answer is too long")
    timing = clean_timing(response_time_sec)
    flag, types = clean_assistance(assistance_flag, assistance_types)
    pseudonym = pseudonymous_id(user.pk)

    with transaction.atomic():
        # Locking the enrollment row serialises this student's submits in this course,
        # so attempt_index is gap-free and never duplicated.
        enrollment = _active_enrollment(user, question.course, lock=True)

        existing = (
            SubmissionEvent.objects.select_related("answer")
            .filter(student_pseudonym=pseudonym, idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            if existing.question_id != question.pk or existing.answer.text != answer:
                raise IdempotencyConflict("idempotency key was already used for a different submission")
            return existing, False

        question = Question.objects.select_related("current_version").get(pk=question.pk)
        version = question.current_version
        if version is None:
            raise QuestionNotAvailable("question has no published version")

        last_index = SubmissionEvent.objects.filter(student_pseudonym=pseudonym, question=question).aggregate(
            m=Max("attempt_index")
        )["m"]
        # Replay order is (timestamp, event_id) with timestamps exported in milliseconds. Keep one
        # student's events strictly increasing at that resolution even when the clock is coarse
        # (Windows) or does not advance; the enrollment lock above serialises this student's submits.
        submitted_at = timezone.now()
        previous = SubmissionEvent.objects.filter(student_pseudonym=pseudonym, course=enrollment.course).aggregate(
            m=Max("submitted_at")
        )["m"]
        if previous is not None and submitted_at < previous + timedelta(milliseconds=1):
            submitted_at = previous + timedelta(milliseconds=1)
        hint_count = HintView.objects.filter(student_pseudonym=pseudonym, question=question).count()
        concept_snapshot = [
            {"concept": link.concept.code, "weight": link.weight}
            for link in version.concept_links.select_related("concept").order_by("concept__code")
        ]

        if question.kind == Question.Kind.SHORT:
            grade = grade_short(answer, version.accepted_answers)
            grading_status, grader_version = SubmissionEvent.GradingStatus.AUTO, SHORT_GRADER_VERSION
        else:
            grade = None
            grading_status, grader_version = SubmissionEvent.GradingStatus.PENDING, CODE_PENDING_GRADER_VERSION

        event = SubmissionEvent.objects.create(
            idempotency_key=idempotency_key,
            student_pseudonym=pseudonym,
            course=enrollment.course,
            study_session_id=study_session_id,
            question=question,
            question_version=version,
            attempt_index=(last_index or 0) + 1,
            submitted_at=submitted_at,
            correct=grade.correct if grade else None,
            score=grade.score if grade else None,
            tests_passed=grade.tests_passed if grade else None,
            tests_total=grade.tests_total if grade else None,
            grading_status=grading_status,
            grader_version=grader_version,
            response_time_sec=timing.seconds,
            timing_source=timing.source,
            server_elapsed_sec=server_elapsed_sec,
            hint_count=hint_count,
            assistance_flag=flag,
            assistance_types=types,
            concept_snapshot=concept_snapshot,
            is_synthetic=enrollment.course.is_synthetic,
        )
        SubmissionAnswer.objects.create(event=event, text=answer)
    return event, True


def show_hint(*, user, question: Question, hint_index: int) -> str:
    """Reveal hint `hint_index` (0-based) of the current version. Hints open in order;
    re-opening an already shown hint is not counted again."""
    version = question.current_version
    if version is None:
        raise QuestionNotAvailable("question has no published version")
    _active_enrollment(user, question.course, lock=False)
    if not (0 <= hint_index < len(version.hints)):
        raise InvalidSubmission("no such hint")
    pseudonym = pseudonymous_id(user.pk)
    shown = HintView.objects.filter(student_pseudonym=pseudonym, question_version=version).count()
    if hint_index > shown:
        raise InvalidSubmission("hints must be opened in order")
    HintView.objects.get_or_create(
        student_pseudonym=pseudonym,
        question_version=version,
        hint_index=hint_index,
        defaults={"question": question},
    )
    return version.hints[hint_index]


def revise_grade(
    *, event: SubmissionEvent, correct: bool, score: float, source: str, revised_by=None, reason="", feedback="",
    tests_passed=None, tests_total=None,
) -> GradeRevision:
    if not (0 <= score <= 1):
        raise InvalidSubmission("score must be between 0 and 1")
    return GradeRevision.objects.create(
        event=event,
        correct=correct,
        score=score,
        tests_passed=tests_passed,
        tests_total=tests_total,
        source=source,
        reason=reason,
        feedback=feedback,
        revised_by=revised_by,
    )
