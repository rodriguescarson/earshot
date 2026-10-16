"""The shared, listener-neutral match pipeline as Microsoft Agent Framework workflows (eng ET7).

An Agent Framework workflow is a bounded run, not a live process, so the pipeline runs one
workflow per event and one per dead-ball window. The worker owns the match state: each run
receives an immutable ``MatchState`` snapshot (before and after the event) and returns lines or
claims. The Director, which applies per-listener rules, sits outside the workflow.

    moment run, per event:  EventStep -> [Spotter] -> MomentBatch -> [Caller] -> LineBatch
                                       -> [Checker] -> MomentResult
    window run, per stoppage expected to last 15 s or more:
                            WindowStep -> [WindowStats] -> WindowStats -> [Analyst] -> ClaimsProposed
                                       -> [ClaimCheck] -> WindowResult

The executors wrap Stream 1's plain classes (``earshot.agents``): ``Spotter.observe``,
``Caller.lines`` and ``check_caller_line``. They are typed here as protocols, so the wiring has no
import-time dependency on them; ``MatchPipeline.from_agents`` builds the real ones.

API checked against the installed agent-framework-core 1.22.0 source: ``Executor`` subclasses with
``@handler`` methods taking ``(message, ctx: WorkflowContext[Out, WorkflowOut])``,
``WorkflowBuilder(start_executor=...)``, ``add_edge``, ``build``, ``await workflow.run(message)``
and ``WorkflowRunResult.get_outputs()``. One built workflow is reused for every run of a match;
runs are sequential (the framework rejects concurrent runs on one instance).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Never, Protocol

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from earshot.contract.claim import AnalystResponse, Claim, Metric
from earshot.contract.events import Action, Event, StoppageKind, StoppageStart
from earshot.contract.line import Language, Line, ListeningMode
from earshot.contract.moment import Moment
from earshot.sim.stoppages import median_dead_ms
from earshot.stats.claims import verify_claim, window_values
from earshot.stats.metrics import Window

ANALYSIS_MIN_EXPECTED_MS = 15_000
LOOKBACK_MS = 10 * 60_000
MAX_CLAIMS_PER_CLUB = 3
MAX_CITED = 12


# ----- executor protocols (implemented by earshot.agents) ---------------------------------------


class SpotterLike(Protocol):
    def observe(self, ev: Event, before: Any, after: Any) -> list[Moment]: ...
    def flush(self) -> list[Moment]: ...


class CallerLike(Protocol):
    def lines(
        self, moment: Moment, ev: Event, state: Any, language: Language, mode: ListeningMode
    ) -> list[Line]: ...


CheckFn = Callable[[Line, Moment, Event, Any], tuple[bool, str]]
"""(line, moment, trigger event, state) -> (ok, reason)."""

AnalystFn = Callable[["WindowStats"], Awaitable[AnalystResponse | None]]
StateStep = Callable[[Any, Event], Any]
"""(state, event) -> new state; the worker's fold over the event stream."""


# ----- messages -----------------------------------------------------------------------------------


class Shared[T]:
    """A read-only reference shared, not copied, between workflow messages.

    The framework deep-copies every message an executor receives (for its ``executor_invoked``
    event, agent-framework-core ``_executor.py``). The match history and the roster positions are
    immutable by contract, so copying them on every run would only make a match O(n^2).
    """

    __slots__ = ("value",)

    def __init__(self, value: T) -> None:
        self.value = value

    def __deepcopy__(self, memo: dict) -> Shared[T]:
        return self

    def __copy__(self) -> Shared[T]:
        return self


@dataclass(frozen=True)
class EventStep:
    ev: Event
    before: Any
    after: Any
    seen: Shared[Mapping[str, Event]]
    """Every event so far by id (read-only): a moment may rest on an earlier event, e.g. a held shot."""
    final: bool = False
    """True on the last event of the match: the Spotter flushes anything it is holding."""


@dataclass(frozen=True)
class MomentBatch:
    step: EventStep
    moments: tuple[Moment, ...]


@dataclass(frozen=True)
class LineBatch:
    step: EventStep
    moments: tuple[Moment, ...]
    candidates: tuple[tuple[Moment, Line], ...]


@dataclass(frozen=True)
class MomentResult:
    event_id: str
    moments: tuple[Moment, ...] = ()
    lines: tuple[Line, ...] = ()
    """Lines that passed the Checker, for every configured language and listening mode."""
    blocked: tuple[tuple[Line, str], ...] = ()


@dataclass(frozen=True)
class WindowStep:
    stoppage: StoppageStart
    state: Any
    events: Shared[tuple[Event, ...]]
    """Everything up to and including the stoppage_start: the Analyst sees nothing later."""
    home_id: str
    away_id: str
    positions: Shared[Mapping[str, str]]


@dataclass(frozen=True)
class TeamWindow:
    team_id: str
    start_wall_ms: int
    end_wall_ms: int
    values: Mapping[Metric, tuple[float | None, float | None]]
    """(previous 10 minutes, last 10 minutes) for every metric."""
    event_ids: tuple[str, ...]
    """The team's most recent on-ball events in the window (for citing)."""


@dataclass(frozen=True)
class WindowStats:
    window_id: str
    step: WindowStep
    timeout_ms: int
    teams: tuple[TeamWindow, ...]


@dataclass(frozen=True)
class ClaimsProposed:
    stats: WindowStats
    response: AnalystResponse | None
    failure: str | None


@dataclass(frozen=True)
class WindowResult:
    window_id: str
    stoppage_event_id: str
    accepted: tuple[Claim, ...]
    rejected: tuple[tuple[Claim, str], ...]
    fallback: str | None
    """"window_summary" when no claim survives (timeout, error, empty, all rejected); else None."""
    analyst_failure: str | None
    stats: WindowStats = field(repr=False)


def analyst_timeout_ms(kind: StoppageKind) -> int:
    """Design: the median dead time for the stoppage type minus 4 s, kept between 3 s and 8 s."""
    return max(3_000, min(8_000, median_dead_ms(kind) - 4_000))


def opens_window(ev: Event) -> bool:
    return (
        isinstance(ev, StoppageStart)
        and ev.expected_dead_ms >= ANALYSIS_MIN_EXPECTED_MS
        and ev.kind not in (StoppageKind.HALF_TIME, StoppageKind.FULL_TIME)
    )


# ----- moment workflow ----------------------------------------------------------------------------


class SpotterExecutor(Executor):
    def __init__(self, spotter: SpotterLike) -> None:
        super().__init__(id="spotter")
        self.spotter = spotter

    @handler
    async def spot(self, step: EventStep, ctx: WorkflowContext[MomentBatch, MomentResult]) -> None:
        moments = list(self.spotter.observe(step.ev, step.before, step.after))
        if step.final:
            moments += self.spotter.flush()
        if moments:
            await ctx.send_message(MomentBatch(step, tuple(moments)))
        else:
            await ctx.yield_output(MomentResult(step.ev.event_id))


class CallerExecutor(Executor):
    def __init__(self, caller: CallerLike, targets: Sequence[tuple[Language, ListeningMode]]) -> None:
        super().__init__(id="caller")
        self.caller = caller
        self.targets = tuple(targets)

    @handler
    async def call(self, batch: MomentBatch, ctx: WorkflowContext[LineBatch]) -> None:
        out: list[tuple[Moment, Line]] = []
        for m in batch.moments:
            trigger = batch.step.seen.value[m.event_ids[0]]
            for language, mode in self.targets:
                out += [(m, ln) for ln in self.caller.lines(m, trigger, batch.step.after, language, mode)]
        await ctx.send_message(LineBatch(batch.step, batch.moments, tuple(out)))


class CheckerExecutor(Executor):
    def __init__(self, check: CheckFn) -> None:
        super().__init__(id="checker")
        self.check = check

    @handler
    async def check_lines(self, batch: LineBatch, ctx: WorkflowContext[Never, MomentResult]) -> None:
        ok: list[Line] = []
        blocked: list[tuple[Line, str]] = []
        for m, ln in batch.candidates:
            passed, reason = self.check(ln, m, batch.step.seen.value[m.event_ids[0]], batch.step.after)
            if passed:
                ok.append(ln)
            else:
                blocked.append((ln, reason))
        await ctx.yield_output(MomentResult(batch.step.ev.event_id, batch.moments, tuple(ok), tuple(blocked)))


def moment_workflow(spotter: SpotterLike, caller: CallerLike, check: CheckFn,
                    targets: Sequence[tuple[Language, ListeningMode]]):  # fmt: skip
    s, c, k = SpotterExecutor(spotter), CallerExecutor(caller, targets), CheckerExecutor(check)
    return WorkflowBuilder(start_executor=s, name="earshot-moment").add_edge(s, c).add_edge(c, k).build()


# ----- window workflow ----------------------------------------------------------------------------


def team_window(step: WindowStep, team_id: str) -> TeamWindow:
    """What the Analyst gets for one club: every claim metric over the last 10 minutes and the 10 before.

    The same numbers the MCP ``team_window`` tool returns and the claim check recomputes.
    """
    end = step.stoppage.wall_ms
    start = end - LOOKBACK_MS
    events, positions = step.events.value, step.positions.value
    values = {m: window_values(m, events, team_id, start, end, step.home_id, positions) for m in Metric}
    w = Window(team_id, start, end)
    recent = [e.event_id for e in events if isinstance(e, Action) and e.team_id == team_id and e in w]
    return TeamWindow(team_id, start, end, values, tuple(recent[-MAX_CITED:]))


class WindowStatsExecutor(Executor):
    def __init__(self, timeout_scale: float = 1.0) -> None:
        super().__init__(id="window_stats")
        self.timeout_scale = timeout_scale

    @handler
    async def stats(self, step: WindowStep, ctx: WorkflowContext[WindowStats]) -> None:
        teams = (team_window(step, step.home_id), team_window(step, step.away_id))
        wid = f"{step.stoppage.event_id}-w"
        timeout = int(analyst_timeout_ms(step.stoppage.kind) * self.timeout_scale)
        await ctx.send_message(WindowStats(wid, step, timeout, teams))


class AnalystExecutor(Executor):
    """Calls the Analyst (a Foundry model, Stream 1) inside the window's time budget."""

    def __init__(self, analyst: AnalystFn) -> None:
        super().__init__(id="analyst")
        self.analyst = analyst

    @handler
    async def analyse(self, stats: WindowStats, ctx: WorkflowContext[ClaimsProposed]) -> None:
        try:
            response = await asyncio.wait_for(self.analyst(stats), timeout=stats.timeout_ms / 1000)
            failure = None if response is not None else "empty"
        except TimeoutError:
            response, failure = None, "timeout"
        except Exception as exc:  # every Analyst failure falls back to the window summary, logged
            response, failure = None, f"error: {type(exc).__name__}: {exc}"
        await ctx.send_message(ClaimsProposed(stats, response, failure))


class ClaimCheckExecutor(Executor):
    """Recomputes every metric claim from the events (``earshot.stats.claims``); cause labels pass
    through unchecked, to be graded offline."""

    def __init__(self) -> None:
        super().__init__(id="claim_check")

    @handler
    async def check_claims(self, proposed: ClaimsProposed, ctx: WorkflowContext[Never, WindowResult]) -> None:
        step = proposed.stats.step
        accepted: list[Claim] = []
        rejected: list[tuple[Claim, str]] = []
        per_club: dict[str, int] = {}
        claims = proposed.response.claims if proposed.response else []
        for c in sorted(claims, key=lambda c: (c.team_id, c.rank)):
            if c.team_id not in (step.home_id, step.away_id):
                rejected.append((c, "unknown team"))
                continue
            if per_club.get(c.team_id, 0) >= MAX_CLAIMS_PER_CLUB:
                rejected.append((c, "more than 3 claims for one club"))
                continue
            v = verify_claim(
                c, step.events.value, step.home_id, step.positions.value, step.stoppage.wall_ms + 1
            )
            if v.ok:
                accepted.append(c)
                per_club[c.team_id] = per_club.get(c.team_id, 0) + 1
            else:
                rejected.append((c, v.reason))
        fallback = None if accepted else "window_summary"
        await ctx.yield_output(
            WindowResult(
                proposed.stats.window_id,
                step.stoppage.event_id,
                tuple(accepted),
                tuple(rejected),
                fallback,
                proposed.failure,
                proposed.stats,
            )
        )


def window_workflow(analyst: AnalystFn, timeout_scale: float = 1.0):
    """``timeout_scale`` shrinks the Analyst's budget for an accelerated clock (1.0 = real time)."""
    s, a, k = WindowStatsExecutor(timeout_scale), AnalystExecutor(analyst), ClaimCheckExecutor()
    return WorkflowBuilder(start_executor=s, name="earshot-window").add_edge(s, a).add_edge(a, k).build()


async def no_analyst(stats: WindowStats) -> AnalystResponse | None:
    """The default until the Foundry Analyst is wired: no claims, so the window summary airs."""
    return None


# ----- the per-match pipeline the worker owns -----------------------------------------------------


@dataclass
class MatchPipeline:
    step_state: StateStep
    state: Any
    home_id: str
    away_id: str
    positions: Mapping[str, str]
    spotter: SpotterLike
    caller: CallerLike
    check: CheckFn
    targets: Sequence[tuple[Language, ListeningMode]]
    analyst: AnalystFn = no_analyst
    timeout_scale: float = 1.0
    events: list[Event] = field(default_factory=list)
    _seen: dict[str, Event] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._moments = moment_workflow(self.spotter, self.caller, self.check, self.targets)
        self._windows = window_workflow(self.analyst, self.timeout_scale)

    async def push(self, ev: Event, *, final: bool = False) -> tuple[MomentResult, WindowResult | None]:
        """Feed one event: one moment run, plus one window run if the event opens an analysis window."""
        before = self.state
        after = self.step_state(before, ev)
        self.events.append(ev)
        self._seen[ev.event_id] = ev
        step = EventStep(ev, before, after, Shared(self._seen), final)
        outputs = (await self._moments.run(step)).get_outputs()
        moment = outputs[0] if outputs else MomentResult(ev.event_id)
        window = None
        if opens_window(ev):
            assert isinstance(ev, StoppageStart)
            wstep = WindowStep(
                ev, after, Shared(tuple(self.events)), self.home_id, self.away_id, Shared(self.positions)
            )
            wout = (await self._windows.run(wstep)).get_outputs()
            window = wout[0] if wout else None
        self.state = after
        return moment, window

    async def replay(self, events: Sequence[Event]) -> list[tuple[MomentResult, WindowResult | None]]:
        out = []
        for i, ev in enumerate(events):
            out.append(await self.push(ev, final=i == len(events) - 1))
        return out

    @classmethod
    def from_agents(
        cls,
        info: Any,
        clubs: Sequence[Any],
        players: Sequence[Any],
        targets: Sequence[tuple[Language, ListeningMode]],
        analyst: AnalystFn = no_analyst,
    ) -> MatchPipeline:
        """Build the pipeline from Stream 1's executors (``earshot.agents``)."""
        from earshot.agents.caller import Caller  # pyright: ignore[reportMissingImports]
        from earshot.agents.checker import check_caller_line  # pyright: ignore[reportMissingImports]
        from earshot.agents.spotter import Spotter  # pyright: ignore[reportMissingImports]
        from earshot.agents.state import MatchState, Roster  # pyright: ignore[reportMissingImports]

        roster = Roster.build(info, list(clubs), list(players))

        def check(line: Line, moment: Moment, ev: Event, state: Any) -> tuple[bool, str]:
            v = check_caller_line(line, moment, ev, state, roster)
            return v.ok, v.reason

        return cls(
            step_state=lambda s, e: s.apply(e, roster),
            state=MatchState.start(info),
            home_id=info.home.team_id,
            away_id=info.away.team_id,
            positions={p.player_id: p.position for p in players},
            spotter=Spotter(roster),
            caller=Caller(roster),
            check=check,
            targets=targets,
            analyst=analyst,
        )
