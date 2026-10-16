"""The Agent Framework wiring (ET7): one run per event and per window, immutable snapshots in,
lines and claims out, deterministic replays. Stand-in executors cover the mechanics; the real
Stream 1 executors run in ``test_replay_with_the_real_agents`` once ``earshot.agents`` is present."""

import asyncio
import importlib.util
from dataclasses import dataclass

import pytest

from earshot.contract.claim import AnalystResponse, Claim, Direction, Metric
from earshot.contract.events import Action, Event, StoppageKind, StoppageStart
from earshot.contract.line import Language, Line, LineSource, ListeningMode
from earshot.contract.moment import Moment, MomentKind
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.pipeline import workflow as wf
from earshot.sim.match import simulate
from earshot.stats.claims import THRESHOLD
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


def pipeline(seed: int, analyst=wf.no_analyst, timeout_scale: float = 1.0):
    r = simulate(seed)
    pos = {p.player_id: p.position for p in world().players.values()}
    p = wf.MatchPipeline(fake_step, FakeState(), r.info.home.team_id, r.info.away.team_id, pos, FakeSpotter(),
                         FakeCaller(), fake_check, TARGETS, analyst, timeout_scale)  # fmt: skip
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

    async def spy(stats: wf.WindowStats):
        idx = next(i for i, e in enumerate(r.events) if e.event_id == stats.step.stoppage.event_id)
        seen_lengths.append((len(stats.step.events.value), idx + 1))
        return None

    p = wf.MatchPipeline(
        fake_step, FakeState(), p.home_id, p.away_id, p.positions, FakeSpotter(), FakeCaller(), fake_check,
        TARGETS, spy,
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


def honest_claim(stats: wf.WindowStats) -> AnalystResponse | None:
    """An Analyst that only states what the window stats show (the strongest change per club)."""
    claims = []
    for t in stats.teams:
        best = None
        for m, (before, after) in t.values.items():
            if before is None or after is None or m is Metric.SHOT_COUNT:
                continue
            s = abs(after - before) / THRESHOLD[m]
            if s >= 1 and (best is None or s > best[0]):
                best = (s, m, before, after)
        if best and t.event_ids:
            _, m, before, after = best
            direction = Direction.UP if after > before else Direction.DOWN
            claims.append(
                Claim(
                    team_id=t.team_id, metric=m, direction=direction, window_start_ms=t.start_wall_ms,
                    window_end_ms=t.end_wall_ms, value_before=round(before, 3), value_after=round(after, 3),
                    cited_event_ids=list(t.event_ids[-3:]),
                )
            )  # fmt: skip
    return AnalystResponse(window_id=stats.window_id, claims=claims)


def liar(stats: wf.WindowStats) -> AnalystResponse | None:
    good = honest_claim(stats)
    if good is None or not good.claims:
        return good
    c = good.claims[0]
    flipped = c.model_copy(
        update={"direction": Direction.DOWN if c.direction is Direction.UP else Direction.UP}
    )
    ghost = c.model_copy(update={"cited_event_ids": ["no-such-event"]})
    future = c.model_copy(
        update={"window_end_ms": c.window_end_ms + 600_000, "window_start_ms": c.window_start_ms + 600_000}
    )
    return AnalystResponse(window_id=stats.window_id, claims=[flipped, ghost, future])


def run_windows(seed, analyst):
    async def wrapped(stats):
        return analyst(stats)

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
    async def slow(stats):
        await asyncio.sleep(stats.timeout_ms / 1000 + 0.05)
        return None

    async def broken(stats):
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


def test_analyst_timeout_follows_the_design_rule():
    assert wf.analyst_timeout_ms(StoppageKind.THROW_IN) == 8_000  # 14.7 s - 4 s, capped at 8 s
    assert wf.analyst_timeout_ms(StoppageKind.FREE_KICK) == 8_000
    assert 3_000 <= wf.analyst_timeout_ms(StoppageKind.OFFSIDE) <= 8_000


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


def test_stoppage_without_window():
    r = simulate(3)
    throw = next(e for e in r.events if isinstance(e, StoppageStart) and e.kind is StoppageKind.THROW_IN)
    assert wf.opens_window(throw) == (throw.expected_dead_ms >= 15_000)
    ft = next(e for e in r.events if isinstance(e, StoppageStart) and e.kind is StoppageKind.FULL_TIME)
    assert not wf.opens_window(ft)
