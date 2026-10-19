"""Run a configured experiment: same split, same data, every model; write results.

Config (JSON):
{
  "events": "exports/events.csv", "graph": "exports/graph.json",
  "split": {"kind": "temporal", "train": 0.6, "val": 0.2},
  "seed": 0, "bootstrap": 1000, "reference": "bkt",
  "models": [{"type": "bkt"}, {"type": "dkg", "name": "dkg_full", "epochs": 15}],
  "ablation": true,          # add the guide's DKG ablations based on the first dkg entry
  "explain": "dkg_full"      # write per-prediction explanations for this model (test part)
}
Outputs in out_dir: metrics.json, predictions.csv, explanations.jsonl, run_info.json.
"""

import csv
import json
import platform
import random
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
import torch

from . import metrics
from .baselines import BKT, PFA, ConceptRate, GlobalRate
from .data import load_dataset
from .dkg import DKG
from .split import make_split

REGISTRY = {"global_rate": GlobalRate, "concept_rate": ConceptRate, "pfa": PFA, "bkt": BKT, "dkg": DKG}

# guide §9: remove one mechanism at a time, plus the dynamic-W × adaptive-α 2×2.
ABLATIONS = {
    "dkg_no_decay": {"decay": False},
    "dkg_no_evidence_propagation": {"propagation": False},
    "dkg_no_transfer": {"transfer": False},
    "dkg_no_graph": {"propagation": False, "transfer": False},
    "dkg_no_ability": {"ability": False},
    "dkg_fixed_alpha": {"adaptive_alpha": False},
    "dkg_fixed_eta": {"adaptive_eta": False},
    "dkg_static_w": {"w_mode": "fixed"},
    "dkg_2x2_static_w_fixed_alpha": {"w_mode": "fixed", "adaptive_alpha": False},
    "dkg_2x2_static_w_adaptive_alpha": {"w_mode": "fixed", "adaptive_alpha": True},
    "dkg_2x2_dynamic_w_fixed_alpha": {"w_mode": "dynamic", "adaptive_alpha": False},
    "dkg_2x2_dynamic_w_adaptive_alpha": {"w_mode": "dynamic", "adaptive_alpha": True},
}


def model_specs(config: dict) -> list[dict]:
    specs = [dict(s) for s in config["models"]]
    base = next((s for s in specs if s["type"] == "dkg"), None)
    if config.get("ablation") and base is not None:
        for name, change in ABLATIONS.items():
            specs.append({**base, **change, "name": name})
    names = [s.get("name", s["type"]) for s in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"model names must be unique: {names}")
    return specs


def build(spec: dict, seed: int):
    params = {k: v for k, v in spec.items() if k not in ("type", "name")}
    if spec["type"] == "dkg":
        return DKG(name=spec.get("name", "dkg"), **{"seed": seed, **params})
    model = REGISTRY[spec["type"]](**params)
    model.name = spec.get("name", spec["type"])
    return model


def run(config: dict, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    seed = int(config.get("seed", 0))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    data = load_dataset(config["events"], config["graph"], approved_edges_only=config.get("approved_edges_only", True))
    sp = config.get("split", {})
    split = make_split(data, sp.get("kind", "temporal"), sp.get("train", 0.6), sp.get("val", 0.2), seed)
    train = data.subset(split.train)
    parts = np.array([split.part(i.idx) for i in data.interactions])
    y = np.array([i.y for i in data.interactions])
    students = np.array([i.student for i in data.interactions])

    predictions, results, runtimes, described, explanations = {}, {}, {}, {}, []
    for spec in model_specs(config):
        model = build(spec, seed)
        started = time.perf_counter()
        model.fit(train)
        p = model.predict(data)
        runtimes[model.name] = round(time.perf_counter() - started, 3)
        predictions[model.name] = p
        val, test = parts == "val", parts == "test"
        threshold = metrics.best_threshold(y[val], p[val]) if val.any() else 0.5
        results[model.name] = {
            "val": metrics.summarize(y[val], p[val]) if val.any() else None,
            "test": metrics.summarize(y[test], p[test]) | metrics.thresholded(y[test], p[test], threshold),
            "calibration_test": metrics.calibration_bins(y[test], p[test]),
        }
        if hasattr(model, "describe"):
            described[model.name] = model.describe()
        if config.get("explain") == model.name and hasattr(model, "explain"):
            for it, exp in zip(data.interactions, model.explain(data)):
                if parts[it.idx] == "test":
                    explanations.append({"event_id": it.event_id, "student_id": it.student, "y": it.y,
                                         "p": float(predictions[model.name][it.idx]), **exp})  # fmt: skip

    test = parts == "test"
    reference = config.get("reference")
    boot = metrics.bootstrap(
        students[test], y[test], {m: p[test] for m, p in predictions.items()},
        reference=reference if reference in predictions else None,
        n_boot=int(config.get("bootstrap", 1000)), seed=seed,
    )  # fmt: skip

    summary = {
        "models": results,
        "bootstrap_test": boot,
        "learned_parameters": described,
        "runtime_sec": runtimes,
    }
    (out / "metrics.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["event_id", "student_id", "question_id", "timestamp", "split", "y", *predictions])
        for it in data.interactions:
            writer.writerow([it.event_id, it.student, it.question, it.t, parts[it.idx], it.y,
                             *(f"{predictions[m][it.idx]:.6f}" for m in predictions)])  # fmt: skip
    if explanations:
        with open(out / "explanations.jsonl", "w", encoding="utf-8") as fh:
            for row in explanations:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    info = {
        "config": config,
        "events_sha256": data.source_sha256,
        "dropped": data.dropped,
        "sizes": {k: int((parts == k).sum()) for k in ("train", "val", "test")},
        "students": {k: len(set(students[parts == k])) for k in ("train", "val", "test")},
        "concepts": len(data.concepts),
        "edges": len(data.edges),
        "questions": len(data.questions),
        "split_kind": split.kind,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "torch": torch.__version__,
                        "scikit-learn": version("scikit-learn"), "platform": platform.platform()},  # fmt: skip
    }
    (out / "run_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary | {"run_info": info}


def print_table(summary: dict) -> str:
    lines = [f"{'model':38} {'AUC':>7} {'LogLoss':>8} {'Brier':>7} {'ECE':>7} {'Acc':>6}   AUC 95% CI"]
    ci = summary["bootstrap_test"]["ci"]
    for name, r in summary["models"].items():
        t = r["test"]
        auc_ci = ci[name]["auc"]
        ci_text = f"[{auc_ci['lo']:.3f}, {auc_ci['hi']:.3f}]" if auc_ci else "—"
        lines.append(f"{name:38} {t['auc']:7.3f} {t['logloss']:8.4f} {t['brier']:7.4f} {t['ece']:7.4f} "
                     f"{t['accuracy']:6.3f}   {ci_text}")  # fmt: skip
    return "\n".join(lines)
