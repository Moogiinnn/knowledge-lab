"""Research engine: no future leakage, correct metrics, exact explanations, reproducibility."""

import csv
import dataclasses
import json
import math

import numpy as np
import pytest
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from engine import metrics, synthetic
from engine.baselines import BKT, PFA, ConceptRate, GlobalRate
from engine.data import Dataset, load_dataset
from engine.dkg import DKG
from engine.experiment import ABLATIONS, model_specs, run
from engine.split import student_split, temporal_split


@pytest.fixture(scope="module")
def synth_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    return synthetic.write(out, n_students=12, days=20, seed=3)


@pytest.fixture(scope="module")
def data(synth_files):
    return load_dataset(*synth_files)


def fast_models():
    return [GlobalRate(), ConceptRate(), PFA(), BKT(), DKG(epochs=2), DKG(epochs=2, w_mode="dynamic", windows=2)]


def flip_future(data: Dataset, cut: int) -> Dataset:
    changed = [dataclasses.replace(i, y=1 - i.y) if i.idx >= cut else i for i in data.interactions]
    return Dataset(changed, data.concepts, data.edges, data.questions)


@pytest.mark.parametrize("model", fast_models(), ids=lambda m: getattr(m, "name", "dkg"))
def test_no_future_leakage(data, model):
    split = temporal_split(data)
    model.fit(data.subset(split.train))
    cut = int(len(data.interactions) * 0.7)
    original = model.predict(data)
    flipped = model.predict(flip_future(data, cut))
    np.testing.assert_allclose(original[: cut + 1], flipped[: cut + 1], atol=1e-6)


def test_fit_uses_only_training_labels(data):
    split = temporal_split(data)
    a = DKG(epochs=2)
    a.fit(data.subset(split.train))
    # Flipping every validation/test label must not change what is learned.
    b = DKG(epochs=2)
    b.fit(flip_future(data, min(split.val | split.test)).subset(split.train))
    np.testing.assert_allclose(a.predict(data), b.predict(data), atol=1e-6)


def test_metrics_match_sklearn():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 300)
    p = np.round(rng.random(300), 2)  # rounded -> ties
    assert metrics.auc(y, p) == pytest.approx(roc_auc_score(y, p))
    assert metrics.logloss(y, p) == pytest.approx(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)))
    assert metrics.brier(y, p) == pytest.approx(brier_score_loss(y, p))
    assert math.isnan(metrics.auc(np.ones(5), np.linspace(0, 1, 5)))
    assert metrics.ece(np.array([1, 0, 1, 0]), np.array([0.5, 0.5, 0.5, 0.5])) == pytest.approx(0)


def test_bootstrap_resamples_students_and_paired_diff():
    rng = np.random.default_rng(1)
    students = np.repeat(np.arange(20), 10).astype(str)
    y = rng.integers(0, 2, 200)
    p = rng.random(200)
    res = metrics.bootstrap(students, y, {"a": p, "b": p.copy()}, reference="a", n_boot=50, seed=0)
    assert res["n_students"] == 20
    assert res["paired_diff"]["b"]["auc"]["lo"] == pytest.approx(0) == res["paired_diff"]["b"]["auc"]["hi"]
    again = metrics.bootstrap(students, y, {"a": p, "b": p.copy()}, reference="a", n_boot=50, seed=0)
    assert res == again


def test_splits(data):
    t = temporal_split(data, 0.6, 0.2)
    times = {i.idx: i.t for i in data.interactions}
    assert max(times[i] for i in t.train) < min(times[i] for i in t.val) <= max(times[i] for i in t.val)
    assert max(times[i] for i in t.val) < min(times[i] for i in t.test)
    s = student_split(data, 0.5, 0.25, seed=1)
    who = {i.idx: i.student for i in data.interactions}
    groups = [{who[i] for i in part} for part in (s.train, s.val, s.test)]
    assert not (groups[0] & groups[1]) and not (groups[0] & groups[2]) and not (groups[1] & groups[2])


def test_dkg_explanation_decomposes_logit_exactly(data):
    model = DKG(epochs=2)
    model.fit(data.subset(temporal_split(data).train))
    p = model.predict(data)
    for n, exp in enumerate(model.explain(data)[:50]):
        parts = exp["c"] + exp["minus_difficulty"] + exp["ability"] + sum(c["contribution"] for c in exp["concepts"].values())
        assert parts == pytest.approx(exp["logit"], abs=1e-5)
        assert 1 / (1 + math.exp(-exp["logit"])) == pytest.approx(p[n], abs=1e-6)


def test_dkg_incoming_weights_are_normalised(data):
    model = DKG(epochs=2, w_norm="incoming")
    model.fit(data.subset(temporal_split(data).train))
    sums = {}
    for e in model.describe()["edges"]:
        target = e["edge"].split("→")[1]
        sums[target] = sums.get(target, 0) + e["w_bar"]
    assert all(v == pytest.approx(1) for v in sums.values())


def test_decay_moves_state_towards_baseline(data):
    import torch

    model = DKG(epochs=0)
    model.fit(data.subset(temporal_split(data).train))
    m = model.model
    with torch.no_grad():
        m.beta_raw.fill_(5.0)  # very fast forgetting
    first = data.interactions[0]
    later = dataclasses.replace(first, idx=1, t=first.t + 90 * 86400)
    exps = [e for _, _, e in m.replay(Dataset([first, later], data.concepts, data.edges, data.questions), explain=True)]
    for code, c in exps[1]["concepts"].items():
        baseline = float(torch.sigmoid(m.b_raw[data.concepts.index(code)]))
        assert c["k_decayed"] == pytest.approx(baseline, abs=1e-3)


def test_ablation_switches_change_behaviour(data):
    train = data.subset(temporal_split(data).train)
    preds = {}
    for name, change in {"full": {}, **ABLATIONS}.items():
        model = DKG(epochs=2, **change)
        model.fit(train)
        preds[name] = model.predict(data)
    assert not np.allclose(preds["full"], preds["dkg_no_graph"])
    assert not np.allclose(preds["full"], preds["dkg_no_transfer"])
    assert not np.allclose(preds["full"], preds["dkg_no_evidence_propagation"])
    assert not np.allclose(preds["full"], preds["dkg_no_decay"])


def test_ungraded_rows_dropped_and_unknown_concepts_rejected(tmp_path, synth_files):
    events, graph = synth_files
    rows = list(csv.DictReader(open(events, encoding="utf-8")))
    rows[0]["correct"] = ""
    path = tmp_path / "e.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    data = load_dataset(path, graph)
    assert data.dropped["ungraded"] == 1 and len(data.interactions) == len(rows) - 1
    rows[1]["concepts"] = json.dumps([{"concept": "nope", "weight": 1}])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="missing from the graph"):
        load_dataset(path, graph)


def test_experiment_is_reproducible(tmp_path, synth_files):
    events, graph = synth_files
    config = {
        "events": str(events), "graph": str(graph), "seed": 5, "bootstrap": 20, "reference": "bkt",
        "models": [{"type": "global_rate"}, {"type": "bkt"}, {"type": "dkg", "name": "dkg_full", "epochs": 2}],
        "explain": "dkg_full",
    }  # fmt: skip
    first = run(config, tmp_path / "a")
    second = run(config, tmp_path / "b")
    assert (tmp_path / "a" / "predictions.csv").read_text() == (tmp_path / "b" / "predictions.csv").read_text()
    assert first["models"] == second["models"]
    for name in ("metrics.json", "predictions.csv", "explanations.jsonl", "run_info.json"):
        assert (tmp_path / "a" / name).exists()
    info = json.loads((tmp_path / "a" / "run_info.json").read_text(encoding="utf-8"))
    assert len(info["events_sha256"]) == 64 and info["environment"]["torch"]


def test_ablation_expansion():
    specs = model_specs({"models": [{"type": "bkt"}, {"type": "dkg", "name": "dkg_full"}], "ablation": True})
    names = [s.get("name", s["type"]) for s in specs]
    assert "dkg_2x2_dynamic_w_adaptive_alpha" in names and len(names) == 2 + len(ABLATIONS)


def test_synthetic_columns_match_export():
    from apps.events.export import COLUMNS

    assert synthetic.COLUMNS == COLUMNS


@pytest.mark.parametrize("w_norm", ["incoming", "slack", "bounded"])
def test_w_norm_ranges(data, w_norm):
    model = DKG(epochs=2, w_norm=w_norm)
    model.fit(data.subset(temporal_split(data).train))
    edges = model.describe()["edges"]
    assert all(0 < e["w_bar"] <= 1 for e in edges)
    incoming = {}
    for e in edges:
        incoming[e["edge"].split("→")[1]] = incoming.get(e["edge"].split("→")[1], 0) + e["w_bar"]
    if w_norm == "incoming":
        assert all(v == pytest.approx(1) for v in incoming.values())
    if w_norm == "slack":
        assert all(v < 1 for v in incoming.values())  # the slack keeps a "no transfer" share
        assert set(model.describe()["slack"]) == set(incoming)


def test_single_incoming_edge_is_learnable_only_without_plain_normalisation(data):
    """The v1 finding: with incoming normalisation a lone incoming edge cannot move."""
    import torch

    single = {}
    for e in data.edges:
        single[e.target] = single.get(e.target, 0) + 1
    lone = [n for n, e in enumerate(data.edges) if single[e.target] == 1]
    assert lone, "synthetic graph should have single-incoming targets"
    grads = {}
    for w_norm in ("incoming", "slack", "bounded"):
        model = DKG(epochs=0, w_norm=w_norm)
        model.fit(data.subset(temporal_split(data).train))
        m = model.model
        m.loss(data.subset(temporal_split(data).train)).backward()
        grads[w_norm] = float(torch.abs(m.w_e.raw.grad[lone]).sum() + torch.abs(m.w_t.raw.grad[lone]).sum())
    assert grads["incoming"] == pytest.approx(0, abs=1e-9)
    assert grads["slack"] > 0 and grads["bounded"] > 0


def test_unknown_w_norm_rejected(data):
    with pytest.raises(ValueError, match="w_norm"):
        DKG(epochs=0, w_norm="nope").fit(data.subset(temporal_split(data).train))


def test_transfer_is_non_negative_and_evidence_follows_residual(data):
    model = DKG(epochs=2)
    model.fit(data.subset(temporal_split(data).train))
    p = model.predict(data)
    seen = {"evidence": 0, "transfer": 0}
    for it, exp in zip(data.interactions, model.explain(data)):
        residual = it.y - p[it.idx]
        for msg in exp["messages"]:
            seen[msg["kind"]] += 1
            if msg["kind"] == "transfer":
                assert msg["contribution"] >= 0  # practice helps even after a wrong answer
            elif abs(residual) > 1e-6 and abs(msg["contribution"]) > 1e-9:
                assert np.sign(msg["contribution"]) == np.sign(residual)
    assert seen["evidence"] > 0 and seen["transfer"] > 0


def test_describe_reports_both_edge_weights(data):
    model = DKG(epochs=2)
    model.fit(data.subset(temporal_split(data).train))
    desc = model.describe()
    assert desc["config"]["w_norm"] == "slack" and desc["kappa"] > 0
    assert all({"w_bar", "w_bar_transfer"} <= set(e) for e in desc["edges"])
    assert set(desc["slack"]) == set(desc["slack_transfer"])


def test_w_prior_fades_with_data_only_in_per_event_mode(data):
    from engine.dkg import DKGConfig, DKGModel

    per_event = DKGModel(data, DKGConfig(w_prior_events=10))
    assert per_event.w_reg_coefficient(1000) == pytest.approx(0.01)
    assert per_event.w_reg_coefficient(10000) == pytest.approx(0.001)
    constant = DKGModel(data, DKGConfig(w_reg_scaling="constant", w_reg=0.02))
    assert constant.w_reg_coefficient(1000) == constant.w_reg_coefficient(10000) == 0.02
    with pytest.raises(ValueError, match="w_reg_scaling"):
        DKGModel(data, DKGConfig(w_reg_scaling="nope")).w_reg_coefficient(10)


def test_resample_students_makes_distinct_copies_in_order(data):
    from engine.edge_bootstrap import resample_students

    sample = resample_students(data, np.random.default_rng(0))
    base = {s.split("#")[0] for s in sample.students}
    assert base <= set(data.students)
    assert len(sample.students) == len(data.students)  # one id per draw, duplicates kept apart
    assert [i.idx for i in sample.interactions] == list(range(len(sample.interactions)))
    times = [i.t for i in sample.interactions]
    assert times == sorted(times)
    assert len({i.event_id for i in sample.interactions}) == len(sample.interactions)


def test_edge_intervals_shape(data):
    from engine.edge_bootstrap import edge_intervals

    train = data.subset(temporal_split(data).train)
    res = edge_intervals(train, {"epochs": 1}, n_boot=3, workers=1)
    assert res["n_boot"] == 3 and len(res["edges"]) == len(data.edges)
    for kinds in res["edges"].values():
        for kind in ("evidence", "transfer"):
            ci = kinds[kind]
            assert ci["lo"] <= ci["median"] <= ci["hi"]
            assert ci["rel_lo"] <= ci["rel_median"] <= ci["rel_hi"]
            assert ci["verdict"] in ("stronger", "weaker", "unclear")
    with pytest.raises(ValueError, match="slack"):
        edge_intervals(train, {"w_norm": "incoming"}, n_boot=1, workers=1)


def test_group_contrast_detects_known_difference():
    from engine.edge_bootstrap import edge_intervals, group_contrast

    rng = np.random.default_rng(0)
    edges = ["a→b", "a→c", "b→d", "c→d"]
    strong = {"a→b", "b→d"}
    draws = [{e: [float(np.exp((0.5 if e in strong else 0.0) + rng.normal(0, 0.1))), 1.0] for e in edges}
             for _ in range(200)]  # fmt: skip
    res = group_contrast(draws, strong, kind="evidence")
    assert res["verdict"] == "group stronger" and res["delta_median"] == pytest.approx(0.5, abs=0.05)
    assert group_contrast(draws, {"a→c"}, kind="evidence", versus={"c→d"})["verdict"] == "unclear"
    # all transfer r are exactly 1: no spread, so no verdict either way
    assert group_contrast(draws, strong, kind="transfer")["verdict"].startswith("degenerate")
    assert edge_intervals(None, draws=draws)["edges"]["a→b"]["evidence"]["verdict"] == "stronger"
    with pytest.raises(ValueError, match="unknown"):
        group_contrast(draws, {"x→y"}, kind="evidence")
    with pytest.raises(ValueError, match="disjoint"):
        group_contrast(draws, {"a→b"}, kind="evidence", versus={"a→b"})


def test_zero_spread_bootstrap_is_flagged_not_significant():
    from engine.edge_bootstrap import group_contrast

    draws = [{"a→b": [1.01, 1.0], "a→c": [1.0, 1.0]} for _ in range(10)]
    assert group_contrast(draws, {"a→b"}, kind="evidence")["verdict"].startswith("degenerate")


def test_effect_size_translation_is_zero_at_zero_and_monotonic():
    from engine.effect_size import evidence_effect, transfer_effect

    effects = [transfer_effect(k, n_students=400) for k in (0.0, 0.3, 0.9)]
    assert effects[0].transfer_pp == 0.0  # identical practice outcomes in both arms
    assert effects[0].transfer_pp < effects[1].transfer_pp < effects[2].transfer_pp
    assert all(e.direct_pp > 0 for e in effects)
    corr = [evidence_effect(k, n_students=2000).mastery_correlation for k in (0.0, 0.5, 0.9)]
    assert abs(corr[0]) < 0.1 and corr[0] < corr[1] < corr[2]
    assert evidence_effect(0.0, n_students=500).gap_from_coupling_pp == 0.0


def test_online_ability_tracks_each_student_from_past_answers_only(data):
    model = DKG(epochs=2)
    model.fit(data.subset(temporal_split(data).train))
    p = model.predict(data)
    first_seen, prev = {}, {}
    for it, exp in zip(data.interactions, model.explain(data)):
        if it.student not in first_seen:
            first_seen[it.student] = True
            assert exp["ability"] == 0.0  # nothing known before a student's first answer
        elif it.student in prev:
            last_y, last_p, last_theta = prev[it.student]
            moved = exp["ability"] - last_theta
            assert np.sign(moved) == np.sign(last_y - last_p) or abs(moved) < 1e-9
        prev[it.student] = (it.y, p[it.idx], exp["ability"])
    without = DKG(epochs=2, ability=False)
    without.fit(data.subset(temporal_split(data).train))
    assert not np.allclose(p, without.predict(data))
    assert all(e["ability"] == 0.0 for e in without.explain(data))


def test_edge_analysis_runs_without_ability_unless_asked():
    from engine.dkg import DKGConfig
    from engine.edge_bootstrap import edge_analysis_config

    assert DKGConfig().ability is True  # prediction default
    assert edge_analysis_config({"epochs": 3}) == {"ability": False, "epochs": 3}
    assert edge_analysis_config({"ability": True})["ability"] is True  # explicit override wins


def test_two_sided_p_and_holm():
    from engine.edge_bootstrap import group_contrast, holm

    rng = np.random.default_rng(1)
    draws = [{"a→b": [float(np.exp(0.5 + rng.normal(0, 0.1))), 1.0], "a→c": [1.0, 1.0]} for _ in range(400)]
    assert group_contrast(draws, {"a→b"}, kind="evidence")["p_two_sided"] == 0.0
    assert holm({"H1": 0.01, "H2": 0.04}) == {"H1": True, "H2": True}  # 0.01 ≤ .025, 0.04 ≤ .05
    assert holm({"H1": 0.03, "H2": 0.04}) == {"H1": False, "H2": False}  # 0.03 > .025 stops
    assert holm({"H1": 0.001, "H2": 0.2}) == {"H1": True, "H2": False}


@pytest.mark.parametrize("switches", [
    {}, {"ability": False}, {"decay": False}, {"adaptive_alpha": False}, {"adaptive_eta": False},
    {"propagation": False}, {"transfer": False}, {"propagation": False, "transfer": False}, {"w_mode": "fixed"},
])  # fmt: skip
def test_batched_replay_matches_sequential_replay(tmp_path, switches):
    """The fast batched replay (training and predict) must compute what replay() computes."""
    import torch

    from engine import sim_course
    from engine.dkg import DKGConfig, DKGModel

    folder = sim_course.write(tmp_path / "sim", n_students=5, seed=4)
    data = load_dataset(folder / "events.csv", folder / "graph.json")
    torch.manual_seed(0)
    model = DKGModel(data, DKGConfig(**switches))
    model.set_questions(sorted({i.question for i in data.interactions})[:-3])  # some unseen questions
    with torch.no_grad():  # off the initial values, so every term contributes
        for p in model.parameters():
            if p.numel():
                p.add_(0.3 * torch.randn_like(p))
    params = [p for p in model.parameters() if p.requires_grad]
    seq = model.loss(data, batched=False)
    bat = model.loss(data, batched=True)
    assert abs(float(seq - bat)) < 1e-6
    for a, b in zip(torch.autograd.grad(seq, params, allow_unused=True), torch.autograd.grad(bat, params, allow_unused=True)):
        if a is not None and b is not None and a.numel():
            assert float((a - b).abs().max()) <= 1e-3 * float(a.abs().max()) + 1e-7
    with torch.no_grad():
        p_seq = torch.stack([p for _, p, _ in model.replay(data)])
        assert torch.allclose(p_seq, model.replay_batched(data), atol=1e-6)
