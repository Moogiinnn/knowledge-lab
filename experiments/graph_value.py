"""Section 4.10: when does the concept graph help next-answer prediction?

Part A (subsets): test AUC of dkg_full vs dkg_no_graph on an existing predictions file, split by
how many earlier attempts the student had on the question's concepts (0 = first attempt on a
new concept, where the graph should matter most). Student bootstrap CI of the difference.

Part B (controlled): synthetic data (engine.synthetic) with student-specific shared knowledge
along every edge (knowledge_coupling 0 / 0.5 / 0.9), DKG with and without the graph, plus the
generator's own probabilities as a ceiling (oracle AUC).

    python experiments/graph_value.py --out results/graph_value \
        --predictions results/rehearsal/predict/predictions.csv --events exports/sim-ds-120/events.csv
"""

import argparse
import csv
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import synthetic  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.dkg import DKG  # noqa: E402
from engine.metrics import auc  # noqa: E402
from engine.split import temporal_split  # noqa: E402

GROUPS = {"0 prior (new concept)": (0, 0), "1-2 prior": (1, 2), "3+ prior": (3, 10**9)}


def prior_counts(events_path):
    """event_id -> fewest earlier attempts of that student on any of the question's concepts."""
    rows = list(csv.DictReader(open(events_path, encoding="utf-8")))
    rows.sort(key=lambda r: (r["student_id"], r["timestamp"], r["event_id"]))
    seen, out = defaultdict(dict), {}
    for r in rows:
        concepts = [c["concept"] for c in json.loads(r["concepts"])]
        s = seen[r["student_id"]]
        out[r["event_id"]] = min(s.get(c, 0) for c in concepts)
        for c in concepts:
            s[c] = s.get(c, 0) + 1
    return out


def subset_diff(rows, rng, boot):
    by = defaultdict(list)
    for r in rows:
        by[r["student_id"]].append((int(r["y"]), float(r["dkg_full"]), float(r["dkg_no_graph"])))
    studs = list(by)

    def stats(groups):
        a = np.array([t for g in groups for t in g])
        return auc(a[:, 0], a[:, 1]), auc(a[:, 0], a[:, 2]), len(a)

    full, no_graph, n = stats([by[s] for s in studs])
    draws = []
    for _ in range(boot):
        f, g, _ = stats([by[s] for s in rng.choice(studs, len(studs))])
        draws.append(f - g)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"n": n, "students": len(studs), "full": full, "no_graph": no_graph, "diff": full - no_graph,
            "ci": [float(lo), float(hi)]}


def part_a(predictions, events, boot):
    prior = prior_counts(events)
    rows = [r for r in csv.DictReader(open(predictions, encoding="utf-8")) if r["split"] == "test"]
    rng = np.random.default_rng(0)
    out = {"all test": subset_diff(rows, rng, boot)}
    for name, (lo, hi) in GROUPS.items():
        out[name] = subset_diff([r for r in rows if lo <= prior[r["event_id"]] <= hi], rng, boot)
    return out


def part_b(couplings, seeds, students):
    out = []
    for coupling in couplings:
        for seed in seeds:
            with tempfile.TemporaryDirectory() as tmp:
                synthetic.write(tmp, n_students=students, seed=seed,
                                knowledge_coupling={e: coupling for e in synthetic.EDGES} if coupling else None)
                data = load_dataset(Path(tmp) / "events.csv", Path(tmp) / "graph.json")
                truth = {r["event_id"]: float(r["true_p"]) for r in csv.DictReader(open(Path(tmp) / "truth.csv"))}
            test = sorted(temporal_split(data).test)
            train = data.subset(temporal_split(data).train)
            y = np.array([data.interactions[i].y for i in test])
            row = {"coupling": coupling, "seed": seed, "students": students,
                   "oracle": auc(y, np.array([truth[data.interactions[i].event_id] for i in test]))}
            for name, cfg in {"full": {}, "no_graph": {"propagation": False, "transfer": False}}.items():
                model = DKG(seed=seed, epochs=15, **cfg)
                model.fit(train)
                row[name] = auc(y, model.predict(data)[test])
            row["diff"] = row["full"] - row["no_graph"]
            out.append(row)
            print(json.dumps(row), flush=True)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--boot", type=int, default=500)
    parser.add_argument("--couplings", type=float, nargs="+", default=[0.0, 0.5, 0.9])
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--students", type=int, default=120)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    a = part_a(args.predictions, args.events, args.boot)
    for name, r in a.items():
        print(f"{name:24s} n={r['n']:5d} full={r['full']:.3f} no_graph={r['no_graph']:.3f} "
              f"diff={r['diff']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]", flush=True)
    b = part_b(args.couplings, args.seeds, args.students)
    (out / "results.json").write_text(json.dumps({"subsets": a, "coupling": b}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
