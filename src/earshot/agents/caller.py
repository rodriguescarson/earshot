"""Caller: play-by-play lines from code templates. No model touches a Caller line.

``slots_for`` is the single code path that turns a moment into template slots: names come from
the roster by id, numbers from the match state and the event's geometry. The Checker re-runs it
and compares, so a line can only air if every name and number came from here.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from earshot.agents.catalog import Catalog, load, placeholders
from earshot.agents.state import MatchState, Roster, spoken_minute
from earshot.contract import Action, Event, Language, Line, LineSource, ListeningMode, Moment, MomentKind
from earshot.contract.events import StoppageStart, Substitution, VarCheck
from earshot.contract.line import SlotValue
from earshot.contract.spadl import ActionType
from earshot.pitch import attacks_right, channel, speech_pan, third, to_attacking

CASUAL_SHOT_SPEED_MIN_KMH = 100
LONG_PASS_M = 35.0
CHARS_PER_SECOND = 14.0
PREEMPT = frozenset(
    {
        MomentKind.GOAL,
        MomentKind.OWN_GOAL,
        MomentKind.RED_CARD,
        MomentKind.PENALTY_AWARDED,
        MomentKind.SHOT,
        MomentKind.SAVE,
        MomentKind.YELLOW_CARD,  # it waited behind fouls, stoppages and window summaries (ET11, 2026-10-17)
    }
)


def kmh(ev: Action) -> int | None:
    if not ev.flight_ms:
        return None
    d = math.hypot(ev.end_x - ev.start_x, ev.end_y - ev.start_y)
    return round(d / (ev.flight_ms / 1000) * 3.6)


def metres(ev: Action) -> int:
    return round(math.hypot(ev.end_x - ev.start_x, ev.end_y - ev.start_y))


def zone_key(x: float, y: float, team_id: str, period_id: int, roster: Roster) -> str:
    ax, ay = to_attacking(x, y, attacks_right(team_id == roster.home_id, period_id))
    return f"{third(ax).value}_{channel(ay).value}"


def est_ms(text: str) -> int:
    return int(len(text) / CHARS_PER_SECOND * 1000) + 300


def _pick(cat: Catalog, family: str, moment_id: str, mode: ListeningMode) -> str:
    options = [t for t in cat.variants(family) if mode.value in cat.templates[t]]
    h = int(hashlib.sha1(moment_id.encode()).hexdigest(), 16)
    return options[h % len(options)]


def _score(state: MatchState, roster: Roster) -> dict[str, SlotValue]:
    return {
        "home": roster.clubs[roster.home_id].short_name,
        "away": roster.clubs[roster.away_id].short_name,
        "home_score": state.home_score,
        "away_score": state.away_score,
    }


def slots_for(
    moment: Moment, ev: Event, state: MatchState, roster: Roster, cat: Catalog, mode: ListeningMode
) -> tuple[str, dict[str, SlotValue]] | None:
    """(template family, slots) for a moment, or None when this mode stays silent on it."""
    k = moment.kind
    club = roster.clubs[moment.club_tags[0]].short_name if moment.club_tags else None
    player = roster.players[moment.player_ids[0]].short_name if moment.player_ids else None
    lex = cat.lexicon
    if k in (MomentKind.GOAL, MomentKind.OWN_GOAL):
        assert isinstance(ev, Action) and player and club
        speed = kmh(ev)
        family = "own_goal" if k is MomentKind.OWN_GOAL else "goal"
        slots: dict[str, SlotValue] = {"player": player, **_score(state, roster)}
        if k is MomentKind.GOAL:
            slots["club"] = club
            loud = speed is not None and (mode is ListeningMode.ANALYST or speed >= CASUAL_SHOT_SPEED_MIN_KMH)
            if loud:
                return "goal_speed", {**slots, "speed": speed}
        return family, slots
    if k is MomentKind.SHOT:
        assert isinstance(ev, Action) and player
        speed = kmh(ev)
        if speed is not None and (mode is ListeningMode.ANALYST or speed >= CASUAL_SHOT_SPEED_MIN_KMH):
            return "shot_speed", {"player": player, "speed": speed}
        return "shot", {"player": player}
    if k is MomentKind.SAVE:
        assert isinstance(ev, Action) and len(moment.player_ids) == 2
        shooter, keeper = (roster.players[p].short_name for p in moment.player_ids)
        speed = kmh(ev)
        if speed is not None and (mode is ListeningMode.ANALYST or speed >= CASUAL_SHOT_SPEED_MIN_KMH):
            return "save_speed", {"player": shooter, "keeper": keeper, "speed": speed}
        return "save", {"player": shooter, "keeper": keeper}
    if k is MomentKind.PENALTY_AWARDED:
        assert club
        return "penalty", {"club": club}
    if k is MomentKind.RED_CARD:
        assert player and club and moment.club_tags
        team = moment.club_tags[0]
        reds = sum(1 for p in state.sent_off if roster.players[p].team_id == team)
        return "red_card", {"player": player, "club": club, "count": 11 - reds}
    if k is MomentKind.YELLOW_CARD:
        assert player and club
        return "yellow_card", {"player": player, "club": club}
    if k in (MomentKind.DANGEROUS_ATTACK, MomentKind.BUILD_UP, MomentKind.POSSESSION_CHANGE):
        assert isinstance(ev, Action) and ev.team_id and player and club
        zone = lex["zone"][zone_key(ev.end_x, ev.end_y, ev.team_id, ev.period_id, roster)]
        if k is MomentKind.POSSESSION_CHANGE:
            return "turnover", {"player": player, "club": club}
        if k is MomentKind.DANGEROUS_ATTACK:
            return "attack", {"player": player, "club": club, "zone": zone}
        if ActionType(ev.type) is ActionType.CROSS:
            side = zone_key(ev.start_x, ev.start_y, ev.team_id, ev.period_id, roster).split("_")[1]
            return "cross", {"player": player, "side": lex["side"][side]}
        if ActionType(ev.type) is ActionType.PASS and metres(ev) >= LONG_PASS_M:
            if mode is ListeningMode.ANALYST:
                return "long_pass", {"player": player, "distance": metres(ev)}
            return "long_pass", {"player": player}
        return "on_ball", {"player": player, "club": club, "zone": zone}
    if k is MomentKind.FOUL:
        assert player
        return "foul", {"player": player}
    if k in (MomentKind.STOPPAGE, MomentKind.CORNER):
        assert isinstance(ev, StoppageStart) and club
        return "stoppage", {"stoppage": lex["stoppage"][ev.kind.value], "club": club}
    if k is MomentKind.OFFSIDE:
        assert club
        return "offside", {"club": club}
    if k is MomentKind.KICKOFF:
        assert club
        if state.home_score + state.away_score > 0:
            slots = {"club": club, **_score(state, roster)}
            if mode is ListeningMode.ANALYST:
                slots["minute"] = spoken_minute(state.period_id, state.clock_ms)
            return "kickoff_after_goal", slots
        return "kickoff", {"club": club}
    if k is MomentKind.SUBSTITUTION:
        assert isinstance(ev, Substitution) and club
        return "substitution", {
            "club": club,
            "on": roster.players[ev.on_id].short_name,
            "off": roster.players[ev.off_id].short_name,
        }
    if k is MomentKind.INJURY:
        assert player
        return "injury", {"player": player}
    if k is MomentKind.VAR:
        assert isinstance(ev, VarCheck)
        return "var_check", {"var_subject": lex["var_subject"][ev.subject]}
    if k in (MomentKind.HALF_TIME, MomentKind.FULL_TIME):
        return k.value, _score(state, roster)
    return None


@dataclass
class Caller:
    roster: Roster

    def lines(
        self, moment: Moment, ev: Event, state: MatchState, language: Language, mode: ListeningMode
    ) -> list[Line]:
        cat = load(language)
        found = slots_for(moment, ev, state, self.roster, cat, mode)
        if found is None:
            return []
        family, slots = found
        template_id = _pick(cat, family, moment.moment_id, mode)
        # Families whose analyst and casual variants take different slots keep only the slots they use.
        used = placeholders(cat.template(template_id, mode))
        slots = {k: v for k, v in slots.items() if k in used}
        text = cat.render(template_id, mode, slots)
        return [
            Line(
                line_id=f"{moment.moment_id}-{language.value}-{mode.value}",
                match_id=moment.match_id,
                moment_id=moment.moment_id,
                source=LineSource.CALLER,
                language=language,
                listening_mode=mode,
                template_id=template_id,
                slots=slots,
                text=text,
                priority=moment.priority,
                preempt=moment.kind in PREEMPT,
                never_drop=moment.never_drop,
                wall_ms=moment.wall_ms,
                deadline_wall_ms=moment.deadline_wall_ms,
                pan=speech_pan(moment.x) if moment.x is not None else 0.0,
                est_ms=est_ms(text),
                club_tags=moment.club_tags,
                player_ids=moment.player_ids,
            )
        ]
