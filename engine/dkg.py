"""DKG-AP: dynamic knowledge graph with adaptive propagation (guide §5–§8, first version).

For interaction t of student u on question q with concepts Q (weights a_qi):

  decay      k⁻_i = b_i + (k_i − b_i)·exp(−β_i·Δτ_i)            β_i ≥ 0, Δτ in days
  predict    p_t  = σ(c − d_q + θ_u + Σ_i a_qi·v_i·k⁻_i)           before y_t is seen
  ability    θ_u ← θ_u + η_θ/√(1+n_u)·(y_t − p_t)                  online, from u's past answers only
  evidence   δ_i  = η_t·a_qi·(y_t − p_t)                          0 ≤ η_t ≤ η_max
  gate       α_ij = σ(θᵀφ_ij)                                     per edge i→j, per interaction
  evidence   m^E_j = Σ_{i∈N⁻(j)} α_ij·W̄^E_ij·δ_i                  signed: follows the residual
  transfer   m^T_j = κ·Σ_{i∈N⁻(j)} W̄^T_ij·a_qi·(1 − k⁻_j)          ≥ 0: practice on i helps j
  update     k_j  = clip(k⁻_j + δ_j + γ·m^E_j + m^T_j, 0, 1)

Each edge has two weights. W^E says how much an answer on i is evidence about j (shared
knowledge); W^T says how much practising i transfers learning to j, whatever the answer.
Both are restricted to the teacher-approved edge mask and, by default, normalised per
target with a learned "none" share s_j (w_norm="slack"): W̄_ij = w_ij / (s_j + Σ_i' w_i'j).
Plain incoming normalisation fixes a lone incoming edge at 1 and cannot learn it.

d_q = d_scale·(teacher difficulty − 3) + u_q, with u_q learned for training questions
(0 for unseen ones). η_t may depend on the current attempt's evidence (hints, time,
assistance), which is observed only for the update, never for p_t.

Ablation switches: decay, propagation (evidence messages), transfer, adaptive_alpha
(θ vs a single constant), adaptive_eta, and w_mode (applies to W^E and W^T alike):
  fixed    W = W0 (teacher initial weights)
  learned  W fitted once on the whole training period
  dynamic  W(k) = (1−λ)·W(k−1) + λ·Ĉ(k) over K chronological training windows, Ĉ(k)
           fitted on history up to window k with a penalty towards W0; the final W is
           frozen for validation and test.
"""

import math
from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np
import torch
from torch import nn

from .data import Dataset, Interaction

DAY = 86400.0


@dataclass
class DKGConfig:
    decay: bool = True
    propagation: bool = True  # evidence messages (W^E)
    transfer: bool = True  # learning-transfer messages (W^T)
    # Online student ability θ_u (Elo-style): added to the logit, updated after each answer by
    # η_θ/√(1+n_u)·(y − p). Absorbs general skill so it is not attributed to concept edges.
    ability: bool = True
    adaptive_alpha: bool = True
    adaptive_eta: bool = True
    w_mode: str = "learned"  # fixed | learned | dynamic
    eta_max: float = 0.5
    epochs: int = 15
    lr: float = 0.05
    l2: float = 1e-3
    # Penalty pulling W̄ towards W0 (teacher weights). The loss is a *mean* NLL, so a MAP
    # penalty must be divided by the number of training events:
    #   per_event  (w_prior_events / n)·‖W̄ − W̄0‖²   W0 counts as w_prior_events interactions;
    #                                                 its pull fades as data grows (default)
    #   constant   w_reg·‖W̄ − W̄0‖²                  pull independent of n (results before 2026-10-09)
    w_reg_scaling: str = "per_event"
    w_prior_events: float = 10.0
    w_reg: float = 1e-2
    # How W is turned into W̄ (see EdgeWeights.fitted):
    #   slack     w_ij / (s_j + Σ_i' w_i'j)    (default; learned per-target "none" share s_j)
    #   incoming  w_ij / Σ_i' w_i'j            (a lone incoming edge is always 1: not learnable)
    #   bounded   σ(r_ij) ∈ (0, 1)             (independent per edge; scale shared with γ / κ)
    w_norm: str = "slack"
    slack_init: float = 1.0
    windows: int = 4
    lam: float = 0.5
    seed: int = 0


def _inv_softplus(x: float) -> float:
    return math.log(math.expm1(x))


def _logit(x: float) -> float:
    return math.log(x / (1 - x))


ETA_FEATURES = ["bias", "log_hints", "log_minutes", "assistance", "time_missing"]
ALPHA_FEATURES = ["bias", "k_source", "k_target", "difficulty", "residual", "log_gap_days", "log_prior_attempts",
                  "log_hints", "is_prerequisite"]  # fmt: skip


class EdgeWeights(nn.Module):
    """One set of edge weights W over the edge mask, with its normalisation, its W0
    anchor and the dynamic-mode state. DKGModel has one for evidence and one for transfer."""

    def __init__(self, dst: torch.Tensor, n_concepts: int, w0: torch.Tensor, config: DKGConfig, learn: bool):
        super().__init__()
        self.dst, self.n, self.norm = dst, n_concepts, config.w_norm
        if self.norm == "bounded":
            start = torch.logit((0.5 * w0).clamp(0.01, 0.99)) if len(w0) else torch.zeros(0)
        elif self.norm in ("incoming", "slack"):
            start = torch.log(torch.expm1(w0)) if len(w0) else torch.zeros(0)
        else:
            raise ValueError(f"unknown w_norm {self.norm!r}")
        self.raw = nn.Parameter(start, requires_grad=learn)
        self.slack_raw = nn.Parameter(
            torch.full((n_concepts,), _inv_softplus(config.slack_init)), requires_grad=learn and self.norm == "slack"
        )
        with torch.no_grad():
            self.register_buffer("w_bar0", self.fitted())
        self.register_buffer("w_prev", self.w_bar0.clone())  # dynamic mode: W(k−1), or the final W

    def fitted(self) -> torch.Tensor:
        if len(self.raw) == 0:
            return self.raw
        if self.norm == "bounded":
            return torch.sigmoid(self.raw)
        w = torch.nn.functional.softplus(self.raw)
        incoming = torch.zeros(self.n).index_add(0, self.dst, w)
        if self.norm == "slack":
            incoming = incoming + torch.nn.functional.softplus(self.slack_raw)
        return w / incoming[self.dst].clamp_min(1e-8)

    def current(self, mode: str, dynamic_active: bool, frozen: bool, lam: float) -> torch.Tensor:
        if mode == "fixed":
            return self.w_bar0
        if mode == "dynamic" and frozen:
            return self.w_prev
        if mode == "dynamic" and dynamic_active:
            return (1 - lam) * self.w_prev + lam * self.fitted()
        return self.fitted()

    def penalty(self) -> torch.Tensor:
        return (self.fitted() - self.w_bar0).pow(2).sum()

    def slack(self) -> dict[int, float]:
        return {int(t): float(torch.nn.functional.softplus(self.slack_raw[t])) for t in self.dst.unique()}


class DKGModel(nn.Module):
    def __init__(self, data: Dataset, config: DKGConfig):
        super().__init__()
        self.config = config
        n = len(data.concepts)
        self.concepts = data.concepts
        self.c = nn.Parameter(torch.tensor(0.0))
        self.v = nn.Parameter(torch.full((n,), 2.0))
        self.d_scale = nn.Parameter(torch.tensor(0.3))
        self.question_index: dict[str, int] = {}
        self.u = nn.Parameter(torch.zeros(0))
        self.k0_raw = nn.Parameter(torch.zeros(n))
        self.b_raw = nn.Parameter(torch.full((n,), _logit(0.3)))
        self.beta_raw = nn.Parameter(torch.full((n,), _inv_softplus(0.05)), requires_grad=config.decay)
        self.w_eta = nn.Parameter(torch.zeros(len(ETA_FEATURES) if config.adaptive_eta else 1))
        self.theta = nn.Parameter(torch.zeros(len(ALPHA_FEATURES) if config.adaptive_alpha else 1))
        self.gamma_raw = nn.Parameter(torch.tensor(_inv_softplus(0.5)), requires_grad=config.propagation)
        self.kappa_raw = nn.Parameter(torch.tensor(_inv_softplus(0.05)), requires_grad=config.transfer)
        self.eta_theta_raw = nn.Parameter(torch.tensor(_inv_softplus(0.3)), requires_grad=config.ability)

        self.edges = [(e.source, e.target, e.relation) for e in data.edges]
        w0 = torch.tensor([max(e.initial_weight, 1e-3) for e in data.edges], dtype=torch.float32)
        self.src = torch.tensor([e[0] for e in self.edges], dtype=torch.long)
        self.dst = torch.tensor([e[1] for e in self.edges], dtype=torch.long)
        self.is_prereq = torch.tensor([float(e[2] == "PREREQUISITE") for e in self.edges])
        self.out_edges: dict[int, list[int]] = defaultdict(list)
        for e_idx, (s, _, _) in enumerate(self.edges):
            self.out_edges[s].append(e_idx)
        learn = config.w_mode != "fixed"
        self.w_e = EdgeWeights(self.dst, n, w0, config, learn and config.propagation)
        self.w_t = EdgeWeights(self.dst, n, w0, config, learn and config.transfer)
        self.dynamic_active = False  # dynamic mode, windows k > 0: blend W(k−1) with the fitted Ĉ(k)
        self.w_frozen = False  # dynamic mode after training: use W(K) as is

    # -- parameters -------------------------------------------------------------------
    def set_questions(self, questions: list[str]):
        self.question_index = {q: i for i, q in enumerate(questions)}
        self.u = nn.Parameter(torch.zeros(len(questions)))

    def active_weights(self) -> list[EdgeWeights]:
        return [w for w, on in ((self.w_e, self.config.propagation), (self.w_t, self.config.transfer)) if on]

    def w_bar(self, weights: EdgeWeights) -> torch.Tensor:
        return weights.current(self.config.w_mode, self.dynamic_active, self.w_frozen, self.config.lam)

    def difficulty(self, it: Interaction) -> torch.Tensor:
        d = self.d_scale * (it.difficulty - 3)
        q = self.question_index.get(it.question)
        return d + self.u[q] if q is not None else d

    def eta(self, it: Interaction) -> torch.Tensor:
        if not self.config.adaptive_eta:
            return self.config.eta_max * torch.sigmoid(self.w_eta[0])
        minutes = (it.response_time_sec or 0) / 60
        psi = torch.tensor([1.0, math.log1p(it.hint_count), math.log1p(minutes),
                            float(it.assistance or 0), float(it.response_time_sec is None)])  # fmt: skip
        return self.config.eta_max * torch.sigmoid(psi @ self.w_eta)

    # -- replay -----------------------------------------------------------------------
    def replay(self, data: Dataset, *, explain: bool = False, mute: tuple[int, int, str] | None = None):
        """Yields (interaction, p, explanation) in order; p is computed before the update.

        mute=(interaction idx, edge index, "evidence" | "transfer") drops that one message, for
        counterfactual checks of the explanations (engine.faithfulness)."""
        k0 = torch.sigmoid(self.k0_raw)
        b = torch.sigmoid(self.b_raw)
        beta = torch.nn.functional.softplus(self.beta_raw)
        gamma = torch.nn.functional.softplus(self.gamma_raw)
        kappa = torch.nn.functional.softplus(self.kappa_raw)
        eta_theta = torch.nn.functional.softplus(self.eta_theta_raw)
        ability: dict[str, torch.Tensor] = {}
        answered: dict[str, int] = defaultdict(int)
        w_e = self.w_bar(self.w_e) if self.config.propagation else None
        w_t = self.w_bar(self.w_t) if self.config.transfer else None
        state: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
        prior_attempts: dict[tuple[str, str], int] = defaultdict(int)

        for it in data.interactions:
            if it.student not in state:
                state[it.student] = (k0, torch.full((len(self.concepts),), it.t))
            k, last = state[it.student]

            def decayed(idx: torch.Tensor) -> torch.Tensor:
                if not self.config.decay:
                    return k[idx]
                gap = (it.t - last[idx]) / DAY
                return b[idx] + (k[idx] - b[idx]) * torch.exp(-beta[idx] * gap)

            q_idx = torch.tensor([c for c, _ in it.concepts], dtype=torch.long)
            a = torch.tensor([w for _, w in it.concepts])
            k_q = decayed(q_idx)
            d_q = self.difficulty(it)
            terms = a * self.v[q_idx] * k_q
            theta_u = ability.get(it.student, torch.tensor(0.0)) if self.config.ability else torch.tensor(0.0)
            z = self.c - d_q + theta_u + terms.sum()
            p = torch.sigmoid(z)

            delta = self.eta(it) * a * (it.y - p)
            touched = {int(c): i for i, c in enumerate(q_idx)}
            update = {int(c): delta[i] for i, c in enumerate(q_idx)}
            decayed_vals = {int(c): k_q[i] for i, c in enumerate(q_idx)}
            messages = []

            edge_ids = [e for c in touched for e in self.out_edges.get(c, [])]
            if edge_ids and (self.config.propagation or self.config.transfer):
                e_idx = torch.tensor(edge_ids, dtype=torch.long)
                targets = self.dst[e_idx]
                new_targets = [int(j) for j in targets.unique() if int(j) not in decayed_vals]
                if new_targets:
                    j_idx = torch.tensor(new_targets, dtype=torch.long)
                    for j, val in zip(new_targets, decayed(j_idx)):
                        decayed_vals[j] = val
                src_pos = [touched[int(s)] for s in self.src[e_idx]]
                names = [f"{self.concepts[int(s)]}→{self.concepts[int(t)]}" for s, t in zip(self.src[e_idx], targets)]

                if self.config.propagation:  # evidence: signed by the source residual
                    src_delta = torch.stack([delta[i] for i in src_pos])
                    alpha = self._alpha(it, e_idx, decayed_vals, d_q, it.y - p, last, prior_attempts)
                    msg_e = gamma * alpha * w_e[e_idx] * src_delta
                    for pos, j in enumerate(targets.tolist()):
                        if mute == (it.idx, int(e_idx[pos]), "evidence"):
                            continue
                        update[j] = update.get(j, 0) + msg_e[pos]
                    if explain:
                        messages += [{"kind": "evidence", "edge": nm, "edge_id": int(e), "alpha": float(al),
                                      "w_bar": float(w_e[e]), "delta_source": float(sd), "contribution": float(m)}
                                     for nm, al, e, sd, m in zip(names, alpha, e_idx, src_delta, msg_e)]  # fmt: skip

                if self.config.transfer:  # learning transfer: practising the source helps the target
                    room = torch.stack([1 - decayed_vals[int(j)] for j in targets])
                    src_a = torch.stack([a[i] for i in src_pos])
                    msg_t = kappa * w_t[e_idx] * src_a * room
                    for pos, j in enumerate(targets.tolist()):
                        if mute == (it.idx, int(e_idx[pos]), "transfer"):
                            continue
                        update[j] = update.get(j, 0) + msg_t[pos]
                    if explain:
                        messages += [{"kind": "transfer", "edge": nm, "edge_id": int(e), "w_bar": float(w_t[e]),
                                      "contribution": float(m)}
                                     for nm, e, m in zip(names, e_idx, msg_t)]  # fmt: skip

            idx = torch.tensor(list(update), dtype=torch.long)
            before = torch.stack([decayed_vals[j] for j in update])
            new_vals = torch.clamp(before + torch.stack([torch.as_tensor(update[j]) for j in update]), 0.0, 1.0)
            state[it.student] = (k.index_put((idx,), new_vals), last.index_put((idx,), torch.full((len(idx),), it.t)))
            prior_attempts[(it.student, it.question)] += 1
            if self.config.ability:
                ability[it.student] = theta_u + eta_theta / math.sqrt(1 + answered[it.student]) * (it.y - p)
                answered[it.student] += 1

            explanation = None
            if explain:
                explanation = {
                    "logit": float(z),
                    "c": float(self.c),
                    "minus_difficulty": float(-d_q),
                    "ability": float(theta_u),
                    "concepts": {self.concepts[int(c)]: {"a": float(w), "v": float(self.v[c]), "k_decayed": float(kd),
                                                         "contribution": float(tm)}
                                 for c, w, kd, tm in zip(q_idx, a, k_q, terms)},
                    "messages": messages,
                    # states written by this update (answered concepts and message targets), after clipping
                    "k_after": {self.concepts[j]: float(v) for j, v in zip(update, new_vals)},
                }  # fmt: skip
            yield it, p, explanation

    def _alpha(self, it, e_idx, decayed_vals, d_q, residual, last, prior_attempts) -> torch.Tensor:
        if not self.config.adaptive_alpha:
            return torch.sigmoid(self.theta[0]).expand(len(e_idx))
        rows = []
        for e in e_idx.tolist():
            s, j = int(self.src[e]), int(self.dst[e])
            gap_days = max(0.0, (it.t - float(last[j])) / DAY)
            rows.append(torch.stack([
                torch.tensor(1.0), decayed_vals[s], decayed_vals[j], d_q, residual,
                torch.tensor(math.log1p(gap_days)), torch.tensor(math.log1p(prior_attempts[(it.student, it.question)])),
                torch.tensor(math.log1p(it.hint_count)), self.is_prereq[e],
            ]))  # fmt: skip
        return torch.sigmoid(torch.stack(rows) @ self.theta)

    # -- batched replay ---------------------------------------------------------------
    # Students never share state, so the s-th interaction of every student can be computed
    # at once: T batched steps instead of one Python step per interaction (≈ 50–100× faster
    # to train). It computes the same p as replay() up to float rounding, which
    # tests/test_engine.py checks for every switch; replay() stays the reference and is
    # still used for explanations.
    def _steps(self, data: Dataset) -> dict:
        cache = getattr(self, "_steps_cache", None)
        if cache is not None and cache[0] is data and cache[1] is self.question_index:
            return cache[2]
        by_student: dict[str, list[tuple[int, Interaction]]] = defaultdict(list)
        for pos, it in enumerate(data.interactions):
            by_student[it.student].append((pos, it))
        seqs = list(by_student.values())
        U, T = len(seqs), max(len(s) for s in seqs)
        C = max(len(it.concepts) for it in data.interactions)
        edge_lists = {}
        for it in data.interactions:
            key = tuple(c for c, _ in it.concepts)
            if key not in edge_lists:
                edge_lists[key] = [(e, p) for p, c in enumerate(key) for e in self.out_edges.get(c, [])]
        E = max((len(v) for v in edge_lists.values()), default=0)
        unseen = len(self.question_index)
        arr = {
            "order": np.full((U, T), -1, np.int64), "valid": np.zeros((U, T), bool),
            "t": np.zeros((U, T), np.float64), "qi": np.zeros((U, T, C), np.int64),
            "aw": np.zeros((U, T, C), np.float32), "qmask": np.zeros((U, T, C), bool),
            "diff": np.zeros((U, T), np.float32), "qidx": np.full((U, T), unseen, np.int64),
            "y": np.zeros((U, T), np.float32), "psi": np.zeros((U, T, len(ETA_FEATURES)), np.float32),
            "log_prior": np.zeros((U, T), np.float32), "log_hint": np.zeros((U, T), np.float32),
            "eid": np.zeros((U, T, E), np.int64), "epos": np.zeros((U, T, E), np.int64),
            "emask": np.zeros((U, T, E), bool),
        }  # fmt: skip
        for u, seq in enumerate(seqs):
            prior: dict[str, int] = defaultdict(int)
            for s, (pos, it) in enumerate(seq):
                arr["order"][u, s], arr["valid"][u, s], arr["t"][u, s] = pos, True, it.t
                for c, (ci, w) in enumerate(it.concepts):
                    arr["qi"][u, s, c], arr["aw"][u, s, c], arr["qmask"][u, s, c] = ci, w, True
                arr["diff"][u, s] = it.difficulty
                arr["qidx"][u, s] = self.question_index.get(it.question, unseen)
                arr["y"][u, s] = it.y
                minutes = (it.response_time_sec or 0) / 60
                arr["psi"][u, s] = [1.0, math.log1p(it.hint_count), math.log1p(minutes), float(it.assistance or 0),
                                    float(it.response_time_sec is None)]  # fmt: skip
                arr["log_prior"][u, s] = math.log1p(prior[it.question])
                arr["log_hint"][u, s] = math.log1p(it.hint_count)
                prior[it.question] += 1
                for k, (e, p) in enumerate(edge_lists[tuple(c for c, _ in it.concepts)]):
                    arr["eid"][u, s, k], arr["epos"][u, s, k], arr["emask"][u, s, k] = e, p, True
            # Padding steps keep the student's last time: a zero time would give exp(+huge) in the
            # unused decay branch and NaN gradients through torch.where.
            arr["t"][u, len(seq):] = seq[-1][1].t
        steps = {name: torch.from_numpy(a) for name, a in arr.items()}
        self._steps_cache = (data, self.question_index, steps)
        return steps

    def replay_batched(self, data: Dataset) -> torch.Tensor:
        """p for every interaction, in data.interactions order (each p before its update)."""
        B = self._steps(data)
        U, T = B["valid"].shape
        n = len(self.concepts)
        cfg = self.config
        b = torch.sigmoid(self.b_raw)
        beta = torch.nn.functional.softplus(self.beta_raw)
        gamma = torch.nn.functional.softplus(self.gamma_raw)
        kappa = torch.nn.functional.softplus(self.kappa_raw)
        eta_theta = torch.nn.functional.softplus(self.eta_theta_raw)
        w_e = self.w_bar(self.w_e) if cfg.propagation else None
        w_t = self.w_bar(self.w_t) if cfg.transfer else None
        u_ext = torch.cat([self.u, torch.zeros(1)])
        use_edges = B["eid"].shape[2] > 0 and (cfg.propagation or cfg.transfer)

        K = torch.sigmoid(self.k0_raw).expand(U, n)
        last = B["t"][:, :1].expand(U, n)  # float64; replay() keeps float32 copies, mirrored below
        theta_u = torch.zeros(U)
        ps = []
        for s in range(T):
            valid = B["valid"][:, s]
            t = B["t"][:, s]
            if cfg.decay:
                gap = (t.float()[:, None] - last.float()) / DAY
                kd = b + (K - b) * torch.exp(-beta * gap)
            else:
                kd = K
            qi, aw = B["qi"][:, s], B["aw"][:, s]
            kq = kd.gather(1, qi)
            d_q = self.d_scale * (B["diff"][:, s] - 3) + u_ext[B["qidx"][:, s]]
            z = self.c - d_q + (theta_u if cfg.ability else 0.0) + (aw * self.v[qi] * kq).sum(1)
            p = torch.sigmoid(z)
            residual = B["y"][:, s] - p
            if cfg.adaptive_eta:
                eta = cfg.eta_max * torch.sigmoid(B["psi"][:, s] @ self.w_eta)
            else:
                eta = (cfg.eta_max * torch.sigmoid(self.w_eta[0])).expand(U)
            delta = eta[:, None] * aw * residual[:, None]
            update = torch.zeros(U, n).scatter_add(1, qi, delta)
            touched = torch.zeros(U, n).scatter_add(1, qi, B["qmask"][:, s].float()) > 0
            if use_edges:
                eid, epos, emask = B["eid"][:, s], B["epos"][:, s], B["emask"][:, s].float()
                tgt = self.dst[eid]
                k_tgt = kd.gather(1, tgt)
                if cfg.propagation:
                    if cfg.adaptive_alpha:
                        gap_days = ((t[:, None] - last.gather(1, tgt)) / DAY).clamp(min=0).float()
                        feats = torch.stack([
                            torch.ones_like(k_tgt), kd.gather(1, self.src[eid]), k_tgt,
                            d_q[:, None].expand_as(k_tgt), residual[:, None].expand_as(k_tgt), torch.log1p(gap_days),
                            B["log_prior"][:, s, None].expand_as(k_tgt), B["log_hint"][:, s, None].expand_as(k_tgt),
                            self.is_prereq[eid],
                        ], dim=-1)  # fmt: skip
                        alpha = torch.sigmoid(feats @ self.theta)
                    else:
                        alpha = torch.sigmoid(self.theta[0]).expand_as(k_tgt)
                    msg_e = gamma * alpha * w_e[eid] * delta.gather(1, epos) * emask
                    update = update.scatter_add(1, tgt, msg_e)
                if cfg.transfer:
                    msg_t = kappa * w_t[eid] * aw.gather(1, epos) * (1 - k_tgt) * emask
                    update = update.scatter_add(1, tgt, msg_t)
                touched = touched | (torch.zeros(U, n).scatter_add(1, tgt, emask) > 0)
            sel = touched & valid[:, None]
            K = torch.where(sel, torch.clamp(kd + update, 0.0, 1.0), K)
            last = torch.where(sel, t[:, None].expand(U, n), last)
            if cfg.ability:
                theta_u = torch.where(valid, theta_u + eta_theta / math.sqrt(1 + s) * residual, theta_u)
            ps.append(p)
        P = torch.stack(ps, 1)
        out = torch.empty(len(data.interactions))
        return out.index_put((B["order"][B["valid"]],), P[B["valid"]])

    # -- training -----------------------------------------------------------------------
    def loss(self, data: Dataset, *, batched: bool = True) -> torch.Tensor:
        if batched:
            ps = self.replay_batched(data)
            ys = [float(it.y) for it in data.interactions]
        else:
            ps, ys = [], []
            for it, p, _ in self.replay(data):
                ps.append(p)
                ys.append(float(it.y))
            ps = torch.stack(ps)
        p = ps.clamp(1e-6, 1 - 1e-6)
        y = torch.tensor(ys)
        nll = -(y * torch.log(p) + (1 - y) * torch.log(1 - p)).mean()
        reg = self.config.l2 * (self.u.pow(2).sum() + self.theta[1:].pow(2).sum())
        if self.config.w_mode != "fixed" and self.edges:
            for weights in self.active_weights():
                reg = reg + self.w_reg_coefficient(len(ys)) * weights.penalty()
        return nll + reg

    def w_reg_coefficient(self, n_events: int) -> float:
        if self.config.w_reg_scaling == "per_event":
            return self.config.w_prior_events / max(n_events, 1)
        if self.config.w_reg_scaling == "constant":
            return self.config.w_reg
        raise ValueError(f"unknown w_reg_scaling {self.config.w_reg_scaling!r}")


class DKG:
    """Engine-protocol wrapper (fit / predict / explain) around DKGModel."""

    def __init__(self, name: str = "dkg", **config):
        self.name = name
        self.config = DKGConfig(**config)

    def _train(self, data: Dataset, epochs: int):
        opt = torch.optim.Adam([p for p in self.model.parameters() if p.requires_grad], lr=self.config.lr)
        for _ in range(epochs):
            opt.zero_grad()
            loss = self.model.loss(data)
            loss.backward()
            opt.step()
            self.train_loss.append(loss.item())

    def fit(self, train: Dataset):
        torch.manual_seed(self.config.seed)
        self.model = DKGModel(train, self.config)
        self.model.set_questions(sorted({i.question for i in train.interactions}))
        self.train_loss: list[float] = []
        if not train.interactions:
            return
        if self.config.w_mode == "dynamic" and self.model.active_weights() and len(self.model.edges):
            times = np.array([i.t for i in train.interactions])
            cuts = np.quantile(times, np.linspace(0, 1, self.config.windows + 1)[1:])
            per_window = max(1, self.config.epochs // self.config.windows)
            for k, cut in enumerate(cuts):
                history = train.subset([i.idx for i in train.interactions if i.t <= cut])
                self.model.dynamic_active = k > 0  # window 0 has no W(k−1) to blend with
                self._train(history, per_window)
                with torch.no_grad():  # W(k) = (1−λ)·W(k−1) + λ·Ĉ(k), for W^E and W^T
                    for weights in self.model.active_weights():
                        weights.w_prev.copy_(self.model.w_bar(weights))
            self.model.w_frozen = True  # W(K) is used unchanged for validation and test
        else:
            self._train(train, self.config.epochs)

    @torch.no_grad()
    def predict(self, data: Dataset) -> np.ndarray:
        if not data.interactions:
            return np.array([])
        return self.model.replay_batched(data).numpy().astype(float)

    @torch.no_grad()
    def explain(self, data: Dataset) -> list[dict]:
        return [e for _, _, e in self.model.replay(data, explain=True)]

    @torch.no_grad()
    def describe(self) -> dict:
        m = self.model
        softplus = torch.nn.functional.softplus
        out = {
            "config": asdict(self.config),
            "c": float(m.c),
            "d_scale": float(m.d_scale),
            "concepts": {
                code: {"v": float(m.v[i]), "k0": float(torch.sigmoid(m.k0_raw[i])),
                       "baseline_b": float(torch.sigmoid(m.b_raw[i])), "beta_per_day": float(softplus(m.beta_raw[i]))}
                for i, code in enumerate(m.concepts)
            },
            "eta_weights": dict(zip(ETA_FEATURES if self.config.adaptive_eta else ["bias"], m.w_eta.tolist())),
            "alpha_theta": dict(zip(ALPHA_FEATURES if self.config.adaptive_alpha else ["bias"], m.theta.tolist())),
            "gamma": float(softplus(m.gamma_raw)),
            "kappa": float(softplus(m.kappa_raw)),
            "eta_theta": float(softplus(m.eta_theta_raw)) if self.config.ability else None,
            "train_loss": self.train_loss,
        }  # fmt: skip
        if len(m.edges):
            # "w_bar" is the evidence weight W̄^E; "w_bar_transfer" is W̄^T.
            w_e = m.w_bar(m.w_e) if self.config.propagation else m.w_e.w_bar0
            w_t = m.w_bar(m.w_t) if self.config.transfer else m.w_t.w_bar0
            out["edges"] = [{"edge": f"{m.concepts[s]}→{m.concepts[t]}", "relation": r,
                             "w_bar0": float(m.w_e.w_bar0[e]), "w_bar": float(w_e[e]),
                             "w_bar_transfer": float(w_t[e])}
                            for e, (s, t, r) in enumerate(m.edges)]  # fmt: skip
            if self.config.w_norm == "slack":
                out["slack"] = {m.concepts[t]: v for t, v in m.w_e.slack().items()}
                out["slack_transfer"] = {m.concepts[t]: v for t, v in m.w_t.slack().items()}
                # r_ij = w_ij / s_j: edge strength relative to the target's "none" share. Unlike
                # W̄ it does not depend on how many other edges enter the same target, so it can
                # be compared across targets.
                for e, row in enumerate(out["edges"]):
                    t = int(m.dst[e])
                    row["r"] = float(softplus(m.w_e.raw[e]) / softplus(m.w_e.slack_raw[t]))
                    row["r_transfer"] = float(softplus(m.w_t.raw[e]) / softplus(m.w_t.slack_raw[t]))
        return out
