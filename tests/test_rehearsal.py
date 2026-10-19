"""Dress rehearsal tooling: simulated course, loading it through the system, preregistered groups."""

import csv
import io
import sys
from pathlib import Path

from django.core.management import call_command

from apps.curriculum.models import Course
from apps.events.export import export_rows
from engine import sim_course

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments.prereg_analysis import decide, hypothesis_groups  # noqa: E402


def test_simulated_graph_meets_preregistration_and_truth_follows_hypotheses():
    graph = sim_course.graph_json("sim")
    h1, h2 = hypothesis_groups({**graph}).values()
    assert len(h1[0]) >= 4 and len(h1[1]) >= 4 and len(h2[0]) >= 3 and len(h2[1]) >= 3
    truth = {e["edge"]: e["transfer"] for e in sim_course.truth_table()}
    mean = lambda edges: sum(truth[e] for e in edges) / len(edges)  # noqa: E731
    assert mean(h1[0]) > mean(h1[1]) and mean(h2[0]) > mean(h2[1])
    assert all(e["transfer"] == 0 for e in sim_course.truth_table() if e["relation"] == "CONTROL")


def test_simulation_is_deterministic():
    a, _ = sim_course.simulate(3, seed=5)
    b, _ = sim_course.simulate(3, seed=5)
    assert [r["event_id"] for r in a] == [r["event_id"] for r in b] and len(a) > 50


def test_loader_replays_through_services_and_respects_consent(db, tmp_path):
    folder = sim_course.write(tmp_path / "sim", n_students=4, seed=1)
    with open(folder / "events.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = io.StringIO()
    call_command("load_simulated_course", str(folder), "--course", "sim-t", "--decline", "0.5", "--seed", "3", stdout=out)
    course = Course.objects.get(code="sim-t")
    assert course.is_synthetic and "SIMULATED" in out.getvalue()
    exported = list(export_rows(course, include_synthetic=True))
    assert 0 < len(exported) <= len(rows) and not list(export_rows(course))  # synthetic stays out by default
    by_student: dict[str, list] = {}
    for r in exported:
        by_student.setdefault((r["student_id"], r["question_id"]), []).append(r["attempt_index"])
    assert all(v == list(range(1, len(v) + 1)) for v in by_student.values())
    stamps = [r["timestamp"] for r in exported]
    assert stamps == sorted(stamps) and stamps[0].startswith("2026-09")


def test_decision_rule():
    c = {"verdict": "group stronger", "delta_median": 0.2}
    assert decide(c, True) == "supported" and decide(c, False) == "inconclusive"
    assert decide({**c, "delta_median": -0.2}, True) == "evidence against"
    assert decide({"verdict": "degenerate (no bootstrap spread)", "delta_median": 0}, True).startswith("invalid")
    assert hypothesis_groups({"concepts": [], "edges": []}) == {"H1": (set(), set()), "H2": (set(), set())}


def test_thin_edges_are_found_only_in_the_early_period(tmp_path):
    from engine.data import load_dataset
    from engine.split import temporal_split
    from experiments.prereg_analysis import concept_attempts, thin_edges

    folder = sim_course.write(tmp_path / "sim", n_students=12, seed=2)
    graph = sim_course.graph_json("sim")
    data = load_dataset(folder / "events.csv", folder / "graph.json")
    early = data.subset(temporal_split(data, 0.6, 0.2).train)
    late_edges = thin_edges(graph, concept_attempts(early))
    assert "stack→dfs" in late_edges and "queue→bfs" in late_edges  # week-13 topics are missing early
    assert not thin_edges(graph, concept_attempts(data), minimum=5)  # the whole semester covers every concept
