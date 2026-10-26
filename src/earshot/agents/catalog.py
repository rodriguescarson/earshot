"""The reviewed template catalogue: every spoken sentence comes from here plus code-filled slots.

Templates are written and reviewed offline per language (Line Safety). A template's literal text
may not contain digits or number words; numbers only ever enter through slots.
"""

from __future__ import annotations

import json
import os
import re
import string
from dataclasses import dataclass
from functools import cache
from importlib import resources
from xml.sax.saxutils import escape

from earshot.contract import Language, ListeningMode
from earshot.contract.line import SlotValue

LOCALES = {Language.EN: "en-GB", Language.ES: "es-ES", Language.AR: "ar-SA", Language.HI: "hi-IN"}

VOICES_STANDARD = {
    # (facts voice, explanation voice): two voices per language (CEO-5)
    Language.EN: ("en-GB-RyanNeural", "en-GB-SoniaNeural"),
    Language.ES: ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
    Language.AR: ("ar-SA-HamedNeural", "ar-SA-ZariyahNeural"),
    Language.HI: ("hi-IN-ArjunNeural", "hi-IN-AartiNeural"),
}
# Azure HD voices (Carson, 2026-10-26, after an A/B on the same goal line): the HD model sets
# emotion from the text, so these take no prosody wrapper. Where a language has no native HD
# voice for a role, the standard voice stays: Arabic's explanation voice (the only native Arabic
# HD voice is male) and Hindi's facts voice (the Hindi HD voices speak short lines about twice
# as slowly as the standard ones, measured 2026-10-26, and the facts voice carries most lines).
VOICES_HD = {
    Language.EN: ("en-GB-Ryan:DragonHDLatestNeural", "en-GB-Sonia:DragonHDLatestNeural"),
    Language.ES: ("es-ES-Tristan:DragonHDLatestNeural", "es-ES-Ximena:DragonHDLatestNeural"),
    Language.AR: ("ar-AE-Rashid:DragonHDLatestNeural", "ar-SA-ZariyahNeural"),
    Language.HI: ("hi-IN-ArjunNeural", "hi-IN-Kavya:MAI-Voice-2.1"),
}
# HD voice -> the standard voice that stands in if the service refuses it (preview voices, regions).
STANDARD_FOR = {
    hd: std
    for lang in VOICES_STANDARD
    for hd, std in zip(VOICES_HD[lang], VOICES_STANDARD[lang], strict=True)
    if hd != std
}


def voice_tier() -> str:
    """``hd`` (default) or ``standard``; ``EARSHOT_VOICE_TIER`` switches the whole fleet at once."""
    tier = os.environ.get("EARSHOT_VOICE_TIER", "hd")
    return tier if tier in ("hd", "standard") else "hd"


def is_hd(voice: str) -> bool:
    return ":" in voice


VOICES = VOICES_HD
"""The active table at import, for callers that only read it; ``voice_for`` reads the tier live."""

NUMERIC_SLOTS = frozenset(
    {
        "home_score",
        "away_score",
        "home_shots",
        "away_shots",
        "speed",
        "distance",
        "count",
        "minute",
        "percent",
        "value",
        "before",
        "after",
        "window_min",
    }
)

DIGITS = re.compile(r"[0-9٠-٩۰-۹०-९]")
"""ASCII, Arabic-Indic, Eastern Arabic-Indic and Devanagari digits."""

NUMBER_WORDS: dict[Language, frozenset[str]] = {
    Language.EN: frozenset(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
        "hundred thousand "
        "first second third fourth fifth sixth seventh eighth ninth tenth dozen".split()
    ),
    Language.ES: frozenset(
        "cero uno una dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince "
        "dieciséis veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa cien ciento mil "
        "primero primera "
        "segundo segunda tercero tercera".split()
    ),
    Language.AR: frozenset(
        "صفر واحد واحدة اثنان اثنين اثنتان ثلاثة ثلاث أربعة أربع خمسة خمس ستة ست سبعة سبع ثمانية ثمان "
        "تسعة تسع عشرة عشر عشرون عشرين مئة مائة ألف الأول الأولى الثاني الثانية الثالث الثالثة".split()
    ),
    Language.HI: frozenset(
        "शून्य एक दो तीन चार पांच पाँच छह सात आठ नौ दस ग्यारह बारह बीस तीस चालीस पचास सौ हज़ार हजार "
        "पहला पहली दूसरा दूसरी तीसरा तीसरी".split()
    ),
}
"""Whole words only: "صفراء" (yellow) contains "صفر" (zero) and "ज़बरदस्त" contains "दस" (ten)."""

_TOKEN = re.compile(r"[^\s.,;:!?¡¿،؛।\"'()\-]+")
_AR_PREFIXES = ("وال", "بال", "لل", "ال", "و", "ب", "ل", "ف")


def number_words_in(language: Language, text: str) -> list[str]:
    """Number words in ``text`` for ``language``, matched as whole tokens (Arabic prefixes stripped)."""
    words = NUMBER_WORDS[language]
    hits = []
    for tok in _TOKEN.findall(text.lower()):
        tok = tok.strip("ـ")
        candidates = {tok}
        if language is Language.AR:
            candidates |= {tok[len(p) :] for p in _AR_PREFIXES if tok.startswith(p) and len(tok) > len(p) + 1}
        if candidates & words:
            hits.append(tok)
    return hits


@dataclass(frozen=True)
class Catalog:
    language: Language
    lexicon: dict
    templates: dict[str, dict[str, str]]

    def template(self, template_id: str, mode: ListeningMode) -> str:
        return self.templates[template_id][mode.value]

    def variants(self, family: str) -> list[str]:
        return sorted(t for t in self.templates if t.split(".")[0] == family)

    def render(self, template_id: str, mode: ListeningMode, slots: dict[str, SlotValue]) -> str:
        tmpl = self.template(template_id, mode)
        missing = placeholders(tmpl) - slots.keys()
        if missing:
            raise KeyError(f"{template_id}: missing slots {sorted(missing)}")
        return tmpl.format(**{k: str(v) for k, v in slots.items()})

    def ssml_body(self, template_id: str, mode: ListeningMode, slots: dict[str, SlotValue]) -> str:
        """Escaped SSML for the line, numbers in slots wrapped in say-as so nothing numeric is free text."""
        tmpl = self.template(template_id, mode)
        out: list[str] = []
        for literal, field, _, _ in string.Formatter().parse(tmpl):
            out.append(escape(literal))
            if field is None:
                continue
            value = slots[field]
            if field in NUMERIC_SLOTS:
                out.append(self._say_number(str(value)))
            else:
                out.append(escape(str(value)))
        return "".join(out)

    def _say_number(self, value: str) -> str:
        if "+" in value:
            a, b = value.split("+", 1)
            return f"{self._say_number(a)} {escape(self.lexicon['plus'])} {self._say_number(b)}"
        return f'<say-as interpret-as="cardinal">{escape(value)}</say-as>'


def placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


@cache
def load(language: Language) -> Catalog:
    raw = json.loads(
        resources.files("earshot.agents.templates").joinpath(f"{language.value}.json").read_text()
    )
    return Catalog(language=language, lexicon=raw["lexicon"], templates=raw["templates"])


def literal_violations(language: Language) -> list[str]:
    """Digits or number words in a template's literal text (outside slots): must be empty."""
    cat = load(language)
    bad: list[str] = []
    for tid, modes in cat.templates.items():
        for mode, tmpl in modes.items():
            literal = " ".join(lit for lit, _, _, _ in string.Formatter().parse(tmpl))
            if DIGITS.search(literal) or number_words_in(language, literal):
                bad.append(f"{language.value}:{tid}:{mode}: {literal!r}")
    return bad
