"""Frames for the render match (CEO-4): recording leaves the events untouched, and the physics caps hold."""

from functools import cache

import numpy as np
import pytest

from earshot.sim.frames import A_MAX, DT, V_MAX, physical, render_frames
from earshot.sim.io import events_jsonl, sha256
from earshot.sim.match import simulate


@cache
def frames():
    return render_frames(26)


@pytest.mark.slow
def test_recording_frames_does_not_change_the_events():
    assert sha256(events_jsonl(frames().events)) == sha256(events_jsonl(simulate(26).events))


@pytest.mark.slow
def test_speed_and_acceleration_caps_hold_and_players_reach_the_ball():
    p = physical(frames())
    assert p["max_speed_ms"] <= V_MAX + 1e-6
    assert p["max_accel_ms2"] <= A_MAX + 1e-6
    assert p["share_actions_ball_within_1_5m"] >= 0.98
    assert 9.0 <= p["median_outfield_km"] <= 12.5


@pytest.mark.slow
def test_players_are_off_the_pitch_when_subbed_off():
    fr = frames()
    on = {pid: ~np.isnan(xy[:, 0]) for pid, xy in fr.players.items()}
    assert sum(1 for v in on.values() if v[0]) == 22
    assert all(v.sum() * DT > 60 for v in on.values() if v.any())
