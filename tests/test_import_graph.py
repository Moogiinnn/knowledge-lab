import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.curriculum.models import Concept, ConceptEdge, Course

from .conftest import make_short_question

TEMPLATES = Path(__file__).resolve().parent.parent / "docs" / "graph_templates"


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8-sig")
    return str(path)


def run(course, concepts, edges, *flags):
    out = io.StringIO()
    call_command("import_graph", course.code, concepts, edges, *flags, stdout=out)
    return out.getvalue()


@pytest.fixture
def files(tmp_path):
    concepts = write(tmp_path, "concepts.csv", "code,name,kind\n"
                     "array,Массив,Б\nstack,Стек,Б\nqueue,Дараалал,STRUCTURE\nbfs,BFS,А\ndfs,DFS,ALGORITHM\n")
    edges = write(tmp_path, "edges.csv", "source,target,relation,confidence,note\n"
                  "queue,bfs,PRE,3,BFS uses a queue\nstack,dfs,PRE,2,\narray,stack,REL,1,\n"
                  "stack,queue,—,,teacher: no link\narray,dfs,CONTROL,,negative control\n")
    return concepts, edges


def test_import_creates_approved_graph(course, files):
    out = run(course, *files)
    dfs = Concept.objects.get(course=course, code="dfs")
    assert dfs.kind == Concept.Kind.ALGORITHM and dfs.name == "DFS"
    assert Concept.objects.get(course=course, code="queue").kind == Concept.Kind.STRUCTURE
    edges = {(e.source.code, e.target.code): e for e in ConceptEdge.objects.filter(source__course=course)}
    assert set(edges) == {("queue", "bfs"), ("stack", "dfs"), ("array", "stack"), ("array", "dfs")}
    assert edges[("queue", "bfs")].confidence == 3 and edges[("queue", "bfs")].note == "BFS uses a queue"
    assert edges[("array", "dfs")].relation == ConceptEdge.Relation.CONTROL
    assert all(e.teacher_approved and e.approved_at for e in edges.values())
    assert "1 rows marked 'no edge'" in out
    assert "WARN  H1: RELATED edges 1" in out and "OK    H2: structure→algorithm edges 2" not in out


def test_dry_run_writes_nothing(course, files):
    before = (Concept.objects.count(), ConceptEdge.objects.count())
    out = run(course, *files, "--dry-run")
    assert "dry run: nothing was written" in out
    assert (Concept.objects.count(), ConceptEdge.objects.count()) == before


@pytest.mark.parametrize("edges_csv,message", [
    ("source,target,relation,confidence\narray,nope,PRE,\n", "unknown concept 'nope'"),
    ("source,target,relation,confidence\narray,stack,?,\n", "still undecided"),
    ("source,target,relation,confidence\narray,stack,MAYBE,\n", "unknown relation"),
    ("source,target,relation,confidence\narray,stack,PRE,5\n", "confidence must be 1, 2 or 3"),
    ("source,target,relation,confidence\narray,array,PRE,\n", "self loop"),
    ("source,target,relation,confidence\narray,stack,PRE,\narray,stack,PRE,\n", "duplicate edge"),
    ("source,target,relation,confidence\narray,stack,PRE,\nstack,queue,PRE,\nqueue,array,PRE,\n", "prerequisite cycle"),
    ("source,target\narray,stack\n", "missing columns"),
])  # fmt: skip
def test_invalid_input_is_rejected_and_nothing_written(course, tmp_path, edges_csv, message):
    concepts = write(tmp_path, "c.csv", "code,name,kind\narray,Массив,Б\nstack,Стек,Б\nqueue,Дараалал,Б\n")
    edges = write(tmp_path, "e.csv", edges_csv)
    before = ConceptEdge.objects.count()
    with pytest.raises(CommandError, match=message):
        run(course, concepts, edges)
    assert ConceptEdge.objects.count() == before


def test_bad_kind_rejected(course, tmp_path):
    concepts = write(tmp_path, "c.csv", "code,name,kind\narray,Массив,X\n")
    edges = write(tmp_path, "e.csv", "source,target,relation,confidence\n")
    with pytest.raises(CommandError, match="unknown kind"):
        run(course, concepts, edges)


def test_cycle_with_existing_edge_is_caught(course, tmp_path):
    ConceptEdge.objects.create(source=Concept.objects.get(course=course, code="stack"),
                               target=Concept.objects.get(course=course, code="array"))  # fmt: skip
    concepts = write(tmp_path, "c.csv", "code,name,kind\n")
    edges = write(tmp_path, "e.csv", "source,target,relation,confidence\narray,stack,PRE,\n")
    with pytest.raises(CommandError, match="prerequisite cycle"):
        run(course, concepts, edges)
    run(course, concepts, edges, "--replace")  # replacing drops the old edge, so no cycle
    assert list(ConceptEdge.objects.values_list("source__code", "target__code")) == [("array", "stack")]


def test_rerun_updates_instead_of_duplicating(course, files):
    run(course, *files)
    run(course, *files)
    assert ConceptEdge.objects.filter(source__course=course).count() == 4
    assert Concept.objects.filter(course=course, code="dfs").count() == 1


def test_graph_is_frozen_once_real_data_exists(course, student, short_question, submit, files):
    submit(student, short_question, "10")
    with pytest.raises(CommandError, match="graph is frozen"):
        run(course, *files)
    out = run(course, *files, "--dry-run")  # checking is still allowed
    assert "dry run" in out
    out = run(course, *files, "--force-after-collection")
    assert "record this deviation" in out


def test_synthetic_course_events_do_not_freeze_the_graph(db, tmp_path, submit):
    from .conftest import make_student

    demo = Course.objects.create(code="demo", name="demo", is_synthetic=True)
    for code in ("array", "stack"):
        Concept.objects.create(course=demo, code=code, name=code)
    submit(make_student(demo, "D1"), make_short_question(demo), "10")
    concepts = write(tmp_path, "c.csv", "code,name,kind\n")
    edges = write(tmp_path, "e.csv", "source,target,relation,confidence\narray,stack,PRE,\n")
    run(demo, concepts, edges)
    assert ConceptEdge.objects.filter(source__course=demo).count() == 1


def test_templates_are_valid_and_export_includes_new_fields(db, course):
    out = run(course, str(TEMPLATES / "concepts.csv"), str(TEMPLATES / "edges.csv"), "--dry-run")
    assert "30 rows marked 'no edge'" in out and "WARN  every concept has a kind" in out
    Concept.objects.filter(course=course, code="bfs").update(kind=Concept.Kind.ALGORITHM)
    ConceptEdge.objects.create(source=Concept.objects.get(course=course, code="queue"),
                               target=Concept.objects.get(course=course, code="bfs"), confidence=2)  # fmt: skip
    buf = io.StringIO()
    call_command("export_graph", course.code, stdout=buf)
    graph = json.loads(buf.getvalue())
    assert {"code": "bfs", "name": "bfs", "kind": "ALGORITHM"} in graph["concepts"]
    assert graph["edges"][0]["confidence"] == 2
