"""The accelerated evaluation run (ET11): targets from the event log, the live broadcaster on a
virtual clock, and the summary the evaluation reports."""

import importlib.util
import json
import time

import pytest

from earshot.contract.events import Card, CardColor, StoppageKind, StoppageStart
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.eval import run as R
from earshot.sim.match import simulate

LIVE = importlib.util.find_spec("earshot.live") is not None
needs_live = pytest.mark.skipif(not LIVE, reason="needs Stream 1's live broadcaster")


def test_targets_come_from_the_event_log():
    r = simulate(26)
    t = R.targets(r.events)
    kinds = {
        k: sum(1 for x in t if x.kind == k) for k in ("goal", "penalty", "red_card", "yellow_card", "shot")
    }
    assert kinds["shot"] == sum(
        1 for e in r.events if getattr(e, "type", None) in {a.value for a in SHOT_TYPES}
    )
    assert kinds["goal"] == sum(
        1 for e in r.events if isinstance(e, StoppageStart) and e.kind is StoppageKind.GOAL
    )
    assert kinds["yellow_card"] == sum(
        1 for e in r.events if isinstance(e, Card) and e.color is CardColor.YELLOW
    )
    goals = [x for x in t if x.kind == "goal"]
    assert all(len(g.linked) == 2 and g.never_drop for g in goals)  # the stoppage and the scoring action
    assert ActionType("shot") in SHOT_TYPES


def test_latency_model_loads_measured_samples(tmp_path):
    p = tmp_path / "latency.json"
    p.write_text(json.dumps({"ready_ms": [400, 900], "analyst_ms": [2500], "delivery_ms": [80]}))
    m = R.LatencyModel.load(p)
    assert not m.provisional
    assert m.ready_ms == (400, 900)
    assert R.LatencyModel.provisional_default().provisional


def test_summary_reports_failed_matches_and_never_counts_them():
    rows = [{"seed": 7, "error": "Traceback...\nValueError: boom\n"}]
    s = R.summarize(rows, R.LatencyModel.provisional_default(), 1.0, 1)
    assert s["failed"] == [{"seed": 7, "error": "ValueError: boom"}]
    assert s["ok"] == 0
    assert s["latency_model"]["provisional"] is True


class SlowAnalyst:
    def run(self, view, window_id, expected_dead_ms):
        time.sleep(0.05)
        return object()


def test_the_real_analyst_carries_its_latency_onto_the_virtual_clock():
    a = R.TimedAnalyst(SlowAnalyst())
    a.run(None, "w", 20_000)
    assert a.last_ms >= 50


@needs_live
def test_one_match_through_the_live_broadcaster():
    lat = R.LatencyModel.provisional_default()
    inline = R.evaluate_match(26, lat, windows="inline")
    assert set(inline["profiles"]) == {"P1", "P2", "P3", "P4", "P5"}
    assert not any("P4" in n for n in inline["notes"])  # the focus stat is live: P4 follows shot speed
    assert inline["profiles"]["P4"]["group"].endswith(".shot_speed")
    for p in inline["profiles"].values():
        assert p["aired"] > 500
        never = [h for h in p["targets"] if h["kind"] in ("goal", "red_card", "penalty")]
        assert never
        assert all(h["latency_ms"] is not None for h in never)
    p1, p5 = inline["profiles"]["P1"], inline["profiles"]["P5"]
    assert p5["numeric_slots_per_min"] > p1["numeric_slots_per_min"]
    assert inline["profiles"]["P2"]["group"].endswith(".spa.spa-09.-")  # follows the home club and a forward

    # The finding this harness measured: when the event feeder waited for each window run (before
    # 24f4efb), a yellow card shown after a foul waited for the Analyst. As built now, it does not.
    bg = R.evaluate_match(26, lat, windows="background")

    def yellow(m):
        return [h["latency_ms"] for h in m["profiles"]["P1"]["targets"] if h["kind"] == "yellow_card"]

    assert max(yellow(bg)) < max(yellow(inline))
    assert all(x is not None and x <= R.COVERAGE_MS for x in yellow(bg))


def test_pacing_keeps_an_hour_under_the_token_budget():
    from types import SimpleNamespace

    now = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    inner = SimpleNamespace(run=lambda v, w, d: SimpleNamespace(tokens=6_000, claims=[], latency_ms=900))
    a = R.TimedAnalyst(inner, tokens_per_hour=10_000, clock=lambda: now[0], sleep=sleep)
    a.run(None, "w1", 20_000)
    assert slept == []
    a.run(None, "w2", 20_000)  # 6,000 used + about 6,000 more would pass 10,000: wait out the hour
    assert sum(slept) >= 3_540
    assert a.paused_s == pytest.approx(sum(slept), abs=60)
    capped = R.TimedAnalyst(inner, max_calls=1)
    capped.run(None, "w1", 20_000)
    assert capped.run(None, "w2", 20_000).error == "skipped: call cap"
    assert len(capped.calls) == 1


@needs_live
def test_real_analyst_path_grades_cause_labels_against_the_truth_log():
    from earshot.agents.analyst import AnalystResult
    from earshot.contract.claim import Claim, Direction, Metric

    r = simulate(26)
    at = {e.event_id: e.wall_ms for e in r.events}

    class Oracle:  # peeks at the truth log: every label it gives must grade as correct
        def run(self, view, window_id, expected_dead_ms):
            sid = window_id.removesuffix("-w")
            t = at[sid]
            recent = [c for c in r.truth if 0 <= t - c.wall_ms <= 12 * 60_000]
            claims = [
                Claim(team_id=c.team_id, metric=Metric.PRESS_HEIGHT, direction=Direction.UP,
                      window_start_ms=max(0, t - 600_000), window_end_ms=t, cause=c.label,
                      cited_event_ids=[sid])
                for c in recent[-1:]
            ]  # fmt: skip
            return AnalystResult(window_id, claims=claims, tokens=1_000, latency_ms=800)

    m = R.evaluate_match(26, R.LatencyModel.provisional_default(), R.TimedAnalyst(Oracle()))
    ex = m["explanations"]
    assert ex["windows"] == len(m["explanations"]["analyst_calls"])
    assert ex["analyst_proposed"]["labels"] > 0
    assert ex["analyst_proposed"]["correct"] == ex["analyst_proposed"]["labels"]
    assert ex["analyst_gated"]["labels"] <= ex["analyst_proposed"]["labels"]  # the claim check filters
    assert ex["causes_aired"] is False  # Carson, 2026-10-16: causes are not spoken
    assert set(ex["baseline"]) == set(R.GRADE_KEYS)
    s = R.summarize([m], R.LatencyModel.provisional_default(), 1.0, 1)
    assert s["explanations"]["analyst_proposed"]["accuracy"] == 1.0
    assert s["explanations"]["analyst_calls"]["tokens_total"] == 1_000 * ex["windows"]


def test_a_long_run_resumes_from_its_rows(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake(args):
        seed = args[0]
        calls.append(seed)
        if seed == 3 and calls.count(3) == 1:
            return {"seed": 3, "error": "Traceback\nRuntimeError: network\n"}
        return {"seed": seed, "match_id": f"m{seed:05d}", "minutes": 90.0, "targets": {}, "notes": [],
                "profiles": {}, "explanations": {}, "wall_s": 1.0}  # fmt: skip

    monkeypatch.setattr(R, "_safe", fake)
    rows = tmp_path / "e.matches.jsonl"
    lat = R.LatencyModel.provisional_default()
    s1, _ = R.run_season([1, 2, 3], lat, workers=1, rows_path=rows)
    assert s1["failed"] == [{"seed": 3, "error": "RuntimeError: network"}]
    s2, m2 = R.run_season([1, 2, 3], lat, workers=1, rows_path=rows)
    assert calls == [1, 2, 3, 3]  # only the failed seed ran again
    assert s2["ok"] == 3
    assert s2["resumed_from_rows"] == 2
    assert [m["seed"] for m in m2] == [1, 2, 3]
    assert len(rows.read_text().splitlines()) == 3
