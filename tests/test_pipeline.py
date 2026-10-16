"""The Agent Framework wiring (ET7): one run per event and per window, immutable snapshots in,
lines and claims out, deterministic replays. Stand-in executors cover the mechanics; the real
Stream 1 executors run in ``test_replay_with_the_real_agents`` once ``earshot.agents`` is present."""

import asyncio
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from earshot.contract.claim import Claim, Direction, Metric
from earshot.contract.events import Action, Event, StoppageKind, StoppageStart
from earshot.contract.line import Language, Line, LineSource, ListeningMode
from earshot.contract.moment import Moment, MomentKind
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.pipeline import workflow as wf
from earshot.sim.match import simulate
from earshot.stats.metrics import Window, metric
from earshot.world.clubs import world

TARGETS = [(Language.EN, ListeningMode.CASUAL), (Language.EN, ListeningMode.ANALYST)]


@dataclass(frozen=True)
class FakeState:
    n: int = 0
    last: str | None = None


def fake_step(s: FakeState, e: Event) -> FakeState:
    return FakeState(s.n + 1, e.event_id)


class FakeSpotter:
    def __init__(self):
        self.held: Event | None = None
        self.k = 0

    def observe(self, ev, before, after):
        assert after.n == before.n + 1  # the snapshot it gets is the state just after this event
        if isinstance(ev, Action) and ActionType(ev.type) in SHOT_TYPES:
            self.k += 1
            return [Moment(moment_id=f"mo{self.k}", match_id=ev.match_id, kind=MomentKind.SHOT, priority=80,
                           never_drop=False, wall_ms=ev.wall_ms, deadline_wall_ms=ev.wall_ms + 2500,
                           event_ids=[ev.event_id])]  # fmt: skip
        return []

    def flush(self):
        return []


class FakeCaller:
    def lines(self, moment, ev, state, language, mode):
        text = f"Shot at event {state.n}" if mode is ListeningMode.ANALYST else "Shot"
        return [
            Line(
                line_id=f"{moment.moment_id}-{language.value}-{mode.value}", match_id=moment.match_id,
                moment_id=moment.moment_id, source=LineSource.CALLER, language=language,
                listening_mode=mode, template_id="shot.1", slots={}, text=text, priority=80,
                wall_ms=moment.wall_ms, deadline_wall_ms=moment.deadline_wall_ms,
            )
        ]  # fmt: skip


def fake_check(line, moment, ev, state):
    # Plays the Checker: block analyst lines (they carry a digit outside a slot).
    return (False, "digit outside a slot") if any(ch.isdigit() for ch in line.text) else (True, "")


THRESHOLD = {Metric.PRESS_HEIGHT: 5.0, Metric.TEMPO: 2.0, Metric.DIRECTNESS: 0.08, Metric.WIDTH: 2.0,
             Metric.LINE_HEIGHT: 4.0, Metric.POSSESSION_SHARE: 0.08}  # fmt: skip
SPAN = 10 * 60_000


def values(step: wf.WindowStep, team: str, m: Metric, start: int, end: int):
    ev, pos = step.events.value, step.positions.value
    span = end - start
    before = metric(m, ev, Window(team, start - span, start), step.home_id, pos)
    after = metric(m, ev, Window(team, start, end), step.home_id, pos)
    return before, after


def fake_verify(claim: Claim, step: wf.WindowStep) -> tuple[bool, str]:
    """A stand-in for agents.claims.verify with the same rules."""
    if claim.window_end_ms > step.stoppage.wall_ms + 1:
        return False, "window ends in the future"
    before, after = values(step, claim.team_id, claim.metric, claim.window_start_ms, claim.window_end_ms)
    if before is None or after is None:
        return False, "not enough evidence"
    if (after > before) != (claim.direction is Direction.UP) or abs(after - before) < THRESHOLD[claim.metric]:
        return False, "direction wrong or below threshold"
    seen = {e.event_id for e in step.events.value}
    if any(i not in seen for i in claim.cited_event_ids):
        return False, "cited event does not exist"
    return True, ""


def pipeline(seed: int, analyst=wf.no_analyst, timeout_scale: float = 1.0, verify=fake_verify):
    r = simulate(seed)
    pos = {p.player_id: p.position for p in world().players.values()}
    p = wf.MatchPipeline(fake_step, FakeState(), r.info.home.team_id, r.info.away.team_id, pos, FakeSpotter(),
                         FakeCaller(), fake_check, TARGETS, analyst, verify, timeout_scale)  # fmt: skip
    return r, p


def test_one_run_per_event_with_lines_checked():
    r, p = pipeline(3)
    results = asyncio.run(p.replay(r.events))
    assert len(results) == len(r.events)
    shots = sum(1 for e in r.events if isinstance(e, Action) and ActionType(e.type) in SHOT_TYPES)
    moments = [m for res, _ in results for m in res.moments]
    assert len(moments) == shots
    aired = [ln for res, _ in results for ln in res.lines]
    blocked = [b for res, _ in results for b in res.blocked]
    assert len(aired) == shots
    assert all(ln.listening_mode is ListeningMode.CASUAL for ln in aired)
    assert len(blocked) == shots
    assert all(reason == "digit outside a slot" for _, reason in blocked)
    assert p.state.n == len(r.events)


def test_window_runs_only_on_qualifying_stoppages_and_see_only_the_past():
    r, p = pipeline(3)
    seen_lengths: list[tuple[int, int]] = []

    async def spy(step: wf.WindowStep, budget_ms: int):
        idx = next(i for i, e in enumerate(r.events) if e.event_id == step.stoppage.event_id)
        seen_lengths.append((len(step.events.value), idx + 1))
        assert 3_000 <= budget_ms <= 8_000
        return wf.AnalystOutcome((), None)

    p = wf.MatchPipeline(
        fake_step, FakeState(), p.home_id, p.away_id, p.positions, FakeSpotter(), FakeCaller(), fake_check,
        TARGETS, spy, fake_verify,
    )  # fmt: skip
    results = asyncio.run(p.replay(r.events))
    windows = [w for _, w in results if w is not None]
    expected = [e for e in r.events if wf.opens_window(e)]
    assert len(windows) == len(expected) > 20
    assert all(a == b for a, b in seen_lengths)
    assert all(w.fallback == "window_summary" and w.analyst_failure == "empty" for w in windows)


def test_replay_is_deterministic():
    r, a = pipeline(5)
    _, b = pipeline(5)
    ra, rb = asyncio.run(a.replay(r.events)), asyncio.run(b.replay(r.events))
    assert [x.lines for x, _ in ra] == [x.lines for x, _ in rb]
    assert [w and w.accepted for _, w in ra] == [w and w.accepted for _, w in rb]


def honest_claim(step: wf.WindowStep) -> wf.AnalystOutcome:
    """An Analyst that only states what the numbers show (the strongest change per club)."""
    end = step.stoppage.wall_ms
    claims = []
    for team in (step.home_id, step.away_id):
        best = None
        for m in THRESHOLD:
            before, after = values(step, team, m, end - SPAN, end)
            if before is None or after is None:
                continue
            size = abs(after - before) / THRESHOLD[m]
            if size >= 1 and (best is None or size > best[0]):
                best = (size, m, before, after)
        cited = [
            e.event_id for e in step.events.value if e.team_id == team and end - SPAN <= e.wall_ms < end
        ][-3:]
        if best and cited:
            _, m, before, after = best
            direction = Direction.UP if after > before else Direction.DOWN
            claims.append(
                Claim(
                    team_id=team, metric=m, direction=direction, window_start_ms=end - SPAN,
                    window_end_ms=end, value_before=round(before, 3), value_after=round(after, 3),
                    cited_event_ids=cited,
                )
            )  # fmt: skip
    return wf.AnalystOutcome(tuple(claims), None)


def liar(step: wf.WindowStep) -> wf.AnalystOutcome:
    good = honest_claim(step)
    if not good.claims:
        return good
    c = good.claims[0]
    flipped = c.model_copy(
        update={"direction": Direction.DOWN if c.direction is Direction.UP else Direction.UP}
    )
    ghost = c.model_copy(update={"cited_event_ids": ["no-such-event"]})
    future = c.model_copy(
        update={"window_end_ms": c.window_end_ms + 600_000, "window_start_ms": c.window_start_ms + 600_000}
    )
    return wf.AnalystOutcome((flipped, ghost, future), None)


def run_windows(seed, analyst):
    async def wrapped(step, budget_ms):
        return analyst(step)

    r, p = pipeline(seed, wrapped)
    return [w for _, w in asyncio.run(p.replay(r.events)) if w is not None]


def test_true_claims_are_accepted_and_false_ones_rejected():
    honest = run_windows(7, honest_claim)
    assert sum(len(w.accepted) for w in honest) > 10
    assert all(not w.rejected for w in honest)
    lies = run_windows(7, liar)
    assert sum(len(w.accepted) for w in lies) == 0
    reasons = " ".join(r for w in lies for _, r in w.rejected)
    assert "direction wrong" in reasons
    assert "does not exist" in reasons
    assert "future" in reasons
    assert all(w.fallback == "window_summary" for w in lies)


def test_analyst_timeout_and_errors_fall_back_to_the_window_summary():
    async def slow(step, budget_ms):
        await asyncio.sleep(budget_ms / 1000 + 1.6)  # past the outer guard (budget + 1.5 s)
        return wf.AnalystOutcome((), None)

    async def broken(step, budget_ms):
        raise RuntimeError("model unavailable")

    r, p = pipeline(2, slow, timeout_scale=0.01)
    ev = next(e for e in r.events if wf.opens_window(e))
    idx = r.events.index(ev)
    for e in r.events[:idx]:
        asyncio.run(p.push(e))
    _, w = asyncio.run(p.push(ev))
    assert w is not None
    assert w.analyst_failure == "timeout"
    assert w.fallback == "window_summary"
    r, p = pipeline(2, broken)
    res = [w for _, w in asyncio.run(p.replay(r.events[: idx + 1])) if w is not None]
    failure = res[-1].analyst_failure
    assert failure is not None
    assert failure.startswith("error: RuntimeError")


def test_analyst_budget_follows_the_design_rule():
    r = simulate(3)
    starts = [e for e in r.events if isinstance(e, StoppageStart) and wf.opens_window(e)]
    for e in starts:
        assert wf.analyst_budget_ms(e) == max(3_000, min(8_000, e.expected_dead_ms - 4_000))
    assert {wf.analyst_budget_ms(e) for e in starts} <= set(range(3_000, 8_001))


def test_without_a_verifier_nothing_airs():
    async def wrapped(step, budget_ms):
        return honest_claim(step)

    r, p = pipeline(7, wrapped, verify=wf.reject_all)
    windows = [w for _, w in asyncio.run(p.replay(r.events)) if w is not None]
    assert sum(len(w.accepted) for w in windows) == 0
    assert all(w.fallback == "window_summary" for w in windows)


def agents_present() -> bool:
    return importlib.util.find_spec("earshot.agents") is not None


@pytest.mark.skipif(not agents_present(), reason="Stream 1 agents not merged yet")
def test_replay_with_the_real_agents():
    r = simulate(11)
    w = world()
    clubs = [w.club(r.info.home.team_id).info(), w.club(r.info.away.team_id).info()]
    players = [
        p.info() for c in (w.club(r.info.home.team_id), w.club(r.info.away.team_id)) for p in c.players
    ]
    runs = []
    for _ in range(2):
        p = wf.MatchPipeline.from_agents(r.info, clubs, players, [(Language.EN, ListeningMode.CASUAL)])
        runs.append(asyncio.run(p.replay(r.events)))
    lines = [[ln.text for res, _ in run for ln in res.lines] for run in runs]
    assert lines[0] == lines[1]
    assert len(lines[0]) > 50
    assert not [b for res, _ in runs[0] for b in res.blocked]

    class StandInAnalyst:
        """Has Analyst.run's shape; states only true changes, so Stream 1's verify should accept them."""

        def run(self, view, window_id, expected_dead_ms):
            from types import SimpleNamespace

            idx = max(
                i
                for i, e in enumerate(r.events)
                if isinstance(e, StoppageStart) and e.wall_ms < view.now_wall_ms
            )
            stoppage = r.events[idx]
            assert isinstance(stoppage, StoppageStart)
            past = wf.Shared(tuple(r.events[: idx + 1]))
            pos: wf.Shared[Mapping[str, str]] = wf.Shared({p.player_id: p.position for p in players})
            step = wf.WindowStep(stoppage, None, past, r.info.home.team_id, r.info.away.team_id, pos)
            claims = list(honest_claim(step).claims)
            return SimpleNamespace(
                claims=claims, timed_out=False, error=None, invalid=[], latency_ms=5, tokens=0
            )

    p = wf.MatchPipeline.from_agents(
        r.info, clubs, players, [(Language.EN, ListeningMode.CASUAL)], StandInAnalyst()
    )
    windows = [w for _, w in asyncio.run(p.replay(r.events)) if w is not None]
    accepted = sum(len(w.accepted) for w in windows)
    assert accepted > 0, [w.rejected[:2] for w in windows[:5]]


def test_stoppage_without_window():
    r = simulate(3)
    throw = next(e for e in r.events if isinstance(e, StoppageStart) and e.kind is StoppageKind.THROW_IN)
    assert wf.opens_window(throw) == (throw.expected_dead_ms >= 15_000)
    ft = next(e for e in r.events if isinstance(e, StoppageStart) and e.kind is StoppageKind.FULL_TIME)
    assert not wf.opens_window(ft)
