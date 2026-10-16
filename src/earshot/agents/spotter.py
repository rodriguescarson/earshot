"""Spotter: turns events into listener-neutral moments with a priority and a deadline."""

from __future__ import annotations

from dataclasses import dataclass

from earshot.agents.state import MatchState, Roster, is_goal, scoring_team
from earshot.contract import Action, Card, Event, Injury, Kickoff, Moment, MomentKind, Restart, StoppageStart
from earshot.contract.events import CardColor, PeriodEnd, StoppageKind, Substitution, VarCheck
from earshot.contract.moment import NEVER_DROP_KINDS
from earshot.contract.spadl import SHOT_TYPES, ActionType, Result
from earshot.pitch import attacks_right, to_attacking

# Neutral priority (0-100) and how long a line stays worth saying (ms after the event).
PRIORITY: dict[MomentKind, tuple[int, int]] = {
    MomentKind.GOAL: (100, 20_000),
    MomentKind.OWN_GOAL: (100, 20_000),
    MomentKind.RED_CARD: (95, 20_000),
    MomentKind.PENALTY_AWARDED: (95, 15_000),
    MomentKind.FULL_TIME: (90, 30_000),
    MomentKind.HALF_TIME: (85, 30_000),
    MomentKind.SAVE: (85, 5_000),
    MomentKind.SHOT: (80, 2_500),
    MomentKind.VAR: (80, 15_000),
    MomentKind.YELLOW_CARD: (70, 15_000),
    MomentKind.KICKOFF: (60, 6_000),
    MomentKind.DANGEROUS_ATTACK: (55, 2_500),
    MomentKind.CORNER: (50, 8_000),
    MomentKind.SUBSTITUTION: (45, 20_000),
    MomentKind.INJURY: (45, 15_000),
    MomentKind.OFFSIDE: (45, 6_000),
    MomentKind.FOUL: (40, 4_000),
    MomentKind.POSSESSION_CHANGE: (35, 2_500),
    MomentKind.STOPPAGE: (25, 8_000),
    MomentKind.BUILD_UP: (15, 2_000),
}

PREEMPT_KINDS = frozenset(
    {
        MomentKind.GOAL,
        MomentKind.OWN_GOAL,
        MomentKind.RED_CARD,
        MomentKind.PENALTY_AWARDED,
        MomentKind.SHOT,
        MomentKind.SAVE,
    }
)

SHOT_HOLD_MS = 1_500
"""How long a missed shot waits for a save before it is described on its own."""

BUILD_UP_GAP_MS = 3_000
"""Play-by-play on routine passes only when nothing was said for this long."""


@dataclass
class Spotter:
    roster: Roster
    _last_moment_wall: int = -(10**9)
    _last_possession: str | None = None
    _attack_announced_for: str | None = None
    _n: int = 0
    _held_shot: Action | None = None

    def _moment(
        self, kind: MomentKind, ev: Event, *, club: str | None, players: list[str], x: float | None
    ) -> Moment:
        prio, ttl = PRIORITY[kind]
        self._n += 1
        self._last_moment_wall = ev.wall_ms
        return Moment(
            moment_id=f"{ev.match_id}-mo{self._n:05d}",
            match_id=ev.match_id,
            kind=kind,
            priority=prio,
            never_drop=kind in NEVER_DROP_KINDS,
            wall_ms=ev.wall_ms,
            deadline_wall_ms=ev.wall_ms + ttl,
            event_ids=[ev.event_id],
            club_tags=[club] if club else [],
            player_ids=players,
            x=x,
        )

    def observe(self, ev: Event, before: MatchState, after: MatchState) -> list[Moment]:
        """Moments for one event, given the state just before and just after it.

        A missed shot is held until the next event (at most SHOT_HOLD_MS later in practice), so a
        shot and the keeper's save become one line instead of two that collide on air.
        """
        out: list[Moment] = []
        held, self._held_shot = self._held_shot, None
        if held is not None:
            is_save = (
                isinstance(ev, Action)
                and ActionType(ev.type) is ActionType.KEEPER_SAVE
                and ev.wall_ms - held.wall_ms <= SHOT_HOLD_MS
            )
            if is_save:
                assert isinstance(ev, Action)
                players = [p for p in (held.player_id, ev.player_id) if p]
                out.append(
                    self._moment(MomentKind.SAVE, held, club=held.team_id, players=players, x=held.start_x)
                )
                self._last_possession = ev.team_id
                return out
            out.append(self._shot_moment(held))
        if isinstance(ev, Action) and ActionType(ev.type) in SHOT_TYPES and not is_goal(ev):
            self._held_shot = ev
            self._last_possession = ev.team_id
            return out
        if isinstance(ev, Action):
            out.extend(self._action(ev, before))
        elif isinstance(ev, StoppageStart):
            out.extend(self._stoppage(ev))
        elif isinstance(ev, Kickoff):
            out.append(self._moment(MomentKind.KICKOFF, ev, club=ev.team_id, players=[], x=52.5))
        elif isinstance(ev, Card) and ev.player_id:
            kind = MomentKind.RED_CARD if ev.color is CardColor.RED else MomentKind.YELLOW_CARD
            out.append(self._moment(kind, ev, club=ev.team_id, players=[ev.player_id], x=after.ball_x))
        elif isinstance(ev, Substitution):
            out.append(
                self._moment(
                    MomentKind.SUBSTITUTION, ev, club=ev.team_id, players=[ev.on_id, ev.off_id], x=None
                )
            )
        elif isinstance(ev, Injury) and ev.player_id:
            out.append(
                self._moment(MomentKind.INJURY, ev, club=ev.team_id, players=[ev.player_id], x=after.ball_x)
            )
        elif isinstance(ev, VarCheck):
            out.append(self._moment(MomentKind.VAR, ev, club=ev.team_id, players=[], x=None))
        elif isinstance(ev, PeriodEnd | Restart):
            pass
        if isinstance(ev, Action) and ev.team_id is not None:
            self._last_possession = ev.team_id
        return out

    def _action(self, ev: Action, before: MatchState) -> list[Moment]:
        assert ev.team_id is not None
        players = [ev.player_id] if ev.player_id else []
        t = ActionType(ev.type)
        if is_goal(ev):
            kind = MomentKind.OWN_GOAL if ev.result is Result.OWNGOAL else MomentKind.GOAL
            club = scoring_team(ev, self.roster)
            self._attack_announced_for = None
            return [self._moment(kind, ev, club=club, players=players, x=ev.start_x)]
        if t is ActionType.KEEPER_SAVE:  # a save with no held shot (the shot was described already)
            return []
        if t is ActionType.FOUL:
            return [self._moment(MomentKind.FOUL, ev, club=ev.team_id, players=players, x=ev.start_x)]
        won_back = t in (ActionType.INTERCEPTION, ActionType.TACKLE) and ev.result is Result.SUCCESS
        if won_back and self._last_possession not in (None, ev.team_id):
            self._attack_announced_for = None
            return [
                self._moment(MomentKind.POSSESSION_CHANGE, ev, club=ev.team_id, players=players, x=ev.start_x)
            ]
        home = ev.team_id == self.roster.home_id
        ax, _ = to_attacking(ev.end_x, ev.end_y, attacks_right(home, ev.period_id))
        forward = t in (ActionType.DRIBBLE, ActionType.TAKE_ON, ActionType.PASS, ActionType.CROSS)
        if forward and ev.result is Result.SUCCESS and ax > 70 and self._attack_announced_for != ev.team_id:
            self._attack_announced_for = ev.team_id
            return [
                self._moment(MomentKind.DANGEROUS_ATTACK, ev, club=ev.team_id, players=players, x=ev.end_x)
            ]
        if t is ActionType.CROSS:
            return [self._moment(MomentKind.BUILD_UP, ev, club=ev.team_id, players=players, x=ev.start_x)]
        if forward and ev.result is Result.SUCCESS and ev.wall_ms - self._last_moment_wall >= BUILD_UP_GAP_MS:
            return [self._moment(MomentKind.BUILD_UP, ev, club=ev.team_id, players=players, x=ev.start_x)]
        return []

    def _shot_moment(self, shot: Action) -> Moment:
        players = [shot.player_id] if shot.player_id else []
        return self._moment(MomentKind.SHOT, shot, club=shot.team_id, players=players, x=shot.start_x)

    def flush(self) -> list[Moment]:
        """Release a held shot at the end of a stream."""
        held, self._held_shot = self._held_shot, None
        return [self._shot_moment(held)] if held is not None else []

    def _stoppage(self, ev: StoppageStart) -> list[Moment]:
        club = ev.restart_team_id
        kind = {
            StoppageKind.CORNER: MomentKind.CORNER,
            StoppageKind.PENALTY: MomentKind.PENALTY_AWARDED,
            StoppageKind.OFFSIDE: MomentKind.OFFSIDE,
            StoppageKind.HALF_TIME: MomentKind.HALF_TIME,
            StoppageKind.FULL_TIME: MomentKind.FULL_TIME,
            StoppageKind.THROW_IN: MomentKind.STOPPAGE,
            StoppageKind.GOAL_KICK: MomentKind.STOPPAGE,
            StoppageKind.FREE_KICK: MomentKind.STOPPAGE,
        }.get(ev.kind)
        if kind is None:  # goal, card, substitution, injury, var: the event itself is the moment
            return []
        if kind is MomentKind.OFFSIDE and ev.restart_team_id:
            club = self.roster.other(ev.restart_team_id)
        return [self._moment(kind, ev, club=club, players=[], x=ev.x)]
