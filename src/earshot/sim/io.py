"""Canonical serialisation for match outputs: one JSON object per line, fixed field order.

The bytes are what determinism (G1) hashes, so nothing here may depend on dict ordering of
anything but the pydantic models, the platform, or the time of day.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from earshot.contract.ball import BallSample
from earshot.contract.events import Event
from earshot.contract.match import MatchInfo

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def events_jsonl(events: list[Event]) -> bytes:
    return "".join(EVENT_ADAPTER.dump_json(e).decode() + "\n" for e in events).encode()


def parse_events(data: bytes | str) -> list[Event]:
    text = data.decode() if isinstance(data, bytes) else data
    return [EVENT_ADAPTER.validate_json(line) for line in text.splitlines() if line.strip()]


def load_events(path: Path) -> list[Event]:
    return parse_events(path.read_bytes())


def info_json(info: MatchInfo) -> bytes:
    return (info.model_dump_json(indent=1) + "\n").encode()


def load_info(path: Path) -> MatchInfo:
    return MatchInfo.model_validate_json(path.read_bytes())


def ball_jsonl(samples: list[BallSample]) -> bytes:
    return "".join(s.model_dump_json() + "\n" for s in samples).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
