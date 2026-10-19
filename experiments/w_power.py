"""Power study: how much data does DKG-AP need to recover which edges carry evidence and
which carry transfer?

Same ground truth as experiments/w_recovery.py (v2): evidence (knowledge_coupling 0.9) and
transfer (edge_strengths 0.9) on disjoint edge sets. The factor varied is the number of
students. Recovery is scored with pair AUC on two weights:
  w_bar  normalised W̄ — confounded by in-degree under slack normalisation (v2 finding)
  r      w_ij / s_j    — edge strength relative to the target's "none" share (primary)
Validation part only; test data is never used.

Each finished fit is appended to rows.jsonl immediately, so an interrupted run keeps its
results; re-running skips (students, seed) cells that are already present.

    python experiments/w_power.py --out results/w_power --students 120 300 600 --seeds 0 1 2
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
from engine.edge_bootstrap import edge_analysis_config  # noqa: E402
from engine.split import temporal_split  # noqa: E402
from experiments.w_recovery import EVIDENCE, TRANSFER, pair_auc  # noqa: E402


def edge_map(desc: dict, key: str) -> dict:
    return {tuple(e["edge"].split("→")): e[key] for e in desc["edges"]}


def run_cell(students: int, seed: int, epochs: int, mechanism: str = "both", ability: bool = False) -> dict:
    """mechanism="none" is the placebo: same graph and edge labels, but no edge carries any
    evidence or transfer, so pair AUC should be ~0.5 unless the scoring picks up structure."""
    if mechanism == "both":
        strengths, coupling = {e: 0.9 for e in TRANSFER}, {e: 0.9 for e in EVIDENCE}
    elif mechanism == "none":
        strengths, coupling = {}, {}
    else:
        raise ValueError(f"unknown mechanism {mechanism!r}")
    with tempfile.TemporaryDirectory() as tmp:
        # Placebo and real data share the data seed, so they differ only in the edge mechanisms.
        events, graph = synthetic.write(
            tmp, n_students=students, seed=300 + seed, transfer=0.0,
            edge_strengths=strengths, knowledge_coupling=coupling,
        )  # fmt: skip
        data = load_dataset(events, graph)
    split = temporal_split(data)
    val = np.array([i.idx in split.val for i in data.interactions])
    y = np.array([i.y for i in data.interactions])
    started = time.perf_counter()
    model = DKG(**edge_analysis_config({"epochs": epochs, "seed": seed, "ability": ability}))
    model.fit(data.subset(split.train))
    p = model.predict(data)
    desc = model.describe()
    return {
        "mechanism": mechanism, "ability": model.config.ability, "w_reg_scaling": model.config.w_reg_scaling,
        "w_prior_events": model.config.w_prior_events,
        "students": students, "seed": seed, "events": len(data.interactions), "train_events": len(split.train),
        "val_auc": metrics.auc(y[val], p[val]), "val_logloss": metrics.logloss(y[val], p[val]),
        "evidence_pair_auc_r": pair_auc(edge_map(desc, "r"), EVIDENCE),
        "transfer_pair_auc_r": pair_auc(edge_map(desc, "r_transfer"), TRANSFER),
        "evidence_pair_auc_wbar": pair_auc(edge_map(desc, "w_bar"), EVIDENCE),
        "transfer_pair_auc_wbar": pair_auc(edge_map(desc, "w_bar_transfer"), TRANSFER),
        "gamma": desc["gamma"], "kappa": desc["kappa"],
        "r": {f"{s}→{t}": v for (s, t), v in edge_map(desc, "r").items()},
        "r_transfer": {f"{s}→{t}": v for (s, t), v in edge_map(desc, "r_transfer").items()},
        "train_loss_last": desc["train_loss"][-1] if desc["train_loss"] else None,
        "seconds": round(time.perf_counter() - started, 1),
    }  # fmt: skip


def summarize(rows: list[dict]) -> dict:
    keys = ("val_auc", "evidence_pair_auc_r", "transfer_pair_auc_r", "evidence_pair_auc_wbar",
            "transfer_pair_auc_wbar", "kappa")  # fmt: skip
    out = {}
    for mech, n in sorted({(mechanism_of(r), r["students"]) for r in rows}):
        rs = [r for r in rows if mechanism_of(r) == mech and r["students"] == n]
        out[f"{mech}/{n}"] = {"seeds": len(rs), "events": int(np.mean([r["events"] for r in rs]))} | {
            k: {"mean": float(np.mean([r[k] for r in rs])), "sd": float(np.std([r[k] for r in rs]))} for k in keys
        }
    return out


def mechanism_of(row: dict) -> str:
    return row.get("mechanism", "both")  # rows from before the placebo option are real-mechanism runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--students", type=int, nargs="+", default=[120, 300, 600])
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--mechanism", choices=["both", "none"], default="both", help="none = placebo data")
    parser.add_argument("--ability", action="store_true",
                        help="turn the online ability term on (off by default for edge analysis)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "rows.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    done = {(mechanism_of(r), r["students"], r["seed"]) for r in rows}

    for students in args.students:
        for seed in args.seeds:
            if (args.mechanism, students, seed) in done:
                print(f"skip {args.mechanism} students={students} seed={seed} (already in {log.name})", flush=True)
                continue
            row = run_cell(students, seed, args.epochs, args.mechanism, args.ability)
            rows.append(row)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{args.mechanism:4} students {students:4} seed {seed}  events {row['events']:6}  valAUC {row['val_auc']:.3f}  "
                  f"r: W^E {row['evidence_pair_auc_r']:.2f} W^T {row['transfer_pair_auc_r']:.2f}  "
                  f"w_bar: W^E {row['evidence_pair_auc_wbar']:.2f} W^T {row['transfer_pair_auc_wbar']:.2f}  "
                  f"kappa {row['kappa']:.3f}  {row['seconds']}s", flush=True)  # fmt: skip

    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps({"args": vars(args), "summary": summary}, indent=2), encoding="utf-8")
    print("\nmechanism/students  seeds  events   valAUC          pairAUC r: W^E     W^T          "
          "pairAUC w_bar: W^E   W^T")  # fmt: skip
    for key, s in summary.items():
        print(f"{key:18} {s['seeds']:5} {s['events']:7}   {s['val_auc']['mean']:.3f}±{s['val_auc']['sd']:.3f}   "
              f"{s['evidence_pair_auc_r']['mean']:.2f}±{s['evidence_pair_auc_r']['sd']:.2f}   "
              f"{s['transfer_pair_auc_r']['mean']:.2f}±{s['transfer_pair_auc_r']['sd']:.2f}      "
              f"{s['evidence_pair_auc_wbar']['mean']:.2f}±{s['evidence_pair_auc_wbar']['sd']:.2f}   "
              f"{s['transfer_pair_auc_wbar']['mean']:.2f}±{s['transfer_pair_auc_wbar']['sd']:.2f}")  # fmt: skip


if __name__ == "__main__":
    main()
