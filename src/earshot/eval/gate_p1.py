"""The pre-registered P1 gate (thresholds in EVALUATION.md). Each check returns a GateResult.

This module covers the checks that apply to the 40-match development slice from the first
engine PR: G1 determinism, G2 completeness and G10 dead-ball windows. G3-G9 join it as their
inputs (reference ranges, truth log, models) land.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from itertools import pairwise

from earshot.contract.events import Action, Card, Event, Substitution, check_stream
from earshot.contract.match import MatchInfo
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.season.run import MatchOutput, run_many
from earshot.sim.io import parse_events, sha256
from earshot.stats.aggregates import goals


@dataclass
class GateResult:
    gate: str
    name: str
    passed: bool
    detail: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        return f"{self.gate} {self.name}: {'PASS' if self.passed else 'FAIL'} {self.detail}"


def g1_determinism(seeds: list[int], workers: int = 8, first: list[MatchOutput] | None = None) -> GateResult:
    """Two runs give identical sha256 for every file; 1 worker and ``workers`` workers agree."""
    a = first if first is not None else run_many(seeds, workers=workers)
    b = run_many(seeds, workers=1)
    fails = []
    for x, y in zip(sorted(a, key=lambda o: o.seed), sorted(b, key=lambda o: o.seed), strict=True):
        for part in ("events", "info", "truth"):
            if sha256(getattr(x, part)) != sha256(getattr(y, part)):
                fails.append(f"seed {x.seed}: {part} differs")
    return GateResult("G1", "determinism", not fails, {"matches": len(seeds), "workers": [workers, 1]}, fails)


def player_problems(events: list[Event], info: MatchInfo) -> list[str]:
    """Events from players who are not on the pitch at that moment (sent off, subbed off, or unused)."""
    on = {info.home.team_id: set(info.home.starting), info.away.team_id: set(info.away.starting)}
    bench = {info.home.team_id: set(info.home.bench), info.away.team_id: set(info.away.bench)}
    gone: set[str] = set()
    problems = []
    for ev in events:
        if ev.player_id is None:
            continue
        team = ev.team_id or ""
        if isinstance(ev, Substitution):
            if ev.off_id not in on.get(team, set()):
                problems.append(f"{ev.event_id}: {ev.off_id} subbed off while not on the pitch")
            if ev.on_id not in bench.get(team, set()) or ev.on_id in gone:
                problems.append(f"{ev.event_id}: {ev.on_id} subbed on but not on the bench")
            on[team].discard(ev.off_id)
            gone.add(ev.off_id)
            on[team].add(ev.on_id)
            bench[team].discard(ev.on_id)
            continue
        if ev.player_id not in on.get(team, set()):
            problems.append(f"{ev.event_id}: {ev.player_id} ({ev.type}) is not on the pitch")
        if isinstance(ev, Card) and ev.color == "red":
            on[team].discard(ev.player_id)
            gone.add(ev.player_id)
    return problems


def completeness_problems(raw_events: bytes, info: MatchInfo, expected_score: tuple[int, int]) -> list[str]:
    try:
        events = parse_events(raw_events)
    except Exception as exc:  # the gate reports any validation failure as a finding
        return [f"validation: {exc}"]
    problems = check_stream(events)
    walls = [e.wall_ms for e in events]
    if any(b < a for a, b in pairwise(walls)):
        problems.append("wall_ms goes backwards")
    ids = [e.event_id for e in events]
    if len(set(ids)) != len(ids):
        problems.append("duplicate event ids")
    score = goals(events, info.home.team_id, info.away.team_id)
    if score != expected_score:
        problems.append(f"score {expected_score} != goal events {score}")
    problems += player_problems(events, info)
    return problems


def g2_completeness(outputs: list[MatchOutput], expected: int) -> GateResult:
    """All matches present, every event validates, and the stream invariants hold."""
    fails = []
    n_events = 0
    for o in outputs:
        info = MatchInfo.model_validate_json(o.info)
        expected_score = (o.aggregates["home_goals"], o.aggregates["away_goals"])
        problems = completeness_problems(o.events, info, expected_score)
        n_events += o.events.count(b"\n")
        fails += [f"{o.match_id}: {p}" for p in problems]
    passed = len(outputs) == expected and not fails
    return GateResult(
        "G2",
        "completeness",
        passed,
        {"matches": f"{len(outputs)}/{expected}", "events": n_events},
        fails[:50],
    )


def home_goal_end_problems(events: list[Event], info: MatchInfo) -> list[str]:
    """Every goal for the home team ends at x > 100 in the first half and x < 5 in the second."""
    home = info.home.team_id
    out = []
    for ev in events:
        if not isinstance(ev, Action):
            continue
        is_shot_goal = ActionType(ev.type) in SHOT_TYPES and ev.result == "success" and ev.team_id == home
        is_own_goal_for_home = ev.result == "owngoal" and ev.team_id != home
        if is_shot_goal or is_own_goal_for_home:
            ok = ev.end_x > 100 if ev.period_id == 1 else ev.end_x < 5
            if not ok:
                out.append(f"{ev.event_id}: home goal ends at x={ev.end_x} in period {ev.period_id}")
    return out


def g10_dead_ball(outputs: list[MatchOutput]) -> GateResult:
    """Median >= 20 dead-ball windows of 15 s or more per match, and home goals end at the right goal."""
    windows = [o.aggregates["windows_15s"] for o in outputs]
    med = statistics.median(windows)
    fails = []
    for o in outputs:
        fails += [
            f"{o.match_id}: {p}"
            for p in home_goal_end_problems(parse_events(o.events), MatchInfo.model_validate_json(o.info))
        ]
    if med < 20:
        fails.append(f"median windows_15s {med} < 20")
    return GateResult(
        "G10",
        "dead-ball windows",
        not fails,
        {"median_windows_15s": med, "min": min(windows), "max": max(windows)},
        fails[:50],
    )
