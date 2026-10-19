"""Translate the synthetic mechanism strengths into quantities a teacher can judge.

The generator's "strength" (0.3, 0.6, 0.9) is an internal parameter. These functions run
counterfactual simulations with the generator's own student model (engine.synthetic) and
report, in percentage points of P(correct) on a medium (difficulty 3) question:

transfer_effect   After a student practises the source concept n times, how much more
                  likely are they to solve a target-only question, compared with the same
                  student and the same practice without transfer? Also: the gain from
                  practising the target itself n times, as a yardstick.

evidence_effect   Among students who answer their first source question correctly versus
                  incorrectly, how much more likely are they to solve a target question?
                  Students share an "ability" term, so this gap is not zero even without
                  coupling; the part due to the coupling is the difference from strength 0.
"""

from dataclasses import dataclass

import numpy as np

from .synthetic import CONCEPTS, initial_mastery, observe, practice, success_probability

MEDIUM = 3


@dataclass(frozen=True)
class TransferEffect:
    strength: float
    n_practice: int
    baseline_p: float  # target, before any practice
    transfer_pp: float  # gain on the target from practising the source (percentage points)
    direct_pp: float  # gain on the target from practising the target itself
    share_of_direct: float  # transfer_pp / direct_pp


@dataclass(frozen=True)
class EvidenceEffect:
    strength: float
    gap_pp: float  # P(target | source right) − P(target | source wrong), percentage points
    gap_from_coupling_pp: float  # gap minus the gap at strength 0 (shared ability alone)
    mastery_correlation: float  # across students, source vs target initial mastery


def transfer_effect(strength: float, source: str = "stack", target: str = "dfs", *, n_practice: int = 5,
                    n_students: int = 5000, seed: int = 0) -> TransferEffect:  # fmt: skip
    s, t = CONCEPTS.index(source), CONCEPTS.index(target)
    edge = {(source, target): strength}
    rng = np.random.default_rng(seed)
    base, with_t, without_t, direct = [], [], [], []
    for _ in range(n_students):
        mastery = initial_mastery(rng, None)
        ability = rng.normal(0, 0.6)
        base.append(success_probability(mastery, {t: 1.0}, MEDIUM, ability))
        # Same practice outcomes for both arms, so only the transfer differs.
        outcomes = []
        m_on, m_off = mastery.copy(), mastery.copy()
        for _ in range(n_practice):
            y, _ = observe(rng, success_probability(m_on, {s: 1.0}, MEDIUM, ability))
            outcomes.append(y)
            practice(m_on, {s: 1.0}, y, 0.0, edge)
            practice(m_off, {s: 1.0}, y, 0.0, {(source, target): 0.0})
        with_t.append(success_probability(m_on, {t: 1.0}, MEDIUM, ability))
        without_t.append(success_probability(m_off, {t: 1.0}, MEDIUM, ability))
        m_direct = mastery.copy()
        for _ in range(n_practice):
            y, _ = observe(rng, success_probability(m_direct, {t: 1.0}, MEDIUM, ability))
            practice(m_direct, {t: 1.0}, y, 0.0, {})
        direct.append(success_probability(m_direct, {t: 1.0}, MEDIUM, ability))
    transfer_pp = 100 * (np.mean(with_t) - np.mean(without_t))
    direct_pp = 100 * (np.mean(direct) - np.mean(base))
    return TransferEffect(strength, n_practice, float(np.mean(base)), float(transfer_pp), float(direct_pp),
                          float(transfer_pp / direct_pp) if direct_pp else float("nan"))  # fmt: skip


def _evidence_gap(strength: float, source: str, target: str, n_students: int, seed: int) -> tuple[float, float]:
    s, t = CONCEPTS.index(source), CONCEPTS.index(target)
    rng = np.random.default_rng(seed)
    right, wrong, ms, mt = [], [], [], []
    for _ in range(n_students):
        mastery = initial_mastery(rng, {(source, target): strength})
        ability = rng.normal(0, 0.6)
        y, _ = observe(rng, success_probability(mastery, {s: 1.0}, MEDIUM, ability))
        (right if y else wrong).append(success_probability(mastery, {t: 1.0}, MEDIUM, ability))
        ms.append(mastery[s])
        mt.append(mastery[t])
    return 100 * (np.mean(right) - np.mean(wrong)), float(np.corrcoef(ms, mt)[0, 1])


def evidence_effect(strength: float, source: str = "stack", target: str = "dfs", *, n_students: int = 20000,
                    seed: int = 0) -> EvidenceEffect:  # fmt: skip
    gap, corr = _evidence_gap(strength, source, target, n_students, seed)
    gap0, _ = _evidence_gap(0.0, source, target, n_students, seed)
    return EvidenceEffect(strength, float(gap), float(gap - gap0), corr)
