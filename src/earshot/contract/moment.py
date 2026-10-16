"""Spotter output: a listener-neutral moment worth describing."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class MomentKind(StrEnum):
    GOAL = "goal"
    OWN_GOAL = "own_goal"
    SHOT = "shot"
    SAVE = "save"
    PENALTY_AWARDED = "penalty_awarded"
    RED_CARD = "red_card"
    YELLOW_CARD = "yellow_card"
    DANGEROUS_ATTACK = "dangerous_attack"
    POSSESSION_CHANGE = "possession_change"
    BUILD_UP = "build_up"
    FOUL = "foul"
    OFFSIDE = "offside"
    CORNER = "corner"
    STOPPAGE = "stoppage"
    RESTART = "restart"
    KICKOFF = "kickoff"
    SUBSTITUTION = "substitution"
    INJURY = "injury"
    VAR = "var"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"


NEVER_DROP_KINDS = frozenset(
    {MomentKind.GOAL, MomentKind.OWN_GOAL, MomentKind.RED_CARD, MomentKind.PENALTY_AWARDED}
)
"""Never dropped as stale and never lost to a block; late ones air with an "earlier" marker."""


class Moment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    moment_id: str
    match_id: str
    kind: MomentKind
    priority: Annotated[int, Field(ge=0, le=100)]
    """Neutral priority; the Director boosts it per listener."""
    never_drop: bool
    wall_ms: Annotated[int, Field(ge=0)]
    deadline_wall_ms: Annotated[int, Field(ge=0)]
    """After this, a line for the moment is stale (unless never_drop)."""
    event_ids: Annotated[list[str], Field(min_length=1)]
    club_tags: list[str] = []
    player_ids: list[str] = []
    x: float | None = None
    """Ball x in the broadcast frame when the moment happened (drives speech pan)."""
