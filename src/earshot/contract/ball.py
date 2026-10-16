"""The 10 Hz ball path, computed from the events after a match is simulated.

The client's continuous layer is driven from this track (batched one message a second on the
wire as ``BallMsg``; served as a static file for clips).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH

BALL_HZ = 10
BALL_STEP_MS = 1000 // BALL_HZ


class BallSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_ms: Annotated[int, Field(ge=0)]
    period_id: Literal[1, 2]
    clock_ms: Annotated[int, Field(ge=0)]
    x: Annotated[float, Field(ge=0.0, le=PITCH_LENGTH)]
    y: Annotated[float, Field(ge=0.0, le=PITCH_WIDTH)]
    in_play: bool
    """False during a stoppage (between ``stoppage_start`` and ``restart``) and at half-time."""
    team_id: str | None
    """Team in possession, or None while the ball is dead or loose."""
