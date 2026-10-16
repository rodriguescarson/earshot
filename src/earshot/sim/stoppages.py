"""Dead time per stoppage type.

Each kind has a log-normal dead-time distribution (median, spread, bounds). ``expected_dead_ms``
on a ``stoppage_start`` is the median for its context plus the time of anything the referee
already knows will happen in the stoppage (a card, substitutions, treatment). VAR checks are not
announced in advance, so they never count towards the expectation.

These parameters are frozen at ``eval-freeze``: the Analyst's timeout is derived from them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from earshot.contract.events import StoppageKind as K


@dataclass(frozen=True)
class DeadTime:
    median_ms: int
    sigma: float
    min_ms: int
    max_ms: int

    def draw(self, rng: np.random.Generator) -> int:
        v = self.median_ms * math.exp(self.sigma * float(rng.standard_normal()))
        return int(min(self.max_ms, max(self.min_ms, v)))


DEAD_TIME: dict[K, DeadTime] = {
    K.THROW_IN: DeadTime(14_700, 0.35, 6_000, 50_000),
    K.GOAL_KICK: DeadTime(27_000, 0.30, 12_000, 80_000),
    K.CORNER: DeadTime(32_500, 0.25, 15_000, 90_000),
    K.FREE_KICK: DeadTime(15_000, 0.40, 6_000, 90_000),
    K.OFFSIDE: DeadTime(16_000, 0.30, 8_000, 60_000),
    K.PENALTY: DeadTime(70_000, 0.25, 40_000, 180_000),
    K.GOAL: DeadTime(68_000, 0.20, 40_000, 150_000),
    K.INJURY: DeadTime(70_000, 0.40, 30_000, 240_000),
    K.SUBSTITUTION: DeadTime(30_000, 0.30, 15_000, 90_000),
    K.CARD: DeadTime(20_000, 0.30, 10_000, 60_000),
    K.VAR: DeadTime(70_000, 0.40, 30_000, 240_000),
    K.HALF_TIME: DeadTime(900_000, 0.03, 900_000, 990_000),
    K.FULL_TIME: DeadTime(0, 0.0, 0, 0),
}

FREE_KICK_DANGEROUS = DeadTime(38_000, 0.25, 20_000, 120_000)
"""A free kick in shooting range: the wall is set and measured."""

EXTRA: dict[str, DeadTime] = {
    "card": DeadTime(12_000, 0.30, 5_000, 40_000),
    "substitution": DeadTime(20_000, 0.30, 10_000, 60_000),
    "injury": DeadTime(55_000, 0.45, 20_000, 200_000),
    "var": DeadTime(70_000, 0.40, 30_000, 240_000),
}
"""Time added to a stoppage by what happens inside it."""


def median_dead_ms(kind: K) -> int:
    """The frozen median dead time for a kind (the Analyst's timeout is this minus 4 s)."""
    return DEAD_TIME[kind].median_ms
