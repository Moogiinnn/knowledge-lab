"""Check edge bootstrap intervals and group contrasts on synthetic data with known edges.

For each seed: one synthetic class with real mechanisms and the matching placebo (same
data seed, no mechanisms). Student-bootstrap DKG fits give
  - per-edge intervals of rel = log r − mean log r ("stronger" / "weaker" / "unclear"),
  - group contrasts: W^E of the true evidence edges vs the rest, and W^T of the true
    transfer edges vs the rest (Δ of mean rel, 95% interval).
A useful method says "group stronger" on real data and "unclear" on the placebo.
Bootstrap draws are saved (draws_*.json), so new contrasts need no refitting.
Training part of the temporal split only.

    python experiments/edge_ci.py --out results/edge_ci_v2 --students 120 --seeds 0 1 2 --boot 40 --workers 6
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import synthetic  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.edge_bootstrap import bootstrap_draws, edge_intervals, group_contrast  # noqa: E402
from engine.split import temporal_split  # noqa: E402
from experiments.w_recovery import EVIDENCE, TRANSFER  # noqa: E402

EVIDENCE_EDGES = {f"{s}→{t}" for s, t in EVIDENCE}
TRANSFER_EDGES = {f"{s}→{t}" for s, t in TRANSFER}


def label(edge: str) -> str:
    return "evidence" if edge in EVIDENCE_EDGES else "transfer" if edge in TRANSFER_EDGES else "none"


def contrasts(draws: list[dict]) -> dict:
    return {
        "W^E evidence-vs-rest": group_contrast(draws, EVIDENCE_EDGES, kind="evidence"),
        "W^T transfer-vs-rest": group_contrast(draws, TRANSFER_EDGES, kind="transfer"),
    }


def report(mechanism: str, seed: int, res: dict, groups: dict, seconds: float):
    print(f"\n=== {mechanism} (seed {seed}, B={res['n_boot']}, {seconds:.0f}s)")
    print(f"{'edge':22} {'truth':9} {'W^E rel [95% CI]':30} {'W^T rel [95% CI]':30}")
    for edge, ci in sorted(res["edges"].items(), key=lambda kv: label(kv[0])):
        truth = label(edge) if mechanism.startswith("both") else "(placebo)"
        cells = [f"{ci[k]['rel_median']:+.2f} [{ci[k]['rel_lo']:+.2f}, {ci[k]['rel_hi']:+.2f}] {ci[k]['verdict']}"
                 for k in ("evidence", "transfer")]  # fmt: skip
        print(f"{edge:22} {truth:9} {cells[0]:30} {cells[1]:30}")
    for name, g in groups.items():
        print(f"  {name:22} Δ {g['delta_median']:+.3f} [{g['lo']:+.3f}, {g['hi']:+.3f}]  {g['verdict']}"
              f"  (P(Δ≤0) {g['share_delta_le_0']:.3f})")  # fmt: skip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--students", type=int, default=120)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0])
    parser.add_argument("--boot", type=int, default=40)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--strength", type=float, default=0.9,
                        help="transfer strength and knowledge coupling of the mechanism edges")
    parser.add_argument("--mechanisms", nargs="+", choices=["both", "none"], default=["both", "none"],
                        help="'none' (placebo) does not depend on --strength, so it can be reused")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    k = args.strength

    for seed in args.seeds:
        for mechanism in args.mechanisms:
            # Strength 0.9 and the placebo keep the original file names (edge_ci_v2 results).
            suffix = f"_k{k:g}" if mechanism == "both" and k != 0.9 else ""
            draws_path = out / f"draws_{mechanism}_s{seed}_n{args.students}{suffix}.json"
            started = time.perf_counter()
            if draws_path.exists():  # reuse saved fits
                draws = json.loads(draws_path.read_text(encoding="utf-8"))["draws"]
            else:
                strengths = {e: k for e in TRANSFER} if mechanism == "both" else {}
                coupling = {e: k for e in EVIDENCE} if mechanism == "both" else {}
                with tempfile.TemporaryDirectory() as tmp:
                    events, graph = synthetic.write(tmp, n_students=args.students, seed=300 + seed, transfer=0.0,
                                                    edge_strengths=strengths, knowledge_coupling=coupling)  # fmt: skip
                    data = load_dataset(events, graph)
                train = data.subset(temporal_split(data).train)
                draws = bootstrap_draws(train, {"epochs": args.epochs}, n_boot=args.boot, seed=seed,
                                        workers=args.workers)  # fmt: skip
                draws_path.write_text(json.dumps({"args": vars(args), "mechanism": mechanism, "seed": seed,
                                                  "draws": draws}), encoding="utf-8")  # fmt: skip
            res = edge_intervals(None, draws=draws)
            groups = contrasts(draws)
            report(f"{mechanism} k={k:g}" if mechanism == "both" else mechanism, seed, res, groups,
                   time.perf_counter() - started)  # fmt: skip
            (out / f"summary_{mechanism}_s{seed}_n{args.students}{suffix}.json").write_text(
                json.dumps({"intervals": res, "contrasts": groups}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
