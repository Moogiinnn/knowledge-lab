"""RQ4: are the DKG-AP edge-message explanations faithful to the model's decisions?

An explanation lists, for every answer, the messages each edge i→j sent to the target state
k_j ("contribution", in state units). What a reader needs is the message's effect on a later
*prediction*. The explanation implies one: at the student's next interaction that involves j,
the message should move the logit by

    attributed = a_{q',j} · v_j · exp(−β_j · Δτ) · contribution

(a: concept weight of that question, v: concept coefficient, the exponential: decay between
the two interactions). The counterfactual replays the same student with that single message
dropped (DKGModel.replay(mute=...)) and measures the actual change of that logit. Clamping of
k to [0, 1] and the transfer "room" term are where the two can differ. A second horizon adds
up the effect on all of the student's later predictions (through later residuals, ability
and further messages), which the explanation does not claim but a teacher may assume.

Faithfulness = agreement between attributed and counterfactual effects (Spearman ρ, sign
agreement, median relative error), with two simpler explanations as reference points: the
raw message contribution (state units) and the edge weight alone (signed like the message).
"""

import math

import numpy as np
import torch
from scipy.stats import spearmanr

from .data import Dataset
from .dkg import DAY, DKGModel


@torch.no_grad()
def _logits(model: DKGModel, data: Dataset, mute=None) -> list[float]:
    return [e["logit"] for _, _, e in model.replay(data, explain=True, mute=mute)]


@torch.no_grad()
def message_effects(model: DKGModel, data: Dataset, *, students=None, per_student: int = 20,
                    only=None, rng: np.random.Generator | None = None) -> list[dict]:
    """One row per sampled message: attributed vs counterfactual effect on later logits.

    only: optional set of interaction idx; a message is used only if the prediction it is
    scored on (the student's next interaction with the target concept) is in this set, e.g.
    the test part of a split."""
    rng = rng or np.random.default_rng(0)
    softplus = lambda x: math.log1p(math.exp(float(x)))  # noqa: E731
    by_student: dict[str, list[int]] = {}
    for it in data.interactions:
        by_student.setdefault(it.student, []).append(it.idx)
    rows = []
    for student in students if students is not None else sorted(by_student):
        sub = data.subset(by_student[student])
        trace = list(model.replay(sub, explain=True))
        its = [it for it, _, _ in trace]
        z_full = [e["logit"] for _, _, e in trace]
        candidates = []
        for pos, (it, _, exp) in enumerate(trace):
            for m in exp["messages"]:
                j = int(model.dst[m["edge_id"]])
                nxt = next((q for q in range(pos + 1, len(its)) if any(c == j for c, _ in its[q].concepts)), None)
                if nxt is None or (only is not None and its[nxt].idx not in only):
                    continue
                candidates.append((pos, nxt, j, m))
        if not candidates:
            continue
        pick = rng.choice(len(candidates), size=min(per_student, len(candidates)), replace=False)
        for c in sorted(pick):
            pos, nxt, j, m = candidates[c]
            it, it_next = its[pos], its[nxt]
            a_j = dict(it_next.concepts)[j]
            decay = math.exp(-softplus(model.beta_raw[j]) * (it_next.t - it.t) / DAY) if model.config.decay else 1.0
            attributed = a_j * float(model.v[j]) * decay * m["contribution"]
            z_muted = _logits(model, sub, mute=(it.idx, m["edge_id"], m["kind"]))
            sign = 1.0 if m["kind"] == "transfer" else math.copysign(1.0, m.get("delta_source", 0.0) or 1.0)
            rows.append({
                "student": student, "kind": m["kind"], "edge": m["edge"],
                "contribution": m["contribution"], "w_bar_signed": sign * m["w_bar"],
                "attributed": attributed,
                "counterfactual_next": z_full[nxt] - z_muted[nxt],
                "counterfactual_all_later": sum(z_full[q] - z_muted[q] for q in range(pos + 1, len(its))),
            })  # fmt: skip
    return rows


def _agreement(x: np.ndarray, y: np.ndarray) -> dict:
    nz = np.abs(y) > 1e-7
    rho = spearmanr(x, y).statistic if len(x) > 2 and np.std(x) > 0 and np.std(y) > 0 else float("nan")
    rel = np.abs(x[nz] - y[nz]) / np.abs(y[nz])
    return {"spearman": float(rho),
            "sign_agreement": float(np.mean(np.sign(x[nz]) == np.sign(y[nz]))) if nz.any() else float("nan"),
            "median_relative_error": float(np.median(rel)) if nz.any() else float("nan")}  # fmt: skip


def summarize(rows: list[dict], *, boot: int = 200, seed: int = 0) -> dict:
    """Agreement of each explanation with the counterfactual next-prediction effect, with
    95% student-bootstrap intervals; plus how much of the total later effect it captures."""
    rng = np.random.default_rng(seed)
    students = sorted({r["student"] for r in rows})
    by = {s: [r for r in rows if r["student"] == s] for s in students}
    explanations = {
        "attributed (a·v·decay·message)": "attributed",
        "raw message contribution": "contribution",
        "edge weight only": "w_bar_signed",
    }

    def compute(rs):
        y = np.array([r["counterfactual_next"] for r in rs])
        out = {name: _agreement(np.array([r[key] for r in rs]), y) for name, key in explanations.items()}
        out["random"] = _agreement(rng.normal(size=len(rs)), y)
        later = np.array([r["counterfactual_all_later"] for r in rs])
        out["next_vs_all_later"] = _agreement(y, later)
        return out

    point = compute(rows)
    draws = [compute([r for s in rng.choice(students, len(students)) for r in by[s]]) for _ in range(boot)]
    result = {"n_messages": len(rows), "n_students": len(students),
              "kinds": {k: sum(r["kind"] == k for r in rows) for k in ("evidence", "transfer")}}  # fmt: skip
    for name, metrics in point.items():
        result[name] = {}
        for metric, value in metrics.items():
            vals = [d[name][metric] for d in draws if not math.isnan(d[name][metric])]
            lo, hi = np.percentile(vals, [2.5, 97.5]) if vals else (float("nan"), float("nan"))
            result[name][metric] = {"value": value, "ci": [float(lo), float(hi)]}
    return result
