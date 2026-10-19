"""The preregistered edge analysis (docs/preregistration.md §1–§5, v0.4) in one command.

    python experiments/prereg_analysis.py --events exports/<course>/events.csv \
        --graph exports/<course>/graph.json --out results/<run> --boot 1000 --workers 12

Steps: DKG-AP with the fixed edge-analysis settings (engine.edge_bootstrap.EDGE_ANALYSIS_DEFAULTS)
fitted on the whole semester (`--period all`, preregistration v0.4; `--period train` is the
first 60% only, as in v0.3, kept for comparison), student bootstrap, group contrasts of W^T for
    H1  PREREQUISITE edges vs RELATED edges,
    H2  structure→algorithm vs structure→structure edges (CONTROL edges excluded),
two-sided bootstrap p-values, Holm at α = 0.05, and the decision rule of §5.

Why the whole semester: the course opens new concepts every week, so the first 60% of the
time contains no attempts on the last topics and their edges cannot be learned (found in the
dress rehearsal, docs/STATUS.md §9). The edge analysis does not predict held-out answers, so
holding data out gains nothing here; prediction questions (RQ1–RQ3) keep the temporal split.

Edges whose source or target concept has fewer than MIN_CONCEPT_ATTEMPTS attempts in the
analysed data are excluded from the hypothesis groups and listed. A hypothesis whose groups
are then too small (H1: ≥ 4 edges each, H2: ≥ 3) is dropped and reported.
`--truth` (simulated data only) adds the ground truth next to every edge.
Outputs: results.json (everything), draws.json (bootstrap fits, reusable), summary.md.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.data import Dataset, load_dataset  # noqa: E402
from engine.edge_bootstrap import bootstrap_draws, edge_intervals, group_contrast, holm  # noqa: E402
from engine.split import temporal_split  # noqa: E402

MIN_EDGES = {"H1": 4, "H2": 3}
MIN_CONCEPT_ATTEMPTS = 30
ALPHA = 0.05


def hypothesis_groups(graph: dict) -> dict:
    kind = {c["code"]: c.get("kind", "") for c in graph["concepts"]}
    edges = [e for e in graph["edges"] if e.get("approved")]
    name = lambda e: f"{e['source']}→{e['target']}"  # noqa: E731
    not_control = [e for e in edges if e["relation"] != "CONTROL"]
    return {
        "H1": ({name(e) for e in edges if e["relation"] == "PREREQUISITE"},
               {name(e) for e in edges if e["relation"] == "RELATED"}),
        "H2": ({name(e) for e in not_control if kind[e["source"]] == "STRUCTURE" and kind[e["target"]] == "ALGORITHM"},
               {name(e) for e in not_control if kind[e["source"]] == "STRUCTURE" and kind[e["target"]] == "STRUCTURE"}),
    }  # fmt: skip


def concept_attempts(data: Dataset) -> Counter:
    counts = Counter()
    for it in data.interactions:
        for c, _weight in it.concepts:
            counts[data.concepts[c]] += 1
    return counts


def thin_edges(graph: dict, counts: Counter, minimum: int = MIN_CONCEPT_ATTEMPTS) -> dict[str, str]:
    """Edges with too little data on either end: {edge: reason}."""
    out = {}
    for e in graph["edges"]:
        low = [c for c in (e["source"], e["target"]) if counts.get(c, 0) < minimum]
        if low:
            out[f"{e['source']}→{e['target']}"] = ", ".join(f"{c}: {counts.get(c, 0)} attempts" for c in low)
    return out


def decide(contrast: dict, rejected: bool) -> str:
    if contrast["verdict"].startswith("degenerate"):
        return "invalid (no bootstrap spread)"
    if not rejected:
        return "inconclusive"
    return "supported" if contrast["delta_median"] > 0 else "evidence against"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--period", choices=["all", "train"], default="all",
                        help="all = whole semester (preregistration v0.4); train = first 60%% (v0.3)")  # fmt: skip
    parser.add_argument("--boot", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--truth", help="truth.json of a simulated course (rehearsal only)")
    parser.add_argument("--draws", help="reuse saved draws.json instead of refitting")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    data = load_dataset(args.events, args.graph)
    analysed = data if args.period == "all" else data.subset(temporal_split(data, 0.6, 0.2).train)
    counts = concept_attempts(analysed)
    excluded = thin_edges(graph, counts)

    started = time.perf_counter()
    if args.draws:
        draws = json.loads(Path(args.draws).read_text(encoding="utf-8"))
    else:
        draws = bootstrap_draws(analysed, {}, n_boot=args.boot, seed=args.seed, workers=args.workers)
        (out / "draws.json").write_text(json.dumps(draws), encoding="utf-8")
    seconds = time.perf_counter() - started

    contrasts, dropped, groups = {}, {}, {}
    for h, (a, b) in hypothesis_groups(graph).items():
        a, b = a - set(excluded), b - set(excluded)
        groups[h] = {"A": sorted(a), "B": sorted(b)}
        if len(a) < MIN_EDGES[h] or len(b) < MIN_EDGES[h]:
            dropped[h] = f"groups have {len(a)} and {len(b)} edges (need ≥ {MIN_EDGES[h]} each)"
            continue
        contrasts[h] = group_contrast(draws, a, kind="transfer", versus=b)
    rejected = holm({h: c["p_two_sided"] for h, c in contrasts.items()}, alpha=ALPHA)
    decisions = {h: decide(c, rejected[h]) for h, c in contrasts.items()}
    intervals = edge_intervals(None, draws=draws)
    truth = None
    if args.truth:
        truth = {e["edge"]: e for e in json.loads(Path(args.truth).read_text(encoding="utf-8"))["edges"]}

    result = {
        "simulated": bool(args.truth), "course": graph.get("course"), "period": args.period,
        "students": len(analysed.students), "events": len(analysed.interactions),
        "events_total": len(data.interactions), "students_total": len(data.students), "concepts": len(data.concepts),
        "edges": len(data.edges), "n_boot": len(draws), "seed": args.seed, "seconds": round(seconds),
        "concept_attempts": dict(counts), "excluded_edges": excluded, "groups": groups,
        "contrasts": contrasts, "holm_rejected": rejected, "decisions": decisions, "dropped": dropped,
        "edge_intervals": intervals["edges"], "truth": truth,
    }  # fmt: skip
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Preregistered edge analysis: {graph.get('course')}", ""]
    if result["simulated"]:
        lines += ["**SIMULATED DATA: dress rehearsal, not research results.**", ""]
    lines += [f"Period: {args.period}; {result['students']} students, {result['events']} events; B = {len(draws)}; "
              f"{seconds / 60:.0f} min. Excluded edges (< {MIN_CONCEPT_ATTEMPTS} attempts on an end): "
              f"{', '.join(excluded) or 'none'}.", "",
              "| Hypothesis | Δ (median) | 95% CI | p (two-sided) | Holm | Decision |", "|---|---|---|---|---|---|"]  # fmt: skip
    for h, c in contrasts.items():
        lines.append(f"| {h} | {c['delta_median']:+.3f} | [{c['lo']:+.3f}, {c['hi']:+.3f}] | {c['p_two_sided']:.3f} | "
                     f"{'rejected' if rejected[h] else 'not rejected'} | {decisions[h]} |")  # fmt: skip
    for h, why in dropped.items():
        lines.append(f"| {h} | — | — | — | — | dropped: {why} |")
    lines += ["", "| Edge | W^T rel [95% CI] | verdict" + (" | truth transfer |" if truth else " |"),
              "|---|---|---|" + ("---|" if truth else "")]  # fmt: skip
    for edge, ci in sorted(intervals["edges"].items(), key=lambda kv: -kv[1]["transfer"]["rel_median"]):
        t = ci["transfer"]
        row = f"| {edge} | {t['rel_median']:+.2f} [{t['rel_lo']:+.2f}, {t['rel_hi']:+.2f}] | {t['verdict']} |"
        lines.append(row + (f" {truth[edge]['transfer']:.2f} ({truth[edge]['relation']}) |" if truth else ""))
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12 + len(contrasts) + len(dropped)]))


if __name__ == "__main__":
    main()
