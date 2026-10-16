"""Live mode: the incremental Director and a fast, fully local match run (no Azure, no speech)."""

import asyncio
from dataclasses import dataclass

import pytest

from earshot.agents.catalog import load
from earshot.agents.director import Profile
from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.live.broadcast import MAX_GROUPS, GroupError, GroupKey, LiveMatch
from earshot.live.director import Cut, Drop, LiveDirector, Start


def _line(i, prio, wall, *, preempt=False, never=False, ttl=3000):
    cat = load(Language.EN)
    return Line(
        line_id=f"l{i}", match_id="m", moment_id=f"mo{i}", source=LineSource.CALLER, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id="foul.1", slots={"player": "X"},
        text=cat.render("foul.1", ListeningMode.CASUAL, {"player": "X"}), priority=prio, preempt=preempt,
        never_drop=never, wall_ms=wall, deadline_wall_ms=wall + ttl,
    )  # fmt: skip


def test_live_director_starts_cuts_and_drops():
    d = LiveDirector(Profile())
    assert d.offer(_line(1, 15, 0), 4000, 0) == []
    started = d.tick(0)
    assert isinstance(started[0], Start)
    cut = d.offer(_line(2, 100, 1000, preempt=True, never=True), 2000, 1000)
    assert isinstance(cut[0], Cut)
    assert cut[0].line.line_id == "l1"
    d.offer(_line(3, 20, 1000, ttl=500), 1000, 1000)
    acts = d.tick(1300)
    assert any(isinstance(a, Start) and a.line.line_id == "l2" for a in acts)
    acts = d.tick(3600)
    assert any(isinstance(a, Drop) and a.line.line_id == "l3" for a in acts)


def test_group_names_round_trip():
    k = GroupKey("m00026", Language.AR, ListeningMode.ANALYST, OutputMode.ONE_EAR, club="spa")
    assert GroupKey.parse(k.name) == k
    assert k.name == "m00026.ar.analyst.one_ear.spa.-.-"


@dataclass
class FakeServed:
    name: str
    duration_ms: int


class FakeAudio:
    """Stands in for AudioStore: no speech service, durations from text length."""

    def __init__(self, cached: set[str] | None = None) -> None:
        self.cached = cached or set()
        self.synthesized: list[str] = []

    def cached_ms(self, line):
        return 1000 if line.line_id in self.cached else None

    def warm(self, line):
        self.synthesized.append(line.line_id)
        return int(len(line.text) / 14 * 1000) + 300

    def serve(self, line, output, *, synthesize=True):
        if output is OutputMode.SCREEN_READER:
            return None
        if line.line_id not in self.cached:
            if not synthesize:
                return None
            self.synthesized.append(line.line_id)
        return FakeServed(f"{line.line_id[:24]:x<24}.m4a", 1000)


@pytest.fixture(scope="module")
def match26():
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    r = simulate(26)
    teams = {r.info.home.team_id, r.info.away.team_id}
    latin = roster(Language.EN)
    clubs = [c for c in latin.clubs if c.team_id in teams]
    players = [p for p in latin.players if p.team_id in teams]
    return r, clubs, players


def _match(match26, audio, **kw):
    r, clubs, players = match26
    sent: list = []

    async def record(group, msg):
        sent.append((group, msg))

    m = LiveMatch(r.info, r.events[:50], clubs, players, audio=audio, send=record, **kw)  # type: ignore[arg-type]
    return m, sent


def test_only_groups_the_match_can_serve_are_created(match26):
    r, _, players = match26
    m, _ = _match(match26, FakeAudio())
    base = (r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE)
    m.join(GroupKey(*base, club=r.info.home.team_id, player=players[0].player_id))
    for bad in (
        GroupKey("other", *base[1:]),
        GroupKey(*base, club="nobody"),
        GroupKey(*base, player="nobody"),
    ):
        with pytest.raises(GroupError):
            m.join(bad)
    for p in players[: MAX_GROUPS - 1]:
        m.join(GroupKey(*base, player=p.player_id))
    assert len(m.groups) == min(MAX_GROUPS, len(players[: MAX_GROUPS - 1]) + 1)
    if len(m.groups) == MAX_GROUPS:
        with pytest.raises(GroupError):
            m.join(GroupKey(*base[:3], OutputMode.ONE_EAR))


def test_cached_only_mode_makes_no_speech_calls_and_airs_text(match26):
    r, _, _ = match26
    audio = FakeAudio(cached={"l1"})
    m, sent = _match(match26, audio, cached_only=lambda: True)
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))

    async def go():
        for i in (1, 2):
            await m._offer(g, _line(i, 15, 0))
        for act in g.director.tick(m.match_ms()):
            if isinstance(act, Start):
                await m._publish_line(g, act)

    asyncio.run(go())
    assert audio.synthesized == []
    urls = {msg["id"]: msg["audio_url"] for _, msg in sent if msg["kind"] == "line"}
    assert urls.get("l1") is not None  # already cached: still voiced
    assert all(u is None for k, u in urls.items() if k != "l1")


def test_cache_hits_do_not_spend_the_speech_budget(match26):
    r, _, _ = match26
    audio = FakeAudio(cached={"l1"})
    m, _ = _match(match26, audio)
    m.budget.limit = 0
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    asyncio.run(m._offer(g, _line(1, 15, 0)))
    assert m.budget.used == 0
    asyncio.run(m._offer(g, _line(2, 15, 0)))
    assert audio.synthesized == []  # over budget: text only


@pytest.mark.slow
def test_a_live_match_publishes_lines_ball_and_state_per_group():
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    r = simulate(26)
    teams = {r.info.home.team_id, r.info.away.team_id}
    latin = roster(Language.EN)
    clubs = [c for c in latin.clubs if c.team_id in teams]
    players = [p for p in latin.players if p.team_id in teams]
    sent: list[tuple[str, dict]] = []

    async def send(group, msg):
        sent.append((group, msg))

    events = [e for e in r.events if e.wall_ms <= 3 * 60_000]
    m = LiveMatch(r.info, events, clubs, players, audio=FakeAudio(), send=send, speed=10.0)  # type: ignore[arg-type]
    en = GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE)
    ar = GroupKey(r.info.match_id, Language.AR, ListeningMode.ANALYST, OutputMode.SCREEN_READER)
    m.join(en)
    m.join(ar)
    asyncio.run(asyncio.wait_for(m.run(), timeout=60))
    kinds = {(g, msg["kind"]) for g, msg in sent}
    for g in (en.name, ar.name):
        assert (g, "line") in kinds
        assert (g, "ball") in kinds
        assert (g, "state") in kinds
    ar_lines = [msg for g, msg in sent if g == ar.name and msg["kind"] == "line"]
    assert all(msg["audio_url"] is None for msg in ar_lines)  # screen-reader mode: text only
    assert any(any(0x0600 <= ord(ch) <= 0x06FF for ch in msg["text"]) for msg in ar_lines)
