"""RQ4 counterfactual check of the edge-message explanations (engine.faithfulness)."""

import numpy as np
import pytest

from engine import synthetic
from engine.data import load_dataset
from engine.dkg import DKG
from engine.faithfulness import message_effects, summarize
from engine.split import temporal_split


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth_faith")
    data = load_dataset(*synthetic.write(out, n_students=12, days=20, seed=5,
                                         edge_strengths={e: 0.6 for e in synthetic.EDGES}))
    model = DKG(epochs=2)
    model.fit(data.subset(temporal_split(data).train))
    return data, model.model


def test_mute_none_is_the_normal_replay(fitted):
    data, m = fitted
    a = [float(p.detach()) for _, p, _ in m.replay(data)]
    b = [float(p.detach()) for _, p, _ in m.replay(data, mute=None)]
    assert a == b


def test_muting_a_message_changes_nothing_up_to_its_own_answer(fitted):
    data, m = fitted
    trace = list(m.replay(data, explain=True))
    pos, (it, _, exp) = next((n, t) for n, t in enumerate(trace) if t[2]["messages"])
    msg = exp["messages"][0]
    muted = [float(p) for _, p, _ in m.replay(data, mute=(it.idx, msg["edge_id"], msg["kind"]))]
    full = [float(p) for _, p, _ in trace]
    assert muted[: pos + 1] == full[: pos + 1]


def test_attribution_is_exact_when_later_messages_do_not_depend_on_the_target(fitted):
    # Without transfer (its "room" 1−k_j shrinks as k_j grows) and with a constant gate (the
    # adaptive gate reads k_j), nothing between the two answers depends on k_j, so the message
    # moves the next logit by exactly a·v·decay·contribution, unless k was clamped to [0, 1].
    data, _ = fitted
    model = DKG(epochs=2, transfer=False, adaptive_alpha=False)
    model.fit(data.subset(temporal_split(data).train))
    rows = message_effects(model.model, data, per_student=10)
    att = np.array([r["attributed"] for r in rows])
    cf = np.array([r["counterfactual_next"] for r in rows])
    assert np.isclose(att, cf, atol=1e-5, rtol=1e-3).mean() > 0.9


def test_attribution_keeps_sign_and_order_with_saturation(fitted):
    # With transfer and the adaptive gate, later messages shrink as k_j grows: the attribution
    # overstates the size a little but must keep the sign and the ranking.
    data, m = fitted
    rows = message_effects(m, data, per_student=10)
    assert rows
    summary = summarize(rows, boot=20)
    attributed = summary["attributed (a·v·decay·message)"]
    assert attributed["sign_agreement"]["value"] > 0.95
    assert attributed["spearman"]["value"] > 0.9
    assert attributed["spearman"]["value"] > summary["random"]["spearman"]["value"]
