"""import_problems: the example folder imports; bad input is refused before anything is written."""

import shutil
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.curriculum.models import Course, Question, QuestionVersion

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "import_example"


@pytest.fixture
def icpc(db):
    course = Course.objects.create(code="icpc-test", name="ICPC test", is_synthetic=True)
    call_command("import_graph", course.code, str(EXAMPLE / "concepts.csv"), str(EXAMPLE / "edges.csv"))
    return course


def test_example_folder_imports_and_publishes(icpc):
    call_command("import_problems", icpc.code, str(EXAMPLE / "problems"), "--publish")
    assert set(Question.objects.filter(course=icpc).values_list("code", flat=True)) == {
        "bfs-shortest", "prefix-sum-queries", "stack-order"}  # fmt: skip
    bfs = Question.objects.get(course=icpc, code="bfs-shortest").current_version
    assert bfs.status == QuestionVersion.Status.PUBLISHED and bfs.difficulty == 3 and bfs.language == "CPP"
    assert {qc.concept.code: qc.weight for qc in bfs.concept_links.select_related("concept")} == {
        "queue": 0.4, "bfs": 0.4, "graph": 0.2}  # fmt: skip
    tests = list(bfs.test_cases.all())
    assert [t.is_hidden for t in tests] == [True, True, False, False]  # 03, 04, sample-1, sample-2 (name order)
    assert tests[2].expected_stdout == "2"
    short = Question.objects.get(course=icpc, code="stack-order").current_version
    assert short.accepted_answers == ["2"] and len(short.hints) == 2
    assert [qc.weight for qc in short.concept_links.all()] == [1.0]  # no weight given: equal weights


def test_second_run_skips_published_questions(icpc):
    call_command("import_problems", icpc.code, str(EXAMPLE / "problems"), "--publish")
    call_command("import_problems", icpc.code, str(EXAMPLE / "problems"), "--publish")
    assert QuestionVersion.objects.filter(question__course=icpc).count() == 3
    call_command("import_problems", icpc.code, str(EXAMPLE / "problems"), "--publish", "--new-version")
    bfs = Question.objects.get(course=icpc, code="bfs-shortest")
    assert bfs.versions.count() == 2 and bfs.current_version.version == 2


def test_dry_run_writes_nothing(icpc):
    call_command("import_problems", icpc.code, str(EXAMPLE / "problems"), "--dry-run")
    assert not Question.objects.filter(course=icpc).exists()


def test_bad_rows_are_all_reported_and_nothing_is_written(icpc, tmp_path):
    folder = tmp_path / "problems"
    shutil.copytree(EXAMPLE / "problems", folder)
    csv_path = folder / "problems.csv"
    text = csv_path.read_text(encoding="utf-8-sig")
    text = text.replace("queue:0.4;bfs:0.4;graph:0.2", "queue:0.5;bfs:0.4;graph:0.2")
    text = text.replace("array:0.3;prefix-sum:0.7", "array:0.3;heap:0.7")
    csv_path.write_text(text.replace(",SHORT,1,", ",SHORT,7,"), encoding="utf-8")
    with pytest.raises(CommandError) as exc:
        call_command("import_problems", icpc.code, str(folder))
    message = str(exc.value)
    assert "sum to 1" in message and "'heap'" in message and "difficulty" in message
    assert not Question.objects.filter(course=icpc).exists()
