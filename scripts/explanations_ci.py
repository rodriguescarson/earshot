"""Bootstrap confidence intervals for the held-out Explanations result (run of record).

Resamples the 40 matches with replacement and recomputes each accuracy as a ratio of sums
(correct labels over labels given), so a match with many labels weighs what it weighed in the
point estimate. Reads the per-match rows the harness wrote for the run of record.

    uv run python scripts/explanations_ci.py reports/eval-heldout-of-record-9001-9040.matches.jsonl

Written by Stream 1 on 2026-10-26 against its own per-match export; adapted here to the rows in
reports/, whose per-match figures sit under the "explanations" key.
"""

import json
import random
import sys

ROWS = ("baseline", "analyst_gated", "analyst_proposed")
DRAWS = 10_000
SEED = 20261026


def part(row: dict, key: str) -> dict:
    return (row.get("explanations") or row)[key]


def accuracy(rows: list[dict], key: str) -> float:
    labels = sum(part(r, key)["labels"] for r in rows)
    return sum(part(r, key)["correct"] for r in rows) / labels


def interval(values: list[float]) -> tuple[float, float]:
    values = sorted(values)
    return values[int(0.025 * len(values))], values[int(0.975 * len(values))]


def main(path: str) -> None:
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rng = random.Random(SEED)
    samples = [[rng.choice(rows) for _ in rows] for _ in range(DRAWS)]
    report: dict[str, object] = {"source": path, "matches": len(rows), "draws": DRAWS, "seed": SEED}
    for key in ROWS:
        lo, hi = interval([accuracy(s, key) for s in samples])
        report[key] = {"accuracy": round(accuracy(rows, key), 4), "ci95": [round(lo, 4), round(hi, 4)]}
    gap = [accuracy(s, "baseline") - accuracy(s, "analyst_gated") for s in samples]
    lo, hi = interval(gap)
    report["baseline_minus_gated"] = {
        "points": round(accuracy(rows, "baseline") - accuracy(rows, "analyst_gated"), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
    }
    report["matches_gated_beats_baseline"] = sum(
        part(r, "analyst_gated")["correct"] / max(1, part(r, "analyst_gated")["labels"])
        > part(r, "baseline")["correct"] / max(1, part(r, "baseline")["labels"])
        for r in rows
    )
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
