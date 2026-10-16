"""The 10 Hz ball path, computed from the events after the match (never stored for the season).

Key points come from the actions: each action starts the ball at its start position, and the
ball reaches the end position after ``flight_ms`` (passes, shots, clearances) or when the next
action begins (carries, take-ons). Between key points the ball moves in a straight line. During
a stoppage the ball is marked dead and drifts to the restart spot. There are no samples during
the half-time interval.
"""

from __future__ import annotations

import numpy as np

from earshot.contract.ball import BALL_STEP_MS, BallSample
from earshot.contract.events import Action, Event, Kickoff, PeriodEnd, Restart, StoppageStart
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH

_CARRY_CAP_MS = 4_000


def _keyframes(events: list[Event]) -> list[tuple[int, float, float]]:
    keys: list[tuple[int, float, float]] = []

    def add(t: int, x: float, y: float) -> None:
        if keys and t < keys[-1][0]:
            t = keys[-1][0]
        keys.append((t, x, y))

    for i, ev in enumerate(events):
        if isinstance(ev, Action):
            add(ev.wall_ms, ev.start_x, ev.start_y)
            if ev.flight_ms is not None:
                add(ev.wall_ms + ev.flight_ms, ev.end_x, ev.end_y)
            elif (ev.end_x, ev.end_y) != (ev.start_x, ev.start_y):
                nxt = next((e.wall_ms for e in events[i + 1 :] if isinstance(e, Action)), ev.wall_ms)
                add(min(nxt, ev.wall_ms + _CARRY_CAP_MS), ev.end_x, ev.end_y)
        elif isinstance(ev, Kickoff):
            add(ev.wall_ms, PITCH_LENGTH / 2, PITCH_WIDTH / 2)
        elif isinstance(ev, StoppageStart) and ev.x is not None and ev.y is not None:
            add(ev.wall_ms, ev.x, ev.y)
    return keys


def ball_path(events: list[Event], step_ms: int = BALL_STEP_MS) -> list[BallSample]:
    """Ball samples every ``step_ms`` of wall time across both periods."""
    keys = _keyframes(events)
    kt = np.array([k[0] for k in keys], dtype=np.float64)
    kx = np.array([k[1] for k in keys])
    ky = np.array([k[2] for k in keys])

    # Period wall ranges and offsets (wall_ms - clock_ms).
    periods: dict[int, list[int]] = {}
    for ev in events:
        if isinstance(ev, Kickoff) and ev.period_id not in periods:
            periods[ev.period_id] = [ev.wall_ms, ev.wall_ms, ev.wall_ms - ev.clock_ms]
        if isinstance(ev, PeriodEnd):
            periods[ev.period_id][1] = ev.wall_ms

    # Dead intervals and possession changes, as sorted wall-time markers.
    dead: list[tuple[int, int]] = []
    open_at: int | None = None
    poss_t: list[int] = []
    poss_team: list[str | None] = []
    for ev in events:
        if isinstance(ev, StoppageStart):
            open_at = ev.wall_ms
        elif isinstance(ev, Restart) and open_at is not None:
            dead.append((open_at, ev.wall_ms))
            open_at = None
        elif isinstance(ev, Action) and ev.team_id and ev.type != "foul":
            poss_t.append(ev.wall_ms)
            poss_team.append(ev.team_id)
    if open_at is not None:
        dead.append((open_at, events[-1].wall_ms + 1))

    out: list[BallSample] = []
    for pid in sorted(periods):
        start, end, offset = periods[pid]
        ts = np.arange(start, end + 1, step_ms, dtype=np.int64)
        xs = np.clip(np.interp(ts, kt, kx), 0.0, PITCH_LENGTH)
        ys = np.clip(np.interp(ts, kt, ky), 0.0, PITCH_WIDTH)
        live = np.ones(len(ts), dtype=bool)
        for a, b in dead:
            live[(ts >= a) & (ts < b)] = False
        idx = np.searchsorted(np.array(poss_t), ts, side="right") - 1
        for t, x, y, lv, j in zip(
            ts.tolist(), xs.tolist(), ys.tolist(), live.tolist(), idx.tolist(), strict=True
        ):
            out.append(
                BallSample(
                    wall_ms=t,
                    period_id=pid,  # type: ignore[arg-type]
                    clock_ms=t - offset,
                    x=round(x, 2),
                    y=round(y, 2),
                    in_play=lv,
                    team_id=poss_team[j] if (lv and j >= 0) else None,
                )
            )
    return out
