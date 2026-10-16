"""Tactical settings over a match, and the scripted changes that make up the hidden truth.

Each team carries five settings on a 0-1 scale (``earshot.world.clubs.SETTINGS``). A change from
the closed vocabulary shifts one or more of them by at least 0.25, ramping in over 60-120 s.
Scripted changes are planned at kick-off (0-3 per team, at least 12 minutes apart). Three more
follow match events: a red card (``RED_CARD_REORG``), trailing late (``CHASING_GAME``) and
leading late (``PROTECTING_LEAD``).

A confounder is never logged: fatigue slows every team's tempo late in the match. It tests
whether an explanation blames tactics for something that is not tactical.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from earshot.contract.claim import CauseLabel as L

LABEL_MOVES: dict[L, dict[str, float]] = {
    L.PRESS_HIGHER: {"press_height": 1.0},
    L.DROP_DEEPER: {"press_height": -1.0, "line_height": -0.8},
    L.GO_DIRECT: {"directness": 1.0},
    L.RAISE_TEMPO: {"tempo": 1.0},
    L.SLOW_TEMPO: {"tempo": -1.0},
    L.WIDEN: {"width": 1.0},
    L.NARROW: {"width": -1.0},
    L.PARK_THE_BUS: {"line_height": -1.0, "press_height": -1.0, "tempo": -0.6},
    L.ALL_OUT_ATTACK: {"line_height": 1.0, "press_height": 0.8, "directness": 0.6, "tempo": 0.5},
    L.RED_CARD_REORG: {"press_height": -1.0, "line_height": -0.8},
    L.CHASING_GAME: {"tempo": 1.0, "directness": 0.8, "line_height": 0.6},
    L.PROTECTING_LEAD: {"press_height": -1.0, "line_height": -0.8, "tempo": -0.6},
}
"""Direction and relative size of each label's moves; the first key is the primary setting."""

SCRIPTED = (
    L.PRESS_HIGHER, L.DROP_DEEPER, L.GO_DIRECT, L.RAISE_TEMPO, L.SLOW_TEMPO, L.WIDEN, L.NARROW,
    L.PARK_THE_BUS, L.ALL_OUT_ATTACK,
)  # fmt: skip

MIN_SHIFT = 0.25
MAX_SHIFT = 0.40
MIN_GAP_MIN = 12.0
LO, HI = 0.05, 0.95


@dataclass(frozen=True)
class PlannedChange:
    label: L
    minute: float
    """Match minute (0-90, second half starting at 45) at which the ramp starts."""
    magnitude: float
    ramp_ms: int


@dataclass
class ActiveChange:
    label: L
    start_wall_ms: int
    ramp_ms: int
    deltas: dict[str, float]

    def weight(self, wall_ms: int) -> float:
        if wall_ms <= self.start_wall_ms:
            return 0.0
        return min(1.0, (wall_ms - self.start_wall_ms) / max(1, self.ramp_ms))


def deltas_for(label: L, magnitude: float) -> dict[str, float]:
    return {k: round(v * magnitude, 4) for k, v in LABEL_MOVES[label].items()}


def fits(settings: dict[str, float], deltas: dict[str, float]) -> bool:
    return all(LO <= settings[k] + d <= HI for k, d in deltas.items())


def plan(rng: np.random.Generator, base: dict[str, float]) -> list[PlannedChange]:
    """Plan 0-3 scripted changes, at least 12 minutes apart, between minutes 12 and 80.

    Each label is picked only if all of its moves fit inside [0.05, 0.95] given the changes
    already planned, so no shift is clipped below the 0.25 minimum.
    """
    n = int(rng.choice(4, p=[0.10, 0.30, 0.40, 0.20]))
    minutes: list[float] = []
    for _ in range(200):
        if len(minutes) == n:
            break
        m = float(rng.uniform(12, 80))
        if 42 <= m <= 48:  # do not ramp across half-time
            continue
        if all(abs(m - o) >= MIN_GAP_MIN for o in minutes):
            minutes.append(m)
    out: list[PlannedChange] = []
    current = dict(base)
    for m in sorted(minutes):
        mag = round(float(rng.uniform(MIN_SHIFT, MAX_SHIFT)), 3)
        options = [lab for lab in SCRIPTED if fits(current, deltas_for(lab, mag))]
        if not options:
            continue
        lab = options[int(rng.integers(len(options)))]
        for k, d in deltas_for(lab, mag).items():
            current[k] += d
        out.append(PlannedChange(lab, round(m, 3), mag, int(rng.integers(60_000, 120_001))))
    return out


def fatigue_tempo(minute: float) -> float:
    """Unlogged tempo drop for every team, from 0 at minute 60 to -0.12 at minute 90."""
    return -0.12 * min(1.0, max(0.0, (minute - 60.0) / 30.0))


def effective(
    base: dict[str, float], changes: list[ActiveChange], wall_ms: int, minute: float
) -> dict[str, float]:
    s = dict(base)
    for c in changes:
        w = c.weight(wall_ms)
        if w:
            for k, d in c.deltas.items():
                s[k] += d * w
    s["tempo"] += fatigue_tempo(minute)
    return {k: min(1.0, max(0.0, v)) for k, v in s.items()}
