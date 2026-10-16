"""StatWatcher (CEO-7) and recaps (CEO-2): every line recomputes from the events, in every language."""

import pytest

from earshot.agents.director import Director, Profile
from earshot.agents.recap import Period, check_recap_line, recap_lines, studio
from earshot.agents.statwatcher import StatWatcher, check_stat_line, stat_line
from earshot.contract import FocusStat, Language, ListeningMode, PeriodEnd
from earshot.tools.snapshots import MatchView

SEEDS = (26, 3, 11)


def views(seed):
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    r = simulate(seed)
    teams = {r.info.home.team_id, r.info.away.team_id}
    out = {}
    for lang in Language:
        t = roster(lang)
        clubs = [c for c in t.clubs if c.team_id in teams]
        players = [p for p in t.players if p.team_id in teams]
        out[lang] = MatchView.of(r.info, clubs, players, r.events, r.events[-1].wall_ms + 1)
    return r, out


@pytest.fixture(scope="module")
def matches():
    return [views(s) for s in SEEDS]


@pytest.fixture(scope="module")
def crossings(matches):
    out = []
    for r, vs in matches:
        w = StatWatcher(r.info.home.team_id, r.info.away.team_id)
        out += [(c, vs) for ev in r.events for c in w.push(ev)]
    return out


def test_every_followable_stat_crosses_somewhere(crossings):
    assert {c.stat for c, _ in crossings} == set(FocusStat)


def test_every_stat_line_recomputes_in_every_language(crossings):
    for c, vs in crossings[::7]:
        for lang, view in vs.items():
            ln = stat_line(c, view, lang)
            v = check_stat_line(ln, view)
            assert v.ok, (ln.text, v.reason)


def test_planted_stat_defects_are_rejected(crossings):
    c, vs = next((c, vs) for c, vs in crossings if c.stat is FocusStat.PRESS_HEIGHT)
    view = vs[Language.EN]
    ln = stat_line(c, view, Language.EN)
    bumped = {**ln.slots, "after": int(ln.slots["after"]) + 3}
    assert not check_stat_line(ln.model_copy(update={"slots": bumped}), view).ok
    assert not check_stat_line(ln.model_copy(update={"wall_ms": 60_000}), view).ok
    other = next(t for t in (view.info.home.team_id, view.info.away.team_id) if t != c.team_id)
    assert not check_stat_line(ln.model_copy(update={"club_tags": [other]}), view).ok


def test_windowed_stats_do_not_repeat_within_five_minutes(crossings):
    seen: dict = {}
    for c, vs in crossings:
        if c.stat in (FocusStat.SHOT_SPEED, FocusStat.PASS_DIFFICULTY):
            continue
        key = (vs[Language.EN].match_id, c.stat, c.team_id)
        assert c.wall_ms - seen.get(key, -(10**9)) >= 5 * 60_000
        seen[key] = c.wall_ms


def test_stat_lines_air_only_for_followers_in_analyst_mode(crossings):
    c, vs = next((c, vs) for c, vs in crossings if c.stat is FocusStat.SHOT_SPEED)
    ln = stat_line(c, vs[Language.EN], Language.EN)

    def aired(profile):
        return [a.line.line_id for a in Director(profile).run([ln])]

    assert aired(Profile(ListeningMode.ANALYST, stat=FocusStat.SHOT_SPEED)) == [ln.line_id]
    assert aired(Profile(ListeningMode.ANALYST, stat=FocusStat.POSSESSION)) == []
    assert aired(Profile(ListeningMode.ANALYST)) == []
    assert aired(Profile(ListeningMode.CASUAL, stat=FocusStat.SHOT_SPEED)) == []


def _half_time_view(r, view):
    end = next(e for e in r.events if isinstance(e, PeriodEnd) and e.period_id == 1)
    return MatchView.of(
        view.info, list(view.clubs.values()), list(view.players.values()), r.events, end.wall_ms + 1
    )


def test_recaps_check_in_every_language_and_mode(matches):
    for r, vs in matches:
        for lang, view in vs.items():
            periods: list[tuple[Period, MatchView]] = [
                ("half_time", _half_time_view(r, view)),
                ("full_time", view),
            ]
            for period, v in periods:
                for mode in ListeningMode:
                    lines = recap_lines(v, period, lang, mode, wall_ms=v.as_of_wall_ms)
                    assert lines[0].template_id == f"recap.{period}.1"
                    for ln in lines:
                        verdict = check_recap_line(ln, v, period)
                        assert verdict.ok, (ln.text, verdict.reason)


def test_recap_score_matches_the_goals_and_planted_defects_fail(matches):
    _, vs = matches[0]
    view = vs[Language.EN]
    s = studio(view, "full_time", Language.EN, ListeningMode.ANALYST, wall_ms=view.as_of_wall_ms)
    head = s["lines"][0]["slots"]
    goals = s["facts"]["goals"]
    home = view.info.home.team_id
    assert head["home_score"] == sum(1 for g in goals if g["team_id"] == home)
    assert head["away_score"] == len(goals) - head["home_score"]
    ln = recap_lines(view, "full_time", Language.EN, ListeningMode.ANALYST, wall_ms=view.as_of_wall_ms)[0]
    wrong = {**ln.slots, "home_score": int(ln.slots["home_score"]) + 1}
    assert not check_recap_line(ln.model_copy(update={"slots": wrong}), view, "full_time").ok
    assert not check_recap_line(ln, view, "half_time").ok
