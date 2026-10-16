"""The shared, listener-neutral match pipeline as Microsoft Agent Framework workflows (eng ET7).

An Agent Framework workflow is a bounded run, not a live process, so the pipeline runs one
workflow per event and one per dead-ball window. The worker owns the match state: each run
receives an immutable ``MatchState`` snapshot (before and after the event) and returns lines or
claims. The Director, which applies per-listener rules, sits outside the workflow.

    moment run, per event:  EventStep -> [Spotter] -> MomentBatch -> [Caller] -> LineBatch
                                       -> [Checker] -> MomentResult
    window run, per stoppage expected to last 15 s or more:
                            WindowStep -> [Analyst] -> ClaimsProposed -> [ClaimCheck] -> WindowResult

The executors wrap Stream 1's code (``earshot.agents``): ``Spotter.observe``, ``Caller.lines``,
``check_caller_line``, the Foundry ``Analyst`` and the claim verifier ``agents.claims.verify``.
They are typed here as protocols and callables, so the wiring has no import-time dependency on
them; ``MatchPipeline.from_agents`` builds the real ones.

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

from earshot.contract.claim import Claim
from earshot.contract.events import Event, StoppageKind, StoppageStart
from earshot.contract.line import Language, Line, ListeningMode
from earshot.contract.moment import Moment

ANALYSIS_MIN_EXPECTED_MS = 15_000
MAX_CLAIMS_PER_CLUB = 3


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

AnalystFn = Callable[["WindowStep", int], Awaitable["AnalystOutcome"]]
"""(window step, budget in ms) -> claims; Stream 1's Foundry Analyst behind ``analyst_from_agents``."""

VerifyFn = Callable[[Claim, "WindowStep"], tuple[bool, str]]
"""(claim, window step) -> (ok, reason); Stream 1's ``agents.claims.verify`` behind ``from_agents``."""
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
class AnalystOutcome:
    claims: tuple[Claim, ...]
    failure: str | None
    """None, or why no claims came back: "timeout", "error: ...", "invalid: ..."."""
    latency_ms: int = 0
    tokens: int = 0


@dataclass(frozen=True)
class ClaimsProposed:
    step: WindowStep
    budget_ms: int
    outcome: AnalystOutcome


@dataclass(frozen=True)
class WindowResult:
    window_id: str
    stoppage_event_id: str
    accepted: tuple[Claim, ...]
    rejected: tuple[tuple[Claim, str], ...]
    fallback: str | None
    """"window_summary" when no claim survives (timeout, error, empty, all rejected); else None."""
    analyst_failure: str | None
    budget_ms: int = 0
    latency_ms: int = 0


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


def analyst_budget_ms(stoppage: StoppageStart) -> int:
    """The design rule: aim to finish 4 s before the expected restart, kept between 3 s and 8 s.

    ``expected_dead_ms`` is the frozen median for the stoppage type (plus announced extras), so
    this is "the median dead time for that stoppage type minus 4 s".
    """
    return max(3_000, min(8_000, stoppage.expected_dead_ms - 4_000))


class AnalystExecutor(Executor):
    """Calls the Analyst (Stream 1's Foundry model with in-process match tools) inside the budget.

    The Analyst enforces its own budget; this executor adds an outer guard (budget + 1.5 s) so a
    hung call can never hold the window run.
    """

    def __init__(self, analyst: AnalystFn, timeout_scale: float = 1.0) -> None:
        super().__init__(id="analyst")
        self.analyst = analyst
        self.timeout_scale = timeout_scale

    @handler
    async def analyse(self, step: WindowStep, ctx: WorkflowContext[ClaimsProposed]) -> None:
        budget_ms = int(analyst_budget_ms(step.stoppage) * self.timeout_scale)
        try:
            outcome = await asyncio.wait_for(self.analyst(step, budget_ms), timeout=budget_ms / 1000 + 1.5)
        except TimeoutError:
            outcome = AnalystOutcome((), "timeout")
        except Exception as exc:  # every Analyst failure falls back to the window summary, logged
            outcome = AnalystOutcome((), f"error: {type(exc).__name__}: {exc}"[:300])
        await ctx.send_message(ClaimsProposed(step, budget_ms, outcome))


class ClaimCheckExecutor(Executor):
    """Recomputes every metric claim from the events with the shared verifier; cause labels pass
    through unchecked, to be graded offline against the truth log."""

    def __init__(self, verify: VerifyFn) -> None:
        super().__init__(id="claim_check")
        self.verify = verify

    @handler
    async def check_claims(self, proposed: ClaimsProposed, ctx: WorkflowContext[Never, WindowResult]) -> None:
        step = proposed.step
        accepted: list[Claim] = []
        rejected: list[tuple[Claim, str]] = []
        per_club: dict[str, int] = {}
        for c in sorted(proposed.outcome.claims, key=lambda c: (c.team_id, c.rank)):
            if c.team_id not in (step.home_id, step.away_id):
                rejected.append((c, "unknown team"))
                continue
            if per_club.get(c.team_id, 0) >= MAX_CLAIMS_PER_CLUB:
                rejected.append((c, "more than 3 claims for one club"))
                continue
            ok, reason = self.verify(c, step)
            if ok:
                accepted.append(c)
                per_club[c.team_id] = per_club.get(c.team_id, 0) + 1
            else:
                rejected.append((c, reason))
        failure = proposed.outcome.failure
        if failure is None and not proposed.outcome.claims:
            failure = "empty"
        await ctx.yield_output(
            WindowResult(
                window_id=f"{step.stoppage.event_id}-w",
                stoppage_event_id=step.stoppage.event_id,
                accepted=tuple(accepted),
                rejected=tuple(rejected),
                fallback=None if accepted else "window_summary",
                analyst_failure=failure,
                budget_ms=proposed.budget_ms,
                latency_ms=proposed.outcome.latency_ms,
            )
        )


def window_workflow(analyst: AnalystFn, verify: VerifyFn, timeout_scale: float = 1.0):
    """``timeout_scale`` shrinks the Analyst's budget for an accelerated clock (1.0 = real time)."""
    a, k = AnalystExecutor(analyst, timeout_scale), ClaimCheckExecutor(verify)
    return WorkflowBuilder(start_executor=a, name="earshot-window").add_edge(a, k).build()


async def no_analyst(step: WindowStep, budget_ms: int) -> AnalystOutcome:
    """The default when no Analyst is configured: no claims, so the window summary airs."""
    return AnalystOutcome((), None)


def reject_all(claim: Claim, step: WindowStep) -> tuple[bool, str]:
    """The default verifier: nothing unverified ever airs."""
    return False, "no verifier configured"


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
    verify: VerifyFn = reject_all
    timeout_scale: float = 1.0
    events: list[Event] = field(default_factory=list)
    _seen: dict[str, Event] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._moments = moment_workflow(self.spotter, self.caller, self.check, self.targets)
        self._windows = window_workflow(self.analyst, self.verify, self.timeout_scale)

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
        analyst: Any = None,
        timeout_scale: float = 1.0,
    ) -> MatchPipeline:
        """Build the pipeline from Stream 1's executors (``earshot.agents``).

        ``analyst`` is an ``earshot.agents.analyst.Analyst`` (Foundry), or None for no Analyst.
        """
        from earshot.agents.caller import Caller  # pyright: ignore[reportMissingImports]
        from earshot.agents.checker import check_caller_line  # pyright: ignore[reportMissingImports]
        from earshot.agents.claims import verify as verify_claim  # pyright: ignore[reportMissingImports]
        from earshot.agents.spotter import Spotter  # pyright: ignore[reportMissingImports]
        from earshot.agents.state import MatchState, Roster  # pyright: ignore[reportMissingImports]

        roster = Roster.build(info, list(clubs), list(players))
        names = {c.team_id: c.short_name for c in clubs}

        def check(line: Line, moment: Moment, ev: Event, state: Any) -> tuple[bool, str]:
            v = check_caller_line(line, moment, ev, state, roster)
            return v.ok, v.reason

        def verify(claim: Claim, step: WindowStep) -> tuple[bool, str]:
            v = verify_claim(claim, tool_view(step, names))
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
            analyst=analyst_from_agents(analyst, names) if analyst is not None else no_analyst,
            verify=verify,
            timeout_scale=timeout_scale,
        )


def tool_view(step: WindowStep, names: Mapping[str, str]) -> Any:
    """The match as Stream 1's in-process tools see it (``earshot.tools.match.MatchView``)."""
    from earshot.tools.match import MatchView, Teams  # pyright: ignore[reportMissingImports]

    return MatchView(
        events=step.events.value,
        teams=Teams(step.home_id, step.away_id, names),
        positions=step.positions.value,
        now_wall_ms=step.stoppage.wall_ms + 1,
    )


def analyst_from_agents(analyst: Any, names: Mapping[str, str]) -> AnalystFn:
    """Wrap ``Analyst.run`` (blocking HTTP) as an ``AnalystFn``, run off the event loop."""

    async def run(step: WindowStep, budget_ms: int) -> AnalystOutcome:
        res = await asyncio.to_thread(
            analyst.run, tool_view(step, names), f"{step.stoppage.event_id}-w", step.stoppage.expected_dead_ms
        )
        failure = "timeout" if res.timed_out else (f"error: {res.error}" if res.error else None)
        if failure is None and res.invalid:
            failure = f"invalid: {len(res.invalid)}"
        return AnalystOutcome(tuple(res.claims), failure, res.latency_ms, res.tokens)

    return run
