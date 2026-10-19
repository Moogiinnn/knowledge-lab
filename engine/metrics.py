"""Evaluation metrics (guide §9): ROC AUC, LogLoss, Brier, ECE, thresholded accuracy/F1,
and student-level bootstrap confidence intervals with paired differences.

RMSE is not reported: on binary outcomes it is sqrt(Brier) and adds no evidence.
"""

import numpy as np

EPS = 1e-6


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann–Whitney AUC with average ranks for ties. NaN when only one class is present."""
    y = np.asarray(y)
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sorted_p = np.asarray(p)[order]
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def logloss(y, p) -> float:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def calibration_bins(y, p, bins: int = 10) -> list[dict]:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        mask = which == b
        if mask.any():
            out.append({"lo": edges[b], "hi": edges[b + 1], "n": int(mask.sum()),
                        "mean_p": float(p[mask].mean()), "mean_y": float(y[mask].mean())})  # fmt: skip
    return out


def ece(y, p, bins: int = 10) -> float:
    n = len(y)
    return float(sum(b["n"] / n * abs(b["mean_p"] - b["mean_y"]) for b in calibration_bins(y, p, bins)))


def best_threshold(y, p) -> float:
    """Accuracy-maximising threshold; chosen on validation and reused on test."""
    candidates = np.unique(np.concatenate([[0.5], np.asarray(p)]))
    accs = [np.mean((np.asarray(p) >= t) == np.asarray(y)) for t in candidates]
    return float(candidates[int(np.argmax(accs))])


def thresholded(y, p, threshold: float) -> dict:
    y, pred = np.asarray(y), np.asarray(p) >= threshold
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else float("nan")
    return {"threshold": threshold, "accuracy": float(np.mean(pred == y)), "f1": float(f1)}


SCALAR_METRICS = {"auc": auc, "logloss": logloss, "brier": brier, "ece": ece}


def summarize(y, p) -> dict:
    return {name: fn(y, p) for name, fn in SCALAR_METRICS.items()} | {"n": int(len(y)), "base_rate": float(np.mean(y))}


def bootstrap(
    students: np.ndarray, y: np.ndarray, preds: dict[str, np.ndarray], *, reference: str | None,
    n_boot: int = 1000, seed: int = 0,
) -> dict:
    """Resample students (not rows) with replacement, so within-student dependence is
    kept. Returns 95% percentile intervals per model and paired differences to `reference`."""
    rng = np.random.default_rng(seed)
    uniq, inverse = np.unique(students, return_inverse=True)
    rows_of = [np.flatnonzero(inverse == s) for s in range(len(uniq))]
    samples = {m: {k: [] for k in SCALAR_METRICS} for m in preds}
    diffs = {m: {k: [] for k in SCALAR_METRICS} for m in preds if m != reference}
    for _ in range(n_boot):
        rows = np.concatenate([rows_of[s] for s in rng.integers(0, len(uniq), len(uniq))])
        yb = y[rows]
        values = {m: {k: fn(yb, p[rows]) for k, fn in SCALAR_METRICS.items()} for m, p in preds.items()}
        for m in preds:
            for k in SCALAR_METRICS:
                samples[m][k].append(values[m][k])
                if reference in values and m != reference:
                    diffs[m][k].append(values[m][k] - values[reference][k])

    def ci(values):
        arr = np.asarray(values, dtype=float)
        arr = arr[~np.isnan(arr)]
        if len(arr) == 0:
            return None
        lo, hi = np.percentile(arr, [2.5, 97.5])
        return {"lo": float(lo), "hi": float(hi)}

    result = {"n_students": int(len(uniq)), "n_boot": n_boot,
              "ci": {m: {k: ci(v) for k, v in ms.items()} for m, ms in samples.items()}}  # fmt: skip
    if reference in preds:
        result["paired_diff_vs"] = reference
        result["paired_diff"] = {
            m: {k: (ci(v) | {"mean": float(np.nanmean(v))}) if ci(v) else None for k, v in ms.items()}
            for m, ms in diffs.items()
        }
    return result
