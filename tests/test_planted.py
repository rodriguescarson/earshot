"""The planted-defect harness: the Analyst-JSON point here; the Caller points once Stream 1's agents exist."""

import importlib.util
from functools import cache

import pytest

from earshot.contract.line import Language, ListeningMode
from earshot.eval import planted
from earshot.sim.match import simulate
from earshot.world.clubs import world

POS = {p.player_id: p.position for p in world().players.values()}


@cache
def matches(n: int = 4):
    return [(r.events, r.info) for r in (simulate(s) for s in range(1, n + 1))]


def clubs_of(info):
    w = world()
    return [w.club(info.home.team_id).info(), w.club(info.away.team_id).info()]


def players_of(info):
    w = world()
    return [p.info() for t in (info.home.team_id, info.away.team_id) for p in w.club(t).players]


def test_analyst_json_defects_are_all_blocked_and_clean_claims_pass():
    s = planted.analyst_json_point(matches(), POS).summary()
    assert s["defects_injected"] > 100
    assert s["recall"] == 1.0
    assert set(s["per_class"]) == set(planted.CLAIM_DEFECTS)
    assert s["clean"] > 300
    assert s["false_block_rate"] == 0.0


def test_injection_is_reproducible():
    a = planted.analyst_json_point(matches(2), POS, seed=9).summary()
    b = planted.analyst_json_point(matches(2), POS, seed=9).summary()
    assert a == b


agents = pytest.mark.skipif(
    importlib.util.find_spec("earshot.agents") is None, reason="Stream 1 agents not merged"
)


@agents
def test_slot_defects_are_blocked_by_the_checker():
    s = planted.slot_values_point(
        matches(2), clubs_of, players_of, Language.EN, ListeningMode.ANALYST
    ).summary()
    assert s["defects_injected"] > 20
    assert s["recall"] == 1.0, s
    assert s["false_block_rate"] == 0.0, s


@agents
def test_roster_defects_pass_the_checker_by_design():
    s = planted.roster_point(matches(2), clubs_of, players_of, Language.EN, ListeningMode.ANALYST).summary()
    assert s["clean"] > 0
    # The Checker reads the same (corrupted) roster, so it cannot see the swap: the auditor's job.
    assert s["recall"] in (None, 0.0), s


@agents
def test_template_numerals_are_blocked_and_names_in_fixed_text_are_not():
    s = planted.template_point(matches(2), clubs_of, players_of, Language.EN, ListeningMode.ANALYST, rate=0.5)
    s = s.summary()
    per = s["per_class"]
    assert per["stray_numeral"]["recall"] == 1.0, s
    assert per["name_in_fixed_text"]["recall"] == 0.0, s  # by design: only the auditor can catch it
    assert s["false_block_rate"] == 0.0, s
