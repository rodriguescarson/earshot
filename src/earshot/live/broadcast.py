"""A live match: a stored match log played in real time, through the shared pipeline, per group.

Flow per event: the Agent Framework pipeline (Spotter -> Caller -> Checker, and the Analyst ->
claim check window run) produces checked lines for every language and mode. Each listening group
(language, mode, output, followed club/player/stat) has its own live Director; when a line's
audio is ready it is offered, and when the Director starts a line it is published to that group
with its audio URL. Ball and state messages go to every group.

It reads stored event logs (``earshot sim``) and never runs the engine or touches truth data.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from earshot.agents.analyst import AnalystResult
from earshot.agents.auditor import Auditor
from earshot.agents.claims import (
    check_claim_line,
    claim_key,
    claim_line,
    fresh,
    gate_cause,
    select_for_mode,
    verify,
    window_summary_line,
)
from earshot.agents.director import Profile
from earshot.agents.recap import Period, check_recap_line, recap_lines
from earshot.agents.state import MatchState, Roster, spoken_minute
from earshot.agents.statwatcher import Crossing, StatWatcher, check_stat_line, stat_line
from earshot.contract import (
    BallMsg,
    FocusStat,
    Language,
    Line,
    LineMsg,
    ListeningMode,
    OutputMode,
    RecapMsg,
    StateMsg,
)
from earshot.contract.ball import BallSample
from earshot.contract.events import Event, PeriodEnd, StoppageStart
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.live.audio import AudioStore
from earshot.live.audioqueue import ENCODE_WORKERS, SPEECH_WORKERS, AudioQueue, Skipped, rank
from earshot.live.director import Cut, Drop, LiveDirector, Start
from earshot.pipeline.workflow import MatchPipeline, WindowResult, WindowStep
from earshot.sim.ballpath import ball_path
from earshot.stats.danger import with_danger
from earshot.tools.snapshots import MatchView
from earshot.voice.tts import ssml_for
from earshot.world.localize import roster as local_roster

log = logging.getLogger("earshot.live")

TICK_S = 0.1
SUMMARY_GAP_MS = 10 * 60_000
BALL_EVERY_MS = 500
DAILY_SPEECH_CHARS = 150_000
MAX_GROUPS = 64
GROUP_IDLE_S = 45.0
"""A group nobody has touched for this long is dropped, so it stops costing speech."""
"""4 languages x 2 modes x 3 outputs is 24; following a club or player adds more."""
"""App-level cap (SEC1), below the 500K-characters-a-day alert that fires the kill switch."""

Send = Callable[[str, dict], Awaitable[None]]

SCRIPT_GROUPS: dict[str, tuple[Language, ...]] = {
    "latin": (Language.EN, Language.ES),
    "ar": (Language.AR,),
    "hi": (Language.HI,),
}


@dataclass(frozen=True)
class GroupKey:
    match_id: str
    language: Language
    listening_mode: ListeningMode
    output_mode: OutputMode
    club: str | None = None
    player: str | None = None
    stat: FocusStat | None = None

    @property
    def name(self) -> str:
        """Same format as the worker's negotiate (``earshot.api.negotiate.GroupKey``)."""
        parts = [
            self.match_id,
            self.language.value,
            self.listening_mode.value,
            self.output_mode.value,
            self.club or "-",
            self.player or "-",
            self.stat.value if self.stat else "-",
        ]
        return ".".join(parts)

    @classmethod
    def parse(cls, name: str) -> GroupKey:
        m, lang, mode, out, club, player, stat = name.split(".")
        return cls(
            m,
            Language(lang),
            ListeningMode(mode),
            OutputMode(out),
            None if club == "-" else club,
            None if player == "-" else player,
            None if stat == "-" else FocusStat(stat),
        )


@dataclass
class Group:
    key: GroupKey
    director: LiveDirector
    aired_claims: dict[tuple[str, str, str], int] = field(default_factory=dict)
    summaries: dict[str, int] = field(default_factory=dict)
    """Fallback summary text -> when it last aired (never the same sentence within ten minutes)."""
    last_seen: float = field(default_factory=time.monotonic)


class WindowAnalyst(Protocol):
    def run(self, view: MatchView, window_id: str, expected_dead_ms: int) -> AnalystResult: ...


class GroupError(ValueError):
    """A group this match will not create (unknown club or player, or the cap is reached)."""


@dataclass
class SpeechBudget:
    limit: int = DAILY_SPEECH_CHARS
    day: str = ""
    used: int = 0

    def allow(self, chars: int) -> bool:
        today = datetime.now(UTC).date().isoformat()
        if today != self.day:
            self.day, self.used = today, 0
        if self.used + chars > self.limit:
            return False
        self.used += chars
        return True


class LiveMatch:
    def __init__(
        self,
        info: MatchInfo,
        events: list[Event],
        clubs: list[ClubInfo],
        players: list[PlayerInfo],
        *,
        audio: AudioStore,
        send: Send,
        audio_base: str = "/audio",
        analyst: WindowAnalyst | None = None,
        speed: float = 1.0,
        cached_only: Callable[[], bool] = lambda: False,
    ) -> None:
        self.info, self.events = info, events
        self.cached_only = cached_only
        self.audio, self.send, self.audio_base = audio, send, audio_base.rstrip("/")
        self.speechq = AudioQueue(SPEECH_WORKERS)
        self.encodeq = AudioQueue(ENCODE_WORKERS)
        """Speech and encoding, each in rank order on threads of its own (``earshot.live.audioqueue``)."""
        self._publishing: Counter[str] = Counter()
        """Serve keys that publishing is waiting on: their encode-ahead job is never skipped as stale."""
        self.speed = speed
        self.budget = SpeechBudget()
        self.ball: list[BallSample] = with_danger(ball_path(events), info.home.team_id)
        self.groups: dict[str, Group] = {}
        self.state = MatchState.start(info)
        self.roster = Roster.build(info, clubs, players)
        self.now_ms = 0
        self.started = time.monotonic()
        self.finished = False
        self.views: dict[Language, tuple[list[ClubInfo], list[PlayerInfo]]] = {}
        targets_all = [(lang, mode) for lang in Language for mode in ListeningMode]
        self.pipelines: dict[str, MatchPipeline] = {}
        for script, langs in SCRIPT_GROUPS.items():
            if script == "latin":
                c, p = clubs, players
            else:
                table = local_roster(langs[0])
                by_c = {x.team_id: x for x in table.clubs}
                by_p = {x.player_id: x for x in table.players}
                c = [by_c.get(x.team_id, x) for x in clubs]
                p = [by_p.get(x.player_id, x) for x in players]
            for lang in langs:
                self.views[lang] = (c, p)
            targets = [t for t in targets_all if t[0] in langs]
            self.pipelines[script] = MatchPipeline.from_agents(
                info, c, p, targets, analyst if script == "latin" else None
            )
        self.watcher = StatWatcher(info.home.team_id, info.away.team_id)
        self.audited: dict[tuple[str, str], bool] = {}
        self.seen: list[Event] = []
        self._tasks: set[asyncio.Task] = set()

    # ----- groups --------------------------------------------------------------------------------

    def join(self, key: GroupKey) -> Group:
        """Join (or create) a group. Raises ``GroupError`` for a group this match cannot serve."""
        g = self.groups.get(key.name)
        if g is None:
            self._check(key)
            profile = Profile(
                listening_mode=key.listening_mode,
                output_mode=key.output_mode,
                club=key.club,
                player=key.player,
                stat=key.stat,
            )
            g = Group(key, LiveDirector(profile))
            self.groups[key.name] = g
            self._spawn(self._send_state(g))
        g.last_seen = time.monotonic()
        return g

    def _check(self, key: GroupKey) -> None:
        # Every new group costs speech, so only groups this match can actually serve are created.
        if key.match_id != self.info.match_id:
            raise GroupError("not the live match")
        if key.club is not None and key.club not in {self.info.home.team_id, self.info.away.team_id}:
            raise GroupError("unknown club")
        if key.player is not None and key.player not in self.roster.players:
            raise GroupError("unknown player")
        if key.stat is not None and key.listening_mode is not ListeningMode.ANALYST:
            raise GroupError("following a stat needs Analyst mode")
        if len(self.groups) >= MAX_GROUPS:
            raise GroupError("too many listening groups")

    def prune(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for name, g in list(self.groups.items()):
            if now - g.last_seen > GROUP_IDLE_S:
                del self.groups[name]

    def summary(self) -> dict[str, Any]:
        names = {c.team_id: c.short_name for c in self.views[Language.EN][0]}
        return {
            "match_id": self.info.match_id,
            "home": {"team_id": self.info.home.team_id, "name": names[self.info.home.team_id]},
            "away": {"team_id": self.info.away.team_id, "name": names[self.info.away.team_id]},
            "home_score": self.state.home_score,
            "away_score": self.state.away_score,
            "minute": spoken_minute(self.state.period_id, self.state.clock_ms),
            "status": self.state.status,
            "finished": self.finished,
            "groups": len(self.groups),
        }

    # ----- the loop --------------------------------------------------------------------------------

    def match_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000 * self.speed)

    async def run(self) -> None:
        self.started = time.monotonic()
        for g in list(self.groups.values()):
            await self._send_state(g)
        i, last_ball = 0, -BALL_EVERY_MS
        feeder: asyncio.Task | None = None
        pending: list[Event] = []
        while not self.finished:
            self.now_ms = self.match_ms()
            while i < len(self.events) and self.events[i].wall_ms <= self.now_ms:
                pending.append(self.events[i])
                i += 1
            if pending and (feeder is None or feeder.done()):
                batch, pending = pending, []
                feeder = asyncio.create_task(self._push_all(batch, final=i >= len(self.events)))
            if self.now_ms - last_ball >= BALL_EVERY_MS:
                last_ball = self.now_ms
                await self._send_ball()
            self.prune()
            for g in list(self.groups.values()):
                for act in g.director.tick(self.now_ms):
                    if isinstance(act, Start):
                        self._spawn(self._publish_line(g, act))
                    elif isinstance(act, Drop):
                        log.debug("dropped (%s) %s", act.reason, act.line.text)
            if i >= len(self.events) and not pending and (feeder is None or feeder.done()):
                self.finished = all(
                    not g.director.queue and g.director.current is None for g in self.groups.values()
                )
            await asyncio.sleep(TICK_S)

    async def _push_all(self, batch: list[Event], final: bool) -> None:
        for n, ev in enumerate(batch):
            last = final and n == len(batch) - 1
            before = self.state
            self.state = self.state.apply(ev, self.roster)
            self.seen.append(ev)
            for c in self.watcher.push(ev):
                self._stat_lines(c)
            if isinstance(ev, PeriodEnd) and ev.period_id in (1, 2):
                self._recaps("half_time" if ev.period_id == 1 else "full_time", ev)
            # The moment run only: the Analyst's window runs on its own task, so a card shown just
            # after a foul is not held behind it (Stream 2's ET11 measured 4.4 s of delay otherwise).
            results = [
                (script, p, await p.push_moment(ev, final=last)) for script, p in self.pipelines.items()
            ]
            if (before.home_score, before.away_score, before.status) != (
                self.state.home_score,
                self.state.away_score,
                self.state.status,
            ):
                for g in list(self.groups.values()):
                    self._spawn(self._send_state(g))
            for script, p, (moment, wstep) in results:
                for line in moment.lines:
                    for g in self._groups_for(line.language, line.listening_mode):
                        self._spawn(self._offer(g, line))
                # Claims are language-neutral: only the Latin pipeline (the one with the Analyst) runs
                # windows, and _window_lines renders them for every group in its own language.
                if wstep is not None and script == "latin":
                    assert isinstance(ev, StoppageStart)
                    await self._start_window(p, wstep, ev)

    async def _start_window(self, pipeline: MatchPipeline, wstep: WindowStep, ev: StoppageStart) -> None:
        """Start a window run without waiting for it (a virtual clock may override this to run inline)."""
        self._spawn(self._run_window(pipeline, wstep, ev))

    async def _run_window(self, pipeline: MatchPipeline, wstep: WindowStep, ev: StoppageStart) -> None:
        window = await pipeline.run_window(wstep)
        if window is not None:
            self._window_lines(ev, window)

    def _groups_for(self, language: Language, mode: ListeningMode) -> list[Group]:
        return [
            g for g in self.groups.values() if g.key.language is language and g.key.listening_mode is mode
        ]

    def _window_lines(self, ev: StoppageStart, window: WindowResult) -> None:
        for g in list(self.groups.values()):
            clubs, players = self.views[g.key.language]
            view = MatchView.of(self.info, clubs, players, self.events, ev.wall_ms + 1)
            window_id = f"{self.info.match_id}-win-{ev.event_id}"
            checked = [(gate_cause(c, view)[0], verify(c, view)) for c in window.accepted]
            ok = fresh([(c, k) for c, k in checked if k.ok], g.aired_claims, ev.wall_ms)
            lines: list[Line] = []
            for c, k in select_for_mode(ok, g.key.listening_mode):
                ln = claim_line(
                    c,
                    k,
                    view=view,
                    window_id=window_id,
                    language=g.key.language,
                    mode=g.key.listening_mode,
                    wall_ms=self.now_ms,
                    deadline_wall_ms=ev.wall_ms + ev.expected_dead_ms,
                )
                if check_claim_line(ln, c, view).ok:
                    g.aired_claims[claim_key(c)] = ev.wall_ms
                    lines.append(ln)
            if not lines:
                fb = window_summary_line(
                    view,
                    window_id=window_id,
                    language=g.key.language,
                    mode=g.key.listening_mode,
                    wall_ms=self.now_ms,
                    deadline_wall_ms=ev.wall_ms + ev.expected_dead_ms,
                )
                if (
                    fb is not None
                    and ev.wall_ms - g.summaries.get(fb.text, -SUMMARY_GAP_MS) >= SUMMARY_GAP_MS
                ):
                    g.summaries[fb.text] = ev.wall_ms
                    lines.append(fb)
            for ln in lines:
                self._spawn(self._offer(g, ln))

    def _view(self, language: Language, as_of_ms: int) -> MatchView:
        clubs, players = self.views[language]
        return MatchView.of(self.info, clubs, players, self.seen, as_of_ms)

    def _stat_lines(self, c: Crossing) -> None:
        """A stat crossing goes only to groups that follow that stat (Analyst mode), after the Checker."""
        for g in list(self.groups.values()):
            if g.key.stat is not c.stat or g.key.listening_mode is not ListeningMode.ANALYST:
                continue
            view = self._view(g.key.language, c.wall_ms + 1)
            line = stat_line(c, view, g.key.language)
            verdict = check_stat_line(line, view)
            if verdict.ok:
                self._spawn(self._offer(g, line))
            else:
                log.warning("stat line blocked: %s (%s)", line.text, verdict.reason)

    def _recaps(self, period: Period, ev: PeriodEnd) -> None:
        """One recap per language and mode present, shared by every group with that pair."""
        built: dict[tuple[Language, ListeningMode], list[Line]] = {}
        for g in list(self.groups.values()):
            pair = (g.key.language, g.key.listening_mode)
            if pair not in built:
                view = self._view(pair[0], ev.wall_ms + 1)
                lines = recap_lines(view, period, pair[0], pair[1], wall_ms=self.now_ms)
                ok = [ln for ln in lines if check_recap_line(ln, view, period).ok]
                if len(ok) != len(lines):
                    log.warning("recap %s %s: %d lines blocked", period, pair, len(lines) - len(ok))
                built[pair] = ok
            lines = built[pair]
            if not lines:
                continue
            msg = RecapMsg(
                period=period, text=" ".join(ln.text for ln in lines), lines=[ln.text for ln in lines]
            )
            self._spawn(self.send(g.key.name, msg.model_dump(mode="json")))
            if g.key.output_mode is OutputMode.SCREEN_READER:
                continue  # the screen shows it under Recaps; it never goes through the live region
            for ln in lines:
                self._spawn(self._offer(g, ln))

    def _audit_ok(self, line: Line) -> bool:
        """The independent auditor over the events so far; a line with any finding never airs."""
        key = (line.line_id, line.text)
        ok = self.audited.get(key)
        if ok is None:
            clubs, players = self.views[line.language]
            findings = Auditor(self.info, self.seen, clubs, players).audit(line, ssml_for(line))
            ok = not findings
            self.audited[key] = ok
            if findings:
                log.warning("auditor blocked %s: %s", line.text, findings[0].detail)
        return ok

    async def _offer(self, g: Group, line: Line) -> None:
        if not self._audit_ok(line):
            return
        if g.key.output_mode is OutputMode.SCREEN_READER:
            duration = int(len(line.text) / 14 * 1000) + 300  # the reader speaks it; estimate its airtime
        else:
            cached = await asyncio.to_thread(self.audio.cached_ms, line)
            if cached is not None:
                duration = cached
            elif self.cached_only() or not self.budget.allow(len(line.text)):
                log.warning("speech unavailable (cached-only or daily budget): %s airs as text", line.line_id)
                duration = int(len(line.text) / 14 * 1000) + 300
            else:
                try:
                    duration = await self.speechq.run(
                        f"warm:{self.audio.speech_key(line)}", rank(line), self.audio.warm, line
                    )
                except Exception as e:  # speech failed (e.g. the kill switch): air it as text (DR-5)
                    log.warning("speech failed for %s (%s): airs as text", line.line_id, e)
                    duration = int(len(line.text) / 14 * 1000) + 300
        for act in g.director.offer(line, duration, self.match_ms()):
            if isinstance(act, Cut):
                log.debug("cut %s", act.line.text)
        if g.key.output_mode is not OutputMode.SCREEN_READER:
            # Encode while the line waits in the Director's queue; publishing awaits the same job.
            self._spawn(self._encode_ahead(line, g.key.output_mode))

    @staticmethod
    def _serve_key(line: Line, output: OutputMode) -> str:
        return f"serve:{line.line_id}:{output.value}"

    async def _encode_ahead(self, line: Line, output: OutputMode) -> None:
        key = self._serve_key(line, output)

        def wanted() -> bool:  # a stale line will never air: leave the CPU to lines that will
            # A line the Director started before its deadline is still wanted after it: publishing
            # waits on this job (otherwise the skip reached publishing and the line aired as text).
            return line.never_drop or self._publishing[key] > 0 or self.match_ms() <= line.deadline_wall_ms

        try:
            await self.encodeq.run(
                key, rank(line), self.audio.serve, line, output, synthesize=False, wanted=wanted
            )
        except Skipped:
            pass
        except Exception as e:  # publishing tries again and airs text if it fails too
            log.debug("encode ahead failed for %s: %s", line.line_id, e)

    async def _publish_line(self, g: Group, start: Start) -> None:
        key = self._serve_key(start.line, g.key.output_mode)
        self._publishing[key] += 1
        try:
            served = await self.encodeq.run(
                key, rank(start.line), self.audio.serve, start.line, g.key.output_mode,
                synthesize=not self.cached_only(),
            )  # fmt: skip
        except Exception as e:  # speech or encoding failed: the line still airs, as text (DR-5)
            log.warning("audio failed for %s (%s): airs as text", start.line.line_id, e)
            served = None
        finally:
            self._publishing[key] -= 1
            if not self._publishing[key]:
                del self._publishing[key]
        msg = LineMsg(
            id=start.line.line_id,
            priority=start.line.priority,
            preempt=start.line.preempt,
            never_drop=start.line.never_drop,
            audio_url=f"{self.audio_base}/{served.name}" if served else None,
            text=start.line.text,
            ssml_hash=served.name[:24] if served else "",
            deadline_ms=start.line.deadline_wall_ms,
            voice_role=start.line.voice_role,
            late=start.late,
            pan=getattr(served, "pan", 0.0) if served else 0.0,
            codec=getattr(served, "codec", "m4a") if served else "m4a",
        )
        await self.send(g.key.name, msg.model_dump(mode="json"))

    async def _send_state(self, g: Group) -> None:
        st = self.state
        status = {"before_kickoff": "before_kickoff", "half_time": "half_time", "full_time": "full_time"}.get(
            st.status, "live"
        )
        msg = StateMsg(
            home_score=st.home_score,
            away_score=st.away_score,
            clock_ms=st.clock_ms,
            period=1 if st.period_id == 1 else 2,
            status=status,  # type: ignore[arg-type]
        )
        await self.send(
            g.key.name, {**msg.model_dump(mode="json"), "minute": spoken_minute(st.period_id, st.clock_ms)}
        )

    async def _send_ball(self) -> None:
        if not self.groups:
            return
        sample = next((b for b in reversed(self.ball) if b.wall_ms <= self.now_ms), None)
        if sample is None:
            return
        msg = BallMsg(clock_ms=sample.clock_ms, x=sample.x, y=sample.y, danger=sample.danger or 0.0)
        payload = {**msg.model_dump(mode="json"), "in_play": sample.in_play}
        for g in list(self.groups.values()):
            await self.send(g.key.name, payload)

    def ball_now(self) -> BallSample | None:
        return next((b for b in reversed(self.ball) if b.wall_ms <= self.now_ms), None)

    def _spawn(self, coro: Awaitable[None]) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # joined before the match loop started: run() sends state then
            coro.close()  # type: ignore[attr-defined]
            return
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
