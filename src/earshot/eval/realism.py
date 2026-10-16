"""G3 realism: season means against the cited reference bands.

Each metric is tagged "calibrated-to" (engine parameters were tuned towards it on development
seeds 1-120) or "emergent" (never tuned). Calibrated-to metrics passing is expected; the honest
signal is in the emergent ones and in the misses.
"""

from __future__ import annotations

import statistics

from earshot.eval.gate_p1 import GateResult
from earshot.eval.reference import GATED, REPORTED, Ref

CALIBRATED_TO = frozenset(
    {
        "goals", "shots", "pass_completion", "fouls", "yellows", "effective_play_min", "corners",
        "home_win_share", "throw_ins", "offsides", "penalties", "reds",
    }
)  # fmt: skip


def season_means(aggregates: list[dict]) -> dict[str, float]:
    keys = [k for k, v in aggregates[0].items() if isinstance(v, int | float)]
    out = {k: statistics.mean(a[k] for a in aggregates) for k in keys}
    out["home_win_share"] = sum(1 for a in aggregates if a["home_goals"] > a["away_goals"]) / len(aggregates)
    out["draw_share"] = sum(1 for a in aggregates if a["home_goals"] == a["away_goals"]) / len(aggregates)
    out["free_kicks_incl_offside"] = out["free_kicks"] + out["offsides"]
    out["added_time_min"] = statistics.mean(sum(a["added_time_min"]) for a in aggregates)
    out["interruptions"] = statistics.mean(
        a["throw_ins"]
        + a["goal_kicks"]
        + a["corners"]
        + a["free_kicks"]
        + a["offsides"]
        + a["penalties"]
        + a["goals"]
        for a in aggregates
    )
    return out


def _row(ref: Ref, value: float, gated: bool) -> dict:
    lo, hi = ref.band()
    return {
        "metric": ref.metric,
        "engine": round(value, 4),
        "band": [round(lo, 4), round(hi, 4)],
        "reference": list(ref.values),
        "seasons": ref.seasons,
        "source": ref.url,
        "verified_source": ref.verified,
        "inside_band": lo <= value <= hi,
        "gated": gated,
        "tag": "calibrated-to" if ref.metric in CALIBRATED_TO else "emergent",
    }


def g3_realism(aggregates: list[dict]) -> GateResult:
    means = season_means(aggregates)
    rows = [_row(r, means[r.metric], True) for r in GATED]
    rows += [_row(r, means[r.metric], False) for r in REPORTED if r.metric in means]
    fails = [
        f"{r['metric']} {r['engine']} outside {r['band']}"
        for r in rows
        if r["gated"] and not r["inside_band"]
    ]
    return GateResult(
        "G3", "realism (season means)", not fails, {"matches": len(aggregates), "rows": rows}, fails
    )
