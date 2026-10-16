"""Danger for the continuous layer: the frozen xT value where the ball is, for the team that has it."""

from __future__ import annotations

from functools import cache

from earshot.contract.ball import BallSample
from earshot.pitch import attacks_right, to_attacking
from earshot.stats.xt import MODEL_PATH, XTModel


@cache
def model() -> XTModel:
    return XTModel.load(MODEL_PATH)


def danger(sample: BallSample, home_id: str, m: XTModel | None = None) -> float:
    """0 while the ball is dead or loose; else the cell's xT relative to the most dangerous cell."""
    if not sample.in_play or sample.team_id is None:
        return 0.0
    m = m or model()
    x, y = to_attacking(sample.x, sample.y, attacks_right(sample.team_id == home_id, sample.period_id))
    return round(m.danger(x, y), 3)


def with_danger(samples: list[BallSample], home_id: str) -> list[BallSample]:
    m = model()
    return [s.model_copy(update={"danger": danger(s, home_id, m)}) for s in samples]
