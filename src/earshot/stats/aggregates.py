"""Per-match aggregates computed from the event stream alone (realism report, gate G3 and G10)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from earshot.contract.events import Action, Card, Event, PeriodEnd, Restart, StoppageKind, StoppageStart
from earshot.contract.spadl import SHOT_TYPES, ActionType

PASS_TYPES = frozenset(
    {
        ActionType.PASS,
        ActionType.CROSS,
        ActionType.THROW_IN,
        ActionType.FREEKICK_CROSSED,
        ActionType.FREEKICK_SHORT,
        ActionType.CORNER_CROSSED,
        ActionType.CORNER_SHORT,
        ActionType.GOAL_KICK,
    }
)
OPEN_PLAY_PASSES = frozenset({ActionType.PASS, ActionType.CROSS})


@dataclass(frozen=True)
class DeadWindow:
    kind: StoppageKind
    start_wall_ms: int
    end_wall_ms: int
    expected_dead_ms: int
    start_event_id: str
    restart_team_id: str | None

    @property
    def dead_ms(self) -> int:
        return self.end_wall_ms - self.start_wall_ms


def dead_windows(events: list[Event]) -> list[DeadWindow]:
    """Every stoppage_start -> restart pair (full time has no restart and is excluded)."""
    out: list[DeadWindow] = []
    open_: StoppageStart | None = None
    for ev in events:
        if isinstance(ev, StoppageStart):
            open_ = ev
        elif isinstance(ev, Restart) and open_ is not None:
            out.append(
                DeadWindow(
                    open_.kind,
                    open_.wall_ms,
                    ev.wall_ms,
                    open_.expected_dead_ms,
                    open_.event_id,
                    open_.restart_team_id,
                )
            )
            open_ = None
    return out


def goals(events: list[Event], home_id: str, away_id: str) -> tuple[int, int]:
    """Score from events: successful shots for the shooter's team, own goals for the other team."""
    score = {home_id: 0, away_id: 0}
    for ev in events:
        if not isinstance(ev, Action) or ev.team_id is None:
            continue
        if ActionType(ev.type) in SHOT_TYPES and ev.result == "success":
            score[ev.team_id] += 1
        elif ev.result == "owngoal":
            score[away_id if ev.team_id == home_id else home_id] += 1
    return score[home_id], score[away_id]


@dataclass(frozen=True)
class MatchAggregates:
    home_goals: int
    away_goals: int
    goals: int
    shots: int
    shots_on_target: int
    passes: int
    pass_completion: float
    fouls: int
    yellows: int
    reds: int
    corners: int
    throw_ins: int
    goal_kicks: int
    free_kicks: int
    offsides: int
    penalties: int
    crosses: int
    actions: int
    effective_play_min: float
    match_wall_min: float
    windows_15s: int
    home_possession: float
    added_time_min: tuple[float, float]

    def as_dict(self) -> dict:
        return asdict(self)


def aggregate(events: list[Event], home_id: str, away_id: str) -> MatchAggregates:
    actions = [e for e in events if isinstance(e, Action)]
    types = Counter(ActionType(a.type) for a in actions)
    passes = [a for a in actions if ActionType(a.type) in OPEN_PLAY_PASSES]
    done = sum(1 for a in passes if a.result == "success")
    shots = [a for a in actions if ActionType(a.type) in SHOT_TYPES]
    on_target = sum(1 for a in shots if a.result == "success") + types[ActionType.KEEPER_SAVE]
    cards = [e for e in events if isinstance(e, Card)]
    starts = Counter(e.kind for e in events if isinstance(e, StoppageStart))
    windows = dead_windows(events)
    ends = {e.period_id: e for e in events if isinstance(e, PeriodEnd)}
    period_ms = {p: ends[p].clock_ms for p in ends}
    dead_in_period = sum(w.dead_ms for w in windows if w.kind is not StoppageKind.HALF_TIME)
    effective_ms = sum(period_ms.values()) - dead_in_period
    poss = Counter(a.team_id for a in actions)
    h, a = goals(events, home_id, away_id)
    last = events[-1]
    return MatchAggregates(
        home_goals=h,
        away_goals=a,
        goals=h + a,
        shots=len(shots),
        shots_on_target=on_target,
        passes=len(passes),
        pass_completion=round(done / max(1, len(passes)), 4),
        fouls=types[ActionType.FOUL],
        yellows=sum(1 for c in cards if c.color == "yellow"),
        reds=sum(1 for c in cards if c.color == "red"),
        corners=starts[StoppageKind.CORNER],
        throw_ins=starts[StoppageKind.THROW_IN],
        goal_kicks=starts[StoppageKind.GOAL_KICK],
        free_kicks=starts[StoppageKind.FREE_KICK],
        offsides=starts[StoppageKind.OFFSIDE],
        penalties=starts[StoppageKind.PENALTY],
        crosses=types[ActionType.CROSS],
        actions=len(actions),
        effective_play_min=round(effective_ms / 60_000, 2),
        match_wall_min=round(last.wall_ms / 60_000, 2),
        windows_15s=sum(1 for w in windows if w.dead_ms >= 15_000),
        home_possession=round(poss[home_id] / max(1, poss[home_id] + poss[away_id]), 4),
        added_time_min=tuple(round((period_ms[p] - 45 * 60_000) / 60_000, 2) for p in (1, 2)),  # type: ignore[arg-type]
    )
