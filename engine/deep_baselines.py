"""Deep knowledge tracing baselines from pyKT (DKT, GKT) under the engine protocol.

The network code is pyKT's, unchanged (engine/third_party/pykt). This adapter only feeds
the engine's data to it and reads predictions back:

- A question with several concepts becomes one step per concept (pyKT's usual expansion).
- Every step of an interaction is predicted from the model state BEFORE the interaction's
  first step, and the interaction's probability is the a_qi-weighted mean over its concepts.
  Training uses the same rule, so a step never sees a response of its own interaction (the
  leak of the naive expansion). An interaction with no earlier step gets the training rate.
- predict(data) replays every student's whole sequence with frozen weights (online
  evaluation, as for the other models).
- Hidden size, learning rate and the number of epochs are chosen on the last 25% (by time)
  of the training interactions; the model is then retrained on the whole training part.
"""

import itertools

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score

from .data import Dataset
from .third_party.pykt.dkt import DKT as PyktDKT
from .third_party.pykt.gkt import GKT as PyktGKT


class _GKTAllConcepts(PyktGKT):
    """pyKT's GKT, returning the predicted probability of every concept after each step
    (its forward() keeps only the next step's concept, which the expansion cannot use)."""

    def forward(self, q, r):
        features = q * 2 + r
        batch_size, seq_len = features.shape
        ht = torch.zeros((batch_size, self.num_c, self.hidden_dim))
        out = []
        for i in range(seq_len):
            xt, qt = features[:, i], q[:, i]
            qt_mask = torch.ne(qt, -1)
            tmp_ht = self._aggregate(xt, qt, ht, batch_size)
            h_next, _, _, _ = self._update(tmp_ht, ht, qt)
            ht = ht.clone()
            ht[qt_mask] = h_next[qt_mask]
            out.append(torch.sigmoid(self.predict(ht).squeeze(-1)))
        return torch.stack(out, dim=1)


class _Sequences:
    """Per-student expanded steps of a dataset, padded into tensors."""

    def __init__(self, data: Dataset, pad: int):
        by_student: dict[str, list] = {}
        for n, it in enumerate(data.interactions):
            by_student.setdefault(it.student, []).append((n, it))
        self.students = sorted(by_student)
        steps, events = [], []  # events: (position in data, student row, step before it, [(concept, weight)])
        for row, s in enumerate(self.students):
            seq = []
            for n, it in by_student[s]:
                events.append((n, row, len(seq) - 1, list(it.concepts)))
                seq += [(c, it.y) for c, _ in it.concepts]
            steps.append(seq)
        length = max(len(s) for s in steps)
        self.q = torch.full((len(steps), length), pad, dtype=torch.long)
        self.r = torch.zeros((len(steps), length), dtype=torch.long)
        for row, seq in enumerate(steps):
            if seq:
                self.q[row, : len(seq)] = torch.tensor([c for c, _ in seq])
                self.r[row, : len(seq)] = torch.tensor([y for _, y in seq])
        self.events = events
        # training targets: (student row, state step, concept, response) for every step after the first interaction
        tgt = [(row, prev, c, y) for n, row, prev, cs in events if prev >= 0
               for (c, _), y in zip(cs, [data.interactions[n].y] * len(cs))]  # fmt: skip
        self.targets = torch.tensor(tgt, dtype=torch.long) if tgt else torch.zeros((0, 4), dtype=torch.long)


class _PyktBaseline:
    max_epochs = 30
    grid = {"hidden": (32, 64), "lr": (1e-3, 5e-3)}
    batch_students = 16
    pad = 0

    def __init__(self, seed: int = 0, max_epochs: int | None = None, hidden=None, lr=None):
        self.seed = seed
        if max_epochs:
            self.max_epochs = max_epochs
        if hidden or lr:  # a smaller grid for large data (the defaults stay as class attributes)
            self.grid = {"hidden": tuple(hidden or self.grid["hidden"]), "lr": tuple(lr or self.grid["lr"])}

    def _net(self, n_concepts: int, hidden: int, data: Dataset):
        raise NotImplementedError

    def _probs(self, net, seqs: _Sequences) -> torch.Tensor:
        return net(seqs.q, seqs.r)  # [students, steps, concepts]

    def _train(self, train: Dataset, hidden: int, lr: float, epochs: int, val=None):
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        net = self._net(len(train.concepts), hidden, train)
        opt = torch.optim.Adam(net.parameters(), lr=lr)
        seqs = _Sequences(train, self.pad)
        history = []
        for _ in range(epochs):
            net.train()
            order = rng.permutation(len(seqs.students))
            for start in range(0, len(order), self.batch_students):
                rows = torch.tensor(order[start: start + self.batch_students])
                t = seqs.targets[torch.isin(seqs.targets[:, 0], rows)]
                if not len(t):
                    continue
                remap = torch.full((len(seqs.students),), -1, dtype=torch.long)
                remap[rows] = torch.arange(len(rows))
                probs = net(seqs.q[rows], seqs.r[rows])
                p = probs[remap[t[:, 0]], t[:, 1], t[:, 2]].clamp(1e-6, 1 - 1e-6)
                loss = F.binary_cross_entropy(p, t[:, 3].float())
                opt.zero_grad()
                loss.backward()
                opt.step()
            if val is not None:
                history.append(val(net))
        return net, history

    def fit(self, train: Dataset):
        self.rate = float(np.mean([i.y for i in train.interactions])) if train.interactions else 0.5
        times = np.array([i.t for i in train.interactions])
        cut = np.quantile(times, 0.75)
        inner = train.subset([i.idx for i in train.interactions if i.t <= cut])
        held = np.array([i.t > cut for i in train.interactions])
        y_held = np.array([i.y for i in train.interactions])[held]

        def val_auc(net):
            p = self._predict_with(net, train)[held]
            return roc_auc_score(y_held, p) if len(set(y_held)) > 1 else 0.5

        best = (-1.0, None, None, None)
        for hidden, lr in itertools.product(self.grid["hidden"], self.grid["lr"]):
            _, hist = self._train(inner, hidden, lr, self.max_epochs, val=val_auc)
            epoch = int(np.argmax(hist)) + 1
            if hist[epoch - 1] > best[0]:
                best = (hist[epoch - 1], hidden, lr, epoch)
        self.selected = {"inner_val_auc": best[0], "hidden": best[1], "lr": best[2], "epochs": best[3]}
        self.net, _ = self._train(train, best[1], best[2], best[3])

    @torch.no_grad()
    def _predict_with(self, net, data: Dataset) -> np.ndarray:
        net.eval()
        seqs = _Sequences(data, self.pad)
        probs = self._probs(net, seqs)
        out = np.full(len(data.interactions), self.rate)
        for n, row, prev, cs in seqs.events:
            if prev >= 0:
                out[n] = float(sum(w * probs[row, prev, c] for c, w in cs) / sum(w for _, w in cs))
        return out

    def predict(self, data: Dataset) -> np.ndarray:
        if not data.interactions:
            return np.array([])
        return self._predict_with(self.net, data)

    def describe(self) -> dict:
        return {"selected": self.selected, "seed": self.seed, "max_epochs": self.max_epochs, "grid": self.grid}


class DKTBaseline(_PyktBaseline):
    """Deep Knowledge Tracing (Piech et al., 2015), pyKT implementation."""

    name = "dkt"

    def _net(self, n_concepts, hidden, data):
        return PyktDKT(n_concepts, hidden)


class GKTBaseline(_PyktBaseline):
    """Graph-based Knowledge Tracing (Nakagawa et al., 2019), pyKT implementation, given the
    same teacher-approved concept graph as KSG-DM (an edge i→j sets graph[i, j] = 1)."""

    name = "gkt"
    max_epochs = 20
    pad = -1

    def _net(self, n_concepts, hidden, data):
        graph = torch.zeros((n_concepts, n_concepts))
        for e in data.edges:
            graph[e.source, e.target] = 1.0
        return _GKTAllConcepts(n_concepts, hidden, hidden, graph_type="dense", graph=graph)
