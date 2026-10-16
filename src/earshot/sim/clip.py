"""Pick an action-dense 3-minute clip and package it as a self-contained fixture.

A judge clip (design DR-8) has a goal 60-90 s in and as much action as possible around it. The
fixture carries everything a consumer needs without running the engine: the match header, both
squads, the events in the window, the 10 Hz ball track and the state at the clip start. It never
carries truth data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from earshot.contract.ball import BallSample
from earshot.contract.events import Action, Card, Event, Restart, StoppageKind, StoppageStart
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.sim.ballpath import ball_path
from earshot.sim.match import ENGINE_VERSION, MatchResult, simulate
from earshot.stats.aggregates import goals
from earshot.world.clubs import world

CLIP_MS = 180_000
FIXTURE_VERSION = 1


@dataclass(frozen=True)
class Clip:
    seed: int
    start_index: int
    end_index: int
    score: float


def _weight(ev: Event) -> float:
    if isinstance(ev, Action):
        t = ActionType(ev.type)
        if t in SHOT_TYPES:
            return 6.0 if ev.result == "success" else 3.0
        if ev.result == "owngoal":
            return 6.0
        if t in (ActionType.CROSS, ActionType.CORNER_CROSSED, ActionType.TAKE_ON):
            return 1.0
        return 0.1
    if isinstance(ev, Card):
        return 3.0 if ev.color == "red" else 2.0
    if isinstance(ev, StoppageStart) and ev.kind in (StoppageKind.CORNER, StoppageKind.PENALTY):
        return 1.5
    return 0.0


def _is_goal(ev: Event) -> bool:
    return isinstance(ev, Action) and (
        (ActionType(ev.type) in SHOT_TYPES and ev.result == "success") or ev.result == "owngoal"
    )


def best_clip(result: MatchResult, goal_from_ms: int = 60_000, goal_to_ms: int = 90_000) -> Clip | None:
    """The densest window that starts at an in-play action and has a goal 60-90 s in."""
    ev = result.events
    best: Clip | None = None
    for gi, g in enumerate(ev):
        if not _is_goal(g):
            continue
        for si in range(gi, -1, -1):
            s = ev[si]
            lead = g.wall_ms - s.wall_ms
            if lead > goal_to_ms:
                break
            if lead < goal_from_ms or not isinstance(s, Action | Restart) or s.period_id != g.period_id:
                continue
            if isinstance(s, Action) and ActionType(s.type) in SHOT_TYPES:
                continue
            ei = si
            while ei + 1 < len(ev) and ev[ei + 1].wall_ms - s.wall_ms <= CLIP_MS:
                ei += 1
            # Never end inside a stoppage: extend to its restart (and the restart action).
            open_stop = None
            for e in ev[si : ei + 1]:
                if isinstance(e, StoppageStart):
                    open_stop = e
                elif isinstance(e, Restart):
                    open_stop = None
            while open_stop is not None and ei + 1 < len(ev):
                ei += 1
                if isinstance(ev[ei], Restart):
                    open_stop = None
                    ei = min(ei + 1, len(ev) - 1)
            if any(e.period_id != s.period_id for e in ev[si : ei + 1]):
                continue
            score = sum(_weight(e) for e in ev[si : ei + 1])
            if best is None or score > best.score:
                best = Clip(result.info.seed, si, ei, score)
    return best


def pick(seeds: list[int]) -> tuple[MatchResult, Clip]:
    found: list[tuple[MatchResult, Clip]] = []
    for seed in seeds:
        r = simulate(seed)
        c = best_clip(r)
        if c is not None:
            found.append((r, c))
    if not found:
        raise RuntimeError("no clip with a goal 60-90 s in")
    return max(found, key=lambda rc: (rc[1].score, -rc[1].seed))


def fixture(result: MatchResult, clip: Clip) -> dict:
    ev = result.events[clip.start_index : clip.end_index + 1]
    start, end = ev[0].wall_ms, ev[-1].wall_ms
    before = result.events[: clip.start_index]
    h, a = goals(before, result.info.home.team_id, result.info.away.team_id)
    ball: list[BallSample] = [b for b in ball_path(result.events) if start <= b.wall_ms <= end]
    w = world()
    clubs = [w.club(result.info.home.team_id), w.club(result.info.away.team_id)]
    return {
        "fixture_version": FIXTURE_VERSION,
        "engine_version": ENGINE_VERSION,
        "description": (
            "Action-dense 3-minute clip with a goal 60-90 s in. Synthetic: every club and player is made up."
        ),
        "seed": result.info.seed,
        "match": result.info.model_dump(mode="json"),
        "clubs": [c.info().model_dump(mode="json") for c in clubs],
        "players": [p.info().model_dump(mode="json") for c in clubs for p in c.players],
        "clip": {
            "start_wall_ms": start,
            "end_wall_ms": end,
            "period_id": ev[0].period_id,
            "start_clock_ms": ev[0].clock_ms,
            "home_score_at_start": h,
            "away_score_at_start": a,
        },
        "events": [e.model_dump(mode="json") for e in ev],
        "ball": [b.model_dump(mode="json") for b in ball],
    }


def dumps(fx: dict) -> str:
    """Stable, diff-friendly JSON: one list item per line."""
    lines = ["{"]
    keys = list(fx)
    for i, k in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        v = fx[k]
        if isinstance(v, list):
            lines.append(f"  {json.dumps(k)}: [")
            for j, item in enumerate(v):
                lines.append(
                    "    "
                    + json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                    + ("," if j < len(v) - 1 else "")
                )
            lines.append(f"  ]{comma}")
        else:
            lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"
