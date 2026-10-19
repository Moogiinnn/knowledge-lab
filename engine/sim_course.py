"""Simulated course for the dress rehearsal. SIMULATED DATA: never research results.

A data structures and algorithms course taught in C, with a teacher-style concept graph that
meets the preregistration's preconditions (docs/preregistration.md: H1 needs ≥ 4 PREREQUISITE
and ≥ 4 RELATED edges, H2 ≥ 3 structure→algorithm and ≥ 3 structure→structure edges) and
ground-truth mechanisms set by the analyst (`TRUTH`). The rehearsal checks that the whole
pipeline runs and how many students the preregistered tests need; whatever it "finds" is
only what was put in here.

Student model: the one of engine/synthetic.py (latent mastery per concept, logistic success
with student ability and question difficulty, hints, learning from practice, transfer along
edges, forgetting towards a baseline), on a 14-week curriculum: each week opens new concepts,
students practise mostly the current week and sometimes review earlier topics.

    python -m engine.sim_course --out data/sim-ds --students 200 --seed 0
"""

import argparse
import csv
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .synthetic import COLUMNS

S, A, O = "STRUCTURE", "ALGORITHM", "OTHER"

# code, Mongolian name, kind, curriculum week
CONCEPTS = [
    ("loops", "Давталт ба нөхцөл", O, 1),
    ("functions", "Функц ба параметр", O, 1),
    ("array", "Массив", S, 2),
    ("pointer", "Заагч", S, 2),
    ("string", "Тэмдэгт мөр", S, 3),
    ("linear-search", "Шугаман хайлт", A, 3),
    ("complexity", "Алгоритмын төвөгшил", O, 3),
    ("recursion", "Рекурс", O, 4),
    ("binary-search", "Хоёртын хайлт", A, 4),
    ("bubble-sort", "Хөөсөн эрэмбэлэлт", A, 5),
    ("linked-list", "Холбоост жагсаалт", S, 5),
    ("merge-sort", "Нийлүүлэн эрэмбэлэлт", A, 6),
    ("quick-sort", "Хурдан эрэмбэлэлт", A, 6),
    ("stack", "Стек", S, 7),
    ("queue", "Дараалал", S, 7),
    ("hash-table", "Хэш хүснэгт", S, 8),
    ("tree", "Мод", S, 9),
    ("tree-traversal", "Модоор аялах", A, 9),
    ("bst", "Хоёртын хайлтын мод", S, 10),
    ("heap", "Овоо (heap)", S, 11),
    ("heap-sort", "Овоон эрэмбэлэлт", A, 11),
    ("graph", "Граф ба дүрслэл", S, 12),
    ("bfs", "Өргөнөөр хайх (BFS)", A, 13),
    ("dfs", "Гүнээр хайх (DFS)", A, 13),
]

PRE, REL, CTRL = "PREREQUISITE", "RELATED", "CONTROL"
EDGES = [
    # structure → algorithm (H2 group A)
    ("array", "linear-search", PRE), ("array", "binary-search", PRE), ("array", "bubble-sort", PRE),
    ("array", "merge-sort", PRE), ("stack", "dfs", PRE), ("queue", "bfs", PRE),
    ("heap", "heap-sort", PRE), ("tree", "tree-traversal", PRE), ("graph", "bfs", PRE),
    # structure → structure (H2 group B)
    ("pointer", "linked-list", PRE), ("linked-list", "stack", PRE), ("linked-list", "queue", PRE),
    ("tree", "bst", PRE), ("tree", "heap", PRE), ("array", "string", REL), ("array", "hash-table", REL),
    ("stack", "queue", REL),
    # other
    ("loops", "array", PRE), ("functions", "recursion", PRE), ("recursion", "merge-sort", PRE),
    ("recursion", "tree-traversal", PRE), ("binary-search", "bst", REL), ("merge-sort", "quick-sort", REL),
    ("linear-search", "binary-search", REL), ("complexity", "quick-sort", REL), ("bfs", "dfs", REL),
    # negative controls: pairs the teacher judges unrelated
    ("string", "heap-sort", CTRL), ("hash-table", "tree-traversal", CTRL), ("bubble-sort", "graph", CTRL),
]  # fmt: skip

# Ground truth chosen by the analyst, in the direction of H1 and H2 and of moderate size
# (0.35 ≈ +8 percentage points on the target after five practices of the source, see
# engine/effect_size.py). CONTROL edges carry nothing.
TRUTH = {
    "transfer": {(PRE, S, A): 0.60, PRE: 0.35, REL: 0.10, CTRL: 0.0},
    "coupling": {PRE: 0.40, REL: 0.20, CTRL: 0.0},
}

# Placebo: no mechanism on any edge (for the false positive rate of the analysis).
PLACEBO = {"transfer": {PRE: 0.0, REL: 0.0, CTRL: 0.0}, "coupling": {PRE: 0.0, REL: 0.0, CTRL: 0.0}}

WEEKS = 14
START = datetime(2026, 9, 7, tzinfo=timezone.utc)


def kinds() -> dict[str, str]:
    return {c: k for c, _, k, _ in CONCEPTS}


def edge_strength(src: str, dst: str, relation: str, truth: dict | None = None) -> float:
    k = kinds()
    t = (truth or TRUTH)["transfer"]
    return t.get((relation, k[src], k[dst]), t[relation])


def build_questions(rng: np.random.Generator) -> list[dict]:
    """Two single-concept questions per concept and one combined question per PREREQUISITE edge."""
    week = {c: w for c, _, _, w in CONCEPTS}
    name = {c: n for c, n, _, _ in CONCEPTS}
    questions = []
    for code, _, _, w in CONCEPTS:
        for j in (1, 2):
            questions.append({"code": f"{code}-{j}", "title": f"{name[code]} {j}", "concepts": {code: 1.0},
                              "difficulty": int(np.clip(1 + w // 4 + (j - 1), 1, 5)), "week": w})  # fmt: skip
    for src, dst, rel in EDGES:
        if rel == PRE:
            questions.append({"code": f"{dst}-x-{src}", "title": f"{name[dst]} ба {name[src]}",
                              "concepts": {dst: 0.6, src: 0.4}, "difficulty": int(np.clip(2 + week[dst] // 4, 1, 5)),
                              "week": max(week[src], week[dst])})  # fmt: skip
    return questions


def graph_json(course: str) -> dict:
    return {
        "course": course,
        "concepts": [{"code": c, "name": n, "kind": k} for c, n, k, _ in CONCEPTS],
        "edges": [{"source": s, "target": t, "relation": r, "initial_weight": 1.0, "approved": True, "confidence": 2}
                  for s, t, r in EDGES],
    }  # fmt: skip


def simulate(n_students: int, seed: int = 0, course: str = "sim-ds", truth: dict | None = None) -> tuple[list[dict], list[dict]]:
    truth = truth or TRUTH
    rng = np.random.default_rng(seed)
    cidx = {c: i for i, (c, *_rest) in enumerate(CONCEPTS)}
    n = len(CONCEPTS)
    order = sorted(cidx, key=lambda c: next(w for cc, _, _, w in CONCEPTS if cc == c))
    questions = build_questions(rng)
    children = {i: [] for i in range(n)}
    for s, t, r in EDGES:
        children[cidx[s]].append((cidx[t], edge_strength(s, t, r, truth)))

    rows = []
    for u in range(n_students):
        student = f"p_sim{seed:02d}{u:04d}"
        mastery = rng.beta(2, 6, n)
        for s, t, r in sorted(EDGES, key=lambda e: order.index(e[1])):  # coupling: target pulled towards source
            c = truth["coupling"][r]
            mastery[cidx[t]] = (1 - c) * mastery[cidx[t]] + c * mastery[cidx[s]]
        baseline = mastery.copy()
        ability = rng.normal(0, 0.6)
        engagement = rng.uniform(0.5, 1.0)  # share of planned sessions the student actually does
        dropout_week = WEEKS + 1 if rng.random() > 0.08 else int(rng.integers(5, WEEKS))
        attempts: dict[str, int] = {}
        last_day = 0.0
        for week in range(1, WEEKS + 1):
            if week >= dropout_week:
                break
            for day in sorted(rng.choice(7, size=2, replace=False)):
                if rng.random() > engagement:
                    continue
                day_index = (week - 1) * 7 + int(day)
                mastery = baseline + (mastery - baseline) * math.exp(-0.03 * (day_index - last_day))
                last_day = day_index
                session = str(uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | u))
                clock = START + timedelta(days=day_index, hours=float(rng.uniform(9, 22)))
                current = [q for q in questions if q["week"] == week]
                earlier = [q for q in questions if q["week"] < week]
                for _ in range(int(rng.integers(3, 6))):
                    pool = current if (not earlier or rng.random() < 0.7) and current else earlier
                    if not pool:
                        break
                    q = pool[int(rng.integers(len(pool)))]
                    a = {cidx[c]: w for c, w in q["concepts"].items()}
                    for _try in range(3):  # retry the same question until correct, at most 3 times
                        logit = 4 * sum(w * mastery[i] for i, w in a.items()) - 1.6 - 0.45 * (q["difficulty"] - 3) + ability
                        p = 1 / (1 + math.exp(-logit))
                        hints = int(rng.poisson(max(0.0, 1.5 * (1 - p))))
                        y = int(rng.random() < min(0.98, p + 0.05 * hints))
                        attempts[q["code"]] = attempts.get(q["code"], 0) + 1
                        for i, w in a.items():  # learning, more when wrong, and transfer to dependants
                            gain = 0.12 * w * (1 - mastery[i]) * (1.3 if not y else 1.0)
                            mastery[i] += gain
                            for j, strength in children[i]:
                                mastery[j] += strength * gain * (1 - mastery[j])
                        minutes = float(rng.lognormal(math.log(2 + 3 * (1 - p)), 0.5))
                        clock += timedelta(minutes=minutes + float(rng.uniform(0.2, 1.5)))
                        rows.append({
                            "event_id": str(uuid.UUID(int=int(rng.integers(0, 2**63)))),
                            "student_id": student, "course_id": course, "session_id": session,
                            "timestamp": clock.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                            "question_id": q["code"], "question_version": 1, "difficulty": q["difficulty"],
                            "attempt_index": attempts[q["code"]], "correct": y, "score": float(y),
                            "tests_passed": "", "tests_total": "", "auto_correct": y, "auto_score": float(y),
                            "grading_status": "AUTO", "grader_version": "sim", "grade_revisions": 0,
                            "response_time_sec": round(minutes * 60), "timing_source": "synthetic",
                            "server_elapsed_sec": round(minutes * 60) + 5, "hint_count": hints,
                            "assistance_flag": "", "assistance_types": "",
                            "concepts": json.dumps([{"concept": c, "weight": w} for c, w in q["concepts"].items()]),
                            "is_synthetic": 1, "_true_p": p,
                        })  # fmt: skip
                        if y:
                            break
    rows.sort(key=lambda r: (r["timestamp"], r["event_id"]))
    return rows, questions


def truth_table(truth: dict | None = None) -> list[dict]:
    truth = truth or TRUTH
    k = kinds()
    return [{"edge": f"{s}→{t}", "relation": r, "source_kind": k[s], "target_kind": k[t],
             "transfer": edge_strength(s, t, r, truth), "coupling": truth["coupling"][r]} for s, t, r in EDGES]  # fmt: skip


def write(out_dir: str | Path, n_students: int, seed: int = 0, course: str = "sim-ds", truth: dict | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows, questions = simulate(n_students, seed, course, truth)
    with open(out / "events.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (out / "graph.json").write_text(json.dumps(graph_json(course), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "truth.json").write_text(json.dumps({"note": "SIMULATED ground truth", "edges": truth_table(truth),
                                                "students": n_students, "seed": seed}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")  # fmt: skip
    with open(out / "truth.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["event_id", "true_p"])
        w.writerows((r["event_id"], f"{r['_true_p']:.6f}") for r in rows)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--students", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--course", default="sim-ds")
    args = parser.parse_args()
    path = write(args.out, args.students, args.seed, args.course)
    print("wrote", path)
