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
from earshot.sim.io import parse_events, sha256
from earshot.sim.runner import MatchOutput, run_many
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


# ----- checks that need the truth log or the fitted models -------------------------------------


def _parsed(o: MatchOutput):
    from earshot.contract.truth import TruthChange

    events = parse_events(o.events)
    info = MatchInfo.model_validate_json(o.info)
    truth = [TruthChange.model_validate_json(x) for x in o.truth.decode().splitlines() if x]
    return events, info, truth


def _positions() -> dict:
    from earshot.world.clubs import world

    return {p.player_id: p.position for p in world().players.values()}


def g4_truth(outputs: list[MatchOutput]) -> GateResult:
    """The truth vocabulary is closed, and every red card is followed by RED_CARD_REORG within 60 s."""
    from earshot.contract.claim import CauseLabel

    fails = []
    reds = changes = 0
    for o in outputs:
        events, _info, truth = _parsed(o)  # parsing rejects any label outside CauseLabel
        changes += len(truth)
        for e in events:
            if isinstance(e, Card) and e.color == "red":
                reds += 1
                ok = any(
                    t.label is CauseLabel.RED_CARD_REORG
                    and t.team_id == e.team_id
                    and 0 <= t.wall_ms - e.wall_ms <= 60_000
                    for t in truth
                )
                if not ok:
                    fails.append(f"{o.match_id}: red card {e.event_id} has no RED_CARD_REORG within 60 s")
    return GateResult(
        "G4", "truth vocabulary", not fails, {"truth_changes": changes, "red_cards": reds}, fails
    )


def g5_planted_signal(outputs: list[MatchOutput]) -> GateResult:
    from earshot.eval.signal import match_samples, summarise

    pos = _positions()
    samples = []
    for o in outputs:
        events, info, truth = _parsed(o)
        samples += match_samples(events, info, truth, pos, o.seed)
    per = summarise(samples)
    fails = [f"{k}: {v}" for k, v in per.items() if not v["passed"]]
    flags = [k for k, v in per.items() if v["too_easy"]]
    return GateResult("G5", "planted signal", not fails, {"per_setting": per, "too_easy": flags}, fails)


def g6_baseline(outputs: list[MatchOutput], shuffles: int = 20) -> GateResult:
    """The baseline detector's recall is at least 1.5x its recall against time-shuffled truth."""
    from earshot.eval import baseline_detector as bd
    from earshot.eval import grading

    pos = _positions()
    preds, truth, spans = [], {}, {}
    for o in outputs:
        events, info, tr = _parsed(o)
        truth[o.match_id], spans[o.match_id] = tr, (0, events[-1].wall_ms)
        preds += [
            grading.Prediction(o.match_id, t, w, lab) for (w, t, lab) in bd.predictions(events, info, pos)
        ]
    real = grading.grade(preds, truth)
    ctrl = statistics.mean(
        grading.grade(preds, grading.shuffled(truth, spans, k)).recall for k in range(shuffles)
    )
    ratio = real.recall / ctrl if ctrl else float("inf")
    detail = {**real.as_dict(), "shuffled_recall": round(ctrl, 4), "ratio": round(ratio, 2), "needed": 1.5}
    return GateResult(
        "G6", "baseline detector", ratio >= 1.5, detail, [] if ratio >= 1.5 else [f"ratio {ratio:.2f}"]
    )


def g7_pass_model(outputs: list[MatchOutput], model=None) -> GateResult:
    """Pass difficulty on held-out matches: log loss <= 0.90x base rate, calibration error <= 0.03."""
    import numpy as np

    from earshot.stats import irls, passes

    m = model or passes.DifficultyModel.load()
    pairs = []
    for o in outputs:
        info = MatchInfo.model_validate_json(o.info)
        pairs.append((parse_events(o.events), info.home.team_id))
    x, y = passes.dataset(pairs)
    p = irls.predict(np.array(m.beta), x)
    ll, base = irls.log_loss(y, p), irls.log_loss(y, np.full(len(y), y.mean()))
    ece = irls.calibration_error(y, p)
    ok = ll <= 0.90 * base and ece <= 0.03
    detail = {"passes": len(y), "log_loss": round(ll, 4), "base_rate_log_loss": round(base, 4),
              "ratio": round(ll / base, 4), "calibration_error": round(ece, 4), "fitted_on": m.fitted_on,
              "xg_model": "not built (xT replaces xG, CEO-7 note)"}  # fmt: skip
    return GateResult("G7", "pass difficulty model", ok, detail, [] if ok else ["thresholds missed"])


def g9_speed(outputs: list[MatchOutput], wall_seconds: float, workers: int) -> GateResult:
    per_match = max(o.seconds for o in outputs)
    ok = per_match < 0.5 and (len(outputs) < 380 or wall_seconds <= 300)
    detail = {"matches": len(outputs), "workers": workers, "wall_s": round(wall_seconds, 2),
              "slowest_match_s": round(per_match, 3)}  # fmt: skip
    return GateResult("G9", "speed", ok, detail, [] if ok else ["too slow"])
