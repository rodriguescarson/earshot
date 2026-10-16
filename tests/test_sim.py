"""Engine tests: determinism, stream invariants, stoppages and the edge cases (red cards, own goals,
penalties, kick-offs, substitutions)."""

from functools import cache

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from earshot.contract.claim import CauseLabel
from earshot.contract.events import (
    Action,
    Card,
    Kickoff,
    PeriodEnd,
    Restart,
    StoppageKind,
    StoppageStart,
    Substitution,
    check_stream,
)
from earshot.contract.spadl import FLIGHT_TYPES, ActionType
from earshot.eval.gate_p1 import home_goal_end_problems, player_problems
from earshot.sim.io import events_jsonl, parse_events, sha256
from earshot.sim.match import ENGINE_VERSION, MatchResult, simulate
from earshot.stats.aggregates import aggregate, goals

GOLDEN = {
    # seed: sha256 of the canonical events file. Changes only with an ENGINE_VERSION bump.
    1: "e2bba485991137b08c41b547ff5d6f1f77312163e16c30d765293d194941543b",
}


@cache
def sim(seed: int) -> MatchResult:
    return simulate(seed)


def find(pred, seeds=range(1, 121)):
    for s in seeds:
        r = sim(s)
        if pred(r):
            return r
    pytest.skip("no seed in range has this case")


def test_same_seed_same_bytes():
    a, b = events_jsonl(simulate(3).events), events_jsonl(simulate(3).events)
    assert sha256(a) == sha256(b)


def test_golden_hash():
    digest = sha256(events_jsonl(sim(1).events))
    assert GOLDEN[1] == digest, f"engine {ENGINE_VERSION}: seed 1 hash is now {digest}; bump ENGINE_VERSION"


def test_different_seeds_differ():
    assert sha256(events_jsonl(sim(1).events)) != sha256(events_jsonl(sim(2).events))


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=1, max_value=10_000))
def test_stream_invariants_hold_for_any_seed(seed):
    r = simulate(seed)
    assert check_stream(r.events) == []
    assert player_problems(r.events, r.info) == []
    assert goals(r.events, r.info.home.team_id, r.info.away.team_id) == (r.home_goals, r.away_goals)
    assert home_goal_end_problems(r.events, r.info) == []
    walls = [e.wall_ms for e in r.events]
    assert walls == sorted(walls)


def test_events_round_trip_through_json():
    r = sim(5)
    assert parse_events(events_jsonl(r.events)) == r.events


def test_flight_and_pressure_qualifiers():
    actions = [e for e in sim(2).events if isinstance(e, Action)]
    for a in actions:
        assert a.nearest_opp_m is not None
        if ActionType(a.type) in FLIGHT_TYPES:
            assert a.flight_ms is not None
            assert a.flight_ms > 0
        else:
            assert a.flight_ms is None


def test_match_shape():
    r = sim(4)
    ev = r.events
    assert isinstance(ev[0], Kickoff)
    assert ev[0].period_id == 1
    assert ev[0].clock_ms == 0
    ends = [e for e in ev if isinstance(e, PeriodEnd)]
    assert [e.period_id for e in ends] == [1, 2]
    assert all(e.clock_ms >= 45 * 60_000 for e in ends)
    last_start = [e for e in ev if isinstance(e, StoppageStart)][-1]
    assert last_start.kind is StoppageKind.FULL_TIME


def test_half_time_then_second_half_kickoff_by_the_other_team():
    r = sim(6)
    ev = r.events
    i = next(i for i, e in enumerate(ev) if isinstance(e, StoppageStart) and e.kind is StoppageKind.HALF_TIME)
    rest = ev[i:]
    restart = next(e for e in rest if isinstance(e, Restart))
    assert restart.kind is StoppageKind.HALF_TIME
    assert restart.period_id == 2
    assert restart.clock_ms == 0
    ko = next(e for e in rest if isinstance(e, Kickoff))
    assert ko.period_id == 2
    assert ko.team_id != r.info.kickoff_team_id
    assert restart.wall_ms - ev[i].wall_ms == r.info.half_time_ms


def test_every_goal_is_followed_by_a_kickoff_of_the_conceding_team():
    r = find(lambda r: r.home_goals + r.away_goals >= 2)
    ev = r.events
    for i, e in enumerate(ev):
        if isinstance(e, StoppageStart) and e.kind is StoppageKind.GOAL:
            j = next(j for j in range(i + 1, len(ev)) if isinstance(ev[j], Restart))
            ko = ev[j + 1]
            assert isinstance(ko, Kickoff)
            assert ko.team_id == e.restart_team_id
            assert ev[j].wall_ms - e.wall_ms >= 40_000


def test_stoppages_carry_expected_dead_time_and_a_restart_team():
    r = sim(7)
    for e in r.events:
        if isinstance(e, StoppageStart) and e.kind is not StoppageKind.FULL_TIME:
            assert e.expected_dead_ms > 0
            assert e.restart_team_id in (r.info.home.team_id, r.info.away.team_id)


def test_restart_actions_match_the_stoppage():
    expected = {
        StoppageKind.THROW_IN: {"throw_in"},
        StoppageKind.GOAL_KICK: {"goal_kick"},
        StoppageKind.CORNER: {"corner_crossed", "corner_short"},
        StoppageKind.PENALTY: {"shot_penalty"},
        StoppageKind.OFFSIDE: {"freekick_short"},
        StoppageKind.FREE_KICK: {"freekick_short", "freekick_crossed", "shot_freekick"},
    }
    for seed in (1, 2, 3):
        ev = sim(seed).events
        for i, e in enumerate(ev):
            if isinstance(e, Restart) and e.kind in expected:
                nxt = next(x for x in ev[i + 1 :] if isinstance(x, Action))
                assert nxt.type in expected[e.kind], (e, nxt)


def test_red_card_reorganises_the_team_within_a_minute():
    r = find(lambda r: any(isinstance(e, Card) and e.color == "red" for e in r.events))
    reds = [e for e in r.events if isinstance(e, Card) and e.color == "red"]
    for card in reds:
        reorg = [t for t in r.truth if t.label is CauseLabel.RED_CARD_REORG and t.team_id == card.team_id]
        assert any(0 <= t.wall_ms - card.wall_ms <= 60_000 for t in reorg)
        later = {e.player_id for e in r.events if e.wall_ms > card.wall_ms and e.team_id == card.team_id
                 and isinstance(e, Action)}  # fmt: skip
        assert card.player_id not in later


def test_own_goal_counts_for_the_other_team():
    r = find(lambda r: any(isinstance(e, Action) and e.result == "owngoal" for e in r.events), range(1, 400))
    og = next(e for e in r.events if isinstance(e, Action) and e.result == "owngoal")
    nxt = next(e for e in r.events if isinstance(e, StoppageStart) and e.wall_ms >= og.wall_ms)
    assert nxt.kind is StoppageKind.GOAL
    assert nxt.restart_team_id == og.team_id


def test_penalty_sequence():
    r = find(lambda r: any(isinstance(e, StoppageStart) and e.kind is StoppageKind.PENALTY for e in r.events))
    ev = r.events
    i = next(i for i, e in enumerate(ev) if isinstance(e, StoppageStart) and e.kind is StoppageKind.PENALTY)
    award = ev[i]
    assert isinstance(award, StoppageStart)
    assert isinstance(ev[i - 1], Action)
    assert ev[i - 1].type == "foul"
    kick = next(e for e in ev[i:] if isinstance(e, Action))
    assert kick.type == "shot_penalty"
    assert kick.team_id == award.restart_team_id


def test_substitutions_happen_inside_stoppages():
    r = find(lambda r: any(isinstance(e, Substitution) for e in r.events))
    open_ = False
    for e in r.events:
        if isinstance(e, StoppageStart):
            open_ = True
        elif isinstance(e, Restart):
            open_ = False
        elif isinstance(e, Substitution):
            assert open_


def test_slice_aggregates_are_in_a_plausible_band():
    """Sanity only: the cited realism gate (G3) runs on the full season."""
    rows = [
        aggregate(sim(s).events, sim(s).info.home.team_id, sim(s).info.away.team_id) for s in range(1, 11)
    ]
    mean = lambda k: sum(getattr(r, k) for r in rows) / len(rows)  # noqa: E731
    assert 1.0 < mean("goals") < 5.0
    assert 15 < mean("shots") < 40
    assert 0.70 < mean("pass_completion") < 0.90
    assert mean("windows_15s") >= 20
