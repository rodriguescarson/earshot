"""The broadcast coordinate frame, stereo pan and pitch zones.

Positions use one fixed broadcast frame: x runs 0-105 m from left to right as the main camera
sees it, y runs 0-68 m from the near touchline to the far one. Teams attack in either
direction and swap at half-time; models that need "towards goal" convert with
:func:`to_attacking`.
"""

from __future__ import annotations

from enum import StrEnum

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
HALF_LENGTH = PITCH_LENGTH / 2

SPEECH_PAN_CAP = 0.6
"""Speech is never panned further than this, so the far-side ear still hears it."""

PAN_BUCKETS = (-0.6, -0.3, 0.0, 0.3, 0.6)
"""Speech pan is quantised into five buckets so the TTS cache key stays small."""


def pan(x: float) -> float:
    """Ball pan in [-1, 1] for the continuous layer: -1 hard left, +1 hard right."""
    return max(-1.0, min(1.0, x / HALF_LENGTH - 1.0))


def speech_pan(x: float) -> float:
    """Pan for speech: the ball pan capped at +/- SPEECH_PAN_CAP."""
    return max(-SPEECH_PAN_CAP, min(SPEECH_PAN_CAP, pan(x)))


def pan_bucket(x: float) -> float:
    """The nearest of the five speech pan buckets for a broadcast-frame x."""
    p = speech_pan(x)
    return min(PAN_BUCKETS, key=lambda b: abs(b - p))


def to_attacking(x: float, y: float, attacks_right: bool) -> tuple[float, float]:
    """Convert broadcast-frame coordinates to the attacking frame (team attacks towards x=105)."""
    if attacks_right:
        return x, y
    return PITCH_LENGTH - x, PITCH_WIDTH - y


def attacks_right(home: bool, period_id: int) -> bool:
    """Home attacks right in the first half and left in the second (ends switch at half-time)."""
    return home == (period_id == 1)


class Third(StrEnum):
    DEFENSIVE = "defensive"
    MIDDLE = "middle"
    ATTACKING = "attacking"


class Channel(StrEnum):
    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


def third(x_attacking: float) -> Third:
    if x_attacking < PITCH_LENGTH / 3:
        return Third.DEFENSIVE
    if x_attacking < 2 * PITCH_LENGTH / 3:
        return Third.MIDDLE
    return Third.ATTACKING


def channel(y_attacking: float) -> Channel:
    """Channel from the attacking team's point of view (left wing = their left)."""
    if y_attacking > PITCH_WIDTH * 2 / 3:
        return Channel.LEFT
    if y_attacking < PITCH_WIDTH / 3:
        return Channel.RIGHT
    return Channel.CENTRE
