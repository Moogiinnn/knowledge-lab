"""Uncertainty of learned edge strengths: student-level bootstrap of DKG-AP fits.

A single class gives a single dataset, and the power study showed that one fit's edge
ranking can be far from the truth even when nothing is there (placebo pair AUC from 0.19
to 0.81). So every reported edge strength needs an interval.

Procedure: resample the training students with replacement (a student drawn twice
becomes two independent students), refit DKG with the same config, and record for every
edge r^E = w^E_ij / s^E_j and r^T = w^T_ij / s^T_j. Percentile intervals follow.

Do not read "the interval of r excludes 1 (the W0 value)" as evidence: Adam moves every
parameter by a similar step whatever its gradient, so r leaves 1 even on placebo data
(found in a smoke test). The reported test is relative instead:
    rel_ij = log r_ij − mean over all edges of log r
computed within each bootstrap fit, so a drift shared by all edges cancels. An interval of
rel above 0 means "stronger than the average edge", below 0 "weaker".
"""

import dataclasses
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .data import Dataset

R0 = 1.0  # r implied by W0 with initial_weight 1 and slack_init 1

# Decision 2026-10-09 (researcher): the online ability term θ_u is used for prediction but
# switched off for edge analysis, because θ_u competes with W^E for the same signal (W^E
# recovery fell from 0.76 to 0.64 pair AUC with it, prediction rose by +0.024 AUC).
EDGE_ANALYSIS_DEFAULTS = {"ability": False}


def edge_analysis_config(config: dict) -> dict:
    """DKG settings for edge analysis: the defaults above unless the caller overrides them."""
    return {**EDGE_ANALYSIS_DEFAULTS, **config}


def resample_students(data: Dataset, rng: np.random.Generator) -> Dataset:
    """Draw students with replacement; each draw becomes a distinct student "<id>#<k>".
    Interactions keep their times and are re-sorted into replay order."""
    by_student: dict[str, list] = {}
    for it in data.interactions:
        by_student.setdefault(it.student, []).append(it)
    students = sorted(by_student)
    drawn = rng.choice(len(students), size=len(students), replace=True)
    rows = []
    for k, s in enumerate(drawn):
        for it in by_student[students[s]]:
            rows.append(dataclasses.replace(it, student=f"{it.student}#{k}", event_id=f"{it.event_id}#{k}"))
    rows.sort(key=lambda it: (it.t, it.event_id))
    rows = [dataclasses.replace(it, idx=n) for n, it in enumerate(rows)]
    return Dataset(rows, data.concepts, data.edges, data.questions, data.source_sha256, data.dropped)


def _fit_once(args) -> dict:
    train, config, seed = args
    import torch

    from .dkg import DKG

    torch.set_num_threads(1)  # several workers run in parallel
    sample = resample_students(train, np.random.default_rng(seed))
    model = DKG(**{**config, "seed": seed})
    model.fit(sample)
    desc = model.describe()
    return {e["edge"]: (e["r"], e["r_transfer"]) for e in desc["edges"]}


KINDS = ("evidence", "transfer")


def bootstrap_draws(train: Dataset, config: dict, *, n_boot: int = 40, seed: int = 0, workers: int = 6) -> list[dict]:
    """Refit DKG on n_boot student resamples. Returns one {edge: [r^E, r^T]} per fit.
    Save the result: intervals and any group contrast can be recomputed from it."""
    if config.get("w_norm", "slack") != "slack":
        raise ValueError("edge intervals are defined for w_norm='slack' only")
    config = edge_analysis_config(config)
    jobs = [(train, config, seed * 100_003 + b) for b in range(n_boot)]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            draws = list(pool.map(_fit_once, jobs))
    else:
        draws = [_fit_once(job) for job in jobs]
    return [{edge: list(values) for edge, values in d.items()} for d in draws]


def relative_strengths(draws: list[dict], kind: str) -> tuple[list[str], np.ndarray]:
    """rel = log r − mean over edges of log r, per draw: (edge names, array n_draws × n_edges)."""
    names = sorted(draws[0])
    r = np.array([[d[edge][KINDS.index(kind)] for edge in names] for d in draws])
    return names, np.log(r) - np.log(r).mean(axis=1, keepdims=True)


def edge_intervals(train: Dataset | None, config: dict | None = None, *, n_boot: int = 40, seed: int = 0,
                   workers: int = 6, level: float = 0.95, draws: list[dict] | None = None) -> dict:  # fmt: skip
    """Bootstrap percentile intervals for r^E and r^T of every edge.

    `config` are DKG keyword arguments; it must use w_norm="slack" (r is defined by s_j).
    Pass `draws` from bootstrap_draws() to reuse earlier fits instead of refitting.
    Returns {"edges": {edge: {"evidence": {...}, "transfer": {...}}}, "n_boot": ..., ...}.
    """
    if draws is None:
        draws = bootstrap_draws(train, config or {}, n_boot=n_boot, seed=seed, workers=workers)

    alpha = (1 - level) / 2
    edges = {edge: {} for edge in sorted(draws[0])}
    for pos, kind in enumerate(KINDS):
        names, rel = relative_strengths(draws, kind)
        r = np.array([[d[edge][pos] for edge in names] for d in draws])
        for e, edge in enumerate(names):
            lo, hi = np.quantile(r[:, e], [alpha, 1 - alpha])
            rlo, rhi = np.quantile(rel[:, e], [alpha, 1 - alpha])
            edges[edge][kind] = {
                "median": float(np.median(r[:, e])), "lo": float(lo), "hi": float(hi),
                "rel_median": float(np.median(rel[:, e])), "rel_lo": float(rlo), "rel_hi": float(rhi),
                "verdict": _verdict(rlo, rhi, "stronger", "weaker"),
            }  # fmt: skip
    students = len(train.students) if train is not None else None
    return {"edges": edges, "n_boot": len(draws), "level": level, "prior_r": R0, "students": students}


def holm(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down: which hypotheses are rejected at family-wise level alpha."""
    rejected, ordered = {}, sorted(p_values.items(), key=lambda kv: kv[1])
    for rank, (name, p) in enumerate(ordered):
        if p > alpha / (len(ordered) - rank):
            rejected.update({n: False for n, _ in ordered[rank:]})
            break
        rejected[name] = True
    return rejected


def _verdict(lo: float, hi: float, above: str, below: str) -> str:
    # Identical draws (e.g. too few epochs, so every fit takes the same optimizer steps)
    # give a zero-width interval that would otherwise "exclude 0" by rounding.
    if hi - lo < 1e-9:
        return "degenerate (no bootstrap spread)"
    return above if lo > 0 else below if hi < 0 else "unclear"


def group_contrast(draws: list[dict], group: set[str], *, kind: str, versus: set[str] | None = None,
                   level: float = 0.95) -> dict:  # fmt: skip
    """Δ = mean rel over `group` − mean rel over `versus` (default: all other edges), per draw.

    Pooling several edges gives more power than testing edges one by one. Edges are named
    "source→target". A one-sided bootstrap share P(Δ ≤ 0) is reported for reference; the
    verdict uses the two-sided interval only.
    """
    names, rel = relative_strengths(draws, kind)
    versus = set(names) - set(group) if versus is None else set(versus)
    unknown = (set(group) | versus) - set(names)
    if unknown:
        raise ValueError(f"unknown edges: {sorted(unknown)}")
    if not group or not versus or set(group) & versus:
        raise ValueError("groups must be non-empty and disjoint")
    a = rel[:, [names.index(e) for e in sorted(group)]].mean(axis=1)
    b = rel[:, [names.index(e) for e in sorted(versus)]].mean(axis=1)
    delta = a - b
    alpha = (1 - level) / 2
    lo, hi = np.quantile(delta, [alpha, 1 - alpha])
    return {
        "kind": kind, "group": sorted(group), "versus": sorted(versus),
        "delta_median": float(np.median(delta)), "lo": float(lo), "hi": float(hi),
        "share_delta_le_0": float(np.mean(delta <= 0)),
        # Two-sided bootstrap p-value for H0: Δ = 0, for multiplicity corrections (Holm).
        # Its resolution is 2/n_boot, so use n_boot ≥ 1000 when it matters.
        "p_two_sided": float(min(1.0, 2 * min(np.mean(delta <= 0), np.mean(delta >= 0)))),
        "verdict": _verdict(lo, hi, "group stronger", "group weaker"),
        "n_boot": len(draws), "level": level,
    }  # fmt: skip
