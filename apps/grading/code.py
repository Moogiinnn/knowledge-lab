"""Grading CODE submissions from runner results.

The result is written as a GradeRevision with source="code_runner" (events are
append-only), together with a CodeRun row holding per-test details for analysis.
"""

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.curriculum.models import Question
from apps.events.models import GradeRevision, SubmissionEvent
from apps.events.services import revise_grade

from .models import CodeRun, CodeTestCase
from .runner import Case, CaseResult, InfrastructureError

GRADER_PREFIX = "code-1"
SOURCE = "code_runner"
STATUS_TEXT = {
    "runtime_error": "алдаа гарч зогссон",
    "timeout": "хугацаа хэтэрсэн",
    "sandbox_error": "санах ой эсвэл орчны хязгаар хэтэрсэн",
    "compile_error": "хөрвүүлэлтийн алдаа",
}
COMPILER_LINES = 12


def normalize_output(text: str) -> str:
    """Ignore trailing spaces on each line and trailing blank lines; nothing else."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def case_passed(case: CodeTestCase, result: CaseResult) -> bool:
    return (
        result.status == "ok"
        and not result.stdout_truncated
        and normalize_output(result.stdout) == normalize_output(case.expected_stdout)
    )


def build_feedback(cases: list[CodeTestCase], results: dict[int, CaseResult]) -> str:
    compile_errors = [r for r in results.values() if r.status == "compile_error"]
    if compile_errors:
        # The program never ran, so no test is meaningful; show the compiler instead.
        message = compile_errors[0].stderr.strip().splitlines()[:COMPILER_LINES]
        return "\n".join([f"Тест: 0/{len(cases)} давсан. Код хөрвөсөнгүй (хөрвүүлэлтийн алдаа):", *message])
    passed = sum(case_passed(c, results[c.order]) for c in cases)
    lines = [f"Тест: {passed}/{len(cases)} давсан."]
    hidden_failed = 0
    for case in cases:
        result = results[case.order]
        if case_passed(case, result):
            continue
        if case.is_hidden:
            hidden_failed += 1
            continue
        reason = STATUS_TEXT.get(result.status, "гаралт таарахгүй")
        lines.append(f"Тест {case.order}: {reason}.")
        lines.append(f"  Оролт: {case.stdin.strip()[:200] or '(хоосон)'}")
        lines.append(f"  Хүлээгдсэн: {normalize_output(case.expected_stdout)[:200]}")
        if result.status == "ok":
            lines.append(f"  Таны гаралт: {normalize_output(result.stdout)[:200] or '(хоосон)'}")
        elif result.stderr.strip():
            lines.append(f"  Алдаа: {result.stderr.strip().splitlines()[-1][:200]}")
    if hidden_failed:
        lines.append(f"Нууц тестээс {hidden_failed} нь давсангүй.")
    return "\n".join(lines)


def gradable_events():
    """PENDING CODE events that nobody has graded yet and whose version has tests."""
    return (
        SubmissionEvent.objects.filter(
            grading_status=SubmissionEvent.GradingStatus.PENDING, question__kind=Question.Kind.CODE
        )
        .exclude(Exists(GradeRevision.objects.filter(event=OuterRef("pk"))))
        .filter(Exists(CodeTestCase.objects.filter(question_version=OuterRef("question_version"))))
        .order_by("submitted_at", "event_id")
    )


def grade_next(runner) -> SubmissionEvent | None:
    """Grade the oldest gradable event. Returns it, or None when the queue is empty.
    Raises InfrastructureError (after recording it) when Docker cannot run."""
    event, started = None, timezone.now()
    try:
        with transaction.atomic():
            event = (
                gradable_events().select_for_update(skip_locked=True, of=("self",)).select_related("question_version")
            ).first()
            if event is None:
                return None
            grade_event(event, runner)
    except InfrastructureError as exc:
        # Recorded outside the rolled-back transaction so failures stay visible.
        language = event.question_version.language if event else "PYTHON"
        CodeRun.objects.create(
            event=event, grader_version=grader_version(runner, language), started_at=started, finished_at=timezone.now(),
            infra_error=str(exc)[:2000],
        )  # fmt: skip
        raise
    return event


def grader_version(runner, language: str = "PYTHON") -> str:
    return f"{GRADER_PREFIX}:{runner.config.tag_for(language)}"


def grade_event(event: SubmissionEvent, runner) -> CodeRun:
    """Run and grade one event. InfrastructureError propagates without writing anything."""
    version = event.question_version
    cases = list(version.test_cases.order_by("order"))
    started = timezone.now()
    raw = runner.run(event.answer.text, version.code_driver, [Case(c.order, c.stdin) for c in cases],
                     language=version.language)  # fmt: skip
    results = {r.order: r for r in raw}
    for case in cases:  # a result missing for a case counts as a failure
        results.setdefault(case.order, CaseResult(case.order, "sandbox_error"))
    run = CodeRun.objects.create(
        event=event,
        grader_version=grader_version(runner, version.language),
        started_at=started,
        finished_at=timezone.now(),
        results=[results[c.order].as_json() for c in cases],
    )
    passed = sum(case_passed(c, results[c.order]) for c in cases)
    revise_grade(
        event=event,
        correct=passed == len(cases),
        score=passed / len(cases),
        tests_passed=passed,
        tests_total=len(cases),
        source=SOURCE,
        reason=grader_version(runner, version.language),
        feedback=build_feedback(cases, results),
    )
    return run
