"""Fit StatWatcher's danger reference: the top-decile cut of a team's 5-minute xT added.

Run: ``uv run python -m earshot.eval.statwatch_reference`` (development seeds 1-40, like the other
frozen models). Writes ``src/earshot/stats/models/statwatch.json``.
"""

from __future__ import annotations

import json

import numpy as np

from earshot.agents.statwatcher import REFERENCE_PATH, danger_samples
from earshot.sim.match import ENGINE_VERSION, simulate

SEEDS = range(1, 41)


def main() -> None:
    samples: list[float] = []
    for seed in SEEDS:
        r = simulate(seed)
        samples += danger_samples(r.events, r.info.home.team_id, r.info.away.team_id)
    out = {
        "fitted_on": f"seeds {SEEDS[0]}-{SEEDS[-1]}, engine {ENGINE_VERSION}",
        "danger_p90": round(float(np.quantile(samples, 0.9)), 6),
        "danger_samples": len(samples),
    }
    REFERENCE_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
