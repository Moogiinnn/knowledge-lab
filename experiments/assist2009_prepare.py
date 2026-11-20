"""ASSISTments 2009–2010 Skill Builder (corrected, collapsed) → engine events.csv + two graphs.

Source: https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/skill-builder-data-2009-2010
(file skill_builder_data_corrected_collapsed.csv, sha256 162ef8d2d28bcbfe…; cite Feng, Heffernan & Koedinger, 2009).
The raw file stays outside the repository (data/external/, git-ignored).

Preparation (all choices are fixed here, before any model is run):
- rows without a skill are dropped (as in DKT and pyKT); "1_13" means two skills, equal weights;
- students with at least 20 such attempts; 1 000 of them sampled with seed 0; each student's first 200 attempts;
- the file has no timestamps: order_id gives the order, and the event time is its rank in seconds. Forgetting
  cannot be estimated, so KSG-DM runs with decay off;
- no teacher difficulty: 3 for every question; no teacher graph:
    graph_none.json  concepts only (KSG-DM without edges);
    graph_data.json  edges from the TRAINING part only (the first 60% by order, the engine's temporal cut):
                     consecutive attempts of a student (window 2), skill a → skill b (a ≠ b), at least 20 such
                     transitions, the 3 most frequent targets per source; relation RELATED (the IJAAS method).

    python experiments/assist2009_prepare.py data/external/assist2009
"""

import collections
import csv
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_ATTEMPTS, N_STUDENTS, MAX_PER_STUDENT, SEED = 20, 1000, 200, 0
MIN_TRANSITIONS, TOP_TARGETS, TRAIN_SHARE = 20, 3, 0.6


def main(folder: str):
    root = Path(folder)
    rows = [r for r in csv.DictReader(open(root / "skill_builder_data_corrected_collapsed.csv", encoding="latin1"))
            if r["skill_id"] not in ("", "NA")]
    rows.sort(key=lambda r: int(r["order_id"]))
    names = {}
    for r in rows:
        for s, n in zip(r["skill_id"].split("_"), r["skill_name"].split("_")):
            names.setdefault(s, n)
    count = collections.Counter(r["user_id"] for r in rows)
    eligible = sorted(u for u, c in count.items() if c >= MIN_ATTEMPTS)
    chosen = set(random.Random(SEED).sample(eligible, N_STUDENTS))
    taken, kept = collections.Counter(), []
    for r in rows:
        if r["user_id"] in chosen and taken[r["user_id"]] < MAX_PER_STUDENT:
            taken[r["user_id"]] += 1
            kept.append(r)

    t0 = datetime(2009, 9, 1, tzinfo=timezone.utc)
    attempts = collections.Counter()
    events = []
    for rank, r in enumerate(kept):
        skills = r["skill_id"].split("_")
        attempts[(r["user_id"], r["problem_id"])] += 1
        ms = float(r["ms_first_response"]) if r["ms_first_response"] not in ("", "NA") else -1
        events.append({
            "event_id": r["order_id"], "student_id": f"a09-{r['user_id']}", "timestamp":
            (t0 + timedelta(seconds=rank)).isoformat().replace("+00:00", "Z"), "question_id": f"p{r['problem_id']}",
            "concepts": json.dumps([{"concept": f"s{s}", "weight": 1 / len(skills)} for s in skills]),
            "correct": int(r["correct"]), "score": int(r["correct"]), "attempt_index": attempts[(r["user_id"], r["problem_id"])],
            "hint_count": int(float(r["hint_count"] or 0)), "response_time_sec": round(ms / 1000, 3) if 0 < ms < 3.6e6 else "",
            "assistance_flag": "", "difficulty": 3,
        })  # fmt: skip
    with open(root / "events.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(events[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(events)

    concepts = sorted({c["concept"] for e in events for c in json.loads(e["concepts"])}, key=lambda s: int(s[1:]))
    concept_list = [{"code": c, "name": names.get(c[1:], c), "kind": ""} for c in concepts]
    (root / "graph_none.json").write_text(json.dumps({"course": "assist2009", "concepts": concept_list, "edges": []},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")  # fmt: skip
    cut = int(len(events) * TRAIN_SHARE)
    last, trans = {}, collections.Counter()
    for e in events[:cut]:
        now = [c["concept"] for c in json.loads(e["concepts"])]
        for a in last.get(e["student_id"], []):
            for b in now:
                if a != b:
                    trans[(a, b)] += 1
        last[e["student_id"]] = now
    edges = []
    for a in concepts:
        out = sorted(((n, b) for (s, b), n in trans.items() if s == a and n >= MIN_TRANSITIONS), reverse=True)[:TOP_TARGETS]
        edges += [{"source": a, "target": b, "relation": "RELATED", "initial_weight": 1.0, "approved": True,
                   "confidence": None, "transitions": n} for n, b in out]  # fmt: skip
    (root / "graph_data.json").write_text(json.dumps({"course": "assist2009", "concepts": concept_list, "edges": edges},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")  # fmt: skip
    print(f"{len(events)} events, {len(chosen)} students, {len(concepts)} concepts, "
          f"{len({e['question_id'] for e in events})} questions, {len(edges)} data edges (train part: {cut} events)")


if __name__ == "__main__":
    main(sys.argv[1])
