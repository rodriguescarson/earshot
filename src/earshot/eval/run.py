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
from collections import Counter, defaultdict
from collections.abc import Awaitable, Coroutine, Sequence
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
    """The real Analyst, with its real latency carried onto the virtual clock."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.last_ms = 0

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        t0 = time.perf_counter()
        res = self.inner.run(view, window_id, expected_dead_ms)
        self.last_ms = int((time.perf_counter() - t0) * 1000)
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
            for p in self.pipelines.values():
                p.push_moment = self._timed(p.push_moment)  # type: ignore[method-assign]

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
    real_analyst: Any | None = None,
    profiles: Sequence[EvalProfile] = PROFILES,
    windows: str = "background",
) -> dict[str, Any]:
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    t0 = time.perf_counter()
    r = simulate(seed)
    latin = roster(Language.EN)
    teams = {r.info.home.team_id, r.info.away.team_id}
    clubs = [c for c in latin.clubs if c.team_id in teams]
    players = [p for p in latin.players if p.team_id in teams]
    rng = stream(seed, "eval", "latency")
    analyst = TimedAnalyst(real_analyst) if real_analyst is not None else LatencyAnalyst(latency, rng)
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
    out["wall_s"] = round(time.perf_counter() - t0, 2)
    return out


def _safe(args: tuple[int, LatencyModel, str]) -> dict[str, Any]:
    seed, latency, windows = args
    try:
        return evaluate_match(seed, latency, windows=windows)
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
    }


def run_season(
    seeds: Sequence[int], latency: LatencyModel, workers: int = 8, windows: str = "background"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``windows``: "background" is the broadcaster as built (window runs on their own task, since
    Stream 1's 24f4efb); "inline" is the broadcaster before it, whose event feeder waited for each
    window run. Kept so that finding stays reproducible."""
    t0 = time.perf_counter()
    jobs = [(s, latency, windows) for s in seeds]
    if workers <= 1:
        matches = [_safe(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            matches = list(pool.map(_safe, jobs, chunksize=1))
    return summarize(matches, latency, time.perf_counter() - t0, workers, windows), matches
