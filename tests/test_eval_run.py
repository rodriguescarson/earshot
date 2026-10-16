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
    inline = R.evaluate_match(26, lat)
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

    # The finding this harness measured: as built, the event feeder waits for each window run, so a
    # yellow card shown after a foul waits for the Analyst. Window runs on their own task do not.
    bg = R.evaluate_match(26, lat, windows="background")

    def yellow(m):
        return [h["latency_ms"] for h in m["profiles"]["P1"]["targets"] if h["kind"] == "yellow_card"]

    assert max(yellow(bg)) < max(yellow(inline))
    assert all(x is not None and x <= R.COVERAGE_MS for x in yellow(bg))
