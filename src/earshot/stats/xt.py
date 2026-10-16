"""Expected threat (xT) on a 12 x 8 grid, by value iteration (Singh, 2019), in-house (eng E-3).

In the attacking frame, each cell's value is the chance that possession there ends in a goal:

    xT(c) = s(c) * g(c) + m(c) * sum_c' T(c -> c') * xT(c')

where s is the share of actions in c that are shots, g the share of those shots that score, m the
share that are moves, and T the share of moves from c that reach c' successfully. Failed moves
are worth zero. Moves are open-play passes, crosses and carries; shots are open-play shots.

Fitted on the development slice and frozen at ``eval-freeze``; refitted on the full season and the
largest cell change reported (design, "Danger signal").
Reference: Karun Singh, "Introducing Expected Threat (xT)", 2019,
https://karun.in/blog/expected-threat.html
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from earshot.contract.events import Action, Event
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH, attacks_right, to_attacking

NX, NY = 12, 8
MOVES = frozenset({"pass", "cross", "dribble"})
SHOTS = frozenset({"shot"})


def cell(x: float, y: float, nx: int = NX, ny: int = NY) -> int:
    i = min(nx - 1, max(0, int(x / PITCH_LENGTH * nx)))
    j = min(ny - 1, max(0, int(y / PITCH_WIDTH * ny)))
    return i * ny + j


@dataclass
class Counts:
    nx: int
    ny: int
    shots: np.ndarray
    goals: np.ndarray
    moves: np.ndarray
    trans: np.ndarray
    """trans[c, c'] = successful moves from c ending in c'."""

    @classmethod
    def empty(cls, nx: int = NX, ny: int = NY) -> Counts:
        n = nx * ny
        return cls(nx, ny, np.zeros(n), np.zeros(n), np.zeros(n), np.zeros((n, n)))

    def add(self, events: Iterable[Event], home_id: str) -> None:
        for e in events:
            if not isinstance(e, Action) or e.team_id is None:
                continue
            right = attacks_right(e.team_id == home_id, e.period_id)
            x0, y0 = to_attacking(e.start_x, e.start_y, right)
            c = cell(x0, y0, self.nx, self.ny)
            if e.type in SHOTS:
                self.shots[c] += 1
                if e.result == "success":
                    self.goals[c] += 1
            elif e.type in MOVES:
                self.moves[c] += 1
                if e.result == "success":
                    x1, y1 = to_attacking(e.end_x, e.end_y, right)
                    self.trans[c, cell(x1, y1, self.nx, self.ny)] += 1


def solve(c: Counts, iterations: int = 200, tol: float = 1e-10) -> np.ndarray:
    total = c.shots + c.moves
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(total > 0, c.shots / total, 0.0)
        m = np.where(total > 0, c.moves / total, 0.0)
        g = np.where(c.shots > 0, c.goals / c.shots, 0.0)
        t = np.where(c.moves[:, None] > 0, c.trans / c.moves[:, None], 0.0)
    xt = np.zeros(len(total))
    for _ in range(iterations):
        new = s * g + m * (t @ xt)
        if np.max(np.abs(new - xt)) < tol:
            xt = new
            break
        xt = new
    return xt.reshape(c.nx, c.ny)


@dataclass(frozen=True)
class XTModel:
    grid: tuple[tuple[float, ...], ...]
    """grid[i][j]: x-band i (own goal at i=0), y-band j."""
    fitted_on: str

    @property
    def nx(self) -> int:
        return len(self.grid)

    @property
    def ny(self) -> int:
        return len(self.grid[0])

    def value(self, x_att: float, y_att: float) -> float:
        k = cell(x_att, y_att, self.nx, self.ny)
        return self.grid[k // self.ny][k % self.ny]

    @property
    def max_value(self) -> float:
        return max(max(r) for r in self.grid)

    def danger(self, x_att: float, y_att: float) -> float:
        """0-1 danger for the ball msg: the cell's xT relative to the most dangerous cell."""
        return min(1.0, self.value(x_att, y_att) / self.max_value) if self.max_value > 0 else 0.0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"fitted_on": self.fitted_on, "nx": self.nx, "ny": self.ny, "grid": self.grid}, indent=1
            )
            + "\n"
        )

    @classmethod
    def load(cls, path: Path) -> XTModel:
        d = json.loads(path.read_text())
        return cls(tuple(tuple(r) for r in d["grid"]), d["fitted_on"])


def fit(matches: Iterable[tuple[list[Event], str]], fitted_on: str) -> XTModel:
    """Fit on ``(events, home_team_id)`` pairs."""
    c = Counts.empty()
    for events, home in matches:
        c.add(events, home)
    grid = solve(c)
    return XTModel(tuple(tuple(round(float(v), 6) for v in row) for row in grid), fitted_on)


MODEL_PATH = Path(__file__).parent / "models" / "xt.json"
