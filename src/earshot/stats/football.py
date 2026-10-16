"""Small football facts shared by agents, tools and evaluation (no model, no truth)."""

from __future__ import annotations

from earshot.contract.events import Action, Event
from earshot.contract.spadl import SHOT_TYPES, ActionType, Result


def is_goal(ev: Event) -> bool:
    """A shot that went in, or any action that went into the actor's own net."""
    return isinstance(ev, Action) and (
        (ActionType(ev.type) in SHOT_TYPES and ev.result is Result.SUCCESS) or ev.result is Result.OWNGOAL
    )


def scoring_team_id(ev: Action, home_id: str, away_id: str) -> str:
    assert ev.team_id is not None
    if ev.result is Result.OWNGOAL:
        return away_id if ev.team_id == home_id else home_id
    return ev.team_id


def spoken_minute(period_id: int, clock_ms: int) -> str:
    """Football minute as said on air: 62:10 in the first half is "63"; added time is "45+2"."""
    base = 45 * (period_id - 1)
    m = clock_ms // 60_000 + 1
    if m > 45:
        return f"{base + 45}+{m - 45}"
    return str(base + m)
