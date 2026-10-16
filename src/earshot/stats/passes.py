"""Pass metrics (CEO-1): distance, accuracy, difficulty; ball and shot speed.

- **Distance:** metres, start to end.
- **Accuracy:** completed over attempted, per player per half (Analyst mode only).
- **Difficulty:** the modelled probability that the pass fails. A logistic fit (IRLS) on distance,
  angle, forward share, zone and pressure (``nearest_opp_m``, under 3 m = under pressure).
  Fitted on development seeds 1-40 and frozen at ``eval-freeze``, with the deciles used for
  "difficulty N out of 10" and "a hard pass" (deciles 8-10).
- **Ball and shot speed:** straight-line distance over the engine's ``flight_ms``, in km/h.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from earshot.contract.events import Action, Event
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH, attacks_right, to_attacking
from earshot.stats import irls

OPEN_PLAY_PASS = frozenset({"pass", "cross"})
FEATURES = (
    "distance_m",
    "abs_angle_rad",
    "forward_share",
    "end_x_frac",
    "end_wide_frac",
    "end_in_box",
    "under_pressure",
    "nearest_opp_m_capped",
    "is_cross",
    "forward_m",
    "end_beyond_75",
)
MODEL_PATH = Path(__file__).parent / "models" / "pass_difficulty.json"


def distance_m(a: Action) -> float:
    return math.hypot(a.end_x - a.start_x, a.end_y - a.start_y)


def speed_kmh(a: Action) -> float | None:
    """Ball speed for any action with a flight time: straight-line distance over flight_ms."""
    if not a.flight_ms:
        return None
    return distance_m(a) / (a.flight_ms / 1000) * 3.6


def is_shot(a: Action) -> bool:
    return ActionType(a.type) in SHOT_TYPES


def features(a: Action, home_id: str) -> list[float]:
    right = attacks_right(a.team_id == home_id, a.period_id)
    x0, y0 = to_attacking(a.start_x, a.start_y, right)
    x1, y1 = to_attacking(a.end_x, a.end_y, right)
    d = math.hypot(x1 - x0, y1 - y0)
    angle = abs(math.atan2(y1 - y0, x1 - x0)) if d > 0 else 0.0
    near = a.nearest_opp_m if a.nearest_opp_m is not None else 10.0
    return [
        d,
        angle,
        (x1 - x0) / d if d > 0 else 0.0,
        x1 / PITCH_LENGTH,
        abs(y1 - PITCH_WIDTH / 2) / (PITCH_WIDTH / 2),
        1.0 if (x1 > 88.5 and abs(y1 - PITCH_WIDTH / 2) < 20.16) else 0.0,
        1.0 if near < 3.0 else 0.0,
        min(near, 10.0),
        1.0 if a.type == "cross" else 0.0,
        max(0.0, x1 - x0),
        1.0 if x1 > 75 else 0.0,
    ]


def dataset(matches: Iterable[tuple[list[Event], str]]) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for events, home in matches:
        for e in events:
            if isinstance(e, Action) and e.type in OPEN_PLAY_PASS and e.result in ("success", "fail"):
                xs.append(features(e, home))
                ys.append(1.0 if e.result == "fail" else 0.0)
    return np.array(xs), np.array(ys)


@dataclass(frozen=True)
class DifficultyModel:
    beta: tuple[float, ...]
    deciles: tuple[float, ...]
    """Nine cut points: difficulty N out of 10 means the predicted failure probability falls in decile N."""
    fitted_on: str

    def probability(self, a: Action, home_id: str) -> float:
        return float(irls.predict(np.array(self.beta), np.array([features(a, home_id)]))[0])

    def out_of_ten(self, p: float) -> int:
        return 1 + sum(1 for c in self.deciles if p > c)

    def is_hard(self, p: float) -> bool:
        """Casual mode says "a hard pass" only for deciles 8-10."""
        return self.out_of_ten(p) >= 8

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fitted_on": self.fitted_on,
                    "features": FEATURES,
                    "beta": self.beta,
                    "deciles": self.deciles,
                },
                indent=1,
            )
            + "\n"
        )

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> DifficultyModel:
        d = json.loads(path.read_text())
        if tuple(d["features"]) != FEATURES:
            raise ValueError("pass difficulty model was fitted on different features")
        return cls(tuple(d["beta"]), tuple(d["deciles"]), d["fitted_on"])


def fit(matches: Iterable[tuple[list[Event], str]], fitted_on: str) -> DifficultyModel:
    x, y = dataset(matches)
    beta = irls.fit(x, y)
    p = irls.predict(beta, x)
    deciles = tuple(float(np.quantile(p, q)) for q in np.arange(0.1, 1.0, 0.1))
    return DifficultyModel(
        tuple(round(float(b), 6) for b in beta), tuple(round(c, 6) for c in deciles), fitted_on
    )


def accuracy(events: list[Event], player_id: str, period_id: int) -> tuple[int, int]:
    """(completed, attempted) open-play passes for a player in a half."""
    done = tried = 0
    for e in events:
        if (
            isinstance(e, Action)
            and e.player_id == player_id
            and e.period_id == period_id
            and e.type in OPEN_PLAY_PASS
        ):
            tried += 1
            done += e.result == "success"
    return done, tried
