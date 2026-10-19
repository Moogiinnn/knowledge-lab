"""RQ4 on synthetic data: are the DKG-AP edge-message explanations faithful? (engine.faithfulness)

Synthetic data with both mechanisms (the w_recovery design: evidence coupling on EVIDENCE edges,
learning transfer on TRANSFER edges), the prediction configuration of DKG-AP (ability on),
trained on the training part of the temporal split. Messages are scored on the student's next
interaction with the target concept, inside the test part.

    python experiments/rq4_faithfulness.py --out results/rq4_faithfulness --seeds 0 1 2
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import synthetic  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.dkg import DKG  # noqa: E402
from engine.faithfulness import message_effects, summarize  # noqa: E402
from engine.split import temporal_split  # noqa: E402
from experiments.w_recovery import EVIDENCE, TRANSFER  # noqa: E402

MAIN = "attributed (a·v·decay·message)"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--students", type=int, default=120)
    parser.add_argument("--per-student", type=int, default=15)
    parser.add_argument("--boot", type=int, default=500)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    all_rows, per_seed = [], {}
    for seed in args.seeds:
        with tempfile.TemporaryDirectory() as tmp:
            synthetic.write(tmp, n_students=args.students, seed=seed,
                            edge_strengths={e: 0.9 for e in TRANSFER},
                            knowledge_coupling={e: 0.9 for e in EVIDENCE})
            data = load_dataset(Path(tmp) / "events.csv", Path(tmp) / "graph.json")
        split = temporal_split(data)
        model = DKG(seed=seed, epochs=15)
        model.fit(data.subset(split.train))
        rows = message_effects(model.model, data, per_student=args.per_student, only=split.test,
                               rng=np.random.default_rng(seed))
        for r in rows:
            r["student"] = f"{seed}:{r['student']}"
            r["seed"] = seed
        all_rows += rows
        per_seed[seed] = summarize(rows, boot=args.boot, seed=seed)
        s = per_seed[seed][MAIN]
        print(f"seed {seed}: {len(rows)} messages; spearman {s['spearman']['value']:.3f}, "
              f"sign {s['sign_agreement']['value']:.3f}, median rel. error {s['median_relative_error']['value']:.3f}",
              flush=True)
    pooled = summarize(all_rows, boot=args.boot)
    (out / "rows.json").write_text(json.dumps(all_rows, indent=1), encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"pooled": pooled, "per_seed": per_seed}, indent=2), encoding="utf-8")
    lines = ["| Тайлбар | Spearman ρ | Тэмдгийн тохирол | Харьцангуй алдааны медиан |", "|---|---|---|---|"]
    for name in [MAIN, "raw message contribution", "edge weight only", "random", "next_vs_all_later"]:
        cells = [f"{pooled[name][k]['value']:.3f} [{pooled[name][k]['ci'][0]:.3f}, {pooled[name][k]['ci'][1]:.3f}]"
                 for k in ("spearman", "sign_agreement", "median_relative_error")]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    summary = f"# RQ4 synthetic ({pooled['n_messages']} messages, {pooled['n_students']} students)\n\n" + "\n".join(lines)
    (out / "summary.md").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
