"""pyKT baselines (DKT, GKT): no use of future or same-interaction responses, valid output."""

import dataclasses

import numpy as np
import pytest

pytest.importorskip("torch")

from engine import synthetic  # noqa: E402
from engine.data import Dataset, load_dataset  # noqa: E402
from engine.deep_baselines import DKTBaseline, GKTBaseline  # noqa: E402


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return load_dataset(*synthetic.write(tmp_path_factory.mktemp("deep"), n_students=8, days=20, seed=5))


def fast(cls):
    model = cls(seed=0, max_epochs=1)
    model.grid = {"hidden": (8,), "lr": (5e-3,)}
    return model


@pytest.mark.parametrize("cls", [DKTBaseline, GKTBaseline])
def test_prediction_ignores_the_answer_being_predicted_and_later_ones(cls, data):
    model = fast(cls)
    model.fit(data.subset([i.idx for i in data.interactions[: len(data.interactions) // 2]]))
    p = model.predict(data)
    assert p.shape == (len(data.interactions),) and np.all((p > 0) & (p < 1))
    cut = len(data.interactions) // 2
    flipped = [dataclasses.replace(i, y=1 - i.y) if i.idx >= cut else i for i in data.interactions]
    q = model.predict(Dataset(flipped, data.concepts, data.edges, data.questions))
    # the answer at `cut` itself is flipped too: its own prediction must not move
    assert np.allclose(p[: cut + 1], q[: cut + 1])


def test_multi_concept_steps_share_the_state_before_the_interaction(data):
    model = fast(DKTBaseline)
    model.fit(data)
    multi = next(i for i in data.interactions if len(i.concepts) > 1)
    changed = [dataclasses.replace(i, y=1 - i.y) if i.idx == multi.idx else i for i in data.interactions]
    p = model.predict(data)
    q = model.predict(Dataset(changed, data.concepts, data.edges, data.questions))
    assert p[multi.idx] == q[multi.idx]
