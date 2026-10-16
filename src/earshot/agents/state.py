"""An immutable match-state snapshot, folded from the event stream one event at a time."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from earshot.contract import (
    Action,
    Card,
    Event,
    Kickoff,
    PeriodEnd,
    Restart,
    StoppageKind,
    StoppageStart,
    Substitution,
)
from earshot.contract.events import CardColor
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.stats.football import is_goal, scoring_team_id, spoken_minute

__all__ = ["MatchState", "Roster", "is_goal", "scoring_team", "spoken_minute"]

Status = Literal["before_kickoff", "live", "dead", "half_time", "full_time"]


@dataclass(frozen=True)
class Roster:
    """Names for one match: looked up by id, never typed by a model."""

    clubs: dict[str, ClubInfo]
    players: dict[str, PlayerInfo]
    home_id: str
    away_id: str

    @classmethod
    def build(cls, match: MatchInfo, clubs: list[ClubInfo], players: list[PlayerInfo]) -> Roster:
        return cls(
            clubs={c.team_id: c for c in clubs},
            players={p.player_id: p for p in players},
            home_id=match.home.team_id,
            away_id=match.away.team_id,
        )

    def other(self, team_id: str) -> str:
        return self.away_id if team_id == self.home_id else self.home_id


def scoring_team(ev: Action, roster: Roster) -> str:
    return scoring_team_id(ev, roster.home_id, roster.away_id)


@dataclass(frozen=True)
class MatchState:
    match_id: str
    home_id: str
    away_id: str
    period_id: int = 1
    clock_ms: int = 0
    wall_ms: int = 0
    home_score: int = 0
    away_score: int = 0
    status: Status = "before_kickoff"
    possession: str | None = None
    ball_x: float = 52.5
    ball_y: float = 34.0
    stoppage: StoppageKind | None = None
    stoppage_wall_ms: int | None = None
    expected_dead_ms: int | None = None
    restart_team_id: str | None = None
    sent_off: frozenset[str] = field(default_factory=frozenset)
    yellows: frozenset[str] = field(default_factory=frozenset)
    on_pitch_changes: tuple[tuple[str, str], ...] = ()
    last_event_id: str | None = None

    @classmethod
    def start(cls, match: MatchInfo) -> MatchState:
        return cls(match_id=match.match_id, home_id=match.home.team_id, away_id=match.away.team_id)

    def score(self, team_id: str) -> int:
        return self.home_score if team_id == self.home_id else self.away_score

    def apply(self, ev: Event, roster: Roster) -> MatchState:
        s = replace(
            self, period_id=ev.period_id, clock_ms=ev.clock_ms, wall_ms=ev.wall_ms, last_event_id=ev.event_id
        )
        if isinstance(ev, Action):
            s = replace(s, ball_x=ev.end_x, ball_y=ev.end_y, possession=ev.team_id, status="live")
            if is_goal(ev):
                scorer = scoring_team(ev, roster)
                if scorer == self.home_id:
                    s = replace(s, home_score=s.home_score + 1)
                else:
                    s = replace(s, away_score=s.away_score + 1)
            return s
        if isinstance(ev, StoppageStart):
            status: Status = "dead"
            if ev.kind is StoppageKind.HALF_TIME:
                status = "half_time"
            elif ev.kind is StoppageKind.FULL_TIME:
                status = "full_time"
            return replace(
                s,
                status=status,
                stoppage=ev.kind,
                stoppage_wall_ms=ev.wall_ms,
                expected_dead_ms=ev.expected_dead_ms,
                restart_team_id=ev.restart_team_id,
                ball_x=ev.x if ev.x is not None else s.ball_x,
                ball_y=ev.y if ev.y is not None else s.ball_y,
            )
        if isinstance(ev, Restart):
            return replace(s, status="live", stoppage=None, stoppage_wall_ms=None, expected_dead_ms=None)
        if isinstance(ev, Kickoff):
            return replace(s, status="live", possession=ev.team_id, ball_x=52.5, ball_y=34.0)
        if isinstance(ev, Card) and ev.player_id is not None:
            if ev.color is CardColor.RED:
                return replace(s, sent_off=s.sent_off | {ev.player_id})
            return replace(s, yellows=s.yellows | {ev.player_id})
        if isinstance(ev, Substitution):
            return replace(s, on_pitch_changes=(*s.on_pitch_changes, (ev.off_id, ev.on_id)))
        if isinstance(ev, PeriodEnd):
            return replace(s, status="half_time" if ev.period_id == 1 else "full_time")
        return s
