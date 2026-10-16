"""Evaluation: grading rules, the shuffled control, the AUC, the baseline detector, the truth store,
and the slice gate checks on a few seeds."""

from functools import cache

import pytest

from earshot.contract.claim import CauseLabel
from earshot.contract.truth import TruthChange
from earshot.eval import baseline_detector, gate_p1, grading, signal
from earshot.hidden.store import read_truth, write_truth
from earshot.sim.match import simulate
from earshot.sim.runner import run_many
from earshot.world.clubs import world

MIN = 60_000


def change(label, wall, team="h", match="m"):
    return TruthChange(
        match_id=match, team_id=team, label=label, wall_ms=wall, ramp_ms=90_000, setting="tempo", delta=0.3
    )


def test_a_label_is_correct_only_for_a_recent_matching_change():
    truth = [change(CauseLabel.RAISE_TEMPO, 20 * MIN)]
    ok = grading.Prediction("m", "h", 25 * MIN, CauseLabel.RAISE_TEMPO)
    assert grading.is_correct(ok, truth)
    assert not grading.is_correct(
        grading.Prediction("m", "h", 19 * MIN, CauseLabel.RAISE_TEMPO), truth
    )  # before
    assert not grading.is_correct(
        grading.Prediction("m", "h", 33 * MIN, CauseLabel.RAISE_TEMPO), truth
    )  # stale
    assert not grading.is_correct(
        grading.Prediction("m", "a", 25 * MIN, CauseLabel.RAISE_TEMPO), truth
    )  # team
    assert not grading.is_correct(
        grading.Prediction("m", "h", 25 * MIN, CauseLabel.SLOW_TEMPO), truth
    )  # label


def test_grade_counts_accuracy_recall_and_false_alarms():
    truth = {"m": [change(CauseLabel.WIDEN, 10 * MIN), change(CauseLabel.NARROW, 40 * MIN)]}
    preds = [
        grading.Prediction("m", "h", 12 * MIN, CauseLabel.WIDEN),
        grading.Prediction("m", "h", 15 * MIN, CauseLabel.WIDEN),
        grading.Prediction("m", "h", 30 * MIN, CauseLabel.GO_DIRECT),
    ]
    g = grading.grade(preds, truth)
    assert g.labels == 3
    assert g.correct == 2
    assert g.recalled == 1
    assert g.recall == pytest.approx(0.5)
    assert g.false_alarms_per_match == pytest.approx(1.0)


def test_shuffled_truth_keeps_labels_and_moves_times_inside_the_match():
    truth = {"m": [change(CauseLabel.WIDEN, 10 * MIN)]}
    s = grading.shuffled(truth, {"m": (0, 90 * MIN)}, seed=1)
    assert s["m"][0].label is CauseLabel.WIDEN
    assert 0 <= s["m"][0].wall_ms < 90 * MIN


def test_auc():
    assert signal.auc([3, 4, 5], [0, 1, 2]) == 1.0
    assert signal.auc([1], [1]) == 0.5
    assert signal.auc([0], [1]) == 0.0


def test_truth_store_round_trip(tmp_path):
    changes = [change(CauseLabel.PRESS_HIGHER, 600_000)]
    p = write_truth(tmp_path, "slice", "m00001", changes)
    assert p.name == "m00001.truth.jsonl"
    assert read_truth(tmp_path, "slice", "m00001") == changes


@cache
def outputs():
    return run_many(range(1, 6), workers=1)


def test_baseline_names_the_red_card_reorganisation():
    for seed in range(1, 200):
        r = simulate(seed)
        reds = [e for e in r.events if e.type == "card" and e.color == "red"]  # type: ignore[union-attr]
        if reds:
            break
    else:
        pytest.skip("no red card in seeds 1-199")
    red = reds[0]
    assert red.team_id is not None
    window = next(w for w in grading.analysis_windows(r.events) if w.wall_ms > red.wall_ms)
    pos = {p.player_id: p.position for p in world().players.values()}
    d = baseline_detector.detect(r.events, r.info, red.team_id, window.wall_ms, pos)
    assert d.label is CauseLabel.RED_CARD_REORG


def test_baseline_only_sees_the_past():
    r = simulate(2)
    pos = {p.player_id: p.position for p in world().players.values()}
    w = grading.analysis_windows(r.events)[30]
    full = baseline_detector.detect(r.events, r.info, r.info.home.team_id, w.wall_ms, pos)
    cut = [e for e in r.events if e.wall_ms < w.wall_ms]
    assert baseline_detector.detect(cut, r.info, r.info.home.team_id, w.wall_ms, pos) == full


def test_gate_checks_run_on_a_few_seeds():
    outs = list(outputs())
    assert gate_p1.g2_completeness(outs, 5).passed
    assert gate_p1.g4_truth(outs).passed
    assert gate_p1.g10_dead_ball(outs).passed
    assert gate_p1.g7_pass_model(outs).detail["passes"] > 1000
    g5 = gate_p1.g5_planted_signal(outs)
    assert set(g5.detail["per_setting"]) == set(signal.SETTING_METRIC)
