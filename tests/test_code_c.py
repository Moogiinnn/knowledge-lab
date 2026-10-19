"""C submissions: language plumbing, compile errors, and the real gcc sandbox."""

import pytest
from django.db import DatabaseError, transaction

from apps.curriculum.models import QuestionVersion
from apps.curriculum.services import copy_as_draft
from apps.events.models import GradeRevision
from apps.grading.code import build_feedback, grade_next
from apps.grading.models import CodeRun, CodeTestCase
from apps.grading.runner import Case, CaseResult, DockerRunner, RunnerConfig, docker_available, image_present

from .test_code_grading import FakeRunner, code_question

C_DRIVER = """#include <stdio.h>
int main(void) {
    int n, a[1000];
    if (scanf("%d", &n) != 1) return 1;
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    printf("%d\\n", sum(a, n));
    return 0;
}"""
C_CASES = ((1, "3\n1 2 3", "6", False), (2, "0", "0", False), (3, "3\n-5 5 10", "10", True))
GOOD = "int sum(const int *a, int n) { int s = 0; for (int i = 0; i < n; i++) s += a[i]; return s; }"


def c_question(course, code="sum-c"):
    return code_question(course, code=code, cases=C_CASES, driver=C_DRIVER, language="C")


def test_language_reaches_the_runner_and_grader_version(course, student, submit):
    q = c_question(course)
    event, _ = submit(student, q, GOOD)
    runner = FakeRunner({1: CaseResult(1, "ok", stdout="6"), 2: CaseResult(2, "ok", stdout="0"),
                         3: CaseResult(3, "ok", stdout="10")})  # fmt: skip
    assert grade_next(runner) == event
    assert runner.calls[0][3] == "C"
    revision = GradeRevision.objects.get(event=event)
    assert revision.correct and revision.score == 1.0
    assert ":fake" not in CodeRun.objects.get(event=event).grader_version  # C uses c_image, not image
    assert "knowledge-lab-c" in CodeRun.objects.get(event=event).grader_version


def test_compile_error_fails_every_case_and_shows_compiler(course, student, submit):
    q = c_question(course)
    event, _ = submit(student, q, "int sum(const int *a, int n) { return s; }")
    message = "solution.c:1:39: error: 's' undeclared (first use in this function)"
    runner = FakeRunner({i: CaseResult(i, "compile_error", stderr=message) for i in (1, 2, 3)})
    grade_next(runner)
    revision = GradeRevision.objects.get(event=event)
    assert (revision.correct, revision.score, revision.tests_passed) == (False, 0.0, 0)
    assert "хөрвүүлэлтийн алдаа" in revision.feedback and "'s' undeclared" in revision.feedback
    assert "Оролт" not in revision.feedback  # hidden and visible tests are not listed when nothing ran


def test_compile_feedback_is_capped():
    cases = [CodeTestCase(order=1, stdin="", expected_stdout="1", is_hidden=False)]
    long = "\n".join(f"line {i}" for i in range(50))
    feedback = build_feedback(cases, {1: CaseResult(1, "compile_error", stderr=long)})
    assert "line 11" in feedback and "line 12" not in feedback


def test_language_is_frozen_and_copied(course):
    q = c_question(course)
    with pytest.raises(DatabaseError, match="VERSION_FROZEN"), transaction.atomic():
        QuestionVersion.objects.filter(pk=q.current_version_id).update(language="PYTHON")
    assert copy_as_draft(q.current_version).language == "C"


def test_c_container_may_execute_from_tmp_but_python_may_not():
    runner = DockerRunner(RunnerConfig())
    c_cmd, py_cmd = " ".join(runner.command("x", "C")), " ".join(runner.command("x", "PYTHON"))
    assert "knowledge-lab-c:2" in c_cmd and "/tmp:rw,exec," in c_cmd
    assert " ".join(runner.command("x", "CPP")) == c_cmd  # C++ uses the same compiler image and limits
    assert "python:3.12-slim" in py_cmd and "/tmp:rw,noexec," in py_cmd
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--user 65534:65534", "--pids-limit 64"]:
        assert flag in c_cmd, flag


# --- Real gcc sandbox (skipped without Docker or the C image) --------------------------

needs_c_image = pytest.mark.skipif(
    not (docker_available() and image_present(RunnerConfig.from_settings().c_image)),
    reason="Docker or the C image is missing (manage.py check_code_runner --build-c)",
)


@needs_c_image
@pytest.mark.parametrize(
    "code,status,stdout",
    [
        (GOOD, "ok", "6"),
        ("int sum(const int *a, int n) { return n; }", "ok", "3"),  # wrong answer, still runs
        ("int sum(const int *a, int n) { return s; }", "compile_error", None),
        ("int sum(const int *a, int n) { for (;;); }", "timeout", None),
        ("int sum(const int *a, int n) { int *p = 0; return *p; }", "runtime_error", None),  # segfault
    ],
)
def test_c_sandbox(code, status, stdout):
    (result,) = DockerRunner().run(code, C_DRIVER, [Case(1, "3\n1 2 3")], language="C")
    assert result.status == status
    if stdout is not None:
        assert result.stdout.strip() == stdout
    if status == "compile_error":
        assert "solution.c" in result.stderr and "/tmp/" not in result.stderr


@needs_c_image
def test_c_end_to_end(course, student, submit):
    q = c_question(course)
    event, _ = submit(student, q, GOOD)
    grade_next(DockerRunner())
    revision = GradeRevision.objects.get(event=event)
    assert (revision.correct, revision.tests_passed, revision.tests_total) == (True, 3, 3)


def test_teacher_creates_c_version_in_admin(client, course):
    from django.urls import reverse

    from apps.curriculum.models import Question

    from .test_teacher_admin import PASSWORD, management, version_post

    from django.contrib.auth import get_user_model
    from apps.accounts.roles import ensure_teacher_group

    teacher = get_user_model().objects.create_user(username="t-c", password=PASSWORD, is_staff=True)
    teacher.groups.add(ensure_teacher_group())
    client.force_login(teacher)
    question = Question.objects.create(course=course, code="c-new", title="C бодлого", kind="CODE")
    data = version_post(question, [(course.concepts.get(code="array"), 1.0)], answers="")
    data.update({"language": "C", "code_driver": C_DRIVER, **management("test_cases", 0)})
    response = client.post(reverse("admin:curriculum_questionversion_add"), data)
    assert response.status_code == 302, response.content.decode()[:1500]
    assert QuestionVersion.objects.get(question=question).language == "C"
