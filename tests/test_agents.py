"""Spotter, Caller, Checker and Director over the committed engine fixture."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from earshot.agents.caller import Caller
from earshot.agents.catalog import NUMERIC_SLOTS, literal_violations, load, number_words_in, placeholders
from earshot.agents.checker import check_caller_line
from earshot.agents.director import Director, Profile
from earshot.agents.spotter import Spotter
from earshot.agents.state import MatchState, Roster, spoken_minute
from earshot.contract import Event, Language, Line, LineSource, ListeningMode, MomentKind
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo

FIXTURE = Path(__file__).parent.parent / "fixtures" / "clip-demo.json"
EV = TypeAdapter(Event)


@pytest.fixture(scope="module")
def run():
    d = json.loads(FIXTURE.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, [ClubInfo(**c) for c in d["clubs"]], [PlayerInfo(**p) for p in d["players"]])
    clip = d["clip"]
    state = replace(
        MatchState.start(match),
        period_id=clip["period_id"],
        home_score=clip["home_score_at_start"],
        away_score=clip["away_score_at_start"],
        status="live",
    )
    spotter, caller = Spotter(roster), Caller(roster)
    by_id, out = {}, []
    for raw in d["events"]:
        ev = EV.validate_python(raw)
        by_id[ev.event_id] = ev
        before, state = state, state.apply(ev, roster)
        for m in spotter.observe(ev, before, state):
            trigger = by_id[m.event_ids[0]]
            for mode in ListeningMode:
                for ln in caller.lines(m, trigger, state, Language.EN, mode):
                    out.append((m, trigger, state, ln))
    return roster, out


def test_every_caller_line_passes_the_checker(run):
    roster, out = run
    assert out
    for m, ev, st, ln in out:
        v = check_caller_line(ln, m, ev, st, roster)
        assert v.ok, (ln.text, v.reason)


def test_goal_is_never_drop_and_preempts(run):
    _, out = run
    goals = [ln for m, _, _, ln in out if m.kind in GOALS]
    assert goals
    assert all(ln.never_drop and ln.preempt and ln.priority == 100 for ln in goals)


def test_shot_and_save_become_one_line(run):
    _, out = run
    saves = [ln for m, _, _, ln in out if m.kind is MomentKind.SAVE]
    assert saves
    assert all(len(ln.player_ids) == 2 for ln in saves)


GOALS = frozenset({MomentKind.GOAL, MomentKind.OWN_GOAL})


def _first_goal(run):
    _, out = run
    return next((m, ev, st, ln) for m, ev, st, ln in out if m.kind in GOALS)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("slots", "wrong_score", "slot mismatch"),
        ("slots", "digit_in_name", "slot mismatch"),
        ("text", "edited", "text is not the template rendering"),
        ("template_id", "unknown", "unknown template"),
    ],
)
def test_planted_defects_are_blocked(run, field, value, reason):
    roster, _ = run
    m, ev, st, ln = _first_goal(run)
    if value == "wrong_score":
        bad = ln.model_copy(update={"slots": {**ln.slots, "home_score": ln.slots["home_score"] + 1}})
    elif value == "digit_in_name":
        bad = ln.model_copy(update={"slots": {**ln.slots, "player": ln.slots["player"] + " 9"}})
    elif value == "edited":
        bad = ln.model_copy(update={"text": ln.text.replace("!", ", the third!", 1)})
        assert bad.text != ln.text
    else:
        bad = ln.model_copy(update={"template_id": "goal.99"})
    v = check_caller_line(bad, m, ev, st, roster)
    assert not v.ok
    assert reason in v.reason


@pytest.mark.parametrize("lang", list(Language))
def test_templates_carry_no_numeric_literals(lang):
    assert literal_violations(lang) == []


@pytest.mark.parametrize("lang", [Language.ES, Language.AR, Language.HI])
def test_every_language_has_the_same_templates_and_slots_as_english(lang):
    en, other = load(Language.EN), load(lang)
    assert set(other.templates) == set(en.templates)
    for tid, modes in en.templates.items():
        for mode, tmpl in modes.items():
            assert placeholders(other.templates[tid][mode]) == placeholders(tmpl), (lang, tid, mode)
    for key in ("zone", "side", "stoppage", "var_subject"):
        assert set(other.lexicon[key]) == set(en.lexicon[key])


@pytest.mark.parametrize(
    ("lang", "text", "hit"),
    [
        (Language.AR, "بطاقة صفراء", False),
        (Language.AR, "يكمل وثلاثة", True),
        (Language.HI, "ज़बरदस्त शॉट", False),
        (Language.HI, "दस खिलाड़ी", True),
        (Language.ES, "un cañonazo", False),
        (Language.EN, "down to ten", True),
    ],
)
def test_number_words_match_whole_words_only(lang, text, hit):
    assert bool(number_words_in(lang, text)) is hit


@pytest.mark.parametrize("lang", list(Language))
def test_every_caller_line_passes_the_checker_in_every_language(lang):
    d = json.loads(FIXTURE.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, [ClubInfo(**c) for c in d["clubs"]], [PlayerInfo(**p) for p in d["players"]])
    state = replace(MatchState.start(match), period_id=2, home_score=2, away_score=2, status="live")
    spotter, caller, by_id, n = Spotter(roster), Caller(roster), {}, 0
    for raw in d["events"]:
        ev = EV.validate_python(raw)
        by_id[ev.event_id] = ev
        before, state = state, state.apply(ev, roster)
        for m in spotter.observe(ev, before, state):
            for mode in ListeningMode:
                for ln in caller.lines(m, by_id[m.event_ids[0]], state, lang, mode):
                    v = check_caller_line(ln, m, by_id[m.event_ids[0]], state, roster)
                    assert v.ok, (ln.text, v.reason)
                    n += 1
    assert n > 40


def test_casual_never_says_a_number_except_score_minute_and_fast_shots(run):
    _, out = run
    for _m, _, _, ln in out:
        if ln.listening_mode is ListeningMode.CASUAL:
            numeric = {k for k in ln.slots if isinstance(ln.slots[k], int | float)}
            assert numeric <= {"home_score", "away_score", "speed", "minute", "count"}, ln.text
            if "speed" in numeric:
                assert ln.slots["speed"] >= 100


def _line(i, prio, wall, *, preempt=False, never=False, ttl=3000, est=2000):
    cat = load(Language.EN)
    tmpl = "foul.1"
    return Line(
        line_id=f"l{i}",
        match_id="m",
        moment_id=f"mo{i}",
        source=LineSource.CALLER,
        language=Language.EN,
        listening_mode=ListeningMode.CASUAL,
        template_id=tmpl,
        slots={"player": "X"},
        text=cat.render(tmpl, ListeningMode.CASUAL, {"player": "X"}),
        priority=prio,
        preempt=preempt,
        never_drop=never,
        wall_ms=wall,
        deadline_wall_ms=wall + ttl,
        est_ms=est,
    )


def test_director_cuts_only_for_clearly_more_important_lines():
    d = Director(Profile())
    aired = d.run(
        [_line(1, 15, 0, est=5000), _line(2, 80, 1000, preempt=True), _line(3, 85, 1200, preempt=True)]
    )
    assert aired[0].cut_at_ms is not None  # build-up cut by the shot
    shot = next(a for a in aired if a.line.line_id == "l2")
    assert shot.cut_at_ms is None  # a save does not cut a shot (margin 15)


def test_director_drops_stale_but_never_a_never_drop_line():
    d = Director(Profile())
    aired = d.run(
        [_line(1, 50, 0, est=10_000), _line(2, 20, 100, ttl=1000), _line(3, 100, 200, never=True, ttl=1000)]
    )
    ids = [a.line.line_id for a in aired]
    assert "l2" not in ids
    assert "l3" in ids
    assert any(x.line.line_id == "l2" and x.reason == "stale" for x in d.dropped)
    late = next(a for a in aired if a.line.line_id == "l3")
    assert late.late


def test_followed_club_wins_ties():
    a, b = _line(1, 30, 0), _line(2, 30, 0)
    a = a.model_copy(update={"club_tags": ["kes"]})
    b = b.model_copy(update={"club_tags": ["cop"]})
    d = Director(Profile(club="cop"))
    aired = d.run([a, b])
    assert aired[0].line.line_id == "l2"


@pytest.mark.parametrize(
    ("period", "clock", "said"),
    [(1, 0, "1"), (2, 16 * 60_000 + 10_000, "62"), (1, 46 * 60_000, "45+2"), (2, 0, "46")],
)
def test_spoken_minute(period, clock, said):
    assert spoken_minute(period, clock) == said


@pytest.mark.parametrize("lang", list(Language))
def test_casual_templates_speak_only_the_score_the_minute_and_fast_shots(lang):
    # Pre-registered (EVALUATION.md, "Casual vs Analyst"): Casual airs no number other than the score,
    # the minute and shot speeds of 100 km/h or more (the Caller only fills speed then).
    import json
    import string
    from pathlib import Path

    allowed = {"home_score", "away_score", "minute", "speed"}
    path = Path(__file__).parent.parent / "src/earshot/agents/templates" / f"{lang.value}.json"
    bad = []
    for tid, modes in json.loads(path.read_text())["templates"].items():
        casual = modes.get("casual")
        if casual is None:
            continue
        names = {f for _, f, _, _ in string.Formatter().parse(casual) if f}
        if (names & NUMERIC_SLOTS) - allowed:
            bad.append(tid)
    assert bad == []
