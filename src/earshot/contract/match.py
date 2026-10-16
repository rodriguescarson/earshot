"""Who is playing: clubs, players and the team sheets for one match.

The Caller fills names from these records (by id, never free text). Everything here is fictional.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Position = Literal["GK", "DF", "MF", "FW"]


class PlayerInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    player_id: str
    """Stable across matches, e.g. ``kes-09``."""
    team_id: str
    name: str
    short_name: str
    """What commentary says: the surname."""
    shirt: Annotated[int, Field(ge=1, le=99)]
    position: Position


class ClubInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: str
    name: str
    short_name: str
    stadium: str
    coach: str


class TeamSheet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team_id: str
    formation: str
    starting: Annotated[list[str], Field(min_length=11, max_length=11)]
    bench: list[str]


class MatchInfo(BaseModel):
    """Header for one simulated match. Home attacks right (towards x=105) in the first half."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    seed: int
    engine_version: str
    round: int | None = None
    home: TeamSheet
    away: TeamSheet
    referee: str
    kickoff_team_id: str
    """Team that kicks off the first half; the other team kicks off the second."""
    half_time_ms: Annotated[int, Field(ge=0)]
    """Wall-clock length of the half-time interval."""


class Roster(BaseModel):
    """All clubs and players in the fictional league."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clubs: list[ClubInfo]
    players: list[PlayerInfo]
