"""Power of the preregistered H1/H2 analysis (v0.4) on simulated classes. SIMULATED DATA.

For each class size and seed: simulate a course (engine.sim_course, with its TRUTH or the
placebo), run exactly the preregistered edge analysis (whole semester, thin-edge rule,
student bootstrap, W^T group contrasts, Holm at 0.05, decision rule) and record the
decisions. Power = share of seeds where a hypothesis is "supported"; on the placebo the same
share is the false positive rate. Rows are appended to rows.jsonl, so a stopped run resumes.

    python experiments/power_prereg.py --out results/power_prereg --students 40 80 120 200 300 \
        --seeds 0-9 --boot 200 --workers 12
    python experiments/power_prereg.py --out results/power_prereg --students 120 --seeds 0-9 --placebo
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import sim_course  # noqa: E402
from engine.data import load_dataset  # noqa: E402
from engine.edge_bootstrap import bootstrap_draws, group_contrast, holm  # noqa: E402
from experiments.prereg_analysis import (  # noqa: E402
    ALPHA, MIN_EDGES, concept_attempts, decide, hypothesis_groups, thin_edges,
)


def seeds(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def one_run(students: int, seed: int, placebo: bool, boot: int, workers: int) -> dict:
    truth = sim_course.PLACEBO if placebo else None
    graph = sim_course.graph_json("sim")
    with tempfile.TemporaryDirectory() as tmp:
        folder = sim_course.write(Path(tmp) / "sim", n_students=students, seed=seed, truth=truth)
        data = load_dataset(folder / "events.csv", folder / "graph.json")
    excluded = thin_edges(graph, concept_attempts(data))
    started = time.perf_counter()
    draws = bootstrap_draws(data, {}, n_boot=boot, seed=seed, workers=workers)
    contrasts, dropped = {}, {}
    for h, (a, b) in hypothesis_groups(graph).items():
        a, b = a - set(excluded), b - set(excluded)
        if len(a) < MIN_EDGES[h] or len(b) < MIN_EDGES[h]:
            dropped[h] = True
            continue
        contrasts[h] = group_contrast(draws, a, kind="transfer", versus=b)
    rejected = holm({h: c["p_two_sided"] for h, c in contrasts.items()}, alpha=ALPHA)
    return {
        "students": students, "seed": seed, "placebo": placebo, "boot": boot, "events": len(data.interactions),
        "excluded_edges": len(excluded), "dropped": dropped, "seconds": round(time.perf_counter() - started),
        **{f"{h}_{k}": c[k] for h, c in contrasts.items() for k in ("delta_median", "lo", "hi", "p_two_sided")},
        **{f"{h}_decision": decide(c, rejected[h]) for h, c in contrasts.items()},
    }  # fmt: skip


def summarize(rows: list[dict]) -> list[str]:
    lines = ["| Data | Students | Runs | H1 supported | H2 supported | Both | H1 Δ (mean) | H2 Δ (mean) |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    keys = sorted({(r["placebo"], r["students"]) for r in rows})
    for placebo, n in keys:
        rs = [r for r in rows if r["placebo"] == placebo and r["students"] == n]
        share = lambda pred: sum(pred(r) for r in rs) / len(rs)  # noqa: E731
        h1 = share(lambda r: r.get("H1_decision") == "supported")
        h2 = share(lambda r: r.get("H2_decision") == "supported")
        both = share(lambda r: r.get("H1_decision") == "supported" and r.get("H2_decision") == "supported")
        d1 = sum(r.get("H1_delta_median", 0) for r in rs) / len(rs)
        d2 = sum(r.get("H2_delta_median", 0) for r in rs) / len(rs)
        lines.append(f"| {'placebo' if placebo else 'mechanism'} | {n} | {len(rs)} | {h1:.0%} | {h2:.0%} | {both:.0%} "
                     f"| {d1:+.3f} | {d2:+.3f} |")  # fmt: skip
    return lines


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--students", type=int, nargs="+", required=True)
    parser.add_argument("--seeds", default="0-9")
    parser.add_argument("--boot", type=int, default=200)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--placebo", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()] if rows_path.exists() else []
    done = {(r["students"], r["seed"], r["placebo"], r["boot"]) for r in rows}

    for n in args.students:
        for seed in seeds(args.seeds):
            if (n, seed, args.placebo, args.boot) in done:
                continue
            row = one_run(n, seed, args.placebo, args.boot, args.workers)
            rows.append(row)
            with open(rows_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{'placebo' if args.placebo else 'mech'} n={n} seed={seed} {row['seconds']}s "
                  f"H1 {row.get('H1_decision')} (p {row.get('H1_p_two_sided', float('nan')):.3f}) "
                  f"H2 {row.get('H2_decision')} (p {row.get('H2_p_two_sided', float('nan')):.3f})", flush=True)  # fmt: skip

    lines = ["# Power of the preregistered analysis (SIMULATED DATA)", "",
             f"B = {args.boot} per run; Holm α = {ALPHA}; whole semester (preregistration v0.4).", ""] + summarize(rows)  # fmt: skip
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
