"""Analyst output: structured claims only, never sentences (Line Safety).

A claim says a metric changed for one team over a stated window, in a stated direction, with an
optional cause label from the closed vocabulary and the event ids it rests on. Code renders every
sentence from templates; the Checker recomputes each metric claim before it can air. Cause
labels cannot be checked live and are graded offline against the hidden truth log.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Metric(StrEnum):
    PRESS_HEIGHT = "press_height"
    TEMPO = "tempo"
    DIRECTNESS = "directness"
    WIDTH = "width"
    LINE_HEIGHT = "line_height"
    POSSESSION_SHARE = "possession_share"
    SHOT_COUNT = "shot_count"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


class CauseLabel(StrEnum):
    """Closed vocabulary. The first nine are scripted changes; the last three follow match events."""

    PRESS_HIGHER = "PRESS_HIGHER"
    DROP_DEEPER = "DROP_DEEPER"
    GO_DIRECT = "GO_DIRECT"
    RAISE_TEMPO = "RAISE_TEMPO"
    SLOW_TEMPO = "SLOW_TEMPO"
    WIDEN = "WIDEN"
    NARROW = "NARROW"
    PARK_THE_BUS = "PARK_THE_BUS"
    ALL_OUT_ATTACK = "ALL_OUT_ATTACK"
    RED_CARD_REORG = "RED_CARD_REORG"
    CHASING_GAME = "CHASING_GAME"
    PROTECTING_LEAD = "PROTECTING_LEAD"


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: str
    metric: Metric
    direction: Direction
    window_start_ms: Annotated[int, Field(ge=0)]
    window_end_ms: Annotated[int, Field(ge=0)]
    """Wall-clock window (``wall_ms``) the change is measured over."""
    value_before: float | None = None
    value_after: float | None = None
    cause: CauseLabel | None = None
    cited_event_ids: Annotated[list[str], Field(min_length=1, max_length=12)]
    rank: Annotated[int, Field(ge=1, le=3)] = 1

    @model_validator(mode="after")
    def _window_order(self) -> Claim:
        if self.window_end_ms <= self.window_start_ms:
            raise ValueError("window_end_ms must be after window_start_ms")
        return self


class AnalystResponse(BaseModel):
    """What the Analyst returns for one dead-ball window: up to 3 ranked claims per club."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_id: str
    claims: Annotated[list[Claim], Field(max_length=6)]
