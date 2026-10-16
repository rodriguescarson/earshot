"""Analyst claims: verified by recomputation, then voiced from templates. The model writes no sentences.

``verify`` recomputes the claimed metric over the claimed window and the equal window before it,
checks the direction and that the change clears the metric's threshold, checks any values the
model gave, and checks every cited event exists inside those windows. Only then does the claim
become a line, and the numbers spoken are the recomputed ones, never the model's.
"""

from __future__ import annotations

from dataclasses import dataclass

from earshot.agents.catalog import load, placeholders
from earshot.contract import (
    CauseLabel,
    Claim,
    Direction,
    Language,
    Line,
    LineSource,
    ListeningMode,
    Metric,
    VoiceRole,
)
from earshot.contract.line import SlotValue
from earshot.stats.metrics import Window, metric
from earshot.tools.match import THRESHOLD, WINDOW_MINUTES, MatchView

SHARE_METRICS = frozenset({Metric.DIRECTNESS, Metric.POSSESSION_SHARE})
VALUE_TOLERANCE = {Metric.DIRECTNESS: 0.02, Metric.POSSESSION_SHARE: 0.02}
DEFAULT_TOLERANCE = 0.75
MAX_CLAIM_AGE_MS = 15 * 60_000


@dataclass(frozen=True)
class ClaimCheck:
    ok: bool
    reason: str = ""
    before: float | None = None
    after: float | None = None

    @property
    def size(self) -> float:
        """Change as a multiple of the metric's threshold (for ranking)."""
        return 0.0 if self.before is None or self.after is None else abs(self.after - self.before)


def verify(claim: Claim, view: MatchView) -> ClaimCheck:
    teams = (view.teams.home_id, view.teams.away_id)
    if claim.team_id not in teams:
        return ClaimCheck(False, f"unknown team {claim.team_id}")
    if claim.window_end_ms > view.now_wall_ms:
        return ClaimCheck(False, "window ends in the future")
    if claim.window_start_ms < view.now_wall_ms - MAX_CLAIM_AGE_MS:
        return ClaimCheck(False, "window too old")
    span = claim.window_end_ms - claim.window_start_ms
    if not any(abs(span - m * 60_000) <= 1_000 for m in WINDOW_MINUTES):
        return ClaimCheck(False, f"window length {span} ms is not one of {WINDOW_MINUTES} minutes")
    past = view.past()
    before = metric(
        claim.metric,
        past,
        Window(claim.team_id, claim.window_start_ms - span, claim.window_start_ms),
        view.teams.home_id,
        view.positions,
    )
    after = metric(
        claim.metric,
        past,
        Window(claim.team_id, claim.window_start_ms, claim.window_end_ms),
        view.teams.home_id,
        view.positions,
    )
    if before is None or after is None:
        return ClaimCheck(False, "not enough evidence to verify")
    change = after - before
    if (change > 0) != (claim.direction is Direction.UP) or change == 0:
        return ClaimCheck(
            False, f"direction {claim.direction.value} contradicts change {change:+.3f}", before, after
        )
    if abs(change) < THRESHOLD[claim.metric]:
        return ClaimCheck(
            False, f"change {change:+.3f} below threshold {THRESHOLD[claim.metric]}", before, after
        )
    tol = VALUE_TOLERANCE.get(claim.metric, DEFAULT_TOLERANCE)
    for name, given, actual in (
        ("value_before", claim.value_before, before),
        ("value_after", claim.value_after, after),
    ):
        if given is not None and abs(given - actual) > tol:
            return ClaimCheck(False, f"{name} {given} != recomputed {actual:.3f}", before, after)
    ids = {e.event_id: e for e in past}
    for eid in claim.cited_event_ids:
        ev = ids.get(eid)
        if ev is None:
            return ClaimCheck(False, f"cited event {eid} does not exist", before, after)
        if not (claim.window_start_ms - span <= ev.wall_ms < claim.window_end_ms):
            return ClaimCheck(False, f"cited event {eid} is outside the windows", before, after)
    return ClaimCheck(True, "", before, after)


_UP, _DOWN = Direction.UP, Direction.DOWN
_AGGRESSIVE = {
    (Metric.PRESS_HEIGHT, _UP),
    (Metric.LINE_HEIGHT, _UP),
    (Metric.TEMPO, _UP),
    (Metric.DIRECTNESS, _UP),
    (Metric.POSSESSION_SHARE, _UP),
    (Metric.SHOT_COUNT, _UP),
}
_CAUTIOUS = {
    (Metric.PRESS_HEIGHT, _DOWN),
    (Metric.LINE_HEIGHT, _DOWN),
    (Metric.TEMPO, _DOWN),
    (Metric.WIDTH, _DOWN),
    (Metric.POSSESSION_SHARE, _DOWN),
    (Metric.SHOT_COUNT, _DOWN),
}
CAUSE_METRICS: dict[CauseLabel, set[tuple[Metric, Direction]]] = {
    CauseLabel.PRESS_HIGHER: {(Metric.PRESS_HEIGHT, _UP), (Metric.LINE_HEIGHT, _UP)},
    CauseLabel.DROP_DEEPER: {(Metric.PRESS_HEIGHT, _DOWN), (Metric.LINE_HEIGHT, _DOWN)},
    CauseLabel.GO_DIRECT: {(Metric.DIRECTNESS, _UP)},
    CauseLabel.RAISE_TEMPO: {(Metric.TEMPO, _UP)},
    CauseLabel.SLOW_TEMPO: {(Metric.TEMPO, _DOWN)},
    CauseLabel.WIDEN: {(Metric.WIDTH, _UP)},
    CauseLabel.NARROW: {(Metric.WIDTH, _DOWN)},
    CauseLabel.CHASING_GAME: _AGGRESSIVE,
    CauseLabel.ALL_OUT_ATTACK: _AGGRESSIVE,
    CauseLabel.PROTECTING_LEAD: _CAUTIOUS,
    CauseLabel.PARK_THE_BUS: _CAUTIOUS,
}
"""Which metric changes each cause can explain (RED_CARD_REORG can explain any)."""
RED_CARD_WINDOW_MS = 15 * 60_000


def gate_cause(claim: Claim, view: MatchView) -> tuple[Claim, str | None]:
    """Drop a cause whose checkable preconditions fail.

    The cause itself cannot be verified live (it is graded offline against the truth log), but its
    preconditions can: a team cannot chase a game it is winning, and "going direct" cannot explain
    a change in width. Returns the claim (cause removed if unsupported) and the reason, if any.
    """
    from earshot.tools.match import match_context

    cause = claim.cause
    if cause is None:
        return claim, None
    ctx = match_context(view)
    side = "home" if claim.team_id == view.teams.home_id else "away"
    other = "away" if side == "home" else "home"
    mine, theirs = ctx[side]["score"], ctx[other]["score"]
    reason = None
    if cause is CauseLabel.CHASING_GAME and not mine < theirs:
        reason = "CHASING_GAME but the team is not behind"
    elif cause is CauseLabel.PROTECTING_LEAD and not mine > theirs:
        reason = "PROTECTING_LEAD but the team is not ahead"
    elif cause is CauseLabel.RED_CARD_REORG and not _recent_red(view, claim.team_id):
        reason = "RED_CARD_REORG without a red card for the team in the last 15 minutes"
    elif cause in CAUSE_METRICS and (claim.metric, claim.direction) not in CAUSE_METRICS[cause]:
        reason = f"{cause.value} does not match {claim.metric.value} {claim.direction.value}"
    if reason:
        return claim.model_copy(update={"cause": None}), reason
    return claim, None


def _recent_red(view: MatchView, team_id: str) -> bool:
    from earshot.contract import Card
    from earshot.contract.events import CardColor

    lo = view.now_wall_ms - RED_CARD_WINDOW_MS
    return any(
        isinstance(e, Card) and e.color is CardColor.RED and e.team_id == team_id and e.wall_ms >= lo
        for e in view.past()
    )


def spoken(m: Metric, value: float) -> int:
    return round(value * 100) if m in SHARE_METRICS else round(value)


def claim_slots(claim: Claim, check: ClaimCheck, club_name: str) -> dict[str, SlotValue]:
    assert check.before is not None and check.after is not None
    minutes = round((claim.window_end_ms - claim.window_start_ms) / 60_000)
    return {
        "club": club_name,
        "before": spoken(claim.metric, check.before),
        "after": spoken(claim.metric, check.after),
        "window_min": minutes,
    }


def claim_template_ids(claim: Claim) -> list[str]:
    ids = [f"claim.{claim.metric.value}.{claim.direction.value}.1"]
    if claim.cause is not None:
        ids.append(f"cause.{claim.cause.value}.1")
    return ids


def render_claim(
    language: Language, mode: ListeningMode, template_ids: list[str], slots: dict[str, SlotValue]
) -> tuple[str, dict]:
    """Text of a composite claim line, and the slots each part actually uses."""
    cat = load(language)
    used: dict[str, SlotValue] = {}
    parts = []
    for tid in template_ids:
        names = placeholders(cat.template(tid, mode))
        part_slots = {k: v for k, v in slots.items() if k in names}
        used.update(part_slots)
        parts.append(cat.render(tid, mode, part_slots))
    return " ".join(parts), used


def claim_line(
    claim: Claim,
    check: ClaimCheck,
    *,
    view: MatchView,
    window_id: str,
    language: Language,
    mode: ListeningMode,
    wall_ms: int,
    deadline_wall_ms: int,
    pan: float = 0.0,
) -> Line:
    ids = claim_template_ids(claim)
    text, slots = render_claim(
        language, mode, ids, claim_slots(claim, check, view.teams.names[claim.team_id])
    )
    return Line(
        line_id=f"{window_id}-{claim.team_id}-{claim.metric.value}-{language.value}-{mode.value}",
        match_id=window_id.split("-win-")[0],
        moment_id=window_id,
        source=LineSource.ANALYST,
        language=language,
        listening_mode=mode,
        template_id="+".join(ids),
        slots=slots,
        text=text,
        priority=45,
        wall_ms=wall_ms,
        deadline_wall_ms=deadline_wall_ms,
        voice_role=VoiceRole.EXPLANATION,
        pan=pan,
        est_ms=int(len(text) / 14 * 1000) + 300,
        club_tags=[claim.team_id],
    )


REPEAT_GAP_MS = 10 * 60_000
"""The same claim (team, metric, direction) does not air again within ten minutes."""


def claim_key(claim: Claim) -> tuple[str, str, str]:
    return (claim.team_id, claim.metric.value, claim.direction.value)


def fresh(
    checked: list[tuple[Claim, ClaimCheck]], aired: dict[tuple[str, str, str], int], now_wall_ms: int
) -> list[tuple[Claim, ClaimCheck]]:
    return [
        cc for cc in checked if now_wall_ms - aired.get(claim_key(cc[0]), -REPEAT_GAP_MS) >= REPEAT_GAP_MS
    ]


def select_for_mode(
    checked: list[tuple[Claim, ClaimCheck]], mode: ListeningMode
) -> list[tuple[Claim, ClaimCheck]]:
    """Casual airs one claim per window (the largest change); Analyst up to two per club."""
    ranked = sorted(checked, key=lambda cc: -(cc[1].size / THRESHOLD[cc[0].metric]))
    if mode is ListeningMode.CASUAL:
        return ranked[:1]
    per_club: dict[str, int] = {}
    out = []
    for c, chk in ranked:
        if per_club.get(c.team_id, 0) < 2:
            per_club[c.team_id] = per_club.get(c.team_id, 0) + 1
            out.append((c, chk))
    return out


def check_claim_line(line: Line, claim: Claim, view: MatchView) -> ClaimCheck:
    """The Checker's pass over an Analyst line: re-verify the claim, re-render, scan the SSML."""
    from earshot.agents.checker import ssml_numeric_leak

    chk = verify(claim, view)
    if not chk.ok:
        return chk
    ids = claim_template_ids(claim)
    text, slots = render_claim(
        line.language, line.listening_mode, ids, claim_slots(claim, chk, view.teams.names[claim.team_id])
    )
    if line.template_id != "+".join(ids) or dict(line.slots) != slots or line.text != text:
        return ClaimCheck(False, "line does not match the verified claim's rendering", chk.before, chk.after)
    cat = load(line.language)
    body = " ".join(
        cat.ssml_body(
            t,
            line.listening_mode,
            {k: v for k, v in slots.items() if k in placeholders(cat.template(t, line.listening_mode))},
        )
        for t in ids
    )
    if leak := ssml_numeric_leak(line.language, body, slots):
        return ClaimCheck(False, f"numeric text outside a slot: {leak}", chk.before, chk.after)
    return chk


def window_summary_line(
    view: MatchView,
    *,
    window_id: str,
    language: Language,
    mode: ListeningMode,
    wall_ms: int,
    deadline_wall_ms: int,
) -> Line | None:
    """The code fallback when the Analyst times out or nothing verifies: who has had the ball."""
    from earshot.tools.match import metric_change

    best = None
    for team in (view.teams.home_id, view.teams.away_id):
        mc = metric_change(view, team, Metric.POSSESSION_SHARE, 5)
        if mc["after"] is not None and mc["after"] >= 0.5 and (best is None or mc["after"] > best[1]):
            best = (team, mc["after"])
    if best is None:
        return None
    slots: dict[str, SlotValue] = {
        "club": view.teams.names[best[0]],
        "percent": round(best[1] * 100),
        "window_min": 5,
    }
    cat = load(language)
    tid = "summary.possession.1"
    used = {k: v for k, v in slots.items() if k in placeholders(cat.template(tid, mode))}
    text = cat.render(tid, mode, used)
    return Line(
        line_id=f"{window_id}-summary-{language.value}-{mode.value}",
        match_id=window_id.split("-win-")[0],
        moment_id=window_id,
        source=LineSource.FALLBACK,
        language=language,
        listening_mode=mode,
        template_id=tid,
        slots=used,
        text=text,
        priority=40,
        wall_ms=wall_ms,
        deadline_wall_ms=deadline_wall_ms,
        voice_role=VoiceRole.FACTS,
        est_ms=int(len(text) / 14 * 1000) + 300,
        club_tags=[best[0]],
    )
