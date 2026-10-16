"""Read and write truth logs: one JSON line per ``TruthChange``, one file per match."""

from __future__ import annotations

from pathlib import Path

from earshot.contract.truth import TruthChange

TRUTH_ROOT = Path("data/hidden")


def truth_path(root: Path, run: str, match_id: str) -> Path:
    return root / run / f"{match_id}.truth.jsonl"


def truth_bytes(changes: list[TruthChange]) -> bytes:
    return "".join(c.model_dump_json() + "\n" for c in changes).encode()


def write_truth(root: Path, run: str, match_id: str, changes: list[TruthChange]) -> Path:
    path = truth_path(root, run, match_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(truth_bytes(changes))
    return path


def read_truth(root: Path, run: str, match_id: str) -> list[TruthChange]:
    text = truth_path(root, run, match_id).read_text()
    return [TruthChange.model_validate_json(line) for line in text.splitlines() if line.strip()]
