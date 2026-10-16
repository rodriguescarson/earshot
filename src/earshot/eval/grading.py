"""Grade cause labels against the hidden truth log (the Explanations criterion and gate G6).

A label given for team T at a window starting at time t is **correct** if the truth log has a
change with that label for T that started in the 12 minutes before t (``RECENT_MS``): long enough
for a 10-minute measurement window plus the ramp, and no longer than the 12-minute minimum gap
between scripted changes. The same rule grades the Analyst and the baseline detector, on the
same windows of the same held-out matches.

- **Accuracy:** correct labels / labels given (windows where no label is given are not counted).
- **Recall:** truth changes matched by at least one correct label in the 12 minutes after they
  start / truth changes.
- **False alarms per match:** labels given that match no truth change.
- **Shuffled control (G6):** the same recall against truth whose change times are drawn uniformly
  over the match. The baseline passes G6 if its recall is at least 1.5 times the control's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from earshot.contract.claim import CauseLabel
from earshot.contract.events import Event, StoppageKind, StoppageStart
from earshot.contract.truth import TruthChange

RECENT_MS = 12 * 60_000
ANALYSIS_MIN_EXPECTED_MS = 15_000


def analysis_windows(events: Sequence[Event]) -> list[StoppageStart]:
    """The dead-ball windows the Analyst (and the baseline) answer in: every stoppage expected to
    last 15 s or more, except half-time and full-time."""
    return [
        e
        for e in events
        if isinstance(e, StoppageStart)
        and e.expected_dead_ms >= ANALYSIS_MIN_EXPECTED_MS
        and e.kind not in (StoppageKind.HALF_TIME, StoppageKind.FULL_TIME)
    ]


@dataclass(frozen=True)
class Prediction:
    match_id: str
    team_id: str
    wall_ms: int
    label: CauseLabel


def is_correct(p: Prediction, truth: list[TruthChange]) -> bool:
    return any(
        t.team_id == p.team_id and t.label == p.label and 0 <= p.wall_ms - t.wall_ms <= RECENT_MS
        for t in truth
    )


@dataclass(frozen=True)
class Grade:
    labels: int
    correct: int
    truth_changes: int
    recalled: int
    matches: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.labels if self.labels else float("nan")

    @property
    def recall(self) -> float:
        return self.recalled / self.truth_changes if self.truth_changes else float("nan")

    @property
    def false_alarms_per_match(self) -> float:
        return (self.labels - self.correct) / self.matches if self.matches else float("nan")

    def as_dict(self) -> dict:
        return {
            "labels": self.labels,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "truth_changes": self.truth_changes,
            "recalled": self.recalled,
            "recall": round(self.recall, 4),
            "false_alarms_per_match": round(self.false_alarms_per_match, 3),
            "matches": self.matches,
        }


def grade(predictions: list[Prediction], truth: dict[str, list[TruthChange]]) -> Grade:
    by_match: dict[str, list[Prediction]] = {}
    for p in predictions:
        by_match.setdefault(p.match_id, []).append(p)
    labels = correct = changes = recalled = 0
    for mid, ts in truth.items():
        preds = by_match.get(mid, [])
        labels += len(preds)
        correct += sum(1 for p in preds if is_correct(p, ts))
        changes += len(ts)
        recalled += sum(
            1
            for t in ts
            if any(
                p.team_id == t.team_id and p.label == t.label and 0 <= p.wall_ms - t.wall_ms <= RECENT_MS
                for p in preds
            )
        )
    return Grade(labels, correct, changes, recalled, len(truth))


def shuffled(
    truth: dict[str, list[TruthChange]], spans: dict[str, tuple[int, int]], seed: int
) -> dict[str, list[TruthChange]]:
    """Truth with each change moved to a uniform random time within its match (the G6 control)."""
    rng = np.random.default_rng(seed)
    out: dict[str, list[TruthChange]] = {}
    for mid, ts in truth.items():
        lo, hi = spans[mid]
        out[mid] = [t.model_copy(update={"wall_ms": int(rng.integers(lo, hi))}) for t in ts]
    return out
