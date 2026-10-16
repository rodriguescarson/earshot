"""G5 planted signal: do the scripted tactical changes show up in the code-computed metrics?

For every truth change and every setting it moves by at least 0.2, compare the setting's metric
over the 10 minutes before the ramp with the 10 minutes after it. Null samples take the same
before/after comparison at random times with no change for that team nearby. Pass if, per
setting, the AUC of |change| against |null| is at least 0.75 (0.70 for line height) and the
direction is right at least 80% of the time. An AUC above 0.97 is flagged "too easy".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from earshot.contract.claim import Metric
from earshot.contract.events import Event, Kickoff, PeriodEnd
from earshot.contract.match import MatchInfo
from earshot.contract.truth import TruthChange
from earshot.rng import stream
from earshot.stats.metrics import Window, metric

SETTING_METRIC: dict[str, Metric] = {
    "press_height": Metric.PRESS_HEIGHT,
    "tempo": Metric.TEMPO,
    "directness": Metric.DIRECTNESS,
    "width": Metric.WIDTH,
    "line_height": Metric.LINE_HEIGHT,
}
WINDOW_MS = 10 * 60_000
NULL_GAP_MS = 90_000
MIN_DELTA = 0.2
AUC_PASS = {"line_height": 0.70}
AUC_DEFAULT = 0.75
TOO_EASY = 0.97
DIRECTION_PASS = 0.80


@dataclass(frozen=True)
class Sample:
    setting: str
    planted: bool
    delta: float
    sign: int
    """+1 or -1 for planted samples (the planted direction), 0 for null samples."""


def periods(events: Sequence[Event]) -> list[tuple[int, int]]:
    starts = [e.wall_ms for e in events if isinstance(e, Kickoff)]
    ends = [e.wall_ms for e in events if isinstance(e, PeriodEnd)]
    first_kickoffs = sorted(
        {e.period_id: e.wall_ms for e in reversed(events) if isinstance(e, Kickoff)}.values()
    )
    return list(zip(first_kickoffs, ends, strict=True)) if len(ends) == 2 else [(min(starts), max(ends))]


def _inside_one_period(spans: list[tuple[int, int]], a: int, b: int) -> bool:
    return any(lo <= a and b <= hi for lo, hi in spans)


def _delta(events, team, t0, t1, setting, home_id, positions) -> float | None:
    m = SETTING_METRIC[setting]
    before = metric(m, events, Window(team, t0 - WINDOW_MS, t0), home_id, positions)
    after = metric(m, events, Window(team, t1, t1 + WINDOW_MS), home_id, positions)
    if before is None or after is None:
        return None
    return after - before


def match_samples(
    events: Sequence[Event],
    info: MatchInfo,
    truth: list[TruthChange],
    positions: Mapping[str, str],
    seed: int,
) -> list[Sample]:
    spans = periods(events)
    home = info.home.team_id
    out: list[Sample] = []
    for c in truth:
        t0, t1 = c.wall_ms, c.wall_ms + c.ramp_ms
        if not _inside_one_period(spans, t0 - WINDOW_MS, t1 + WINDOW_MS):
            continue
        for setting, d in (c.deltas or {c.setting: c.delta}).items():
            if abs(d) < MIN_DELTA or setting not in SETTING_METRIC:
                continue
            # Skip if another change to the same setting overlaps the comparison.
            clash = any(
                o is not c
                and o.team_id == c.team_id
                and setting in (o.deltas or {o.setting: o.delta})
                and t0 - WINDOW_MS <= o.wall_ms <= t1 + WINDOW_MS
                for o in truth
            )
            if clash:
                continue
            dm = _delta(events, c.team_id, t0, t1, setting, home, positions)
            if dm is not None:
                out.append(Sample(setting, True, dm, 1 if d > 0 else -1))
    rng = stream(seed, "eval", "signal-null")
    for team in (info.home.team_id, info.away.team_id):
        changes = [c.wall_ms for c in truth if c.team_id == team]
        for _ in range(6):
            lo, hi = spans[int(rng.integers(len(spans)))]
            if hi - lo < 2 * WINDOW_MS + NULL_GAP_MS:
                continue
            t0 = int(rng.integers(lo + WINDOW_MS, hi - WINDOW_MS - NULL_GAP_MS))
            if any(abs(t - t0) < WINDOW_MS + 2 * 60_000 for t in changes):
                continue
            for setting in SETTING_METRIC:
                dm = _delta(events, team, t0, t0 + NULL_GAP_MS, setting, home, positions)
                if dm is not None:
                    out.append(Sample(setting, False, dm, 0))
    return out


def auc(pos: list[float], neg: list[float]) -> float:
    """P(a random positive outranks a random negative), ties counting half (Mann-Whitney)."""
    if not pos or not neg:
        return float("nan")
    p = np.asarray(pos)[:, None]
    n = np.asarray(neg)[None, :]
    return float(((p > n).sum() + 0.5 * (p == n).sum()) / (p.size * n.size))


def summarise(samples: list[Sample]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for setting in SETTING_METRIC:
        planted = [s for s in samples if s.setting == setting and s.planted]
        null = [s for s in samples if s.setting == setting and not s.planted]
        a = auc([abs(s.delta) for s in planted], [abs(s.delta) for s in null])
        right = sum(1 for s in planted if s.delta * s.sign > 0) / len(planted) if planted else float("nan")
        need = AUC_PASS.get(setting, AUC_DEFAULT)
        out[setting] = {
            "planted": len(planted),
            "null": len(null),
            "auc": round(a, 3),
            "direction_right": round(right, 3),
            "auc_needed": need,
            "passed": bool(a >= need and right >= DIRECTION_PASS),
            "too_easy": bool(a > TOO_EASY),
        }
    return out
