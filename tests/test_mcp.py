"""MCP match tools (CEO-3, ET5): contract tests, the clock-bound leakage test, input validation,
snapshot staleness, rate limits and judge-code rotation."""

import asyncio
import json
from functools import cache

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from starlette.testclient import TestClient

from earshot.api.mcp import ANON_PER_MINUTE, JUDGE_PER_MINUTE, build_app, build_server
from earshot.api.ratelimit import JudgeCodes, SlidingLimit, code_hash
from earshot.sim.match import simulate
from earshot.tools import match_tools as t
from earshot.tools.snapshots import MatchView, ScheduledReplay, SnapshotDir, SnapshotWriter
from earshot.world.clubs import world

SEED = 26


@cache
def match():
    r = simulate(SEED)
    w = world()
    clubs = [w.club(r.info.home.team_id).info(), w.club(r.info.away.team_id).info()]
    players = [
        p.info() for c in (w.club(r.info.home.team_id), w.club(r.info.away.team_id)) for p in c.players
    ]
    return r, clubs, players


def view_at(minutes: float) -> MatchView:
    r, clubs, players = match()
    return MatchView.of(r.info, clubs, players, r.events, int(minutes * 60_000))


def test_a_view_never_holds_future_events():
    r, _, _ = match()
    v = view_at(30)
    assert v.events
    assert max(e.wall_ms for e in v.events) <= v.as_of_wall_ms
    with pytest.raises(ValueError, match="after as_of"):
        MatchView(r.info, {}, {}, tuple(r.events), 1_000, 0)


@pytest.mark.parametrize("minutes", [0.5, 20, 46, 70, 200])
def test_no_tool_returns_anything_after_its_clock(minutes):
    """The leakage test: every event id and wall time in every tool answer is <= as_of."""
    v = view_at(minutes)
    home = v.info.home.team_id
    player = v.info.home.starting[5]
    answers = [
        t.key_moments(v, 50),
        t.team_window(v, home),
        t.player_report(v, player),
        t.catch_up(v),
        t.recap(v),
    ]
    known = {e.event_id: e.wall_ms for e in v.events}
    for a in answers:
        assert a["as_of"]["wall_ms"] == v.as_of_wall_ms
        text = json.dumps(a)
        for m in json.loads(text).get("moments", []) + json.loads(text).get("last_moments", []):
            assert m["event_id"] in known
            assert m["wall_ms"] <= v.as_of_wall_ms
        window = a.get("window")
        if window:
            assert window["end_wall_ms"] <= v.as_of_wall_ms


def test_tool_contracts_at_full_time():
    r, _, _ = match()
    v = view_at(1_000)
    cu = t.catch_up(v)
    assert cu["as_of"]["status"] == "full_time"
    assert (cu["score"]["home"], cu["score"]["away"]) == (r.home_goals, r.away_goals)
    rec = t.recap(v)
    assert len(rec["goals"]) == r.home_goals + r.away_goals
    assert set(rec["teams"]) == {r.info.home.team_id, r.info.away.team_id}
    tw = t.team_window(v, r.info.away.team_id, minutes=15)
    assert set(tw["metrics"]) == {"press_height", "tempo", "directness", "width", "line_height",
                                  "possession_share", "shot_count"}  # fmt: skip
    pr = t.player_report(v, r.info.home.starting[3])
    assert pr["passes"]["attempted"] >= pr["passes"]["completed"]
    assert t.as_of(view_at(0))["status"] in ("before_kickoff", "live")


def test_bad_inputs_get_clear_errors():
    v = view_at(30)
    with pytest.raises(t.ToolInputError, match="not playing"):
        t.team_window(v, "zzz")
    with pytest.raises(t.ToolInputError, match="unknown player"):
        t.player_report(v, "zzz-99")
    with pytest.raises(t.ToolInputError, match="minutes"):
        t.team_window(v, v.info.home.team_id, minutes=90)
    with pytest.raises(t.ToolInputError, match="limit"):
        t.key_moments(v, 0)


def replay_source(minutes_ago: float):
    r, clubs, players = match()
    return ScheduledReplay(r.info, clubs, players, r.events, kickoff_unix_s=1_000_000.0,
                           clock=lambda: 1_000_000.0 + minutes_ago * 60)  # fmt: skip


def test_mcp_server_lists_and_calls_the_tools():
    server = build_server(replay_source(30))

    async def go():
        names = {x.name for x in await server.list_tools()}
        assert names == {"list_matches", "key_moments", "team_window", "player_report", "catch_up", "recap"}
        ok = await server.call_tool("catch_up", {"match_id": f"m{SEED:05d}"})
        assert isinstance(ok, CallToolResult)
        assert not ok.is_error
        with pytest.raises(ToolError, match="not playing"):
            await server.call_tool("team_window", {"match_id": f"m{SEED:05d}", "team_id": "zzz"})
        with pytest.raises(ToolError, match="list_matches"):
            await server.call_tool("catch_up", {"match_id": "m99999"})
        with pytest.raises(ToolError, match="match_id"):
            await server.call_tool("catch_up", {"match_id": "../../etc"})

    asyncio.run(go())


def test_snapshots_are_at_most_five_seconds_behind_and_say_so(tmp_path):
    r, clubs, players = match()
    now = [2_000_000.0]
    writer = SnapshotWriter(tmp_path, r.info, clubs, players, clock=lambda: now[0])
    for e in r.events[:400]:
        writer.push(e)
    src = SnapshotDir(tmp_path, clock=lambda: now[0] + 1.0)
    v = src.view(r.info.match_id)
    assert r.events[399].wall_ms - v.as_of_wall_ms <= 5_000
    assert v.staleness_ms == 1_000
    assert t.as_of(v)["staleness_ms"] == 1_000
    assert src.match_ids() == [r.info.match_id]


def test_http_app_rate_limits_anonymous_callers_and_judges(tmp_path):
    codes_file = tmp_path / "codes"
    codes_file.write_text(code_hash("judge-abc") + "\n")
    app = build_app(replay_source(20), JudgeCodes(codes_file))
    with TestClient(app) as client:
        _rate_limit_checks(client)


def _rate_limit_checks(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    first = client.post("/mcp", json=body, headers=headers)
    assert first.status_code == 200
    statuses = [client.post("/mcp", json=body, headers=headers).status_code for _ in range(ANON_PER_MINUTE)]
    assert statuses[-1] == 429
    judge = {**headers, "x-earshot-code": "judge-abc"}
    assert all(
        client.post("/mcp", json=body, headers=judge).status_code == 200 for _ in range(ANON_PER_MINUTE + 5)
    )
    assert client.get("/healthz").status_code == 200
    args = {"match_id": f"m{SEED:05d}", "team_id": "zzz"}
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "team_window", "arguments": args},
    }
    res = client.post("/mcp", json=call, headers=judge).json()["result"]
    assert res["isError"] is True
    assert "not playing" in res["content"][0]["text"]


def test_judge_codes_rotate_without_a_redeploy(tmp_path):
    clock = [0.0]
    f = tmp_path / "codes"
    f.write_text(code_hash("old-code"))
    codes = JudgeCodes(f, clock=lambda: clock[0])
    assert codes.valid("old-code")
    f.write_text(code_hash("new-code"))
    clock[0] = 31.0
    assert not codes.valid("old-code")
    assert codes.valid("new-code")
    assert not codes.valid(None)
    assert not codes.valid("")


def test_sliding_limit():
    clock = [0.0]
    lim = SlidingLimit(3, clock=lambda: clock[0])
    assert [lim.allow("k") for _ in range(4)] == [True, True, True, False]
    clock[0] = 60.0
    assert lim.allow("k")
    assert JUDGE_PER_MINUTE == 60
    assert ANON_PER_MINUTE == 10
