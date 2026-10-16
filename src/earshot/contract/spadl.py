"""SPADL vocabulary, vendored.

The action types, results and body parts follow SPADL as defined by socceraction
(https://github.com/ML-KULeuven/socceraction, MIT licence, Copyright (c) 2019 KU Leuven DTAI).
We vendor the vocabulary instead of depending on the package: socceraction 1.x does not import
on the current pandera / NumPy 2 stack (eng review E-3). Only the names are copied; no code.
"""

from __future__ import annotations

from enum import StrEnum


class ActionType(StrEnum):
    PASS = "pass"
    CROSS = "cross"
    THROW_IN = "throw_in"
    FREEKICK_CROSSED = "freekick_crossed"
    FREEKICK_SHORT = "freekick_short"
    CORNER_CROSSED = "corner_crossed"
    CORNER_SHORT = "corner_short"
    TAKE_ON = "take_on"
    FOUL = "foul"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    SHOT = "shot"
    SHOT_PENALTY = "shot_penalty"
    SHOT_FREEKICK = "shot_freekick"
    KEEPER_SAVE = "keeper_save"
    KEEPER_CLAIM = "keeper_claim"
    KEEPER_PUNCH = "keeper_punch"
    KEEPER_PICK_UP = "keeper_pick_up"
    CLEARANCE = "clearance"
    BAD_TOUCH = "bad_touch"
    NON_ACTION = "non_action"
    DRIBBLE = "dribble"
    GOAL_KICK = "goal_kick"


class Result(StrEnum):
    FAIL = "fail"
    SUCCESS = "success"
    OFFSIDE = "offside"
    OWNGOAL = "owngoal"
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"


class Bodypart(StrEnum):
    FOOT = "foot"
    HEAD = "head"
    OTHER = "other"
    HEAD_OTHER = "head/other"
    FOOT_LEFT = "foot_left"
    FOOT_RIGHT = "foot_right"


SHOT_TYPES = frozenset({ActionType.SHOT, ActionType.SHOT_PENALTY, ActionType.SHOT_FREEKICK})
FLIGHT_TYPES = frozenset(
    {
        ActionType.PASS,
        ActionType.CROSS,
        ActionType.THROW_IN,
        ActionType.FREEKICK_CROSSED,
        ActionType.FREEKICK_SHORT,
        ActionType.CORNER_CROSSED,
        ActionType.CORNER_SHORT,
        ActionType.CLEARANCE,
        ActionType.GOAL_KICK,
        *SHOT_TYPES,
    }
)
"""Actions where the ball travels through the air or along the ground: they carry ``flight_ms``."""
