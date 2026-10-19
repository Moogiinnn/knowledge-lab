"""Section 4.2 with the current code: can an evidence-only edge weight represent transfer?

The first DKG-AP version had one weight per edge, used like today's W^E (messages signed by
the source residual). This reruns that question with the current model by switching the
transfer mechanism off (transfer=False): synthetic data where only TRANSFER edges carry
learning transfer (edge_strengths 0.9, no knowledge coupling), and we check whether the
learned evidence weight r^E ranks the transfer edges above the others (pair AUC; 0.5 =
chance, < 0.5 = ranked backwards). For comparison the two-mechanism model is fitted on the
same data and its r^T is scored the same way. Training part of the temporal split only.

    python experiments/w_single_mechanism.py --out results/rerun_2026-10-11/w_single_mechanism --seeds 0 1 2
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import synthetic  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.dkg import DKG  # noqa: E402
from engine.edge_bootstrap import edge_analysis_config  # noqa: E402
from engine.split import temporal_split  # noqa: E402
from experiments.w_recovery import TRANSFER, pair_auc  # noqa: E402

TRANSFER_EDGES = {f"{s}→{t}" for s, t in TRANSFER}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--students", type=int, default=120)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in args.seeds:
        with tempfile.TemporaryDirectory() as tmp:
            synthetic.write(tmp, n_students=args.students, seed=seed, edge_strengths={e: 0.9 for e in TRANSFER})
            data = load_dataset(Path(tmp) / "events.csv", Path(tmp) / "graph.json")
        train = data.subset(temporal_split(data).train)
        row = {"seed": seed, "students": args.students}
        for name, cfg in {"evidence_only": {"transfer": False}, "two_mechanisms": {}}.items():
            model = DKG(**edge_analysis_config({**cfg, "seed": seed}))
            model.fit(train)
            edges = model.describe()["edges"]
            r_e = {e["edge"]: e["r"] for e in edges}
            r_t = {e["edge"]: e["r_transfer"] for e in edges}
            row[f"{name}_pairAUC_rE"] = pair_auc(r_e, TRANSFER_EDGES)
            if name == "two_mechanisms":
                row[f"{name}_pairAUC_rT"] = pair_auc(r_t, TRANSFER_EDGES)
        rows.append(row)
        print(json.dumps(row), flush=True)
    (out / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
