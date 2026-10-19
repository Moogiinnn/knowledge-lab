"""Method-selection study (v2): can DKG-AP tell evidence edges from transfer edges?

Synthetic data with two known edge mechanisms on disjoint edge sets:
  evidence  knowledge_coupling 0.9: an answer on the source is informative about the target
  transfer  edge_strengths 0.9:     practising the source raises the target, right or wrong
Each set contains two targets with a single incoming edge. For each variant we fit on the
temporal train part and report validation metrics, and whether W̄^E ranks evidence edges
above the others and W̄^T ranks transfer edges above the others. Test data is never used.

    python experiments/w_recovery.py --out results/w_recovery_v2 --seeds 0 1 2 --students 120

v1 of this study (single W, transfer-only ground truth) found W learned in the wrong
direction: evidence-style messages cannot represent answer-independent transfer.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import metrics, synthetic  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.dkg import DKG  # noqa: E402
from engine.split import temporal_split  # noqa: E402

EVIDENCE = {("array", "linked-list"), ("recursion", "tree"), ("queue", "bfs"), ("stack", "dfs")}
TRANSFER = {("array", "stack"), ("linked-list", "queue"), ("tree", "bfs"), ("recursion", "dfs")}
assert not EVIDENCE & TRANSFER and EVIDENCE | TRANSFER == set(synthetic.EDGES)

VARIANTS = {
    "no_graph": {"propagation": False, "transfer": False},
    "fixed_W0": {"w_mode": "fixed"},
    "learned": {"w_mode": "learned"},
    "learned_lowreg": {"w_mode": "learned", "w_reg": 1e-4},
}


def pair_auc(weights: dict, positives: set) -> float:
    """Share of (positive, other) edge pairs where the positive edge has the larger weight."""
    pos = [w for k, w in weights.items() if k in positives]
    neg = [w for k, w in weights.items() if k not in positives]
    return float(np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--students", type=int, default=120)
    parser.add_argument("--epochs", type=int, default=15)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for seed in args.seeds:
        with tempfile.TemporaryDirectory() as tmp:
            events, graph = synthetic.write(
                tmp, n_students=args.students, seed=200 + seed, transfer=0.0,
                edge_strengths={e: 0.9 for e in TRANSFER}, knowledge_coupling={e: 0.9 for e in EVIDENCE},
            )  # fmt: skip
            data = load_dataset(events, graph)
        split = temporal_split(data)
        val = np.array([i.idx in split.val for i in data.interactions])
        y = np.array([i.y for i in data.interactions])
        for name, change in VARIANTS.items():
            started = time.perf_counter()
            model = DKG(epochs=args.epochs, seed=seed, **change)
            model.fit(data.subset(split.train))
            p = model.predict(data)
            desc = model.describe()
            w_e = {tuple(e["edge"].split("→")): e["w_bar"] for e in desc["edges"]}
            w_t = {tuple(e["edge"].split("→")): e["w_bar_transfer"] for e in desc["edges"]}
            row = {
                "seed": seed, "variant": name,
                "val_auc": metrics.auc(y[val], p[val]), "val_logloss": metrics.logloss(y[val], p[val]),
                "evidence_pair_auc": pair_auc(w_e, EVIDENCE), "transfer_pair_auc": pair_auc(w_t, TRANSFER),
                "gamma": desc["gamma"], "kappa": desc["kappa"],
                "w_evidence": {f"{s}→{t}": w for (s, t), w in w_e.items()},
                "w_transfer": {f"{s}→{t}": w for (s, t), w in w_t.items()},
                "seconds": round(time.perf_counter() - started, 1),
            }  # fmt: skip
            rows.append(row)
            print(f"seed {seed} {name:15} valAUC {row['val_auc']:.3f} LL {row['val_logloss']:.4f} "
                  f"W^E pairAUC {row['evidence_pair_auc']:.2f}  W^T pairAUC {row['transfer_pair_auc']:.2f}  "
                  f"gamma {row['gamma']:.2f} kappa {row['kappa']:.3f}", flush=True)  # fmt: skip

    keys = ("val_auc", "val_logloss", "evidence_pair_auc", "transfer_pair_auc", "kappa")
    summary = {
        name: {k: {"mean": float(np.mean([r[k] for r in rows if r["variant"] == name])),
                   "sd": float(np.std([r[k] for r in rows if r["variant"] == name]))} for k in keys}
        for name in VARIANTS
    }  # fmt: skip
    truth = {"evidence": sorted(f"{s}→{t}" for s, t in EVIDENCE), "transfer": sorted(f"{s}→{t}" for s, t in TRANSFER)}
    (out / "w_recovery.json").write_text(
        json.dumps({"truth": truth, "args": vars(args), "rows": rows, "summary": summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("\nvariant          valAUC          valLogLoss        W^E pairAUC   W^T pairAUC   (0.5 = chance)")
    for name, s in summary.items():
        print(f"{name:15} {s['val_auc']['mean']:.3f}±{s['val_auc']['sd']:.3f}   "
              f"{s['val_logloss']['mean']:.4f}±{s['val_logloss']['sd']:.4f}   "
              f"{s['evidence_pair_auc']['mean']:.2f}±{s['evidence_pair_auc']['sd']:.2f}     "
              f"{s['transfer_pair_auc']['mean']:.2f}±{s['transfer_pair_auc']['sd']:.2f}")  # fmt: skip


if __name__ == "__main__":
    main()
