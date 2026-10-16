"""The seven claim metrics, computed by code over a wall-clock window (Line Safety).

These are the single definition shared by the Checker (which recomputes every metric claim), the
MCP match tools, StatWatcher, the baseline detector and the evaluation. Each takes the event
stream, a team and a window ``[start_wall_ms, end_wall_ms)``. A metric is ``None`` when the
window holds too little evidence to compute it (the Checker treats that as "cannot verify").

Definitions (all positions in the team's attacking frame, own goal at x=0):

- **press_height** (m): mean distance from own goal at which the team engages the ball, over its
  defensive actions (tackles, interceptions, fouls committed) and the opponent's on-ball actions
  made under pressure (nearest opponent under 3 m). At least 5.
- **tempo** (actions/min): 60 / the median gap in seconds between consecutive in-play on-ball
  actions by the team. At least 10 gaps.
- **directness** (0-1): share of open-play passes that travel 25 m or more and gain ground.
  At least 5 passes.
- **width** (m): mean distance from the pitch's long axis (y=34) of the team's in-possession
  actions. At least 10 actions.
- **line_height** (m): mean distance from own goal of on-ball actions by the team's defenders.
  At least 3 actions.
- **possession_share** (0-1): the team's share of possession time (time between consecutive
  on-ball actions, gaps over 15 s or across a stoppage excluded).
- **shot_count** (shots): shots of any type.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from earshot.contract.claim import Metric
from earshot.contract.events import Action, Event, StoppageStart
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH, attacks_right, to_attacking

OPEN_PLAY_PASS = frozenset({"pass", "cross"})
DEFENSIVE = frozenset({"tackle", "interception", "foul"})
ON_BALL = frozenset(
    {"pass", "cross", "dribble", "take_on", "throw_in", "freekick_short", "goal_kick", "clearance"}
)
LONG_PASS_M = 25.0
MAX_POSSESSION_GAP_MS = 15_000

MIN_EVIDENCE = {
    Metric.PRESS_HEIGHT: 5,
    Metric.DIRECTNESS: 5,
    Metric.WIDTH: 10,
    Metric.TEMPO: 10,
    Metric.LINE_HEIGHT: 3,
}


@dataclass(frozen=True)
class Window:
    team_id: str
    start_wall_ms: int
    end_wall_ms: int

    def __contains__(self, ev: Event) -> bool:
        return self.start_wall_ms <= ev.wall_ms < self.end_wall_ms


def attacking(ev: Action, home_id: str, end: bool = False) -> tuple[float, float]:
    """An action's start (or end) in the acting team's attacking frame."""
    right = attacks_right(ev.team_id == home_id, ev.period_id)
    return to_attacking(ev.end_x, ev.end_y, right) if end else to_attacking(ev.start_x, ev.start_y, right)


def possession_ms(events: Sequence[Event], start_wall_ms: int, end_wall_ms: int) -> dict[str, int]:
    """Time each team had the ball in the window: gaps between consecutive in-play actions.

    A gap belongs to the team that has the ball after the earlier action: the actor, except
    after a foul or a failed tackle (the other team keeps or gets the ball). Gaps that span a
    stoppage, or exceed 15 s, are not counted.
    """
    out: dict[str, int] = {}
    prev: Action | None = None
    teams: set[str] = {e.team_id for e in events if isinstance(e, Action) and e.team_id}
    for ev in events:
        if isinstance(ev, StoppageStart):
            prev = None
            continue
        if not isinstance(ev, Action) or ev.team_id is None:
            continue
        if prev is not None and prev.period_id == ev.period_id:
            gap = ev.wall_ms - prev.wall_ms
            lo, hi = max(prev.wall_ms, start_wall_ms), min(ev.wall_ms, end_wall_ms)
            if 0 < gap <= MAX_POSSESSION_GAP_MS and hi > lo:
                owner = prev.team_id
                if prev.type == "foul" or (prev.type == "tackle" and prev.result == "fail"):
                    owner = next((t for t in teams if t != prev.team_id), owner)
                assert owner is not None
                out[owner] = out.get(owner, 0) + (hi - lo)
        prev = ev
    return out


def team_actions(events: Sequence[Event], w: Window) -> list[Action]:
    return [e for e in events if isinstance(e, Action) and e.team_id == w.team_id and e in w]


def press_height(events: Sequence[Event], w: Window, home_id: str) -> float | None:
    xs: list[float] = []
    for a in events:
        if not isinstance(a, Action) or a not in w or a.team_id is None:
            continue
        if a.team_id == w.team_id and a.type in DEFENSIVE:
            xs.append(attacking(a, home_id)[0])
        elif (
            a.team_id != w.team_id
            and a.type in ON_BALL
            and a.nearest_opp_m is not None
            and a.nearest_opp_m < 3
        ):
            xs.append(PITCH_LENGTH - attacking(a, home_id)[0])
    return sum(xs) / len(xs) if len(xs) >= MIN_EVIDENCE[Metric.PRESS_HEIGHT] else None


def tempo(events: Sequence[Event], w: Window) -> float | None:
    gaps: list[int] = []
    prev: Action | None = None
    for ev in events:
        if isinstance(ev, StoppageStart):
            prev = None
            continue
        if not isinstance(ev, Action) or ev.team_id is None:
            continue
        if (
            prev is not None
            and prev.team_id == ev.team_id == w.team_id
            and prev.type in ON_BALL
            and ev in w
            and 0 < ev.wall_ms - prev.wall_ms <= MAX_POSSESSION_GAP_MS
        ):
            gaps.append(ev.wall_ms - prev.wall_ms)
        prev = ev
    if len(gaps) < MIN_EVIDENCE[Metric.TEMPO]:
        return None
    gaps.sort()
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
    return 60_000 / median


def directness(events: Sequence[Event], w: Window, home_id: str) -> float | None:
    passes = [a for a in team_actions(events, w) if a.type in OPEN_PLAY_PASS]
    if len(passes) < MIN_EVIDENCE[Metric.DIRECTNESS]:
        return None
    long_fwd = 0
    for a in passes:
        (x0, y0), (x1, y1) = attacking(a, home_id), attacking(a, home_id, end=True)
        if math.hypot(x1 - x0, y1 - y0) >= LONG_PASS_M and x1 > x0:
            long_fwd += 1
    return long_fwd / len(passes)


def width(events: Sequence[Event], w: Window) -> float | None:
    acts = [
        a for a in team_actions(events, w) if a.type in ON_BALL and a.type not in ("throw_in", "goal_kick")
    ]
    if len(acts) < MIN_EVIDENCE[Metric.WIDTH]:
        return None
    return sum(abs(a.start_y - PITCH_WIDTH / 2) for a in acts) / len(acts)


def line_height(
    events: Sequence[Event], w: Window, home_id: str, positions: Mapping[str, str]
) -> float | None:
    xs = [
        attacking(a, home_id)[0]
        for a in team_actions(events, w)
        if a.type in ON_BALL and a.player_id and positions.get(a.player_id) == "DF"
    ]
    return sum(xs) / len(xs) if len(xs) >= MIN_EVIDENCE[Metric.LINE_HEIGHT] else None


def possession_share(events: Sequence[Event], w: Window) -> float | None:
    p = possession_ms(events, w.start_wall_ms, w.end_wall_ms)
    total = sum(p.values())
    return p.get(w.team_id, 0) / total if total > 0 else None


def shot_count(events: Sequence[Event], w: Window) -> float:
    return float(sum(1 for a in team_actions(events, w) if ActionType(a.type) in SHOT_TYPES))


def metric(
    m: Metric, events: Sequence[Event], w: Window, home_id: str, positions: Mapping[str, str]
) -> float | None:
    """One metric for one team over one window."""
    if m is Metric.PRESS_HEIGHT:
        return press_height(events, w, home_id)
    if m is Metric.TEMPO:
        return tempo(events, w)
    if m is Metric.DIRECTNESS:
        return directness(events, w, home_id)
    if m is Metric.WIDTH:
        return width(events, w)
    if m is Metric.LINE_HEIGHT:
        return line_height(events, w, home_id, positions)
    if m is Metric.POSSESSION_SHARE:
        return possession_share(events, w)
    if m is Metric.SHOT_COUNT:
        return shot_count(events, w)
    raise ValueError(m)


def all_metrics(
    events: Sequence[Event], w: Window, home_id: str, positions: Mapping[str, str]
) -> dict[Metric, float | None]:
    return {m: metric(m, events, w, home_id, positions) for m in Metric}
