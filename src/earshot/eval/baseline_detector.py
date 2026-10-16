"""The baseline detector: the required comparator for the Analyst's cause labels.

A transparent rule set over the same code-computed metrics the Analyst sees. At a dead-ball
window starting at ``t``, for one team, it compares each metric over the last 10 minutes with the
10 minutes before. Each change is scaled by the metric's typical 10-minute swing (its spread in
null windows on the development slice, frozen in ``SPREAD``). Then:

1. Game-state rules first: a red card for the team in the last 10 match minutes gives
   ``RED_CARD_REORG``; trailing by 1-2 after minute 65 with tempo or directness up gives
   ``CHASING_GAME``; leading by 1 after minute 72 with press height down gives ``PROTECTING_LEAD``.
2. Otherwise the metric with the largest scaled change, if it is at least ``Z_MIN``, maps to a label:
   press height up or down gives PRESS_HIGHER or DROP_DEEPER; tempo gives RAISE_TEMPO or
   SLOW_TEMPO; directness up gives GO_DIRECT; width gives WIDEN or NARROW; line height down with
   press down gives DROP_DEEPER, line height up with press up gives ALL_OUT_ATTACK.
3. Otherwise no label.

Its thresholds were set on seeds 1-40 and are frozen at ``eval-freeze`` like the Analyst prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from earshot.contract.claim import CauseLabel as L
from earshot.contract.claim import Metric
from earshot.contract.events import Card, Event
from earshot.contract.match import MatchInfo
from earshot.stats.aggregates import goals
from earshot.stats.metrics import Window, metric

WINDOW_MS = 10 * 60_000
Z_MIN = 2.5
SPREAD: dict[Metric, float] = {
    # Standard deviation of the 10-minute before/after change in null windows, seeds 1-40,
    # engine 1.1.0 (``eval.signal``; regenerate with the same command if the engine changes).
    Metric.PRESS_HEIGHT: 7.59,
    Metric.TEMPO: 2.02,
    Metric.DIRECTNESS: 0.0924,
    Metric.WIDTH: 2.36,
    Metric.LINE_HEIGHT: 4.70,
}
_SINGLE: dict[tuple[Metric, int], L] = {
    (Metric.PRESS_HEIGHT, 1): L.PRESS_HIGHER,
    (Metric.PRESS_HEIGHT, -1): L.DROP_DEEPER,
    (Metric.TEMPO, 1): L.RAISE_TEMPO,
    (Metric.TEMPO, -1): L.SLOW_TEMPO,
    (Metric.DIRECTNESS, 1): L.GO_DIRECT,
    (Metric.WIDTH, 1): L.WIDEN,
    (Metric.WIDTH, -1): L.NARROW,
}


@dataclass(frozen=True)
class Detection:
    team_id: str
    wall_ms: int
    label: L | None
    z: dict[str, float]


def _event_minute(e: Event) -> float:
    return (e.period_id - 1) * 45 + e.clock_ms / 60_000


def _minute(events: Sequence[Event], wall_ms: int) -> float:
    last = None
    for e in events:
        if e.wall_ms > wall_ms:
            break
        last = e
    if last is None:
        return 0.0
    return (last.period_id - 1) * 45 + last.clock_ms / 60_000


def detect(
    events: Sequence[Event], info: MatchInfo, team_id: str, wall_ms: int, positions: Mapping[str, str]
) -> Detection:
    """The baseline's label for ``team_id`` at a window starting at ``wall_ms`` (events up to then only)."""
    past = [e for e in events if e.wall_ms < wall_ms]
    home = info.home.team_id
    z: dict[str, float] = {}
    for m, spread in SPREAD.items():
        before = metric(
            m, past, Window(team_id, wall_ms - 2 * WINDOW_MS, wall_ms - WINDOW_MS), home, positions
        )
        after = metric(m, past, Window(team_id, wall_ms - WINDOW_MS, wall_ms), home, positions)
        if before is not None and after is not None:
            z[m.value] = (after - before) / spread
    minute = _minute(past, wall_ms)
    h, a = goals(past, home, info.away.team_id)
    diff = (h - a) if team_id == home else (a - h)
    # Red cards are looked up in match minutes, so a card just before half-time still counts.
    if any(
        isinstance(e, Card) and e.color == "red" and e.team_id == team_id and minute - _event_minute(e) <= 10
        for e in past
    ):
        return Detection(team_id, wall_ms, L.RED_CARD_REORG, z)
    if minute >= 65 and diff in (-1, -2) and (z.get("tempo", 0) >= Z_MIN or z.get("directness", 0) >= Z_MIN):
        return Detection(team_id, wall_ms, L.CHASING_GAME, z)
    if minute >= 72 and diff == 1 and z.get("press_height", 0) <= -Z_MIN:
        return Detection(team_id, wall_ms, L.PROTECTING_LEAD, z)
    if z.get("line_height", 0) <= -Z_MIN and z.get("press_height", 0) <= -1.0:
        return Detection(team_id, wall_ms, L.DROP_DEEPER, z)
    if z.get("line_height", 0) >= Z_MIN and z.get("press_height", 0) >= 1.0:
        return Detection(team_id, wall_ms, L.ALL_OUT_ATTACK, z)
    best = max(
        ((Metric(k), v) for k, v in z.items() if Metric(k) is not Metric.LINE_HEIGHT),
        key=lambda kv: abs(kv[1]),
        default=None,
    )
    if best is None or abs(best[1]) < Z_MIN:
        return Detection(team_id, wall_ms, None, z)
    return Detection(team_id, wall_ms, _SINGLE.get((best[0], 1 if best[1] > 0 else -1)), z)


def predictions(
    events: Sequence[Event], info: MatchInfo, positions: Mapping[str, str]
) -> list[tuple[int, str, L]]:
    """(wall_ms, team_id, label) for every analysis window and team where the baseline gives a label."""
    from earshot.eval.grading import analysis_windows

    out = []
    for w in analysis_windows(events):
        for team in (info.home.team_id, info.away.team_id):
            d = detect(events, info, team, w.wall_ms, positions)
            if d.label is not None:
                out.append((w.wall_ms, team, d.label))
    return out
