import json
from pathlib import Path

from pydantic import TypeAdapter

from earshot.contract.ball import BALL_STEP_MS, BallSample
from earshot.contract.events import Event, Restart, StoppageStart, check_stream
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.sim.ballpath import ball_path
from earshot.sim.clip import dumps, fixture, pick
from earshot.sim.match import ENGINE_VERSION, simulate

FIXTURE = Path(__file__).parent.parent / "fixtures" / "clip-demo.json"


def test_ball_path_is_10hz_and_dead_during_stoppages():
    r = simulate(3)
    path = ball_path(r.events)
    p1 = [b for b in path if b.period_id == 1]
    assert all(
        b - a == BALL_STEP_MS
        for a, b in zip([s.wall_ms for s in p1], [s.wall_ms for s in p1[1:]], strict=False)
    )
    starts = [
        e for e in r.events if isinstance(e, StoppageStart) and e.kind.value not in ("half_time", "full_time")
    ]
    by_wall = {b.wall_ms: b for b in path}
    for s in starts[:20]:
        t = s.wall_ms - s.wall_ms % BALL_STEP_MS + BALL_STEP_MS
        restart = next(e for e in r.events if isinstance(e, Restart) and e.wall_ms >= s.wall_ms)
        if t < restart.wall_ms and t in by_wall:
            assert not by_wall[t].in_play
            assert by_wall[t].team_id is None


def test_ball_path_covers_both_periods_and_skips_half_time():
    r = simulate(3)
    path = ball_path(r.events)
    assert {b.period_id for b in path} == {1, 2}
    gap = min(b.wall_ms for b in path if b.period_id == 2) - max(b.wall_ms for b in path if b.period_id == 1)
    assert gap >= r.info.half_time_ms


def test_committed_fixture_is_valid_and_current():
    data = json.loads(FIXTURE.read_text())
    assert data["engine_version"] == ENGINE_VERSION
    MatchInfo.model_validate(data["match"])
    [ClubInfo.model_validate(c) for c in data["clubs"]]
    [PlayerInfo.model_validate(p) for p in data["players"]]
    events = [TypeAdapter(Event).validate_python(e) for e in data["events"]]
    assert check_stream(events) == []
    balls = [BallSample.model_validate(b) for b in data["ball"]]
    assert balls[0].wall_ms >= data["clip"]["start_wall_ms"]
    assert balls[-1].wall_ms <= data["clip"]["end_wall_ms"]
    assert "truth" not in data
    r, c = pick(list(range(1, 41)))
    current = dumps(fixture(r, c)) == FIXTURE.read_text()  # a bool: pytest's diff of 250 KB strings is slow
    assert current, "fixture is stale: run `uv run earshot clip --seeds 1-40 --out fixtures/clip-demo.json`"
