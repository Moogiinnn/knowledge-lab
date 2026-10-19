"""C++ submissions (ICPC-style): language plumbing and the real g++ sandbox."""

import pytest
from django.urls import reverse

from apps.curriculum.models import QuestionVersion
from apps.events.models import GradeRevision
from apps.grading.code import grade_next
from apps.grading.models import CodeRun
from apps.grading.runner import Case, CaseResult, DockerRunner, RunnerConfig, docker_available, image_present

from .test_code_grading import FakeRunner, code_question
from .test_study_views import client_for  # noqa: F401  (fixture)

# A whole program reading stdin, as in programming contests (no driver).
CASES = ((1, "3\n3 1 2", "1 2 3", False), (2, "1\n5", "5", False), (3, "4\n4 4 -1 0", "-1 0 4 4", True))
GOOD = """#include <bits/stdc++.h>
using namespace std;
int main() {
    int n; cin >> n;
    vector<int> a(n);
    for (auto &x : a) cin >> x;
    sort(a.begin(), a.end());
    for (int i = 0; i < n; i++) cout << a[i] << (i + 1 < n ? ' ' : '\\n');
}"""


def cpp_question(course, code="sort-cpp"):
    return code_question(course, code=code, cases=CASES, driver="", language="CPP")


def test_cpp_reaches_the_runner_and_uses_the_compiler_image(course, student, submit):
    q = cpp_question(course)
    assert QuestionVersion.objects.get(pk=q.current_version_id).language == "CPP"
    event, _ = submit(student, q, GOOD)
    runner = FakeRunner({1: CaseResult(1, "ok", stdout="1 2 3"), 2: CaseResult(2, "ok", stdout="5"),
                         3: CaseResult(3, "ok", stdout="-1 0 4 4")})  # fmt: skip
    assert grade_next(runner) == event
    assert runner.calls[0][3] == "CPP"
    assert "knowledge-lab-c" in CodeRun.objects.get(event=event).grader_version
    assert GradeRevision.objects.get(event=event).correct


def test_cpp_label_and_editor_mode(client_for, course, student):
    q = cpp_question(course)
    html = client_for(student).get(reverse("question", args=[q.pk])).content.decode()
    assert "solution.cpp · C++17 · g++" in html and 'data-mode="text/x-c++src"' in html


needs_image = pytest.mark.skipif(
    not (docker_available() and image_present(RunnerConfig.from_settings().c_image)),
    reason="Docker or the C/C++ image is missing (manage.py check_code_runner --build-c)",
)


@needs_image
@pytest.mark.parametrize(
    "code,status,stdout",
    [
        (GOOD, "ok", "1 2 3"),
        ("int main(){ return y; }", "compile_error", None),
        ("int main(){ for(;;); }", "timeout", None),
        ("#include <vector>\nint main(){ std::vector<int> v; return v.at(3); }", "runtime_error", None),
    ],
)
def test_cpp_sandbox(code, status, stdout):
    (result,) = DockerRunner().run(code, "", [Case(1, "3\n3 1 2")], language="CPP")
    assert result.status == status
    if stdout is not None:
        assert result.stdout.strip() == stdout
    if status == "compile_error":
        assert "solution.cpp" in result.stderr and "/tmp/" not in result.stderr


@needs_image
def test_cpp_end_to_end(course, student, submit):
    q = cpp_question(course)
    event, _ = submit(student, q, GOOD)
    grade_next(DockerRunner())
    revision = GradeRevision.objects.get(event=event)
    assert (revision.correct, revision.tests_passed, revision.tests_total) == (True, 3, 3)
