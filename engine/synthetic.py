"""Synthetic interaction logs for testing the engine end to end (never research data).

Students have latent mastery per concept that grows with practice, transfers along
prerequisite edges, and decays towards a baseline between sessions. The output uses
exactly the export format (events CSV + graph JSON), with is_synthetic=1.
"""

import csv
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# Kept identical to apps.events.export.COLUMNS (checked by a test).
COLUMNS = [
    "event_id", "student_id", "course_id", "session_id", "timestamp", "question_id", "question_version",
    "difficulty", "attempt_index", "correct", "score", "tests_passed", "tests_total", "auto_correct", "auto_score",
    "grading_status", "grader_version", "grade_revisions", "response_time_sec", "timing_source",
    "server_elapsed_sec", "hint_count", "assistance_flag", "assistance_types", "concepts", "is_synthetic",
]  # fmt: skip

CONCEPTS = ["array", "linked-list", "stack", "queue", "recursion", "tree", "bfs", "dfs"]
EDGES = [("array", "linked-list"), ("array", "stack"), ("linked-list", "queue"), ("queue", "bfs"),
         ("stack", "dfs"), ("recursion", "dfs"), ("recursion", "tree"), ("tree", "bfs")]  # fmt: skip


def _questions(rng):
    questions = []
    for n, concept in enumerate(CONCEPTS):
        for j in range(3):
            mapping = {concept: 1.0}
            parents = [s for s, t in EDGES if t == concept]
            if j == 2 and parents:
                mapping = {concept: 0.6, parents[0]: 0.4}
            questions.append({"code": f"q-{concept}-{j + 1}", "concepts": mapping,
                              "difficulty": int(np.clip(1 + n // 2 + j, 1, 5))})  # fmt: skip
    return questions


# --- The generator's student model. engine/effect_size.py reuses these functions to turn
# the mechanism strengths into quantities a teacher can judge, so keep them the only copy.

def initial_mastery(rng, knowledge_coupling: dict | None) -> np.ndarray:
    """Mastery per concept at the start; coupled targets are pulled towards their source."""
    cidx = {c: i for i, c in enumerate(CONCEPTS)}
    mastery = rng.beta(2, 6, len(CONCEPTS))
    for src, dst in EDGES:  # EDGES is in topological order, so sources are set first
        c = (knowledge_coupling or {}).get((src, dst), 0.0)
        mastery[cidx[dst]] = (1 - c) * mastery[cidx[dst]] + c * mastery[cidx[src]]
    return mastery


def success_probability(mastery: np.ndarray, a: dict[int, float], difficulty: int, ability: float) -> float:
    """P(correct) for a question with concept weights a = {concept index: a_qi}, before hints."""
    logit = 4 * sum(w * mastery[i] for i, w in a.items()) - 1.6 - 0.45 * (difficulty - 3) + ability
    return 1 / (1 + math.exp(-logit))


def observe(rng, p: float) -> tuple[int, int]:
    """Hints taken and the observed answer (hints raise the chance of a correct answer)."""
    hints = int(rng.poisson(max(0.0, 1.5 * (1 - p))))
    y = int(rng.random() < min(0.98, p + 0.05 * hints))
    return y, hints


def practice(mastery: np.ndarray, a: dict[int, float], y: int, transfer: float, edge_strengths: dict | None):
    """Learning from one attempt (more when wrong), plus transfer to each dependent concept."""
    cidx = {c: i for i, c in enumerate(CONCEPTS)}
    for i, w in a.items():
        gain = 0.12 * w * (1 - mastery[i]) * (1.3 if not y else 1.0)
        mastery[i] += gain
        for s, t in EDGES:
            if s == CONCEPTS[i]:
                strength = (edge_strengths or {}).get((s, t), transfer)
                mastery[cidx[t]] += strength * gain * (1 - mastery[cidx[t]])


def generate(
    n_students: int = 60, days: int = 42, seed: int = 0, *, transfer: float = 0.4, forget: float = 0.03,
    edge_strengths: dict[tuple[str, str], float] | None = None,
    knowledge_coupling: dict[tuple[str, str], float] | None = None,
):
    """Two ground-truth edge mechanisms, so tests can check what a model recovers:

    edge_strengths      learning transfer per (source, target): practising the source
                        raises the target's mastery, whatever the answer (default: `transfer`).
    knowledge_coupling  shared knowledge per (source, target): a student's initial mastery
                        of the target is pulled towards their mastery of the source, so an
                        answer on the source is evidence about the target (default: none).
    """
    rng = np.random.default_rng(seed)
    cidx = {c: i for i, c in enumerate(CONCEPTS)}
    questions = _questions(rng)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = []
    for s in range(n_students):
        student = f"p_synth{s:04d}"
        mastery = initial_mastery(rng, knowledge_coupling)
        baseline = mastery.copy()
        ability = rng.normal(0, 0.6)
        last_day = 0.0
        attempts: dict[str, int] = {}
        order = sorted(range(len(questions)), key=lambda q: (cidx[next(iter(questions[q]["concepts"]))], rng.random()))
        pos = 0
        for day in sorted(rng.choice(days, size=int(rng.integers(6, 14)), replace=False)):
            gap = day - last_day
            mastery = baseline + (mastery - baseline) * math.exp(-forget * gap)
            last_day = day
            session = str(uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | s))
            clock = start + timedelta(days=int(day), hours=float(rng.uniform(9, 20)))
            for _ in range(int(rng.integers(3, 9))):
                if pos >= len(order):
                    pos = int(rng.integers(0, len(order)))
                q = questions[order[pos]]
                a = {cidx[c]: w for c, w in q["concepts"].items()}
                p = success_probability(mastery, a, q["difficulty"], ability)
                y, hints = observe(rng, p)
                attempts[q["code"]] = attempts.get(q["code"], 0) + 1
                practice(mastery, a, y, transfer, edge_strengths)
                minutes = float(rng.lognormal(math.log(2 + 3 * (1 - p)), 0.5))
                clock += timedelta(minutes=minutes + float(rng.uniform(0.2, 1.5)))
                rows.append({
                    "event_id": str(uuid.UUID(int=int(rng.integers(0, 2**63)))),
                    "student_id": student, "course_id": "synthetic", "session_id": session,
                    "timestamp": clock.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "question_id": q["code"], "question_version": 1, "difficulty": q["difficulty"],
                    "attempt_index": attempts[q["code"]], "correct": y, "score": float(y),
                    "tests_passed": "", "tests_total": "", "auto_correct": y, "auto_score": float(y),
                    "grading_status": "AUTO", "grader_version": "synthetic", "grade_revisions": 0,
                    "response_time_sec": round(minutes * 60), "timing_source": "synthetic",
                    "server_elapsed_sec": round(minutes * 60) + 5, "hint_count": hints,
                    "assistance_flag": 0, "assistance_types": "",
                    "concepts": json.dumps([{"concept": c, "weight": w} for c, w in q["concepts"].items()]),
                    "is_synthetic": 1,
                    "_true_p": p,
                })  # fmt: skip
                if y or attempts[q["code"]] >= 3:
                    pos += 1
    graph = {
        "course": "synthetic",
        "concepts": [{"code": c, "name": c} for c in CONCEPTS],
        "edges": [{"source": s, "target": t, "relation": "PREREQUISITE", "initial_weight": 1.0, "approved": True}
                  for s, t in EDGES],
    }  # fmt: skip
    return rows, graph


def write(out_dir: str | Path, **kwargs) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows, graph = generate(**kwargs)
    events, graph_path = out / "events.csv", out / "graph.json"
    with open(events, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    # The generator's own success probability: an oracle reference (ceiling) for models.
    with open(out / "truth.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["event_id", "true_p"])
        writer.writerows((r["event_id"], f"{r['_true_p']:.6f}") for r in rows)
    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    return events, graph_path
