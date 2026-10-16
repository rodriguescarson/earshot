"""A line of commentary: produced by an executor, scheduled by the Director, voiced by Voice."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Language(StrEnum):
    EN = "en"
    ES = "es"
    AR = "ar"
    HI = "hi"


class ListeningMode(StrEnum):
    CASUAL = "casual"
    ANALYST = "analyst"


class OutputMode(StrEnum):
    APP_VOICE = "app_voice"
    SCREEN_READER = "screen_reader"
    ONE_EAR = "one_ear"


class LineSource(StrEnum):
    CALLER = "caller"
    ANALYST = "analyst"
    STATWATCHER = "statwatcher"
    FALLBACK = "fallback"
    ON_DEMAND = "on_demand"
    RECAP = "recap"
    NOTICE = "notice"


class VoiceRole(StrEnum):
    FACTS = "facts"
    EXPLANATION = "explanation"


class FocusStat(StrEnum):
    PRESS_HEIGHT = "press_height"
    DANGER = "danger"
    PASS_DIFFICULTY = "pass_difficulty"
    SHOT_SPEED = "shot_speed"
    POSSESSION = "possession"


SlotValue = str | int | float


class Line(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: str
    match_id: str
    moment_id: str | None
    source: LineSource
    language: Language
    listening_mode: ListeningMode
    template_id: str
    slots: dict[str, SlotValue]
    text: str
    """Rendered by code from template_id + slots; the Checker re-renders and compares exactly."""
    priority: Annotated[int, Field(ge=0, le=100)]
    preempt: bool = False
    never_drop: bool = False
    wall_ms: Annotated[int, Field(ge=0)]
    deadline_wall_ms: Annotated[int, Field(ge=0)]
    voice_role: VoiceRole = VoiceRole.FACTS
    pan: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0
    est_ms: Annotated[int, Field(ge=0)] = 0
    """Airtime estimate at 1x speech rate."""
    club_tags: list[str] = []
    player_ids: list[str] = []
    stat_tag: FocusStat | None = None
    late: bool = False
    """A never-drop line airing after its deadline carries an "earlier" marker."""
