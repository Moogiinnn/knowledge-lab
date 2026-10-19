"""Research interaction log.

One SubmissionEvent row = one submit of one answer by one student. Rows are append-only:
grading corrections go to GradeRevision, and the export reports both the original
automatic grade and the final grade.

Rows store only the research pseudonym, never the user id, so the log can be exported
without joining to auth tables. The answer text is kept in SubmissionAnswer so it is
not part of the default export (code can contain names).
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.curriculum.models import Course, Question, QuestionVersion


class SubmissionEvent(models.Model):
    class GradingStatus(models.TextChoices):
        AUTO = "AUTO"  # graded by the server at submit time
        PENDING = "PENDING"  # waiting for code runner or teacher

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Client-generated UUID per submit; a retry of the same request returns the same event.
    idempotency_key = models.UUIDField()
    student_pseudonym = models.CharField(max_length=40, db_index=True)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="events")
    # Random per-login id (not the Django session key).
    study_session_id = models.UUIDField()
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="events")
    question_version = models.ForeignKey(QuestionVersion, on_delete=models.PROTECT, related_name="events")
    attempt_index = models.PositiveIntegerField()
    submitted_at = models.DateTimeField()  # server clock, UTC

    correct = models.BooleanField(null=True)
    score = models.FloatField(null=True)  # normalised to [0, 1]
    tests_passed = models.PositiveIntegerField(null=True)
    tests_total = models.PositiveIntegerField(null=True)
    grading_status = models.CharField(max_length=8, choices=GradingStatus.choices)
    grader_version = models.CharField(max_length=40)

    response_time_sec = models.PositiveIntegerField(null=True)  # null = not measured
    timing_source = models.CharField(max_length=40, default="client_active_visible")
    # Server-side wall time from first opening the question page (since the previous
    # submit) to this submit. Includes idle time; a cross-check for response_time_sec.
    server_elapsed_sec = models.PositiveIntegerField(null=True)
    # Hints shown for this question up to this submit, counted on the server.
    hint_count = models.PositiveIntegerField(default=0)
    # Self-reported. null = student did not answer the question.
    assistance_flag = models.BooleanField(null=True)
    assistance_types = models.JSONField(default=list)

    # [{"concept": code, "weight": a_qi}] of question_version at submit time.
    concept_snapshot = models.JSONField()
    is_synthetic = models.BooleanField(default=False)

    class Meta:
        verbose_name = "оролдлого"
        verbose_name_plural = "оролдлогууд (event log)"
        constraints = [
            models.UniqueConstraint(fields=["student_pseudonym", "idempotency_key"], name="event_idempotency"),
            models.UniqueConstraint(
                fields=["student_pseudonym", "question", "attempt_index"], name="event_attempt_index_unique"
            ),
            models.CheckConstraint(condition=Q(attempt_index__gte=1), name="event_attempt_index_positive"),
            models.CheckConstraint(
                condition=Q(score__isnull=True) | (Q(score__gte=0) & Q(score__lte=1)), name="event_score_range"
            ),
            models.CheckConstraint(
                condition=Q(tests_passed__isnull=True, tests_total__isnull=True)
                | Q(tests_passed__isnull=False, tests_total__isnull=False, tests_passed__lte=F("tests_total")),
                name="event_tests_consistent",
            ),
            models.CheckConstraint(
                condition=Q(response_time_sec__isnull=True) | Q(response_time_sec__lte=86400),
                name="event_response_time_range",
            ),
            models.CheckConstraint(
                condition=Q(grading_status="PENDING", correct__isnull=True, score__isnull=True)
                | Q(grading_status="AUTO", correct__isnull=False, score__isnull=False),
                name="event_grading_status_consistent",
            ),
        ]
        indexes = [models.Index(fields=["course", "submitted_at", "event_id"], name="event_replay_order")]


class SubmissionAnswer(models.Model):
    event = models.OneToOneField(SubmissionEvent, on_delete=models.PROTECT, primary_key=True, related_name="answer")
    text = models.TextField()

    class Meta:
        verbose_name = "хариулт"
        verbose_name_plural = "хариултууд"


class GradeRevision(models.Model):
    """Append-only grading history. The latest revision is the event's final grade."""

    event = models.ForeignKey(SubmissionEvent, on_delete=models.PROTECT, related_name="revisions")
    correct = models.BooleanField("зөв эсэх")
    score = models.FloatField("оноо (0–1)", validators=[MinValueValidator(0), MaxValueValidator(1)])
    tests_passed = models.PositiveIntegerField(null=True, blank=True)
    tests_total = models.PositiveIntegerField(null=True, blank=True)
    source = models.CharField(max_length=20)  # "teacher" | "code_runner"
    reason = models.TextField("засварын шалтгаан (дотоод)", blank=True)
    feedback = models.TextField("оюутанд харагдах тайлбар", blank=True)
    revised_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    revised_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "дүнгийн засвар"
        verbose_name_plural = "дүнгийн засварууд"
        constraints = [
            models.CheckConstraint(condition=Q(score__gte=0) & Q(score__lte=1), name="revision_score_range"),
        ]
        indexes = [models.Index(fields=["event", "revised_at"], name="revision_latest")]


class HintView(models.Model):
    """A hint shown to a student. hint_count on SubmissionEvent is derived from these rows."""

    student_pseudonym = models.CharField(max_length=40)
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="+")
    question_version = models.ForeignKey(QuestionVersion, on_delete=models.PROTECT, related_name="+")
    hint_index = models.PositiveSmallIntegerField()
    shown_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "hint үзэлт"
        verbose_name_plural = "hint үзэлтүүд"
        constraints = [
            models.UniqueConstraint(
                fields=["student_pseudonym", "question_version", "hint_index"], name="hint_view_once"
            ),
        ]
