"""Director: decides what airs on the single audio channel for one listener group, and when.

The rules are an ordered policy table of small pure functions, each tested on its own:
boost for the followed club and player, drop stale lines, mark late never-drop lines, and let
high-priority lines cut lower ones. Scheduling here is event-driven over wall time, so the same
code runs offline (clip rendering, evaluation on an accelerated clock) and live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from earshot.contract import Line, ListeningMode, OutputMode

READY_DELAY_MS = 350
"""Event-to-ready latency used offline (pipeline plus cached audio); live uses measured times."""
MIN_GAP_MS = 250


@dataclass(frozen=True)
class Profile:
    listening_mode: ListeningMode = ListeningMode.CASUAL
    output_mode: OutputMode = OutputMode.APP_VOICE
    club: str | None = None
    player: str | None = None
    rate: float = 1.0


@dataclass(frozen=True)
class Aired:
    line: Line
    start_ms: int
    end_ms: int
    cut_at_ms: int | None = None
    late: bool = False


@dataclass(frozen=True)
class Dropped:
    line: Line
    at_ms: int
    reason: str


def effective_priority(line: Line, profile: Profile) -> int:
    p = line.priority
    if profile.club and profile.club in line.club_tags:
        p += 10
    if profile.player and profile.player in line.player_ids:
        p += 15
    return p


def is_stale(line: Line, now_ms: int) -> bool:
    return not line.never_drop and now_ms > line.deadline_wall_ms


PREEMPT_MARGIN = 15
"""A line only cuts another if it is clearly more important (a goal cuts a shot; a save does not)."""


def can_preempt(incoming: Line, current: Line, profile: Profile) -> bool:
    if not incoming.preempt or current.never_drop:
        return False
    return effective_priority(incoming, profile) >= effective_priority(current, profile) + PREEMPT_MARGIN


Rule = Callable[[Line, int, Profile], str | None]
"""A drop rule: returns a reason to drop the line at ``now``, or None to keep it."""

DROP_RULES: list[Rule] = [
    lambda line, now, _p: "stale" if is_stale(line, now) else None,
]


@dataclass
class Director:
    profile: Profile
    duration_ms: Callable[[Line], int] = lambda line: line.est_ms
    aired: list[Aired] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)

    def _dur(self, line: Line) -> int:
        return int(self.duration_ms(line) / self.profile.rate)

    def run(self, lines: Iterable[Line], ready_delay_ms: int = READY_DELAY_MS) -> list[Aired]:
        pending = sorted(lines, key=lambda line: (line.wall_ms, -line.priority))
        queue: list[tuple[int, Line]] = []
        current: Aired | None = None
        i = 0
        now = pending[0].wall_ms if pending else 0
        while i < len(pending) or queue or current:
            next_ready = pending[i].wall_ms + ready_delay_ms if i < len(pending) else None
            free_at = current.end_ms if current else now
            if next_ready is not None and (current is None or next_ready < free_at):
                now = max(now, next_ready)
                line = pending[i]
                i += 1
                if current and can_preempt(line, current.line, self.profile):
                    self.aired[-1] = Aired(
                        current.line, current.start_ms, now, cut_at_ms=now, late=current.late
                    )
                    current = None
                queue.append((now, line))
                # Everything ready at this same instant competes together.
                while i < len(pending) and pending[i].wall_ms + ready_delay_ms <= now:
                    queue.append((now, pending[i]))
                    i += 1
                if current is not None:
                    continue
            else:
                now = max(now, free_at)
                current = None
            # Channel is free: pick the best surviving line.
            survivors: list[tuple[int, Line]] = []
            for ready, line in queue:
                reason = next((r for rule in DROP_RULES if (r := rule(line, now, self.profile))), None)
                if reason:
                    self.dropped.append(Dropped(line, now, reason))
                else:
                    survivors.append((ready, line))
            queue = survivors
            if not queue:
                continue
            queue.sort(key=lambda rl: (-effective_priority(rl[1], self.profile), rl[1].wall_ms))
            _, best = queue.pop(0)
            start = now + (MIN_GAP_MS if self.aired and self.aired[-1].end_ms >= now - MIN_GAP_MS else 0)
            late = best.never_drop and start > best.deadline_wall_ms
            current = Aired(best, start, start + self._dur(best), late=late)
            self.aired.append(current)
        return self.aired
