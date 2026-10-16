"""Live verification of Analyst metric claims (Line Safety: "each metric claim's value and direction
match code-computed statistics over the cited window; cited events exist").

A claim says ``metric`` for ``team_id`` went ``direction`` over ``[window_start_ms, window_end_ms)``.
It is measured against the equal-length window just before it:

    before = metric over [start - L, start),  after = metric over [start, end),  L = end - start

It is accepted only if all of these hold:
- the window has ended by now (no claim about the future) and is between 2 and 20 minutes long;
- both values can be computed (enough evidence);
- the change is in the claimed direction and at least ``THRESHOLD[metric]``;
- any values the claim states are within ``TOLERANCE[metric]`` of the computed ones;
- every cited event exists, belongs to the claimed team, and falls inside ``[start - L, end)``.

The cause label is not checked here: it cannot be verified live and is graded offline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from earshot.contract.claim import Claim, Direction, Metric
from earshot.contract.events import Event
from earshot.stats.metrics import Window, metric

THRESHOLD: dict[Metric, float] = {
    Metric.PRESS_HEIGHT: 5.0,
    Metric.TEMPO: 2.0,
    Metric.DIRECTNESS: 0.08,
    Metric.WIDTH: 2.0,
    Metric.LINE_HEIGHT: 4.0,
    Metric.POSSESSION_SHARE: 0.08,
    Metric.SHOT_COUNT: 2.0,
}
"""The smallest change worth a claim. The Director orders claims by change / threshold."""

TOLERANCE: dict[Metric, float] = {
    Metric.PRESS_HEIGHT: 0.5,
    Metric.TEMPO: 0.5,
    Metric.DIRECTNESS: 0.01,
    Metric.WIDTH: 0.5,
    Metric.LINE_HEIGHT: 0.5,
    Metric.POSSESSION_SHARE: 0.01,
    Metric.SHOT_COUNT: 0.0,
}
"""How far a stated value may be from the computed one (rounding in the Analyst's JSON)."""

MIN_WINDOW_MS = 2 * 60_000
MAX_WINDOW_MS = 20 * 60_000


@dataclass(frozen=True)
class ClaimVerdict:
    ok: bool
    reason: str
    before: float | None = None
    after: float | None = None


def window_values(
    m: Metric, events: Sequence[Event], team_id: str, start: int, end: int, home_id: str,
    positions: Mapping[str, str],
) -> tuple[float | None, float | None]:  # fmt: skip
    length = end - start
    before = metric(m, events, Window(team_id, start - length, start), home_id, positions)
    after = metric(m, events, Window(team_id, start, end), home_id, positions)
    return before, after


def strength(m: Metric, before: float | None, after: float | None) -> float:
    if before is None or after is None:
        return 0.0
    return abs(after - before) / THRESHOLD[m]


def verify_claim(
    claim: Claim, events: Sequence[Event], home_id: str, positions: Mapping[str, str], now_wall_ms: int
) -> ClaimVerdict:
    start, end = claim.window_start_ms, claim.window_end_ms
    if end > now_wall_ms:
        return ClaimVerdict(False, "window ends in the future")
    if not MIN_WINDOW_MS <= end - start <= MAX_WINDOW_MS:
        return ClaimVerdict(False, f"window length {end - start} ms outside 2-20 min")
    past = [e for e in events if e.wall_ms < now_wall_ms]
    before, after = window_values(claim.metric, past, claim.team_id, start, end, home_id, positions)
    if before is None or after is None:
        return ClaimVerdict(False, "not enough evidence to compute the metric", before, after)
    change = after - before
    want_up = claim.direction is Direction.UP
    if (change > 0) != want_up or change == 0:
        return ClaimVerdict(False, f"direction wrong: {before:.3f} -> {after:.3f}", before, after)
    if abs(change) < THRESHOLD[claim.metric]:
        return ClaimVerdict(
            False, f"change {change:.3f} below threshold {THRESHOLD[claim.metric]}", before, after
        )
    tol = TOLERANCE[claim.metric]
    for name, stated, computed in (
        ("before", claim.value_before, before),
        ("after", claim.value_after, after),
    ):
        if stated is not None and abs(stated - computed) > tol + 1e-9:
            return ClaimVerdict(False, f"value_{name} {stated} != computed {computed:.3f}", before, after)
    by_id = {e.event_id: e for e in past}
    for eid in claim.cited_event_ids:
        ev = by_id.get(eid)
        if ev is None:
            return ClaimVerdict(False, f"cited event {eid} does not exist", before, after)
        if not start - (end - start) <= ev.wall_ms < end:
            return ClaimVerdict(False, f"cited event {eid} is outside the window", before, after)
        # Press height also rests on the opponent's actions under pressure, so it may cite them.
        if ev.team_id not in (claim.team_id, None) and claim.metric is not Metric.PRESS_HEIGHT:
            return ClaimVerdict(False, f"cited event {eid} belongs to the other team", before, after)
    return ClaimVerdict(True, "ok", before, after)
