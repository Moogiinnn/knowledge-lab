"""Data splits (guide §9).

temporal: one global time cut-off for train / validation / test. Every prediction
  sees only earlier history; test-time state updates use earlier test answers
  (online evaluation) but global parameters are never re-fitted on them.
student: student-disjoint split for generalisation to new students; within each
  part the replay order is still chronological.
"""

import hashlib
from dataclasses import dataclass

from .data import Dataset


@dataclass(frozen=True)
class Split:
    train: frozenset[int]
    val: frozenset[int]
    test: frozenset[int]
    kind: str

    def part(self, idx: int) -> str:
        return "train" if idx in self.train else "val" if idx in self.val else "test"


def temporal_split(data: Dataset, train: float = 0.6, val: float = 0.2) -> Split:
    times = sorted(i.t for i in data.interactions)
    if not times:
        raise ValueError("empty dataset")
    t_train = times[min(len(times) - 1, int(len(times) * train))]
    t_val = times[min(len(times) - 1, int(len(times) * (train + val)))]
    parts = {"train": set(), "val": set(), "test": set()}
    for i in data.interactions:
        parts["train" if i.t < t_train else "val" if i.t < t_val else "test"].add(i.idx)
    return Split(frozenset(parts["train"]), frozenset(parts["val"]), frozenset(parts["test"]), "temporal")


def student_split(data: Dataset, train: float = 0.6, val: float = 0.2, seed: int = 0) -> Split:
    def bucket(student: str) -> float:
        h = hashlib.sha256(f"{seed}:{student}".encode()).digest()
        return int.from_bytes(h[:8], "big") / 2**64

    parts = {"train": set(), "val": set(), "test": set()}
    for i in data.interactions:
        u = bucket(i.student)
        parts["train" if u < train else "val" if u < train + val else "test"].add(i.idx)
    return Split(frozenset(parts["train"]), frozenset(parts["val"]), frozenset(parts["test"]), "student")


def make_split(data: Dataset, kind: str, train: float, val: float, seed: int) -> Split:
    if kind == "temporal":
        return temporal_split(data, train, val)
    if kind == "student":
        return student_split(data, train, val, seed)
    raise ValueError(f"unknown split kind {kind!r}")
