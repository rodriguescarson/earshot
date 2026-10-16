"""The match tools. Pure functions over the event stream up to ``now``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from earshot.contract import Action, Card, Event, Metric
from earshot.contract.events import CardColor
from earshot.stats.football import is_goal, scoring_team_id, spoken_minute
from earshot.stats.metrics import Window, metric

WINDOW_MINUTES = (5, 10)

THRESHOLD: dict[Metric, float] = {
    Metric.PRESS_HEIGHT: 4.0,
    Metric.TEMPO: 3.0,
    Metric.DIRECTNESS: 0.08,
    Metric.WIDTH: 3.0,
    Metric.LINE_HEIGHT: 4.0,
    Metric.POSSESSION_SHARE: 0.10,
    Metric.SHOT_COUNT: 2.0,
}
"""Smallest change worth a claim. The Checker rejects claims about anything smaller."""

UNIT: dict[Metric, str] = {
    Metric.PRESS_HEIGHT: "metres from own goal",
    Metric.TEMPO: "on-ball actions per minute",
    Metric.DIRECTNESS: "share of open-play passes that go long and forward",
    Metric.WIDTH: "metres from the middle",
    Metric.LINE_HEIGHT: "metres from own goal",
    Metric.POSSESSION_SHARE: "share of possession",
    Metric.SHOT_COUNT: "shots",
}


@dataclass(frozen=True)
class Teams:
    home_id: str
    away_id: str
    names: Mapping[str, str]


@dataclass(frozen=True)
class MatchView:
    """Everything the tools may see: the match so far, never past ``now_wall_ms``."""

    events: Sequence[Event]
    teams: Teams
    positions: Mapping[str, str]
    now_wall_ms: int

    def past(self) -> list[Event]:
        return [e for e in self.events if e.wall_ms < self.now_wall_ms]


def windows(now_wall_ms: int, minutes: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """(previous window, current window), each ``minutes`` long, ending at now."""
    span = minutes * 60_000
    return (now_wall_ms - 2 * span, now_wall_ms - span), (now_wall_ms - span, now_wall_ms)


def match_context(v: MatchView) -> dict:
    past = v.past()
    home, away = v.teams.home_id, v.teams.away_id
    score = {home: 0, away: 0}
    reds = {home: 0, away: 0}
    last_goal = None
    period, clock = 1, 0
    for e in past:
        period, clock = e.period_id, e.clock_ms
        if isinstance(e, Action) and is_goal(e):
            t = scoring_team_id(e, home, away)
            score[t] += 1
            last_goal = {
                "team_id": t,
                "minute": spoken_minute(e.period_id, e.clock_ms),
                "event_id": e.event_id,
            }
        if isinstance(e, Card) and e.color is CardColor.RED and e.team_id in reds:
            reds[e.team_id] += 1
    return {
        "home": {
            "team_id": home,
            "name": v.teams.names[home],
            "score": score[home],
            "red_cards": reds[home],
        },
        "away": {
            "team_id": away,
            "name": v.teams.names[away],
            "score": score[away],
            "red_cards": reds[away],
        },
        "minute": spoken_minute(period, clock),
        "period": period,
        "last_goal": last_goal,
    }


def metric_change(v: MatchView, team_id: str, m: Metric, minutes: int = 5) -> dict:
    if minutes not in WINDOW_MINUTES:
        raise ValueError(f"minutes must be one of {WINDOW_MINUTES}")
    if team_id not in (v.teams.home_id, v.teams.away_id):
        raise ValueError(f"unknown team {team_id}")
    past = v.past()
    (b0, b1), (a0, a1) = windows(v.now_wall_ms, minutes)
    before = metric(m, past, Window(team_id, b0, b1), v.teams.home_id, v.positions)
    after = metric(m, past, Window(team_id, a0, a1), v.teams.home_id, v.positions)
    change = None if before is None or after is None else after - before
    evidence = [
        e.event_id for e in past if isinstance(e, Action) and e.team_id == team_id and a0 <= e.wall_ms < a1
    ][-5:]
    return {
        "team_id": team_id,
        "metric": m.value,
        "unit": UNIT[m],
        "minutes": minutes,
        "window_start_ms": a0,
        "window_end_ms": a1,
        "before": None if before is None else round(before, 3),
        "after": None if after is None else round(after, 3),
        "change": None if change is None else round(change, 3),
        "threshold": THRESHOLD[m],
        "notable": change is not None and abs(change) >= THRESHOLD[m],
        "evidence_event_ids": evidence,
    }


TABLE_KEYS = ("team_id", "metric", "before", "after", "change", "threshold", "notable", "evidence_event_ids")


def metric_table(v: MatchView, minutes: int = 5) -> dict:
    """Every metric for both teams: the Analyst's usual first call (compact: the window is shared)."""
    rows = [metric_change(v, t, m, minutes) for t in (v.teams.home_id, v.teams.away_id) for m in Metric]
    return {
        "window_start_ms": rows[0]["window_start_ms"],
        "window_end_ms": rows[0]["window_end_ms"],
        "minutes": minutes,
        "units": {m.value: UNIT[m] for m in Metric},
        "rows": [{k: r[k] for k in TABLE_KEYS} for r in rows],
    }


def recent_events(v: MatchView, team_id: str | None = None, minutes: int = 5, limit: int = 20) -> list[dict]:
    lo = v.now_wall_ms - minutes * 60_000
    out = []
    for e in v.past():
        if e.wall_ms < lo or (team_id and e.team_id != team_id):
            continue
        item = {
            "event_id": e.event_id,
            "type": e.type,
            "team_id": e.team_id,
            "minute": spoken_minute(e.period_id, e.clock_ms),
        }
        if isinstance(e, Action):
            item["result"] = e.result.value
        out.append(item)
    return out[-limit:]
