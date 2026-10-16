"""The Director, live: the same rules as the offline Director, decided as time passes.

``offer`` adds a line the moment its audio is ready (and may cut what is on air); ``tick`` starts
the best surviving line whenever the channel is free and drops stale ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from earshot.agents.director import DROP_RULES, MIN_GAP_MS, Profile, can_preempt, effective_priority
from earshot.contract import Line


@dataclass(frozen=True)
class Start:
    line: Line
    at_ms: int
    duration_ms: int
    late: bool


@dataclass(frozen=True)
class Cut:
    line: Line
    at_ms: int


@dataclass(frozen=True)
class Drop:
    line: Line
    at_ms: int
    reason: str


@dataclass
class LiveDirector:
    profile: Profile
    queue: list[tuple[Line, int]] = field(default_factory=list)
    """(line, duration_ms) waiting for the channel."""
    current: tuple[Line, int, int] | None = None
    """(line, start_ms, end_ms) on air."""
    last_end_ms: int = -(10**9)

    def offer(self, line: Line, duration_ms: int, now_ms: int) -> list[Cut]:
        self.queue.append((line, int(duration_ms / self.profile.rate)))
        if self.current and now_ms < self.current[2] and can_preempt(line, self.current[0], self.profile):
            cut = Cut(self.current[0], now_ms)
            self.current = None
            self.last_end_ms = now_ms
            return [cut]
        return []

    def tick(self, now_ms: int) -> list[Start | Drop]:
        out: list[Start | Drop] = []
        if self.current and now_ms >= self.current[2]:
            self.last_end_ms = self.current[2]
            self.current = None
        if self.current or now_ms < self.last_end_ms + MIN_GAP_MS:
            return out
        keep: list[tuple[Line, int]] = []
        for line, dur in self.queue:
            reason = next((r for rule in DROP_RULES if (r := rule(line, now_ms, self.profile))), None)
            if reason:
                out.append(Drop(line, now_ms, reason))
            else:
                keep.append((line, dur))
        self.queue = keep
        if not keep:
            return out
        keep.sort(key=lambda ld: (-effective_priority(ld[0], self.profile), ld[0].wall_ms))
        line, dur = keep[0]
        self.queue = keep[1:]
        late = line.never_drop and now_ms > line.deadline_wall_ms
        self.current = (line, now_ms, now_ms + dur)
        out.append(Start(line, now_ms, dur, late))
        return out
