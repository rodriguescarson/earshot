"""Read-only match tools (CEO-3): one definition, two surfaces.

The Analyst calls these in-process (as Agent Framework function tools, no network hop); the public
MCP app exposes the same functions over streamable HTTP. Every tool:
- reads a ``MatchView``, which by construction holds no event after ``as_of`` (the clock bound);
- returns plain JSON with an ``as_of`` block, so a caller always knows how fresh the answer is;
- computes every number with ``earshot.stats``, the same code the Checker uses.

Tools: ``key_moments``, ``team_window``, ``player_report``, ``catch_up``, ``recap``.
"""

from __future__ import annotations

import math
from typing import Any

from earshot.contract.claim import Metric
from earshot.contract.events import (
    Action,
    Card,
    Event,
    Restart,
    StoppageKind,
    StoppageStart,
    Substitution,
    VarCheck,
)
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.stats.aggregates import goals
from earshot.stats.claims import THRESHOLD, window_values
from earshot.stats.passes import OPEN_PLAY_PASS, speed_kmh
from earshot.tools.snapshots import MatchView

MAX_LIMIT = 50


class ToolInputError(ValueError):
    """A parameter is unknown or out of range; the message says which."""


def spoken_minute(period_id: int, clock_ms: int) -> str:
    """The minute as said on air: 62:10 in the first half is "63"; added time is "45+2"."""
    m = clock_ms // 60_000 + 1
    base = 45 * (period_id - 1)
    return f"{base + 45}+{m - 45}" if m > 45 else str(base + m)


def status(view: MatchView) -> str:
    """before_kickoff, live, dead_ball, half_time or full_time, from the events in the view."""
    open_stop: StoppageStart | None = None
    for e in view.events:
        if isinstance(e, StoppageStart):
            open_stop = e
        elif isinstance(e, Restart):
            open_stop = None
    if not view.events:
        return "before_kickoff"
    if open_stop is None:
        return "live"
    if open_stop.kind in (StoppageKind.HALF_TIME, StoppageKind.FULL_TIME):
        return open_stop.kind.value
    return "dead_ball"


def as_of(view: MatchView) -> dict[str, Any]:
    last = view.events[-1] if view.events else None
    return {
        "status": status(view),
        "match_id": view.match_id,
        "wall_ms": view.as_of_wall_ms,
        "period": last.period_id if last else 1,
        "clock_ms": last.clock_ms if last else 0,
        "minute": spoken_minute(last.period_id, last.clock_ms) if last else "0",
        "staleness_ms": view.staleness_ms,
    }


def _name(view: MatchView, player_id: str | None) -> str | None:
    return view.players[player_id].short_name if player_id and player_id in view.players else None


def _club(view: MatchView, team_id: str | None) -> str | None:
    return view.clubs[team_id].short_name if team_id and team_id in view.clubs else None


def _moment(
    view: MatchView, ev: Event, kind: str, team_id: str | None, player_id: str | None, **extra
) -> dict:
    return {
        "event_id": ev.event_id,
        "kind": kind,
        "minute": spoken_minute(ev.period_id, ev.clock_ms),
        "period": ev.period_id,
        "wall_ms": ev.wall_ms,
        "team_id": team_id,
        "team": _club(view, team_id),
        "player_id": player_id,
        "player": _name(view, player_id),
        **extra,
    }


def _moments(view: MatchView) -> list[dict]:
    out: list[dict] = []
    events = view.events
    home, away = view.info.home.team_id, view.info.away.team_id
    for i, ev in enumerate(events):
        if isinstance(ev, Action) and ev.team_id:
            t = ActionType(ev.type)
            if t in SHOT_TYPES and ev.result == "success":
                kmh = speed_kmh(ev)
                out.append(
                    _moment(
                        view,
                        ev,
                        "goal",
                        ev.team_id,
                        ev.player_id,
                        shot_kmh=round(kmh) if kmh else None,
                        penalty=t is ActionType.SHOT_PENALTY,
                    )
                )
            elif ev.result == "owngoal":
                scorer = away if ev.team_id == home else home
                out.append(_moment(view, ev, "own_goal", scorer, ev.player_id))
            elif t is ActionType.KEEPER_SAVE and i > 0:
                shot = events[i - 1]
                if isinstance(shot, Action) and ActionType(shot.type) in SHOT_TYPES:
                    out.append(
                        _moment(
                            view,
                            shot,
                            "save",
                            shot.team_id,
                            shot.player_id,
                            keeper_id=ev.player_id,
                            keeper=_name(view, ev.player_id),
                        )
                    )
        elif isinstance(ev, Card):
            out.append(
                _moment(view, ev, f"{ev.color.value}_card", ev.team_id, ev.player_id, reason=ev.reason.value)
            )
        elif isinstance(ev, StoppageStart) and ev.kind is StoppageKind.PENALTY:
            out.append(_moment(view, ev, "penalty_awarded", ev.restart_team_id, None))
        elif isinstance(ev, Substitution):
            out.append(
                _moment(
                    view,
                    ev,
                    "substitution",
                    ev.team_id,
                    ev.on_id,
                    off_id=ev.off_id,
                    off=_name(view, ev.off_id),
                )
            )
        elif isinstance(ev, VarCheck):
            out.append(_moment(view, ev, "var_check", None, None, subject=ev.subject, outcome=ev.outcome))
    return out


def _score(view: MatchView) -> dict:
    h, a = goals(list(view.events), view.info.home.team_id, view.info.away.team_id)
    return {
        "home_team": _club(view, view.info.home.team_id),
        "home": h,
        "away_team": _club(view, view.info.away.team_id),
        "away": a,
    }


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolInputError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def key_moments(view: MatchView, limit: int = 10) -> dict:
    """The latest key moments (goals, own goals, saves, cards, penalties, substitutions, VAR), newest last."""
    return {"as_of": as_of(view), "moments": _moments(view)[-_limit(limit) :]}


def team_window(view: MatchView, team_id: str, minutes: int = 10) -> dict:
    """Every claim metric for one club over the last ``minutes`` and the ``minutes`` before."""
    if team_id not in (view.info.home.team_id, view.info.away.team_id):
        raise ToolInputError(f"team_id {team_id!r} is not playing in {view.match_id}")
    if not 2 <= minutes <= 20:
        raise ToolInputError("minutes must be between 2 and 20")
    end = view.as_of_wall_ms
    start = end - minutes * 60_000
    positions = {p.player_id: p.position for p in view.players.values()}
    metrics = {}
    for m in Metric:
        before, after = window_values(m, view.events, team_id, start, end, view.info.home.team_id, positions)
        metrics[m.value] = {
            "previous": None if before is None else round(before, 3),
            "last": None if after is None else round(after, 3),
        }
    return {
        "as_of": as_of(view),
        "team_id": team_id,
        "team": _club(view, team_id),
        "minutes": minutes,
        "window": {"start_wall_ms": start, "end_wall_ms": end},
        "metrics": metrics,
    }


def player_report(view: MatchView, player_id: str) -> dict:
    """One player's match so far: touches, passing, carries, shots and speeds, discipline."""
    if player_id not in view.players:
        raise ToolInputError(f"unknown player_id {player_id!r}")
    p = view.players[player_id]
    if p.team_id not in (view.info.home.team_id, view.info.away.team_id):
        raise ToolInputError(f"{player_id} is not in this match")
    mine = [e for e in view.events if e.player_id == player_id]
    acts = [e for e in mine if isinstance(e, Action)]
    passes = [a for a in acts if a.type in OPEN_PLAY_PASS]
    done = [a for a in passes if a.result == "success"]
    carries = [a for a in acts if a.type == "dribble"]
    shots = [a for a in acts if ActionType(a.type) in SHOT_TYPES]
    shot_speeds = [s for s in (speed_kmh(a) for a in shots) if s]
    lengths = [math.hypot(a.end_x - a.start_x, a.end_y - a.start_y) for a in done]
    cards = [e.color.value for e in mine if isinstance(e, Card)]
    sheet = view.info.home if p.team_id == view.info.home.team_id else view.info.away
    on = player_id in sheet.starting
    for e in view.events:
        if isinstance(e, Substitution) and e.team_id == p.team_id:
            if e.on_id == player_id:
                on = True
            elif e.off_id == player_id:
                on = False
    if "red" in cards:
        on = False
    return {
        "as_of": as_of(view),
        "player_id": player_id,
        "player": p.short_name,
        "team_id": p.team_id,
        "team": _club(view, p.team_id),
        "position": p.position,
        "on_pitch": on,
        "touches": len(acts),
        "passes": {
            "completed": len(done),
            "attempted": len(passes),
            "accuracy": round(len(done) / len(passes), 3) if passes else None,
            "longest_completed_m": round(max(lengths), 1) if lengths else None,
        },
        "carries": {
            "count": len(carries),
            "metres": round(sum(math.hypot(a.end_x - a.start_x, a.end_y - a.start_y) for a in carries), 1),
        },
        "shots": {
            "count": len(shots),
            "goals": sum(1 for s in shots if s.result == "success"),
            "fastest_kmh": round(max(shot_speeds)) if shot_speeds else None,
        },
        "fouls_committed": sum(1 for a in acts if a.type == "foul"),
        "cards": cards,
    }


def catch_up(view: MatchView) -> dict:
    """Score, minute and the last five key moments: what "Catch me up" says."""
    return {"as_of": as_of(view), "score": _score(view), "last_moments": _moments(view)[-5:]}


def recap(view: MatchView) -> dict:
    """Code statistics for a half-time or full-time recap: scorers, cards and team totals."""
    teams = {}
    home = view.info.home.team_id
    total_actions = sum(1 for e in view.events if isinstance(e, Action) and e.team_id)
    for tid in (home, view.info.away.team_id):
        acts = [e for e in view.events if isinstance(e, Action) and e.team_id == tid]
        passes = [a for a in acts if a.type in OPEN_PLAY_PASS]
        shots = [a for a in acts if ActionType(a.type) in SHOT_TYPES]
        saves_against = sum(
            1
            for i, e in enumerate(view.events)
            if isinstance(e, Action)
            and e.type == "keeper_save"
            and e.team_id != tid
            and i > 0
            and view.events[i - 1].team_id == tid
        )
        teams[tid] = {
            "team": _club(view, tid),
            "shots": len(shots),
            "shots_on_target": sum(1 for s in shots if s.result == "success") + saves_against,
            "pass_completion": round(sum(1 for a in passes if a.result == "success") / len(passes), 3)
            if passes
            else None,
            "corners": sum(
                1
                for e in view.events
                if isinstance(e, StoppageStart) and e.kind is StoppageKind.CORNER and e.restart_team_id == tid
            ),
            "action_share": round(len(acts) / total_actions, 3) if total_actions else None,
        }
    moments = _moments(view)
    return {
        "as_of": as_of(view),
        "score": _score(view),
        "goals": [m for m in moments if m["kind"] in ("goal", "own_goal")],
        "cards": [m for m in moments if m["kind"].endswith("_card")],
        "teams": teams,
    }


# ----- claim tools: what the Analyst needs to make a checkable claim (Stream 1) ------------------
# These share the Checker's definitions (earshot.stats.claims), so "notable" here and the
# Checker's threshold can never disagree.

WINDOW_MINUTES = (5, 10)
UNIT: dict[Metric, str] = {
    Metric.PRESS_HEIGHT: "metres from own goal",
    Metric.TEMPO: "on-ball actions per minute",
    Metric.DIRECTNESS: "share of open-play passes that go long and forward",
    Metric.WIDTH: "metres from the middle",
    Metric.LINE_HEIGHT: "metres from own goal",
    Metric.POSSESSION_SHARE: "share of possession",
    Metric.SHOT_COUNT: "shots",
}
TABLE_KEYS = ("team_id", "metric", "before", "after", "change", "threshold", "notable", "evidence_event_ids")


def _home_away(view: MatchView) -> tuple[str, str]:
    return view.info.home.team_id, view.info.away.team_id


def _positions(view: MatchView) -> dict[str, str]:
    return {pid: p.position for pid, p in view.players.items()}


def match_context(view: MatchView) -> dict:
    """Score, each team's score state (ahead, level, behind), red cards, minute and the last goal."""
    home, away = _home_away(view)
    score = {home: 0, away: 0}
    reds = {home: 0, away: 0}
    last_goal = None
    period, clock = 1, 0
    for e in view.events:
        period, clock = e.period_id, e.clock_ms
        if (
            isinstance(e, Action)
            and e.team_id is not None
            and ((ActionType(e.type) in SHOT_TYPES and e.result == "success") or e.result == "owngoal")
        ):
            t = (away if e.team_id == home else home) if e.result == "owngoal" else e.team_id
            score[t] += 1
            last_goal = {
                "team_id": t,
                "minute": spoken_minute(e.period_id, e.clock_ms),
                "event_id": e.event_id,
            }
        if isinstance(e, Card) and e.color == "red" and e.team_id in reds:
            reds[e.team_id] += 1

    def side(t: str, o: str) -> dict:
        state = "ahead" if score[t] > score[o] else "behind" if score[t] < score[o] else "level"
        return {
            "team_id": t,
            "name": _club(view, t),
            "score": score[t],
            "red_cards": reds[t],
            "score_state": state,
        }

    return {
        "home": side(home, away),
        "away": side(away, home),
        "minute": spoken_minute(period, clock),
        "period": period,
        "last_goal": last_goal,
        **as_of(view),
    }


def metric_change(view: MatchView, team_id: str, metric: Metric, minutes: int = 5) -> dict:
    """One metric for one team: the last ``minutes`` against the equal window before it."""
    if minutes not in WINDOW_MINUTES:
        raise ToolInputError(f"minutes must be one of {WINDOW_MINUTES}")
    home, away = _home_away(view)
    if team_id not in (home, away):
        raise ToolInputError(f"unknown team {team_id}")
    span = minutes * 60_000
    now = view.as_of_wall_ms
    past = [e for e in view.events if e.wall_ms < now]
    before, after = window_values(metric, past, team_id, now - span, now, home, _positions(view))
    change = None if before is None or after is None else after - before
    evidence = [
        e.event_id for e in past if isinstance(e, Action) and e.team_id == team_id and e.wall_ms >= now - span
    ][-5:]
    return {
        "team_id": team_id,
        "metric": metric.value,
        "unit": UNIT[metric],
        "minutes": minutes,
        "window_start_ms": now - span,
        "window_end_ms": now,
        "before": None if before is None else round(before, 3),
        "after": None if after is None else round(after, 3),
        "change": None if change is None else round(change, 3),
        "threshold": THRESHOLD[metric],
        "notable": change is not None and abs(change) >= THRESHOLD[metric],
        "evidence_event_ids": evidence,
    }


def metric_table(view: MatchView, minutes: int = 5) -> dict:
    """Every claim metric for both teams: the Analyst's usual first call (the window is shared)."""
    rows = [metric_change(view, t, m, minutes) for t in _home_away(view) for m in Metric]
    return {
        "window_start_ms": rows[0]["window_start_ms"],
        "window_end_ms": rows[0]["window_end_ms"],
        "minutes": minutes,
        "units": {m.value: UNIT[m] for m in Metric},
        "rows": [{k: r[k] for k in TABLE_KEYS} for r in rows],
    }


def recent_events(
    view: MatchView, team_id: str | None = None, minutes: int = 5, limit: int = 20
) -> list[dict]:
    """Recent events with ids, so a claim can cite them."""
    lo = view.as_of_wall_ms - minutes * 60_000
    out = []
    for e in view.events:
        if e.wall_ms < lo or e.wall_ms >= view.as_of_wall_ms or (team_id and e.team_id != team_id):
            continue
        item: dict[str, Any] = {
            "event_id": e.event_id,
            "type": e.type,
            "team_id": e.team_id,
            "minute": spoken_minute(e.period_id, e.clock_ms),
        }
        if isinstance(e, Action):
            item["result"] = str(e.result)
        out.append(item)
    return out[-_limit(limit) :]
