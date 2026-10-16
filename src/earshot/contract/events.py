"""The match event stream (eng review decision E-2).

One ordered stream per match. On-ball actions use the vendored SPADL vocabulary; everything
SPADL lacks (stoppages, restarts, kick-offs, substitutions, cards, injuries, VAR, period ends)
is an explicit event, so the Director opens and closes dead-ball windows on real events.

Invariants (tested): clocks never go backwards within a period, and every ``stoppage_start``
is followed by exactly one ``restart`` of the same kind before the next ``stoppage_start``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from earshot.contract.spadl import FLIGHT_TYPES, ActionType, Bodypart, Result
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH

X = Annotated[float, Field(ge=0.0, le=PITCH_LENGTH)]
Y = Annotated[float, Field(ge=0.0, le=PITCH_WIDTH)]


class StoppageKind(StrEnum):
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    PENALTY = "penalty"
    OFFSIDE = "offside"
    GOAL = "goal"
    SUBSTITUTION = "substitution"
    INJURY = "injury"
    CARD = "card"
    VAR = "var"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    match_id: str
    period_id: Literal[1, 2]
    clock_ms: Annotated[int, Field(ge=0)]
    """Match clock within the period (resets to 0 at each period start, like a broadcast clock)."""
    wall_ms: Annotated[int, Field(ge=0)]
    """Real elapsed time since kick-off, including dead time; drives scheduling."""
    team_id: str | None = None
    player_id: str | None = None


class Action(_Base):
    """An on-ball SPADL action in the broadcast frame."""

    type: Literal[
        "pass", "cross", "throw_in", "freekick_crossed", "freekick_short", "corner_crossed",
        "corner_short", "take_on", "foul", "tackle", "interception", "shot", "shot_penalty",
        "shot_freekick", "keeper_save", "keeper_claim", "keeper_punch", "keeper_pick_up",
        "clearance", "bad_touch", "non_action", "dribble", "goal_kick",
    ]  # fmt: skip
    result: Result
    bodypart: Bodypart
    start_x: X
    start_y: Y
    end_x: X
    end_y: Y
    flight_ms: Annotated[int, Field(ge=0)] | None = None
    """Ball travel time for passes, crosses, shots and clearances (gives ball and shot speed)."""
    nearest_opp_m: Annotated[float, Field(ge=0.0)] | None = None
    """Distance to the nearest opponent at the moment of the action (under 3 m = under pressure)."""

    @model_validator(mode="after")
    def _flight_only_on_flight_types(self) -> Action:
        if self.flight_ms is not None and ActionType(self.type) not in FLIGHT_TYPES:
            raise ValueError(f"flight_ms is only allowed on flight actions, not {self.type}")
        return self


class StoppageStart(_Base):
    type: Literal["stoppage_start"] = "stoppage_start"
    kind: StoppageKind
    restart_team_id: str | None
    expected_dead_ms: Annotated[int, Field(ge=0)]
    """The engine's expected dead time for this kind; the real length is unknown until restart."""
    x: X | None = None
    y: Y | None = None


class Restart(_Base):
    type: Literal["restart"] = "restart"
    kind: StoppageKind


class Kickoff(_Base):
    type: Literal["kickoff"] = "kickoff"


class Substitution(_Base):
    type: Literal["substitution"] = "substitution"
    off_id: str
    on_id: str


class CardColor(StrEnum):
    YELLOW = "yellow"
    RED = "red"


class CardReason(StrEnum):
    FOUL = "foul"
    SECOND_YELLOW = "second_yellow"
    DISSENT = "dissent"
    HANDBALL = "handball"
    TIME_WASTING = "time_wasting"
    DENYING_GOAL_CHANCE = "denying_goal_chance"


class Card(_Base):
    type: Literal["card"] = "card"
    color: CardColor
    reason: CardReason


class Injury(_Base):
    type: Literal["injury"] = "injury"


class VarCheck(_Base):
    type: Literal["var_check"] = "var_check"
    subject: Literal["goal", "penalty", "red_card", "offside"]
    outcome: Literal["stands", "overturned"]


class PeriodEnd(_Base):
    type: Literal["period_end"] = "period_end"


Event = Annotated[
    Action | StoppageStart | Restart | Kickoff | Substitution | Card | Injury | VarCheck | PeriodEnd,
    Field(discriminator="type"),
]

EVENT_MODELS: tuple[type[BaseModel], ...] = (
    Action, StoppageStart, Restart, Kickoff, Substitution, Card, Injury, VarCheck, PeriodEnd,
)  # fmt: skip


def check_stream(events: list[Event]) -> list[str]:
    """Return every invariant violation in a match stream (empty list = valid)."""
    problems: list[str] = []
    last: dict[int, int] = {}
    open_stoppage: StoppageStart | None = None
    for ev in events:
        prev = last.get(ev.period_id)
        if prev is not None and ev.clock_ms < prev:
            problems.append(f"{ev.event_id}: clock goes backwards in period {ev.period_id}")
        last[ev.period_id] = ev.clock_ms
        if isinstance(ev, StoppageStart):
            if open_stoppage is not None:
                problems.append(f"{ev.event_id}: stoppage_start while {open_stoppage.event_id} is open")
            open_stoppage = ev
        elif isinstance(ev, Restart):
            if open_stoppage is None:
                problems.append(f"{ev.event_id}: restart without a stoppage_start")
            elif ev.kind != open_stoppage.kind:
                problems.append(f"{ev.event_id}: restart kind {ev.kind} != stoppage {open_stoppage.kind}")
            open_stoppage = None
    if open_stoppage is not None and open_stoppage.kind is not StoppageKind.FULL_TIME:
        problems.append(f"{open_stoppage.event_id}: stoppage never restarted")
    return problems
