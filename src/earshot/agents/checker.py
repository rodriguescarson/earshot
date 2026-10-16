"""Checker: every line is re-derived by code before it can air.

A Caller line passes only if its slots equal what ``slots_for`` computes from the event and the
match state, its text equals the template rendering, its template carries no numeric literal,
and the final SSML holds no digit or number word outside a slot. Anything else is blocked and
logged; Caller lines are never replaced by free text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from earshot.agents.caller import slots_for
from earshot.agents.catalog import DIGITS, NUMBER_WORDS, NUMERIC_SLOTS, literal_violations, load, placeholders
from earshot.agents.state import MatchState, Roster
from earshot.contract import Event, Language, Line, LineSource, Moment

SAY_AS = re.compile(r"<say-as[^>]*>.*?</say-as>")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


@cache
def _bad_templates(language: Language) -> frozenset[str]:
    return frozenset(v.split(":")[1] for v in literal_violations(language))


def ssml_numeric_leak(language: Language, ssml_body: str, slots: dict) -> str | None:
    """Return the offending text if anything numeric sits outside a numeric slot."""
    stripped = SAY_AS.sub(" ", ssml_body)
    words = re.compile(NUMBER_WORDS[language], re.IGNORECASE)
    for key, value in slots.items():
        if key not in NUMERIC_SLOTS and (DIGITS.search(str(value)) or words.search(str(value))):
            return f"slot {key}={value!r}"
    if m := DIGITS.search(stripped) or words.search(stripped):
        return m.group(0)
    return None


def check_caller_line(line: Line, moment: Moment, ev: Event, state: MatchState, roster: Roster) -> Verdict:
    if line.source is not LineSource.CALLER:
        return Verdict(False, f"not a Caller line: {line.source}")
    cat = load(line.language)
    modes = cat.templates.get(line.template_id)
    if modes is None or line.listening_mode.value not in modes:
        return Verdict(False, f"unknown template {line.template_id}/{line.listening_mode.value}")
    if line.template_id in _bad_templates(line.language):
        return Verdict(False, f"template {line.template_id} has a numeric literal")
    expected = slots_for(moment, ev, state, roster, cat, line.listening_mode)
    if expected is None:
        return Verdict(False, "this moment has no line in this mode")
    family, slots = expected
    if line.template_id.split(".")[0] != family:
        return Verdict(False, f"template family {line.template_id} != {family}")
    used = placeholders(cat.template(line.template_id, line.listening_mode))
    slots = {k: v for k, v in slots.items() if k in used}
    if dict(line.slots) != slots:
        diff = {k: (line.slots.get(k), slots.get(k)) for k in used if line.slots.get(k) != slots.get(k)}
        return Verdict(False, f"slot mismatch {diff}")
    if line.text != cat.render(line.template_id, line.listening_mode, slots):
        return Verdict(False, "text is not the template rendering")
    leak = ssml_numeric_leak(
        line.language, cat.ssml_body(line.template_id, line.listening_mode, slots), slots
    )
    if leak is not None:
        return Verdict(False, f"numeric text outside a slot: {leak}")
    return Verdict(True)
