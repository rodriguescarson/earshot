"""Match tools, claim verification and the cause gate: no model calls (planted claims only)."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from earshot.agents.analyst import AnalystResult, _parse, response_schema, timeout_for
from earshot.agents.claims import (
    check_claim_line,
    claim_line,
    fresh,
    gate_cause,
    select_for_mode,
    verify,
)
from earshot.agents.state import Roster
from earshot.contract import (
    CauseLabel,
    Claim,
    Direction,
    Event,
    Language,
    ListeningMode,
    Metric,
    StoppageStart,
)
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.tools.match import (
    THRESHOLD,
    MatchView,
    Teams,
    match_context,
    metric_change,
    metric_table,
    recent_events,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "clip-demo.json"
EV = TypeAdapter(Event)


@pytest.fixture(scope="module")
def world():
    from earshot.sim.match import simulate

    d = json.loads(FIXTURE.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, [ClubInfo(**c) for c in d["clubs"]], [PlayerInfo(**p) for p in d["players"]])
    events = simulate(d["seed"]).events
    teams = Teams(roster.home_id, roster.away_id, {t: c.short_name for t, c in roster.clubs.items()})
    positions = {p.player_id: p.position for p in roster.players.values()}
    stop = next(
        e for e in events if isinstance(e, StoppageStart) and e.wall_ms >= d["clip"]["start_wall_ms"] + 60_000
    )
    return MatchView(events, teams, positions, now_wall_ms=stop.wall_ms)


def notable(view):
    rows = [r for r in metric_table(view)["rows"] if r["notable"]]
    assert rows, "fixture window has no notable change"
    return rows[0]


def claim_from(view, row, **over):
    table = metric_table(view)
    base = {
        "team_id": row["team_id"],
        "metric": row["metric"],
        "direction": "up" if row["change"] > 0 else "down",
        "window_start_ms": table["window_start_ms"],
        "window_end_ms": table["window_end_ms"],
        "value_before": row["before"],
        "value_after": row["after"],
        "cause": None,
        "cited_event_ids": row["evidence_event_ids"][:2],
        "rank": 1,
    }
    return Claim(**{**base, **over})


def test_tools_never_see_the_future(world):
    later = [e for e in world.events if e.wall_ms >= world.now_wall_ms]
    assert later, "fixture should have events after now"
    seen = {r["event_id"] for r in recent_events(world, minutes=10, limit=10_000)}
    assert not seen & {e.event_id for e in later}
    for r in metric_table(world)["rows"]:
        assert set(r["evidence_event_ids"]) <= {e.event_id for e in world.past()}


def test_metric_change_rejects_bad_arguments(world):
    with pytest.raises(ValueError, match="minutes"):
        metric_change(world, world.teams.home_id, Metric.TEMPO, 7)
    with pytest.raises(ValueError, match="unknown team"):
        metric_change(world, "nobody", Metric.TEMPO, 5)


def test_context_reports_both_teams(world):
    ctx = match_context(world)
    assert {ctx["home"]["team_id"], ctx["away"]["team_id"]} == {world.teams.home_id, world.teams.away_id}


def test_a_true_claim_verifies_with_the_recomputed_numbers(world):
    row = notable(world)
    chk = verify(claim_from(world, row), world)
    assert chk.ok, chk.reason
    assert chk.before == pytest.approx(row["before"], abs=1e-3)
    assert chk.after == pytest.approx(row["after"], abs=1e-3)


@pytest.mark.parametrize(
    ("defect", "reason"),
    [
        ("wrong_direction", "direction wrong"),
        ("fabricated_value", "computed"),
        ("missing_event", "does not exist"),
        ("future_window", "future"),
        ("odd_window", "outside 2-20 min"),
        ("unknown_team", "unknown team"),
    ],
)
def test_planted_claim_defects_are_rejected(world, defect, reason):
    row = notable(world)
    c = claim_from(world, row)
    if defect == "wrong_direction":
        c = c.model_copy(
            update={"direction": Direction.DOWN if c.direction is Direction.UP else Direction.UP}
        )
    elif defect == "fabricated_value":
        c = c.model_copy(update={"value_after": (c.value_after or 0) + 50})
    elif defect == "missing_event":
        c = c.model_copy(update={"cited_event_ids": ["no-such-event"]})
    elif defect == "future_window":
        c = c.model_copy(
            update={
                "window_end_ms": world.now_wall_ms + 60_000,
                "window_start_ms": world.now_wall_ms - 240_000,
            }
        )
    elif defect == "odd_window":
        c = c.model_copy(update={"window_start_ms": c.window_end_ms - 60_000})
    else:
        c = c.model_copy(update={"team_id": "nobody"})
    chk = verify(c, world)
    assert not chk.ok
    assert reason in chk.reason


def test_a_change_below_threshold_is_rejected(world):
    rows = [
        r
        for r in metric_table(world)["rows"]
        if r["change"] is not None and 0 < abs(r["change"]) < r["threshold"]
    ]
    if not rows:
        pytest.skip("no sub-threshold change in this window")
    chk = verify(claim_from(world, rows[0]), world)
    assert not chk.ok
    assert "below threshold" in chk.reason


def test_cause_gate_drops_score_and_metric_contradictions(world):
    row = notable(world)
    c = claim_from(world, row)
    ctx = match_context(world)
    side = "home" if c.team_id == world.teams.home_id else "away"
    other = "away" if side == "home" else "home"
    level_or_ahead = ctx[side]["score"] >= ctx[other]["score"]
    gated, why = gate_cause(c.model_copy(update={"cause": CauseLabel.CHASING_GAME}), world)
    if level_or_ahead:
        assert gated.cause is None
        assert "not behind" in (why or "")
    wrong = CauseLabel.WIDEN if c.metric is not Metric.WIDTH else CauseLabel.GO_DIRECT
    gated, why = gate_cause(c.model_copy(update={"cause": wrong}), world)
    assert gated.cause is None
    assert "does not match" in (why or "")


def test_claim_line_speaks_recomputed_numbers_and_passes_the_line_check(world):
    row = notable(world)
    c = claim_from(world, row, value_before=None, value_after=None)
    chk = verify(c, world)
    for lang in Language:
        for mode in ListeningMode:
            ln = claim_line(
                c,
                chk,
                view=world,
                window_id="m-win-1",
                language=lang,
                mode=mode,
                wall_ms=0,
                deadline_wall_ms=1,
            )
            assert check_claim_line(ln, c, world).ok
            if mode is ListeningMode.CASUAL:
                assert not any(
                    isinstance(v, int) and k != "window_min"
                    for k, v in ln.slots.items()
                    if k in ("before", "after")
                )


def test_casual_airs_one_claim_and_analyst_two_per_club(world):
    rows = [r for r in metric_table(world)["rows"] if r["notable"]]
    checked = [(claim_from(world, r), verify(claim_from(world, r), world)) for r in rows]
    checked = [cc for cc in checked if cc[1].ok]
    assert len(select_for_mode(checked, ListeningMode.CASUAL)) <= 1
    per_club: dict[str, int] = {}
    for c, _ in select_for_mode(checked, ListeningMode.ANALYST):
        per_club[c.team_id] = per_club.get(c.team_id, 0) + 1
    assert all(n <= 2 for n in per_club.values())


def test_a_claim_does_not_repeat_within_ten_minutes(world):
    row = notable(world)
    cc = (claim_from(world, row), verify(claim_from(world, row), world))
    key = (cc[0].team_id, cc[0].metric.value, cc[0].direction.value)
    assert fresh([cc], {key: world.now_wall_ms - 60_000}, world.now_wall_ms) == []
    assert fresh([cc], {key: world.now_wall_ms - 11 * 60_000}, world.now_wall_ms) == [cc]


def test_schema_is_strict_and_lists_every_metric_and_cause():
    s = response_schema("a", "b")
    item = s["schema"]["properties"]["claims"]["items"]
    assert s["strict"] is True
    assert set(item["required"]) == set(item["properties"])
    assert set(item["properties"]["metric"]["enum"]) == {m.value for m in Metric}


def test_parse_keeps_valid_claims_and_logs_invalid_ones():
    good = {
        "team_id": "a", "metric": "tempo", "direction": "up", "window_start_ms": 0, "window_end_ms": 300_000,
        "value_before": None, "value_after": None, "cause": None, "cited_event_ids": ["e1"], "rank": 1,
    }  # fmt: skip
    bad = {**good, "cited_event_ids": []}
    res = AnalystResult("w")
    _parse(json.dumps({"claims": [good, bad]}), res)
    assert len(res.claims) == 1
    assert len(res.invalid) == 1


@pytest.mark.parametrize(("dead", "budget"), [(10_000, 6.0), (30_000, 8.0), (5_000, 3.0)])
def test_timeout_aims_to_finish_before_the_restart(dead, budget):
    assert timeout_for(dead) == budget


def test_thresholds_cover_every_metric():
    assert set(THRESHOLD) == set(Metric)
