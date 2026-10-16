"""The reviewed template catalogue: every spoken sentence comes from here plus code-filled slots.

Templates are written and reviewed offline per language (Line Safety). A template's literal text
may not contain digits or number words; numbers only ever enter through slots.
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from functools import cache
from importlib import resources
from xml.sax.saxutils import escape

from earshot.contract import Language, ListeningMode
from earshot.contract.line import SlotValue

LOCALES = {Language.EN: "en-GB", Language.ES: "es-ES", Language.AR: "ar-SA", Language.HI: "hi-IN"}

VOICES = {
    # (facts voice, explanation voice): two voices per language (CEO-5)
    Language.EN: ("en-GB-RyanNeural", "en-GB-SoniaNeural"),
    Language.ES: ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
    Language.AR: ("ar-SA-HamedNeural", "ar-SA-ZariyahNeural"),
    Language.HI: ("hi-IN-ArjunNeural", "hi-IN-AartiNeural"),
}

NUMERIC_SLOTS = frozenset(
    {"home_score", "away_score", "speed", "distance", "count", "minute", "percent", "value"}
)

DIGITS = re.compile(r"[0-9٠-٩۰-۹०-९]")
"""ASCII, Arabic-Indic, Eastern Arabic-Indic and Devanagari digits."""

NUMBER_WORDS = {
    Language.EN: r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|thousand|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|half-dozen|dozen)\b",
    Language.ES: r"\b(cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce"
    r"|quince|veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|mil|primero"
    r"|primera|segundo|segunda|tercero|tercera)\b",
    Language.AR: r"(صفر|واحد|اثنان|اثنين|ثلاثة|أربعة|خمسة|ستة|سبعة|ثمانية|تسعة|عشرة|عشرون|مئة|مائة|ألف"
    r"|الأول|الثاني|الثالث)",
    Language.HI: r"(शून्य|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ|दस|बीस|तीस|सौ|हज़ार|हजार|पहला|दूसरा|तीसरा)",
}


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
    words = re.compile(NUMBER_WORDS[language], re.IGNORECASE)
    bad: list[str] = []
    for tid, modes in cat.templates.items():
        for mode, tmpl in modes.items():
            literal = "".join(lit for lit, _, _, _ in string.Formatter().parse(tmpl))
            if DIGITS.search(literal) or words.search(literal):
                bad.append(f"{language.value}:{tid}:{mode}: {literal!r}")
    return bad
