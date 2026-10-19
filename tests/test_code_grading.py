import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction

from apps.curriculum.models import Question, QuestionVersion
from apps.curriculum.services import copy_as_draft, create_version, publish_version
from apps.events.export import export_rows
from apps.events.models import GradeRevision
from apps.grading.code import build_feedback, grade_next, gradable_events, normalize_output
from apps.grading.models import CodeRun, CodeTestCase
from apps.grading.runner import Case, CaseResult, DockerRunner, InfrastructureError, RunnerConfig, docker_available

DRIVER = "import sys, json\nprint(total(json.loads(sys.stdin.read())))"


class FakeRunner:
    """Evaluates nothing: returns canned results so grading logic is tested without Docker."""

    config = RunnerConfig(image="fake")

    def __init__(self, outputs=None, error=None):
        self.outputs = outputs or {}
        self.error = error
        self.calls = []

    def run(self, code, driver, cases, language="PYTHON"):
        self.calls.append((code, driver, cases, language))
        if self.error:
            raise InfrastructureError(self.error)
        return [self.outputs.get(c.order, CaseResult(c.order, "ok", stdout="")) for c in cases]


def code_question(course, code="sum", cases=((1, "[1,2,3]", "6", False), (2, "[]", "0", True)), driver=DRIVER,
                  language="PYTHON"):  # fmt: skip
    question = Question.objects.create(course=course, code=code, title=code, kind="CODE")
    version = create_version(question, prompt="total(xs)", concept_weights={"array": 1}, code_driver=driver,
                             language=language)  # fmt: skip
    for order, stdin, expected, hidden in cases:
        CodeTestCase.objects.create(
            question_version=version, order=order, stdin=stdin, expected_stdout=expected, is_hidden=hidden
        )
    publish_version(version)
    question.refresh_from_db()
    return question


def test_normalize_output():
    assert normalize_output("6  \r\n\n\n") == "6"
    assert normalize_output("a \nb") == "a\nb"
    assert normalize_output("6") != normalize_output("06")


def test_all_tests_passing_gives_full_score(course, student, submit):
    q = code_question(course)
    event, _ = submit(student, q, "def total(xs): return sum(xs)")
    runner = FakeRunner({1: CaseResult(1, "ok", stdout="6\n"), 2: CaseResult(2, "ok", stdout="0")})
    assert grade_next(runner) == event
    code, driver, cases, language = runner.calls[0]
    assert language == "PYTHON"
    assert code == "def total(xs): return sum(xs)" and driver == DRIVER
    assert [c.stdin for c in cases] == ["[1,2,3]", "[]"]
    revision = GradeRevision.objects.get(event=event)
    assert (revision.correct, revision.score, revision.tests_passed, revision.tests_total) == (True, 1.0, 2, 2)
    assert revision.source == "code_runner" and revision.revised_by is None
    assert CodeRun.objects.get(event=event).results[0]["status"] == "ok"
    assert grade_next(runner) is None  # nothing left in the queue


def test_partial_score_and_feedback_hides_hidden_cases(course, student, submit):
    q = code_question(course)
    submit(student, q, "def total(xs): return sum(xs) + 1")
    grade_next(FakeRunner({1: CaseResult(1, "ok", stdout="7"), 2: CaseResult(2, "ok", stdout="1")}))
    revision = GradeRevision.objects.get()
    assert (revision.correct, revision.score, revision.tests_passed) == (False, 0.0, 0)
    assert "Тест 1" in revision.feedback and "Хүлээгдсэн: 6" in revision.feedback and "Таны гаралт: 7" in revision.feedback
    assert "Нууц тестээс 1" in revision.feedback
    assert "[]" not in revision.feedback  # hidden input is never shown


def test_runtime_error_and_timeout_fail(course, student, submit):
    q = code_question(course)
    submit(student, q, "boom")
    grade_next(FakeRunner({
        1: CaseResult(1, "runtime_error", stderr="Traceback...\nNameError: name 'boom' is not defined"),
        2: CaseResult(2, "timeout"),
    }))  # fmt: skip
    revision = GradeRevision.objects.get()
    assert revision.tests_passed == 0
    assert "NameError" in revision.feedback


def test_truncated_output_does_not_pass(course, student, submit):
    q = code_question(course, cases=((1, "", "6", False),))
    submit(student, q, "x")
    grade_next(FakeRunner({1: CaseResult(1, "ok", stdout="6", stdout_truncated=True)}))
    assert GradeRevision.objects.get().correct is False


def test_missing_result_counts_as_failure(course, student, submit):
    q = code_question(course)
    submit(student, q, "x")

    class Short(FakeRunner):
        def run(self, code, driver, cases, language="PYTHON"):
            return [CaseResult(1, "ok", stdout="6")]

    grade_next(Short())
    assert GradeRevision.objects.get().tests_passed == 1


def test_infra_error_records_run_and_leaves_event_pending(course, student, submit):
    q = code_question(course)
    event, _ = submit(student, q, "x")
    with pytest.raises(InfrastructureError):
        grade_next(FakeRunner(error="daemon down"))
    assert CodeRun.objects.get(event=event).infra_error == "daemon down"
    assert not GradeRevision.objects.exists()
    assert list(gradable_events()) == [event]  # retried later


def test_teacher_graded_and_testless_events_are_skipped(course, student, submit):
    graded = code_question(course, code="graded")
    event, _ = submit(student, graded, "x")
    GradeRevision.objects.create(event=event, correct=True, score=1, source="teacher")
    no_tests = code_question(course, code="no-tests", cases=())
    submit(student, no_tests, "x")
    assert grade_next(FakeRunner()) is None


def test_export_reports_code_runner_grade(course, student, submit):
    q = code_question(course)
    submit(student, q, "x")
    grade_next(FakeRunner({1: CaseResult(1, "ok", stdout="6"), 2: CaseResult(2, "ok", stdout="5")}))
    (row,) = list(export_rows(course))
    assert (row["correct"], row["score"], row["tests_passed"], row["tests_total"]) == (0, 0.5, 1, 2)
    assert row["grading_status"] == "REVISED:code_runner" and row["auto_correct"] == ""


def test_code_driver_is_frozen_and_copied(course):
    q = code_question(course)
    with pytest.raises(DatabaseError, match="VERSION_FROZEN"), transaction.atomic():
        QuestionVersion.objects.filter(pk=q.current_version_id).update(code_driver="print(1)")
    draft = copy_as_draft(q.current_version)
    assert draft.code_driver == DRIVER
    assert list(draft.test_cases.values_list("order", "stdin", "expected_stdout")) == [
        (1, "[1,2,3]", "6"), (2, "[]", "0"),
    ]


def test_worker_command_once(course, student, submit, monkeypatch):
    q = code_question(course)
    submit(student, q, "x")
    fake = FakeRunner({1: CaseResult(1, "ok", stdout="6"), 2: CaseResult(2, "ok", stdout="0")})
    monkeypatch.setattr("apps.grading.management.commands.run_code_grader.DockerRunner", lambda: fake)
    out = io.StringIO()
    call_command("run_code_grader", "--once", stdout=out)
    assert "1 submissions graded" in out.getvalue()
    monkeypatch.setattr(
        "apps.grading.management.commands.run_code_grader.DockerRunner", lambda: FakeRunner(error="down")
    )
    submit(student, q, "y")
    with pytest.raises(CommandError, match="Docker unavailable"):
        call_command("run_code_grader", "--once", stdout=io.StringIO())


def test_docker_command_is_locked_down():
    cmd = DockerRunner(RunnerConfig()).command("kl-test")
    joined = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--user 65534:65534", "no-new-privileges",
                 "--pids-limit 64", "--memory 256m", "--memory-swap 256m"]:
        assert flag in joined, flag
    assert "-v" not in cmd and "--volume" not in cmd and "--mount" not in cmd


def test_runner_reports_missing_docker():
    runner = DockerRunner(RunnerConfig(docker="definitely-not-docker"))
    with pytest.raises(InfrastructureError):
        runner.run("print(1)", "", [Case(1, "")])


# --- Real sandbox (skipped when Docker is not running) ------------------------------

needs_docker = pytest.mark.skipif(not docker_available(), reason="Docker is not running")


@needs_docker
@pytest.mark.parametrize(
    "code,status,stdout",
    [
        ("def total(xs): return sum(xs)", "ok", "6"),
        ("def total(xs):\n    while True: pass", "timeout", None),
        ("import socket\nsocket.create_connection(('1.1.1.1', 53), 2)\ndef total(xs): return 0", "runtime_error", None),
        ("open('/etc/evil', 'w')\ndef total(xs): return 0", "runtime_error", None),
        ("import os\nprint(os.getuid())\ndef total(xs): return 0", "ok", "65534\n0"),
    ],
)
def test_docker_sandbox(code, status, stdout):
    (result,) = DockerRunner().run(code, DRIVER, [Case(1, "[1,2,3]")])
    assert result.status == status
    if stdout is not None:
        assert normalize_output(result.stdout).endswith(normalize_output(stdout))


@needs_docker
def test_docker_output_flood_is_contained():
    (result,) = DockerRunner().run("while True: print('x' * 1000)", "", [Case(1, "")])
    assert result.status != "ok" or result.stdout_truncated
