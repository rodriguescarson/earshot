"""Half-time and full-time recaps (CEO-2): studio text, audio per language, and JSON.

Built by code from the event log only (the ``recap`` match tool and the possession metric), never
from cause labels. Each sentence is a template with code-filled slots, so the same Checker rules
apply: ``check_recap_line`` rebuilds the recap from the events and compares every line.
Recaps are shared per ``{match, language, listening_mode}``, not per listener.
"""

from __future__ import annotations

from typing import Literal

from earshot.agents.catalog import load, placeholders
from earshot.agents.checker import Verdict, ssml_numeric_leak
from earshot.contract import Language, Line, LineSource, ListeningMode
from earshot.contract.line import SlotValue, VoiceRole
from earshot.stats.metrics import possession_ms
from earshot.tools.match_tools import recap
from earshot.tools.snapshots import MatchView

Period = Literal["half_time", "full_time"]
PRIORITY = 60
TTL_MS = 120_000
"""Recaps air in the half-time break or after the final whistle: long dead time."""
MAX_GOALS = 8


def facts(view: MatchView) -> dict:
    """The language-neutral numbers a recap is built from (also the studio JSON)."""
    r = recap(view)
    home, away = view.info.home.team_id, view.info.away.team_id
    poss = possession_ms(view.events, 0, view.as_of_wall_ms + 1)
    total = poss.get(home, 0) + poss.get(away, 0)
    share = {t: (poss.get(t, 0) / total if total else None) for t in (home, away)}
    return {**r, "possession_share": share}


def _sentences(
    view: MatchView, period: Period, mode: ListeningMode
) -> list[tuple[str, dict[str, SlotValue]]]:
    f = facts(view)
    home, away = view.info.home.team_id, view.info.away.team_id
    names = {t: view.clubs[t].short_name for t in (home, away)}
    score = f["score"]
    out: list[tuple[str, dict[str, SlotValue]]] = [
        (
            f"recap.{period}.1",
            {
                "home": names[home],
                "home_score": score["home"],
                "away": names[away],
                "away_score": score["away"],
            },
        )
    ]
    goals = f["goals"][:MAX_GOALS]
    for g in goals:
        kind = "own_goal" if g["kind"] == "own_goal" else "goal"
        out.append(
            (f"recap.{kind}.1", {"player": g["player"], "club": names[g["team_id"]], "minute": g["minute"]})
        )
    if not goals:
        out.append(("recap.no_goals.1", {}))
    for c in f["cards"]:
        if c["kind"] == "red_card":
            out.append(
                (
                    "recap.red_card.1",
                    {"player": c["player"], "club": names[c["team_id"]], "minute": c["minute"]},
                )
            )
    shots = {t: f["teams"][t]["shots"] for t in (home, away)}
    if mode is ListeningMode.ANALYST:
        out.append(
            (
                "recap.shots.1",
                {
                    "home": names[home],
                    "home_shots": shots[home],
                    "away": names[away],
                    "away_shots": shots[away],
                },
            )
        )
    elif shots[home] == shots[away]:
        out.append(("recap.shots_level.1", {}))
    else:
        out.append(("recap.shots.1", {"club": names[max(shots, key=lambda t: shots[t])]}))
    share = f["possession_share"]
    if share[home] is not None and share[away] is not None and round(share[home] * 100) != 50:
        lead = home if share[home] > share[away] else away
        out.append(("recap.possession.1", {"club": names[lead], "percent": round(share[lead] * 100)}))
    return out


def recap_lines(
    view: MatchView, period: Period, language: Language, mode: ListeningMode, *, wall_ms: int
) -> list[Line]:
    cat = load(language)
    lines = []
    for i, (tid, slots) in enumerate(_sentences(view, period, mode)):
        used = {k: v for k, v in slots.items() if k in placeholders(cat.template(tid, mode))}
        text = cat.render(tid, mode, used)
        lines.append(
            Line(
                line_id=f"{view.match_id}-recap-{period}-{i:02d}-{language.value}-{mode.value}",
                match_id=view.match_id,
                moment_id=f"{view.match_id}-recap-{period}",
                source=LineSource.RECAP,
                language=language,
                listening_mode=mode,
                template_id=tid,
                slots=used,
                text=text,
                priority=PRIORITY,
                wall_ms=wall_ms,
                deadline_wall_ms=wall_ms + TTL_MS,
                voice_role=VoiceRole.FACTS,
                est_ms=int(len(text) / 14 * 1000) + 300,
            )
        )
    return lines


def studio(view: MatchView, period: Period, language: Language, mode: ListeningMode, *, wall_ms: int) -> dict:
    """The recap as a studio would take it: one block of text, the lines, and the numbers."""
    lines = recap_lines(view, period, language, mode, wall_ms=wall_ms)
    return {
        "match_id": view.match_id,
        "period": period,
        "language": language.value,
        "mode": mode.value,
        "text": " ".join(ln.text for ln in lines),
        "lines": [
            {"id": ln.line_id, "template_id": ln.template_id, "slots": ln.slots, "text": ln.text}
            for ln in lines
        ],
        "facts": facts(view),
    }


def check_recap_line(line: Line, view: MatchView, period: Period) -> Verdict:
    """Rebuild the recap from the events and require this exact line in it."""
    if line.source is not LineSource.RECAP:
        return Verdict(False, "not a recap line")
    expected = {
        ln.line_id: ln
        for ln in recap_lines(view, period, line.language, line.listening_mode, wall_ms=line.wall_ms)
    }
    exp = expected.get(line.line_id)
    if exp is None:
        return Verdict(False, "no such line in the rebuilt recap")
    if (line.template_id, dict(line.slots), line.text) != (exp.template_id, dict(exp.slots), exp.text):
        return Verdict(False, f"rebuilt {exp.slots} != {line.slots}")
    cat = load(line.language)
    body = cat.ssml_body(line.template_id, line.listening_mode, dict(line.slots))
    leak = ssml_numeric_leak(line.language, body, dict(line.slots))
    return Verdict(False, f"numeric text outside a slot: {leak}") if leak else Verdict(True)
