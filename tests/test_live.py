"""Live mode: the incremental Director and a fast, fully local match run (no Azure, no speech)."""

import asyncio
from dataclasses import dataclass

import pytest

from earshot.agents.catalog import load
from earshot.agents.director import Profile
from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.live.broadcast import GROUP_IDLE_S, MAX_GROUPS, GroupError, GroupKey, LiveMatch
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

    def speech_key(self, line):
        return line.line_id

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


def real_lines(match26, k=2):
    """Real Caller lines (no numbers, so no event-log context needed), retimed to the test clock."""
    from earshot.agents.catalog import NUMERIC_SLOTS
    from earshot.eval.planted import _caller_items

    r, clubs, players = match26
    out = []
    for ln, *_ in _caller_items(r.events, r.info, clubs, players, Language.EN, ListeningMode.CASUAL):
        if not set(ln.slots) & NUMERIC_SLOTS and ln.text not in {x.text for x in out}:
            out.append(
                ln.model_copy(update={"line_id": f"l{len(out) + 1}", "wall_ms": 0, "deadline_wall_ms": 3000})
            )
        if len(out) == k:
            return out
    raise AssertionError("not enough lines")


def test_the_auditor_blocks_a_made_up_line_before_it_airs(match26):
    r, _, _ = match26
    m, _ = _match(match26, FakeAudio())
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    asyncio.run(m._offer(g, _line(1, 15, 0)))  # player "X" is not in the reviewed roster
    assert g.director.queue == []
    asyncio.run(m._offer(g, real_lines(match26, 1)[0]))
    assert len(g.director.queue) == 1


def test_cached_only_mode_makes_no_speech_calls_and_airs_text(match26):
    r, _, _ = match26
    audio = FakeAudio(cached={"l1"})
    m, sent = _match(match26, audio, cached_only=lambda: True)
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    lines = real_lines(match26)

    async def go():
        for ln in lines:
            await m._offer(g, ln)
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
    first, second = real_lines(match26)
    asyncio.run(m._offer(g, first))
    assert m.budget.used == 0
    asyncio.run(m._offer(g, second))
    assert audio.synthesized == []  # over budget: text only
    assert len(g.director.queue) == 2  # both reached the Director: the test is not vacuous


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


def test_groups_nobody_listens_to_are_dropped(match26):
    r, _, _ = match26
    m, _ = _match(match26, FakeAudio())
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    m.prune(g.last_seen + GROUP_IDLE_S - 1)
    assert g.key.name in m.groups
    m.prune(g.last_seen + GROUP_IDLE_S + 1)
    assert g.key.name not in m.groups


def test_stat_lines_reach_followers_and_recaps_reach_every_group(match26):
    from earshot.contract import FocusStat, PeriodEnd

    r, _, _ = match26
    m, sent = _match(match26, FakeAudio())
    mid = r.info.match_id
    follow = m.join(
        GroupKey(mid, Language.EN, ListeningMode.ANALYST, OutputMode.APP_VOICE, stat=FocusStat.SHOT_SPEED)
    )
    plain = m.join(GroupKey(mid, Language.EN, ListeningMode.ANALYST, OutputMode.APP_VOICE))
    reader = m.join(GroupKey(mid, Language.AR, ListeningMode.CASUAL, OutputMode.SCREEN_READER))
    with pytest.raises(GroupError):
        m.join(GroupKey(mid, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE, stat=FocusStat.DANGER))
    half = next(e for e in r.events if isinstance(e, PeriodEnd) and e.period_id == 1)

    async def go():
        for ev in r.events:
            if ev.wall_ms > half.wall_ms:
                break
            m.seen.append(ev)
            for c in m.watcher.push(ev):
                m._stat_lines(c)
        m._recaps("half_time", half)
        await asyncio.gather(*list(m._tasks))

    asyncio.run(go())
    queued = {g.key.name: [ln.source.value for ln, _ in g.director.queue] for g in (follow, plain, reader)}
    assert "statwatcher" in queued[follow.key.name]
    assert "statwatcher" not in queued[plain.key.name]
    assert "recap" in queued[plain.key.name]
    assert "recap" not in queued[reader.key.name]  # screen-reader mode: the Recaps section only
    recaps = {g: msg for g, msg in sent if msg["kind"] == "recap"}
    assert set(recaps) == {follow.key.name, plain.key.name, reader.key.name}
    assert recaps[reader.key.name]["text"].startswith("ملخص الاستراحة")


def test_a_slow_web_pubsub_never_stalls_the_match_loop():
    import time

    from earshot.live.app import Hub

    class SlowWps:
        def __init__(self):
            self.sent: list[str] = []

        def send_to_group(self, group, message, content_type):
            time.sleep(0.5)
            self.sent.append(message["kind"])

    wps = SlowWps()
    hub = Hub(wps=wps)
    q: asyncio.Queue[str] = asyncio.Queue()
    hub.subscribers["g"].add(q)

    async def go():
        t = time.perf_counter()
        for kind in ("ball", "line", "ball", "state"):
            await hub.send("g", {"kind": kind})
        took = time.perf_counter() - t
        await asyncio.sleep(1.2)
        return took

    took = asyncio.run(go())
    assert took < 0.05
    assert q.qsize() == 4  # SSE gets everything, at once
    assert wps.sent == ["line", "state"]  # ball samples never go to Web PubSub


def test_a_speech_failure_still_airs_the_line_as_text(match26):
    # Found when the kill switch cut speech off (Stream 2, 2026-10-16): warm() raised and the line
    # was never offered. It now reaches the Director and airs without audio.
    class BrokenAudio(FakeAudio):
        def warm(self, line):
            raise RuntimeError("403 from the speech service")

        def serve(self, line, output, *, synthesize=True):
            raise RuntimeError("403 from the speech service")

    r, _, _ = match26
    m, sent = _match(match26, BrokenAudio())
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    line = real_lines(match26, 1)[0]

    async def go():
        await m._offer(g, line)
        for act in g.director.tick(m.match_ms()):
            if isinstance(act, Start):
                await m._publish_line(g, act)

    asyncio.run(go())
    aired = [msg for _, msg in sent if msg["kind"] == "line"]
    assert [msg["text"] for msg in aired] == [line.text]
    assert aired[0]["audio_url"] is None


def test_a_started_line_is_not_skipped_as_stale_while_publishing_waits_for_its_encode(match26):
    # Found in the worker's logs (load test, 0.25 vCPU, 2026-10-17): the Director started a line
    # before its deadline, publishing joined its encode-ahead job still queued behind another
    # encode, the deadline passed meanwhile, and the job's staleness skip aired the line as text.
    import threading

    r, _, _ = match26
    line = real_lines(match26, 1)[0]
    m, sent = _match(match26, FakeAudio(cached={line.line_id}))
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    clock = [0]
    m.match_ms = lambda: clock[0]  # type: ignore[method-assign]

    async def go():
        gate = threading.Event()
        busy = asyncio.ensure_future(m.encodeq.run("busy", (0, 0, 0), gate.wait))
        await asyncio.sleep(0)  # the other encode holds the only slot
        await m._offer(g, line)
        await asyncio.sleep(0)  # the encode-ahead job is queued
        start = next(a for a in g.director.tick(clock[0]) if isinstance(a, Start))
        publish = asyncio.ensure_future(m._publish_line(g, start))
        await asyncio.sleep(0)
        clock[0] = line.deadline_wall_ms + 1  # the deadline passes while the job waits
        gate.set()
        await asyncio.gather(busy, publish)

    asyncio.run(go())
    aired = [msg for _, msg in sent if msg["kind"] == "line"]
    assert [msg["text"] for msg in aired] == [line.text]
    assert aired[0]["audio_url"] is not None
    assert m.encodeq.skipped == 0


def test_a_stale_line_nobody_is_publishing_is_still_skipped(match26):
    import threading

    r, _, _ = match26
    line = real_lines(match26, 1)[0]
    m, _ = _match(match26, FakeAudio(cached={line.line_id}))
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))
    clock = [0]
    m.match_ms = lambda: clock[0]  # type: ignore[method-assign]

    async def go():
        gate = threading.Event()
        busy = asyncio.ensure_future(m.encodeq.run("busy", (0, 0, 0), gate.wait))
        await asyncio.sleep(0)
        await m._offer(g, line)
        await asyncio.sleep(0)
        clock[0] = line.deadline_wall_ms + 1
        gate.set()
        await busy
        for _ in range(20):  # let the encode-ahead task take its turn
            await asyncio.sleep(0.01)

    asyncio.run(go())
    assert m.encodeq.skipped == 1


def test_lines_with_the_same_speech_share_one_synthesis(match26):
    # A moment often reads the same in Casual and Analyst: one speech call, not one per line.
    import time as _time

    class SlowAudio(FakeAudio):
        def speech_key(self, line):
            return line.text

        def warm(self, line):
            _time.sleep(0.2)
            return super().warm(line)

    r, _, _ = match26
    line = real_lines(match26, 1)[0]
    twin = line.model_copy(update={"line_id": "l-twin"})
    audio = SlowAudio()
    m, _ = _match(match26, audio)
    g = m.join(GroupKey(r.info.match_id, Language.EN, ListeningMode.CASUAL, OutputMode.APP_VOICE))

    async def go():
        await asyncio.gather(m._offer(g, line), m._offer(g, twin))

    asyncio.run(go())
    assert len(audio.synthesized) == 1
