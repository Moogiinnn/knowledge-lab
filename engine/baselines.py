"""Baseline models (guide §9). Every model follows the same protocol:

    fit(train: Dataset)        learn global parameters from training interactions only
    predict(data: Dataset)     replay all interactions in order; for each one return the
                               probability computed BEFORE its outcome is observed, then
                               update per-student state with the outcome. Global
                               parameters stay frozen (online evaluation).
"""

import itertools
from collections import defaultdict

import numpy as np
from sklearn.linear_model import LogisticRegression

from .data import Dataset


class GlobalRate:
    name = "global_rate"

    def fit(self, train: Dataset):
        self.rate = float(np.mean([i.y for i in train.interactions])) if train.interactions else 0.5

    def predict(self, data: Dataset) -> np.ndarray:
        return np.full(len(data.interactions), self.rate)


class ConceptRate:
    """Student's smoothed success rate on the question's concepts, weighted by a_qi."""

    name = "concept_rate"

    def __init__(self, prior_strength: float = 2.0):
        self.prior_strength = prior_strength

    def fit(self, train: Dataset):
        ys = [i.y for i in train.interactions]
        self.prior = float(np.mean(ys)) if ys else 0.5

    def predict(self, data: Dataset) -> np.ndarray:
        succ, seen = defaultdict(float), defaultdict(float)
        out = np.empty(len(data.interactions))
        for n, it in enumerate(data.interactions):
            p = 0.0
            for c, w in it.concepts:
                key = (it.student, c)
                p += w * (succ[key] + self.prior_strength * self.prior) / (seen[key] + self.prior_strength)
            out[n] = p
            for c, _ in it.concepts:
                succ[(it.student, c)] += it.y
                seen[(it.student, c)] += 1
        return out


class PFA:
    """Performance Factors Analysis as logistic regression: per concept, a_qi-weighted
    log counts of the student's earlier successes and failures, plus difficulty."""

    name = "pfa"

    def __init__(self, C: float = 1.0):
        self.C = C

    def features(self, data: Dataset) -> np.ndarray:
        k = len(data.concepts)
        succ, fail = defaultdict(float), defaultdict(float)
        X = np.zeros((len(data.interactions), 3 * k + 1))
        for n, it in enumerate(data.interactions):
            for c, w in it.concepts:
                key = (it.student, c)
                X[n, c] = w
                X[n, k + c] = w * np.log1p(succ[key])
                X[n, 2 * k + c] = w * np.log1p(fail[key])
            X[n, 3 * k] = it.difficulty
            for c, _ in it.concepts:
                if it.y:
                    succ[(it.student, c)] += 1
                else:
                    fail[(it.student, c)] += 1
        return X

    def fit(self, train: Dataset):
        y = np.array([i.y for i in train.interactions])
        self.model = None
        self.fallback = float(y.mean()) if len(y) else 0.5
        if len(set(y)) == 2:
            self.model = LogisticRegression(C=self.C, max_iter=2000).fit(self.features(train), y)

    def predict(self, data: Dataset) -> np.ndarray:
        if self.model is None:
            return np.full(len(data.interactions), self.fallback)
        return self.model.predict_proba(self.features(data))[:, 1]


class BKT:
    """Bayesian Knowledge Tracing per concept, parameters fitted by grid search on the
    training log-likelihood. A question's probability is the a_qi-weighted mix of its
    concepts' predictions; every tagged concept is updated with the outcome."""

    name = "bkt"
    GRID = {
        "p_init": [0.1, 0.3, 0.5, 0.7],
        "p_learn": [0.02, 0.05, 0.1, 0.2, 0.35],
        "p_guess": [0.05, 0.15, 0.25],
        "p_slip": [0.05, 0.1, 0.2],
    }
    DEFAULT = (0.3, 0.1, 0.15, 0.1)

    @staticmethod
    def _step(mastery, y, learn, guess, slip):
        if y:
            post = mastery * (1 - slip) / (mastery * (1 - slip) + (1 - mastery) * guess)
        else:
            post = mastery * slip / (mastery * slip + (1 - mastery) * (1 - guess))
        return post + (1 - post) * learn

    @staticmethod
    def _correct(mastery, guess, slip):
        return mastery * (1 - slip) + (1 - mastery) * guess

    def fit(self, train: Dataset):
        sequences = defaultdict(lambda: defaultdict(list))  # concept -> student -> [y]
        for it in train.interactions:
            for c, _ in it.concepts:
                sequences[c][it.student].append(it.y)
        self.params = {}
        for c, by_student in sequences.items():
            best, best_ll = self.DEFAULT, -np.inf
            for init, learn, guess, slip in itertools.product(*self.GRID.values()):
                ll = 0.0
                for ys in by_student.values():
                    m = init
                    for y in ys:
                        p = min(max(self._correct(m, guess, slip), 1e-6), 1 - 1e-6)
                        ll += np.log(p if y else 1 - p)
                        m = self._step(m, y, learn, guess, slip)
                if ll > best_ll:
                    best, best_ll = (init, learn, guess, slip), ll
            self.params[c] = best

    def predict(self, data: Dataset) -> np.ndarray:
        mastery = {}
        out = np.empty(len(data.interactions))
        for n, it in enumerate(data.interactions):
            p = 0.0
            for c, w in it.concepts:
                init, learn, guess, slip = self.params.get(c, self.DEFAULT)
                p += w * self._correct(mastery.get((it.student, c), init), guess, slip)
            out[n] = p
            for c, _ in it.concepts:
                init, learn, guess, slip = self.params.get(c, self.DEFAULT)
                key = (it.student, c)
                mastery[key] = self._step(mastery.get(key, init), it.y, learn, guess, slip)
        return out
