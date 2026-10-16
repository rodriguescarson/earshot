"""The accelerated evaluation run (eng ET11): whole matches through the live broadcaster, on a virtual clock.

What is real: the engine's events, the Agent Framework pipeline (Spotter, Caller, Checker, and the
window run with the claim check), Stream 1's ``LiveMatch`` (state, per-group window lines, claim
gating) and its ``LiveDirector`` for every evaluation profile. What is modelled: time. The clock is
virtual, so a 100-minute match runs in seconds, and the parts that wait on a network draw their
latency from a ``LatencyModel``:

- ``ready_ms``: from the moment a line is offered to the moment its audio is ready (speech
  synthesis or the cache), added to the measured compute time of the pipeline for that event;
- ``analyst_ms``: one Analyst call. Window lines wait for it. With ``windows="inline"`` the event
  feeder waits for it too, as the broadcaster did before window runs moved to their own task;
- ``delivery_ms``: from the Director starting a line to the listener hearing its first byte.

The deployed load test measures these. A model built in code says so in ``source``, and results run
with a provisional model are never reported in EVALUATION.md as the evaluation.

Targets come from the event log, not from the Spotter's moments, so a moment the Spotter misses
counts against coverage:
- **Never-drop:** every goal (the ``goal`` stoppage), red card and penalty awarded must air, at any
  latency; its latency is reported.
- **Coverage:** every shot and yellow card should air within 3.0 s of the event.
A target is covered by an aired line whose moment lists the target's event (or, for a goal or a
penalty, the action just before the stoppage).

Profiles (``docs/designs/earshot.md``, evaluation profiles): P1 Casual app voice; P2 Analyst app
voice following the home club and its first-named forward; P3 Casual screen reader; P4 Analyst one
ear following shot speed; P5 Analyst app voice. If ``LiveMatch`` refuses a profile's group, the
profile runs without its focus stat and the result says so.

Entry point: ``uv run earshot evaluate``; 8 worker processes by default (ET11).
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import json
import math
import statistics
import time
import traceback
from collections import Counter, defaultdict, deque
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np

from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.contract.events import Action, Card, CardColor, Event, StoppageKind, StoppageStart
from earshot.contract.line import FocusStat
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.contract.moment import Moment
from earshot.rng import stream

TICK_MS = 100
ANALYST_TOKENS_GUESS = 6_000
"""Tokens per Analyst call before any is measured (the worker reserves the same)."""
COVERAGE_MS = 3_000
SHOT_TYPES = frozenset({"shot", "shot_penalty", "shot_freekick"})
SCORE_SLOTS = frozenset({"home_score", "away_score"})
CASUAL_NUMBERS = SCORE_SLOTS | {"minute"}
"""Casual airs no number but the score and the minute, plus shot speeds of 100 km/h or more."""
CASUAL_FAST_SHOT_KMH = 100


# ----- profiles and latency ---------------------------------------------------------------------


@dataclass(frozen=True)
class EvalProfile:
    name: str
    mode: ListeningMode
    output: OutputMode
    follow: bool = False
    stat: FocusStat | None = None


PROFILES: tuple[EvalProfile, ...] = (
    EvalProfile("P1", ListeningMode.CASUAL, OutputMode.APP_VOICE),
    EvalProfile("P2", ListeningMode.ANALYST, OutputMode.APP_VOICE, follow=True),
    EvalProfile("P3", ListeningMode.CASUAL, OutputMode.SCREEN_READER),
    EvalProfile("P4", ListeningMode.ANALYST, OutputMode.ONE_EAR, stat=FocusStat.SHOT_SPEED),
    EvalProfile("P5", ListeningMode.ANALYST, OutputMode.APP_VOICE),
)


@dataclass(frozen=True)
class LatencyModel:
    """Empirical samples (milliseconds) for each modelled wait; one is drawn per use."""

    ready_ms: tuple[int, ...]
    analyst_ms: tuple[int, ...]
    delivery_ms: tuple[int, ...]
    source: str
    """Where the samples came from: a load-test file, or 'provisional: ...' for a model built in code."""

    @property
    def provisional(self) -> bool:
        return self.source.startswith("provisional")

    @classmethod
    def load(cls, path: Path) -> LatencyModel:
        d = json.loads(path.read_text())
        return cls(
            tuple(int(x) for x in d["ready_ms"]),
            tuple(int(x) for x in d["analyst_ms"]),
            tuple(int(x) for x in d["delivery_ms"]),
            source=str(path),
        )

    @classmethod
    def provisional_default(cls) -> LatencyModel:
        # Stream 1's offline Director constant (READY_DELAY_MS, "pipeline plus cached audio"); an
        # Analyst that uses its whole budget (the worst case short of a timeout); delivery not modelled.
        from earshot.agents.director import READY_DELAY_MS

        return cls(
            (READY_DELAY_MS,),
            (),
            (0,),
            source="provisional: ready = READY_DELAY_MS, Analyst = full budget, delivery = 0",
        )


def draw(rng: np.random.Generator, samples: Sequence[int], default: int) -> int:
    return int(samples[int(rng.integers(len(samples)))]) if samples else default


# ----- targets ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    kind: str
    """goal, red_card, penalty (never-drop) or shot, yellow_card (coverage)."""
    event_id: str
    wall_ms: int
    linked: frozenset[str]

    @property
    def never_drop(self) -> bool:
        return self.kind in ("goal", "red_card", "penalty")


def targets(events: Sequence[Event]) -> list[Target]:
    out: list[Target] = []
    last_action: Action | None = None
    for ev in events:
        if isinstance(ev, Action):
            if ev.type in SHOT_TYPES:
                out.append(Target("shot", ev.event_id, ev.wall_ms, frozenset({ev.event_id})))
            last_action = ev
        elif isinstance(ev, Card):
            kind = "red_card" if ev.color is CardColor.RED else "yellow_card"
            out.append(Target(kind, ev.event_id, ev.wall_ms, frozenset({ev.event_id})))
        elif isinstance(ev, StoppageStart) and ev.kind in (StoppageKind.GOAL, StoppageKind.PENALTY):
            kind = "goal" if ev.kind is StoppageKind.GOAL else "penalty"
            linked = {ev.event_id} | ({last_action.event_id} if last_action else set())
            out.append(Target(kind, ev.event_id, ev.wall_ms, frozenset(linked)))
    return out


# ----- the virtual match ------------------------------------------------------------------------


@dataclass(frozen=True)
class AiredLine:
    line: Line
    start_ms: int
    duration_ms: int
    late: bool
    event_ids: frozenset[str]


@dataclass
class GroupLog:
    aired: list[AiredLine] = field(default_factory=list)
    dropped: Counter[str] = field(default_factory=Counter)
    cut: int = 0


class LatencyAnalyst:
    """Stands in for the Analyst in the season run: no claims, and the time a call takes."""

    def __init__(self, latency: LatencyModel, rng: np.random.Generator) -> None:
        self.latency, self.rng = latency, rng
        self.last_ms = 0

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        from earshot.agents.analyst import AnalystResult

        budget = max(3_000, min(8_000, expected_dead_ms - 4_000))  # workflow.analyst_budget_ms
        ms = draw(self.rng, self.latency.analyst_ms, budget)
        self.last_ms = min(ms, budget + 1_500)  # the workflow's outer guard
        return AnalystResult(window_id, timed_out=ms > budget, latency_ms=ms)


class TimedAnalyst:
    """The real Analyst, with its real latency carried onto the virtual clock.

    ``tokens_per_hour`` paces calls (sleeping between them, outside the measured latency) so a
    long run stays under the kill-switch alert on hourly tokens; ``max_calls`` caps a trial run.
    """

    def __init__(
        self,
        inner: Any,
        *,
        tokens_per_hour: int | None = None,
        max_calls: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner, self.tokens_per_hour, self.max_calls = inner, tokens_per_hour, max_calls
        self.clock, self.sleep = clock, sleep
        self.last_ms = 0
        self.calls: list[dict[str, Any]] = []
        self.paused_s = 0.0
        self._recent: deque[tuple[float, int]] = deque()

    def _pace(self) -> None:
        if not self.tokens_per_hour:
            return
        per_call = statistics.mean(c["tokens"] for c in self.calls) if self.calls else ANALYST_TOKENS_GUESS
        while True:
            now = self.clock()
            while self._recent and now - self._recent[0][0] >= 3_600:
                self._recent.popleft()
            if sum(t for _, t in self._recent) + per_call <= self.tokens_per_hour or not self._recent:
                return
            step = min(60.0, max(1.0, 3_600 - (now - self._recent[0][0])))
            self.paused_s += step
            self.sleep(step)

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        from earshot.agents.analyst import AnalystResult

        if self.max_calls is not None and len(self.calls) >= self.max_calls:
            self.last_ms = 0
            return AnalystResult(window_id, error="skipped: call cap")
        self._pace()
        t0 = time.perf_counter()
        res = self.inner.run(view, window_id, expected_dead_ms)
        self.last_ms = int((time.perf_counter() - t0) * 1000)
        tokens = int(getattr(res, "tokens", 0) or 0)
        self._recent.append((self.clock(), tokens))
        failure = "timeout" if getattr(res, "timed_out", False) else getattr(res, "error", None)
        self.calls.append(
            {
                "latency_ms": int(getattr(res, "latency_ms", self.last_ms) or self.last_ms),
                "tokens": tokens,
                "claims": len(getattr(res, "claims", []) or []),
                "failure": failure,
            }
        )
        return res


def _virtual_match_class():
    from earshot.live.broadcast import GroupError, GroupKey, LiveMatch
    from earshot.live.director import Cut, Drop, Start

    class VirtualMatch(LiveMatch):
        """``LiveMatch`` with its clock, audio and sends replaced; everything else runs as live."""

        def __init__(
            self,
            info: MatchInfo,
            events: list[Event],
            clubs: list[ClubInfo],
            players: list[PlayerInfo],
            *,
            latency: LatencyModel,
            rng: np.random.Generator,
            analyst: LatencyAnalyst | TimedAnalyst | None,
            windows: str = "background",
        ) -> None:
            super().__init__(
                info, events, clubs, players, audio=cast(Any, None), send=self._discard, analyst=analyst
            )
            self.latency, self.rng, self.timer = latency, rng, analyst
            self.windows_block = windows == "inline"
            self.vnow = 0
            self.push_ms = 0
            self.seq = 0
            self.offers: list[tuple[int, int, str, Line, int]] = []
            self.logs: dict[str, GroupLog] = defaultdict(GroupLog)
            self.moments: dict[str, Moment] = {}
            self.notes: list[str] = []
            self.windows: list[tuple[StoppageStart, Any]] = []
            for p in self.pipelines.values():
                p.push_moment = self._timed(p.push_moment)  # type: ignore[method-assign]
                p.run_window = self._windowed(p.run_window)  # type: ignore[method-assign]

        def _windowed(self, run_window):
            async def windowed(wstep):
                res = await run_window(wstep)
                if res is not None:
                    self.windows.append((wstep.stoppage, res))
                return res

            return windowed

        def _timed(self, push_moment):
            async def timed(ev: Event, *, final: bool = False):
                t0 = time.perf_counter()
                moment, wstep = await push_moment(ev, final=final)
                self.push_ms += int((time.perf_counter() - t0) * 1000)
                for m in moment.moments:
                    self.moments[m.moment_id] = m
                return moment, wstep

            return timed

        async def _start_window(self, pipeline, wstep, ev) -> None:
            # The live broadcaster runs windows on their own task; on the virtual clock the run is
            # awaited here and its cost lands in timer.last_ms ("inline" then holds the feeder).
            await self._run_window(pipeline, wstep, ev)

        # The live loop's time, transport and housekeeping, replaced:
        def match_ms(self) -> int:
            return self.vnow

        def prune(self, now: float | None = None) -> None:
            return None

        async def _discard(self, group: str, message: dict) -> None:
            return None

        async def _send_ball(self) -> None:
            return None

        async def _send_state(self, g) -> None:
            return None

        def _spawn(self, coro: Awaitable[None]) -> None:
            # Every coroutine spawned here completes without suspending; run it now, in order.
            c = cast(Coroutine[Any, Any, None], coro)
            try:
                c.send(None)
            except StopIteration:
                return
            c.close()
            raise RuntimeError("a virtual-clock coroutine suspended")

        async def _offer(self, g, line: Line) -> None:
            if g.key.output_mode is OutputMode.SCREEN_READER:
                duration = int(len(line.text) / 14 * 1000) + 300  # as LiveMatch estimates the reader
            else:
                duration = line.est_ms or int(len(line.text) / 14 * 1000) + 300
            wait = self.push_ms + draw(self.rng, self.latency.ready_ms, 0)
            if line.source in (LineSource.ANALYST, LineSource.FALLBACK) and self.timer is not None:
                wait += self.timer.last_ms
            self.seq += 1
            heapq.heappush(self.offers, (self.vnow + wait, self.seq, g.key.name, line, duration))

        def join_profile(self, p: EvalProfile) -> str:
            club = player = None
            if p.follow:
                club = self.info.home.team_id
                fw = [x for x in self.roster.players.values() if x.team_id == club and x.position == "FW"]
                starting = set(self.info.home.starting)
                player = next((x.player_id for x in fw if x.player_id in starting), None)
            key = GroupKey(self.info.match_id, Language.EN, p.mode, p.output, club, player, p.stat)
            try:
                self.join(key)
            except GroupError as e:
                self.notes.append(f"{p.name}: {e}; runs without its focus stat")
                key = GroupKey(self.info.match_id, Language.EN, p.mode, p.output, club, player, None)
                self.join(key)
            return key.name

        async def play(self) -> None:
            n, i, t, busy_until = len(self.events), 0, 0, 0
            while True:
                while i < n and self.events[i].wall_ms <= t and busy_until <= t:
                    ev = self.events[i]
                    i += 1
                    self.vnow, self.push_ms = max(ev.wall_ms, busy_until), 0
                    if self.timer is not None:
                        self.timer.last_ms = 0
                    await self._push_all([ev], final=i == n)
                    if self.windows_block and self.timer is not None and self.timer.last_ms:
                        busy_until = self.vnow + self.timer.last_ms  # as built: the feeder waits
                self.vnow = t
                while self.offers and self.offers[0][0] <= t:
                    ready, _, name, line, dur = heapq.heappop(self.offers)
                    g = self.groups[name]
                    for act in g.director.offer(line, dur, ready):
                        if isinstance(act, Cut):
                            self.logs[name].cut += 1
                for name, g in self.groups.items():
                    for act in g.director.tick(t):
                        if isinstance(act, Start):
                            ids = self._event_ids(act.line)
                            self.logs[name].aired.append(
                                AiredLine(act.line, act.at_ms, act.duration_ms, act.late, ids)
                            )
                        elif isinstance(act, Drop):
                            self.logs[name].dropped[act.reason] += 1
                idle = all(not g.director.queue and g.director.current is None for g in self.groups.values())
                if i >= n and not self.offers and idle:
                    return
                t += TICK_MS

        def _event_ids(self, line: Line) -> frozenset[str]:
            m = self.moments.get(line.moment_id or "")
            return frozenset(m.event_ids) if m else frozenset()

    return VirtualMatch


# ----- one match --------------------------------------------------------------------------------


def numeric_slots(line: Line) -> list[tuple[str, float]]:
    out = []
    for name, v in line.slots.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, int | float):
            out.append((name, float(v)))
        elif isinstance(v, str):
            with contextlib.suppress(ValueError):
                out.append((name, float(v)))
    return out


def casual_violations(line: Line) -> list[str]:
    bad = []
    for name, v in numeric_slots(line):
        if name in CASUAL_NUMBERS or (name == "speed" and v >= CASUAL_FAST_SHOT_KMH):
            continue
        bad.append(f"{line.template_id}:{name}={v:g}")
    return bad


def playing_minutes(events: Sequence[Event]) -> float:
    ht = [e for e in events if isinstance(e, StoppageStart) and e.kind is StoppageKind.HALF_TIME]
    end = events[-1].wall_ms
    interval = 0
    if ht:
        restart = next(
            (e.wall_ms for e in events if e.wall_ms > ht[0].wall_ms and e.period_id == 2), ht[0].wall_ms
        )
        interval = restart - ht[0].wall_ms
    return (end - interval) / 60_000


def evaluate_match(
    seed: int,
    latency: LatencyModel,
    real_analyst: TimedAnalyst | None = None,
    profiles: Sequence[EvalProfile] = PROFILES,
    windows: str = "background",
) -> dict[str, Any]:
    """One match. ``real_analyst`` (a ``TimedAnalyst`` around Stream 1's Analyst) makes real model
    calls in every analysis window and grades their cause labels; without it the Analyst is a
    latency-only stand-in. The baseline detector is graded on the same windows either way."""
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    t0 = time.perf_counter()
    r = simulate(seed)
    latin = roster(Language.EN)
    teams = {r.info.home.team_id, r.info.away.team_id}
    clubs = [c for c in latin.clubs if c.team_id in teams]
    players = [p for p in latin.players if p.team_id in teams]
    rng = stream(seed, "eval", "latency")
    analyst = real_analyst if real_analyst is not None else LatencyAnalyst(latency, rng)
    vm = _virtual_match_class()(
        r.info, list(r.events), clubs, players, latency=latency, rng=rng, analyst=analyst, windows=windows
    )
    names = {p.name: vm.join_profile(p) for p in profiles}
    asyncio.run(vm.play())
    goals = targets(r.events)
    minutes = playing_minutes(r.events)
    out: dict[str, Any] = {
        "seed": seed,
        "match_id": r.info.match_id,
        "minutes": round(minutes, 2),
        "targets": dict(Counter(t.kind for t in goals)),
        "notes": vm.notes,
        "profiles": {},
    }
    for p in profiles:
        log = vm.logs[names[p.name]]
        first: dict[str, int] = {}
        late: set[str] = set()
        for a in log.aired:
            for ev_id in a.event_ids:
                if ev_id not in first or a.start_ms < first[ev_id]:
                    first[ev_id] = a.start_ms
                    if a.late:
                        late.add(ev_id)
        hits = []
        for tg in goals:
            starts = [first[e] for e in tg.linked if e in first]
            if starts:
                lat = min(starts) - tg.wall_ms + draw(rng, latency.delivery_ms, 0)
                hits.append({"kind": tg.kind, "event_id": tg.event_id, "latency_ms": lat})
            else:
                hits.append({"kind": tg.kind, "event_id": tg.event_id, "latency_ms": None})
        nums = sum(len(numeric_slots(a.line)) for a in log.aired)
        viol = (
            [v for a in log.aired for v in casual_violations(a.line)]
            if p.mode is ListeningMode.CASUAL
            else []
        )
        out["profiles"][p.name] = {
            "group": names[p.name],
            "aired": len(log.aired),
            "dropped": dict(log.dropped),
            "cut": log.cut,
            "targets": hits,
            "numeric_slots": nums,
            "numeric_slots_per_min": round(nums / minutes, 3) if minutes else None,
            "casual_violations": viol,
            "by_source": dict(Counter(a.line.source.value for a in log.aired)),
        }
    out["explanations"] = explanations(r, players, vm.windows, real_analyst)
    out["wall_s"] = round(time.perf_counter() - t0, 2)
    return out


GRADE_KEYS = ("labels", "correct", "truth_changes", "recalled", "matches")


def explanations(
    r: Any, players: Sequence[PlayerInfo], windows: Sequence[tuple[Any, Any]], analyst: Any
) -> dict:
    """Cause-label grades for one match (counts only: they add up across matches, and the truth log
    never leaves this process)."""
    from earshot.eval.baseline_detector import predictions as baseline_predictions
    from earshot.eval.grading import Prediction, analysis_windows, grade

    mid = r.info.match_id
    truth = {mid: list(r.truth)}
    positions = {p.player_id: p.position for p in players}

    def counts(preds: list[Prediction]) -> dict[str, int]:
        g = grade(preds, truth)
        return {k: int(getattr(g, k)) for k in GRADE_KEYS}

    base = [Prediction(mid, t, w, lab) for w, t, lab in baseline_predictions(r.events, r.info, positions)]
    out: dict[str, Any] = {"windows": len(analysis_windows(r.events)), "baseline": counts(base)}
    if analyst is None or not hasattr(analyst, "calls"):
        return out
    aired = [
        Prediction(mid, c.team_id, st.wall_ms, c.cause) for st, w in windows for c in w.accepted if c.cause
    ]
    proposed = aired + [
        Prediction(mid, c.team_id, st.wall_ms, c.cause) for st, w in windows for c, _ in w.rejected if c.cause
    ]
    out |= {
        "analyst_aired": counts(aired),
        "analyst_proposed": counts(proposed),
        "window_outcomes": dict(Counter(w.analyst_failure or "claims" for _, w in windows)),
        "analyst_calls": analyst.calls,
        "analyst_paused_s": round(analyst.paused_s, 1),
    }
    return out


def real_analyst(config: dict[str, Any]) -> TimedAnalyst:
    """Stream 1's Analyst in patient mode (it waits out rate limits; the wait is not latency)."""
    from earshot.agents.analyst import Analyst

    return TimedAnalyst(
        Analyst(patient=True),
        tokens_per_hour=config.get("tokens_per_hour"),
        max_calls=config.get("max_calls"),
    )


def _safe(args: tuple[int, LatencyModel, str, dict[str, Any] | None]) -> dict[str, Any]:
    seed, latency, windows, analyst = args
    try:
        return evaluate_match(seed, latency, real_analyst(analyst) if analyst else None, windows=windows)
    except Exception:
        return {"seed": seed, "error": traceback.format_exc(limit=6)}


# ----- the season -------------------------------------------------------------------------------


def pct(xs: Sequence[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return s[k]


def summarize(
    matches: Sequence[dict[str, Any]],
    latency: LatencyModel,
    wall_s: float,
    workers: int,
    windows: str = "background",
) -> dict[str, Any]:
    ok = [m for m in matches if "error" not in m]
    failed = [
        {"seed": m["seed"], "error": m["error"].strip().splitlines()[-1]} for m in matches if "error" in m
    ]
    profiles: dict[str, Any] = {}
    names = sorted({p for m in ok for p in m["profiles"]})
    for name in names:
        rows = [m["profiles"][name] for m in ok]
        by_kind: dict[str, list[int | None]] = defaultdict(list)
        for row in rows:
            for h in row["targets"]:
                by_kind[h["kind"]].append(h["latency_ms"])
        nd = {}
        for kind in ("goal", "red_card", "penalty"):
            lats = by_kind.get(kind, [])
            aired = [x for x in lats if x is not None]
            nd[kind] = {
                "n": len(lats),
                "aired": len(aired),
                "share": round(len(aired) / len(lats), 4) if lats else None,
                "p50_ms": pct(aired, 50),
                "p95_ms": pct(aired, 95),
                "max_ms": max(aired) if aired else None,
            }
        cov = {}
        for kind in ("shot", "yellow_card"):
            lats = by_kind.get(kind, [])
            within = [x for x in lats if x is not None and x <= COVERAGE_MS]
            aired = [x for x in lats if x is not None]
            cov[kind] = {
                "n": len(lats),
                "within_3s": len(within),
                "share_within_3s": round(len(within) / len(lats), 4) if lats else None,
                "aired_at_all": len(aired),
                "p50_ms": pct(aired, 50),
                "p95_ms": pct(aired, 95),
            }
        both = [x for k in ("shot", "yellow_card") for x in by_kind.get(k, [])]
        minutes = sum(m["minutes"] for m in ok)
        violations = Counter(v.split("=")[0] for row in rows for v in row["casual_violations"])
        profiles[name] = {
            "never_drop": nd,
            "never_drop_all_aired": all(v["aired"] == v["n"] for v in nd.values()),
            "coverage": cov,
            "coverage_share_within_3s": round(
                sum(1 for x in both if x is not None and x <= COVERAGE_MS) / len(both), 4
            )
            if both
            else None,
            "numeric_slots_per_min": round(sum(r["numeric_slots"] for r in rows) / minutes, 3)
            if minutes
            else None,
            "casual_violations": dict(violations.most_common()),
            "aired_lines": sum(r["aired"] for r in rows),
            "dropped": dict(sum((Counter(r["dropped"]) for r in rows), Counter())),
        }
    expl = summarize_explanations(ok)
    p1, p5 = profiles.get("P1", {}), profiles.get("P5", {})
    ratio = None
    if p1.get("numeric_slots_per_min") and p5.get("numeric_slots_per_min") is not None:
        ratio = round(p5["numeric_slots_per_min"] / p1["numeric_slots_per_min"], 2)
    return {
        "matches": len(matches),
        "ok": len(ok),
        "failed": failed,
        "workers": workers,
        "wall_s": round(wall_s, 1),
        "median_match_s": round(statistics.median(m["wall_s"] for m in ok), 2) if ok else None,
        "latency_model": asdict(latency) | {"provisional": latency.provisional},
        "windows": windows,
        "notes": sorted({n for m in ok for n in m["notes"]}),
        "profiles": profiles,
        "analyst_vs_casual_numeric_ratio": ratio,
        "explanations": expl,
    }


def summarize_explanations(ok: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> dict[str, Any] | None:
        rows = [m["explanations"][key] for m in ok if key in m.get("explanations", {})]
        if not rows:
            return None
        c = {k: sum(r[k] for r in rows) for k in GRADE_KEYS}
        return c | {
            "accuracy": round(c["correct"] / c["labels"], 4) if c["labels"] else None,
            "recall": round(c["recalled"] / c["truth_changes"], 4) if c["truth_changes"] else None,
            "false_alarms_per_match": round((c["labels"] - c["correct"]) / c["matches"], 3)
            if c["matches"]
            else None,
        }

    calls = [c for m in ok for c in m.get("explanations", {}).get("analyst_calls", [])]
    tokens = [c["tokens"] for c in calls]
    lat = [c["latency_ms"] for c in calls if c["failure"] is None]
    return {
        "windows": sum(m.get("explanations", {}).get("windows", 0) for m in ok),
        "baseline": total("baseline"),
        "analyst_aired": total("analyst_aired"),
        "analyst_proposed": total("analyst_proposed"),
        "window_outcomes": dict(
            sum((Counter(m.get("explanations", {}).get("window_outcomes", {})) for m in ok), Counter())
        ),
        "analyst_calls": {
            "n": len(calls),
            "tokens_total": sum(tokens),
            "tokens_per_call": {
                "p50": pct(tokens, 50),
                "p95": pct(tokens, 95),
                "max": max(tokens, default=None),
            },
            "latency_ms": {"p50": pct(lat, 50), "p95": pct(lat, 95), "max": max(lat, default=None)},
            "failures": dict(Counter(c["failure"] for c in calls if c["failure"])),
            "paused_s": round(sum(m.get("explanations", {}).get("analyst_paused_s", 0) for m in ok), 1),
        }
        if calls
        else None,
    }


def run_season(
    seeds: Sequence[int],
    latency: LatencyModel,
    workers: int = 8,
    windows: str = "background",
    analyst: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``windows``: "background" is the broadcaster as built (window runs on their own task, since
    Stream 1's 24f4efb); "inline" is the broadcaster before it, whose event feeder waited for each
    window run. Kept so that finding stays reproducible.

    ``analyst``: {"tokens_per_hour": ..., "max_calls": ...} for real Analyst calls; each worker
    process gets its share of the hourly tokens."""
    t0 = time.perf_counter()
    if analyst and analyst.get("tokens_per_hour"):
        analyst = analyst | {"tokens_per_hour": analyst["tokens_per_hour"] // max(1, workers)}
    jobs = [(s, latency, windows, analyst) for s in seeds]
    if workers <= 1:
        matches = [_safe(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            matches = list(pool.map(_safe, jobs, chunksize=1))
    return summarize(matches, latency, time.perf_counter() - t0, workers, windows), matches
