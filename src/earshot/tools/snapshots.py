"""What the match tools read: a clock-bounded view of one match (eng E-4).

The worker writes a compact snapshot per match every 5 s plus an append-only event log; the
public MCP app reads them, so its answers are at most 5 s behind live and every answer says how
old it is (``as_of``). Nothing here can hold truth data: snapshots carry the header and the
public event stream only.

Two sources implement the same interface:
- ``SnapshotDir``: files written by ``SnapshotWriter`` (locally a directory; deployed, a Blob
  container the MCP app's identity can only read).
- ``ScheduledReplay``: a stored event log plus the wall-clock time its kick-off aired. Because
  every match is a seeded replay, this gives the same view without a running worker.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, TypeAdapter

from earshot.contract.events import Event
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo

EVENT = TypeAdapter(Event)
SNAPSHOT_EVERY_MS = 5_000


class Snapshot(BaseModel):
    """The compact per-match state the worker publishes every 5 s."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    as_of_wall_ms: int
    """Match wall time covered: every event with wall_ms <= this is in the log."""
    written_at_unix_ms: int
    events_in_log: int
    info: MatchInfo
    clubs: list[ClubInfo]
    players: list[PlayerInfo]


@dataclass(frozen=True)
class MatchView:
    """Everything a tool may see: the match up to ``as_of_wall_ms`` and no further."""

    info: MatchInfo
    clubs: dict[str, ClubInfo]
    players: dict[str, PlayerInfo]
    events: tuple[Event, ...]
    as_of_wall_ms: int
    staleness_ms: int
    """How far behind real time the view is (0 for an in-process view)."""

    def __post_init__(self) -> None:
        if any(e.wall_ms > self.as_of_wall_ms for e in self.events):
            raise ValueError("a match view may not contain events after as_of_wall_ms")

    @property
    def match_id(self) -> str:
        return self.info.match_id

    @classmethod
    def of(
        cls,
        info: MatchInfo,
        clubs: list[ClubInfo],
        players: list[PlayerInfo],
        events: list[Event],
        as_of_wall_ms: int,
        staleness_ms: int = 0,
    ) -> MatchView:
        bounded = tuple(e for e in events if e.wall_ms <= as_of_wall_ms)
        return cls(
            info,
            {c.team_id: c for c in clubs},
            {p.player_id: p for p in players},
            bounded,
            as_of_wall_ms,
            staleness_ms,
        )


class MatchSource(Protocol):
    def match_ids(self) -> list[str]: ...
    def view(self, match_id: str) -> MatchView: ...


class SnapshotWriter:
    """Worker side: append events as they air and rewrite the snapshot at most every 5 s."""

    def __init__(
        self,
        root: Path,
        info: MatchInfo,
        clubs: list[ClubInfo],
        players: list[PlayerInfo],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root, self.info, self.clubs, self.players, self.clock = root, info, clubs, players, clock
        (root / "events").mkdir(parents=True, exist_ok=True)
        (root / "snapshots").mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.last_wall = -1
        self._last_written = -SNAPSHOT_EVERY_MS
        (root / "events" / f"{info.match_id}.ndjson").write_text("")

    def push(self, ev: Event) -> None:
        with (self.root / "events" / f"{self.info.match_id}.ndjson").open("a") as f:
            f.write(EVENT.dump_json(ev).decode() + "\n")
        self.n += 1
        self.last_wall = ev.wall_ms
        if ev.wall_ms - self._last_written >= SNAPSHOT_EVERY_MS:
            self.flush()

    def flush(self) -> None:
        snap = Snapshot(
            match_id=self.info.match_id,
            as_of_wall_ms=max(0, self.last_wall),
            written_at_unix_ms=int(self.clock() * 1000),
            events_in_log=self.n,
            info=self.info,
            clubs=self.clubs,
            players=self.players,
        )
        tmp = self.root / "snapshots" / f"{self.info.match_id}.json.tmp"
        tmp.write_text(snap.model_dump_json())
        tmp.replace(self.root / "snapshots" / f"{self.info.match_id}.json")
        self._last_written = self.last_wall


class SnapshotDir:
    """MCP side: read the latest snapshot and the first ``events_in_log`` lines of the log."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time) -> None:
        self.root, self.clock = root, clock

    def match_ids(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "snapshots").glob("*.json"))

    def view(self, match_id: str) -> MatchView:
        path = self.root / "snapshots" / f"{match_id}.json"
        if not path.exists():
            raise KeyError(match_id)
        snap = Snapshot.model_validate_json(path.read_text())
        lines = (self.root / "events" / f"{match_id}.ndjson").read_text().splitlines()[: snap.events_in_log]
        events = [EVENT.validate_json(x) for x in lines if x]
        staleness = max(0, int(self.clock() * 1000) - snap.written_at_unix_ms)
        return MatchView.of(snap.info, snap.clubs, snap.players, events, snap.as_of_wall_ms, staleness)


class ScheduledReplay:
    """A stored match replayed on the real clock from ``kickoff_unix_s``: the view is whatever has
    aired by now. Used by the MCP app for the demo match when no worker is publishing."""

    def __init__(
        self,
        info: MatchInfo,
        clubs: list[ClubInfo],
        players: list[PlayerInfo],
        events: list[Event],
        kickoff_unix_s: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.info, self.clubs, self.players, self.events = info, clubs, players, events
        self.kickoff, self.clock = kickoff_unix_s, clock

    def match_ids(self) -> list[str]:
        return [self.info.match_id]

    def view(self, match_id: str) -> MatchView:
        if match_id != self.info.match_id:
            raise KeyError(match_id)
        now_wall = max(0, int((self.clock() - self.kickoff) * 1000))
        # Loop the replay so the demo match is always live.
        length = self.events[-1].wall_ms + 60_000
        as_of = now_wall % length
        return MatchView.of(self.info, self.clubs, self.players, self.events, as_of, 0)


def load_dir_events(path: Path) -> list[Event]:
    return [EVENT.validate_json(x) for x in path.read_text().splitlines() if x.strip()]


def dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
