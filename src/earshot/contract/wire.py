"""Messages the server publishes to listeners over Web PubSub."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from earshot.contract.line import VoiceRole


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LineMsg(_Msg):
    kind: Literal["line"] = "line"
    id: str
    priority: Annotated[int, Field(ge=0, le=100)]
    preempt: bool
    never_drop: bool
    audio_url: str | None
    text: str
    ssml_hash: str
    deadline_ms: int
    voice_role: VoiceRole
    late: bool = False
    pan: float = 0.0
    """Streamed path only: the browser places the line at this pan (-1 left, 1 right)."""
    codec: Literal["m4a", "mp3"] = "m4a"


class BallMsg(_Msg):
    kind: Literal["ball"] = "ball"
    clock_ms: int
    x: float
    y: float
    danger: Annotated[float, Field(ge=0.0, le=1.0)]


class StateMsg(_Msg):
    kind: Literal["state"] = "state"
    home_score: int
    away_score: int
    clock_ms: int
    period: Literal[1, 2]
    status: Literal["before_kickoff", "live", "half_time", "full_time"]


class RecapMsg(_Msg):
    """Half-time or full-time recap for the screen's Recaps section (its lines also air as LineMsgs)."""

    kind: Literal["recap"] = "recap"
    period: Literal["half_time", "full_time"]
    text: str
    lines: list[str]


WireMsg = Annotated[LineMsg | BallMsg | StateMsg | RecapMsg, Field(discriminator="kind")]
