from django.db import models

from apps.curriculum.models import QuestionVersion


class CodeTestCase(models.Model):
    """One input/expected-output pair for a CODE question version."""

    question_version = models.ForeignKey(QuestionVersion, on_delete=models.PROTECT, related_name="test_cases")
    order = models.PositiveSmallIntegerField()
    stdin = models.TextField(blank=True)
    expected_stdout = models.TextField()
    # Hidden cases are graded but their content is not shown to students.
    is_hidden = models.BooleanField(default=True)

    class Meta:
        verbose_name = "кодын тест"
        verbose_name_plural = "кодын тестүүд"
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["question_version", "order"], name="test_case_order_unique")]


class CodeRun(models.Model):
    """One attempt to run an event's code against its version's test cases.

    A run that fails for infrastructure reasons (Docker down, image missing) keeps
    `infra_error` and produces no grade; the worker retries later.
    """

    event = models.ForeignKey("events.SubmissionEvent", on_delete=models.PROTECT, related_name="code_runs")
    grader_version = models.CharField(max_length=80)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    # [{"order", "status", "duration_ms", "stdout", "stderr"}], outputs truncated.
    results = models.JSONField(default=list)
    infra_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "кодын ажиллуулалт"
        verbose_name_plural = "кодын ажиллуулалтууд"
        indexes = [models.Index(fields=["event", "started_at"], name="code_run_event")]
