"""The independent auditor (design "Line Safety"): Facts are measured here, not by the Checker.

A separate code path from the Checker. It never trusts a line's slots: it re-parses the aired text
against the *reviewed* template files (read straight from disk, not through the catalogue the
Caller and Checker share), binds every name to the reviewed roster, recomputes every number from the
event log, and scans the exact SSML for numerals outside a ``say-as`` slot.

Allowed values (design): values in the event log, plus values derived from it by named code
functions (score, minute, counts, metric values). Metric values use the shared metric definitions
(``earshot.stats``), which is the design's "named code functions"; everything else is counted here.

A finding is a line that should not have aired. ``audit`` returns the findings for one line.
"""

from __future__ import annotations

import json
import math
import re
import string
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from earshot.agents.catalog import NUMBER_WORDS
from earshot.contract import Language, Line, LineSource, Metric
from earshot.contract.events import Action, Card, Event, StoppageStart
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.contract.spadl import SHOT_TYPES, ActionType
from earshot.stats import metrics
from earshot.stats.claims import window_values

REVIEWED_TEMPLATES = Path(__file__).parent.parent / "agents" / "templates"
NAME_SLOTS = frozenset({"player", "keeper", "club", "home", "away", "on", "off"})
ID_SLOTS = {"player": 0, "keeper": 1, "on": 0, "off": 1}
"""Which of a line's ``player_ids`` each name slot must be: [actor, keeper], or [on, off] for a sub."""
LEXICON_SLOTS = frozenset({"zone", "side", "stoppage", "var_subject", "cause"})
DIGITS = re.compile(r"[0-9٠-٩۰-۹०-९]")
SAY_AS = re.compile(r"<say-as\b[^>]*>(.*?)</say-as>", re.S)
TAG = re.compile(r"<[^>]+>")
MINUTE_SPAN_MS = 10 * 60_000
NEAR_MS = 3_000


@dataclass(frozen=True)
class Finding:
    line_id: str
    kind: str
    detail: str


@cache
def reviewed(language: Language) -> dict:
    """The reviewed template file for a language, read from disk (not the shared catalogue)."""
    return json.loads((REVIEWED_TEMPLATES / f"{language.value}.json").read_text())


def _pattern(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for literal, name, _, _ in string.Formatter().parse(template):
        parts.append(re.escape(literal))
        if name:
            parts.append(f"(?P={name})" if name in seen else f"(?P<{name}>.+?)")
            seen.add(name)
    return re.compile("^" + "".join(parts) + "$", re.S)


def parse(line: Line) -> dict[str, str] | None:
    """Slot values read back from the aired text via the reviewed template(s); None if no match."""
    t = reviewed(line.language)["templates"]
    texts = []
    for tid in line.template_id.split("+"):
        modes = t.get(tid)
        if modes is None or line.listening_mode.value not in modes:
            return None
        texts.append(modes[line.listening_mode.value])
    m = _pattern(" ".join(texts)).match(line.text)
    return m.groupdict() if m else None


def _num(v: str) -> int | None:
    try:
        return int(v)
    except ValueError:
        return None


class Auditor:
    """Audits aired lines for one match against its event log and the reviewed roster."""

    def __init__(
        self,
        info: MatchInfo,
        events: Sequence[Event],
        clubs: Sequence[ClubInfo],
        players: Sequence[PlayerInfo],
    ) -> None:
        self.info, self.events = info, list(events)
        self.home, self.away = info.home.team_id, info.away.team_id
        self.club_names = {c.team_id: c.short_name for c in clubs}
        self.player_names = {p.player_id: p.short_name for p in players}
        self.player_team = {p.player_id: p.team_id for p in players}
        self.positions = {p.player_id: p.position for p in players}
        self.by_id = {e.event_id: e for e in self.events}
        self._parsed: dict[str, str] = {}

    # ----- independent counts --------------------------------------------------------------------

    def _upto(self, wall_ms: int) -> list[Event]:
        return [e for e in self.events if e.wall_ms <= wall_ms]

    def score(self, wall_ms: int) -> tuple[int, int]:
        h = a = 0
        for e in self._upto(wall_ms):
            if not isinstance(e, Action) or not e.team_id:
                continue
            scored = ActionType(e.type) in SHOT_TYPES and e.result == "success"
            own = e.result == "owngoal"
            if scored or own:
                for_home = (e.team_id == self.home) != own
                h, a = h + for_home, a + (not for_home)
        return h, a

    @staticmethod
    def minute(period_id: int, clock_ms: int) -> str:
        m = clock_ms // 60_000 + 1
        base = 45 * (period_id - 1)
        return f"{base + 45}+{m - 45}" if m > 45 else str(base + m)

    def minutes_near(self, wall_ms: int) -> set[str]:
        return {
            self.minute(e.period_id, e.clock_ms)
            for e in self.events
            if wall_ms - MINUTE_SPAN_MS <= e.wall_ms <= wall_ms + 1000
        }

    def goal_minutes(self, wall_ms: int) -> set[str]:
        return {
            self.minute(e.period_id, e.clock_ms)
            for e in self._upto(wall_ms)
            if isinstance(e, Action)
            and ((ActionType(e.type) in SHOT_TYPES and e.result == "success") or e.result == "owngoal")
        }

    def red_card_minutes(self, wall_ms: int) -> set[str]:
        return {
            self.minute(e.period_id, e.clock_ms)
            for e in self._upto(wall_ms)
            if isinstance(e, Card) and e.color.value == "red"
        }

    @staticmethod
    def kmh(a: Action) -> int | None:
        if not a.flight_ms:
            return None
        return round(math.hypot(a.end_x - a.start_x, a.end_y - a.start_y) / (a.flight_ms / 1000) * 3.6)

    def near_actions(self, line: Line) -> list[Action]:
        return [
            e
            for e in self.events
            if isinstance(e, Action)
            and abs(e.wall_ms - line.wall_ms) <= NEAR_MS
            and e.player_id in line.player_ids
        ]

    def shots(self, team: str, wall_ms: int) -> int:
        return sum(
            1
            for e in self._upto(wall_ms)
            if isinstance(e, Action) and e.team_id == team and ActionType(e.type) in SHOT_TYPES
        )

    def on_pitch(self, team: str, wall_ms: int) -> int:
        reds = {
            e.player_id
            for e in self._upto(wall_ms)
            if isinstance(e, Card) and e.team_id == team and e.color.value == "red"
        }
        return 11 - len(reds)

    def _stoppage(self, line: Line) -> StoppageStart | None:
        if line.moment_id and "-win-" in line.moment_id:
            ev = self.by_id.get(line.moment_id.split("-win-")[1])
            return ev if isinstance(ev, StoppageStart) else None
        return None

    def _metric_values(self, metric: Metric, team: str, end: int, minutes: int) -> tuple[set[int], set[int]]:
        share = metric in (Metric.POSSESSION_SHARE, Metric.DIRECTNESS)
        befores, afters = set(), set()
        for e in (end, end + 1):
            b, a = window_values(
                metric, self.events, team, e - minutes * 60_000, e, self.home, self.positions
            )
            for v, out in ((b, befores), (a, afters)):
                if v is not None:
                    out.add(round(v * 100) if share else round(v))
        return befores, afters

    # ----- the audit -------------------------------------------------------------------------------

    def allowed(self, slot: str, value: str, line: Line) -> bool | None:
        """True/False for a slot whose value the auditor can derive; None if it has no derivation."""
        n = _num(value)
        team = line.club_tags[0] if line.club_tags else None
        if team is None and "club" in self._parsed:  # recap sentences name the club instead of tagging it
            team = next((t for t, name in self.club_names.items() if name == self._parsed["club"]), None)
        if slot in ("home_score", "away_score"):
            h, a = self.score(line.wall_ms)
            return n == (h if slot == "home_score" else a)
        if slot == "minute":
            if line.source is LineSource.RECAP:
                # bound to the kind of event the recap sentence names
                if line.template_id.startswith("recap.red_card"):
                    return value in self.red_card_minutes(line.wall_ms)
                if line.template_id.startswith(("recap.goal", "recap.own_goal")):
                    return value in self.goal_minutes(line.wall_ms)
            return value in self.minutes_near(line.wall_ms)
        if slot == "speed":
            if line.moment_id and line.moment_id in self.by_id:
                ev = self.by_id[line.moment_id]
                return isinstance(ev, Action) and n == self.kmh(ev)
            return n in {self.kmh(a) for a in self.near_actions(line)}
        if slot == "distance":
            return n in {
                round(math.hypot(a.end_x - a.start_x, a.end_y - a.start_y)) for a in self.near_actions(line)
            }
        if slot == "count":
            return team is not None and n == self.on_pitch(team, line.wall_ms)
        if slot in ("home_shots", "away_shots"):
            return n == self.shots(self.home if slot == "home_shots" else self.away, line.wall_ms)
        if slot == "value" and line.source is LineSource.STATWATCHER:
            return n == 10  # top difficulty decile; the crossing itself is recomputed below
        if slot == "window_min":
            return n in (5, 10)
        if slot in ("before", "after", "percent") and team is not None:
            return self._derived(slot, n, line, team)
        return None

    def _derived(self, slot: str, n: int | None, line: Line, team: str) -> bool | None:
        tid = line.template_id
        if line.source is LineSource.RECAP and slot == "percent":
            p = metrics.possession_ms(self.events, 0, line.wall_ms + 1)
            total = sum(p.values())
            return total > 0 and n == round(p.get(team, 0) / total * 100)
        if tid.startswith("stat."):
            stat = tid.split(".")[1]
            metric = Metric.PRESS_HEIGHT if stat == "press_height" else Metric.POSSESSION_SHARE
            end = line.wall_ms
        else:
            ev = self._stoppage(line)
            if ev is None:
                return None
            end = ev.wall_ms + 1
            if tid.startswith("summary.possession"):
                metric = Metric.POSSESSION_SHARE
            elif tid.startswith("claim."):
                metric = Metric(tid.split(".")[1])
            else:
                return None
        window_min = _num(self._parsed.get("window_min", "5")) or 5
        befores, afters = self._metric_values(metric, team, end, window_min)
        pool = befores if slot == "before" else afters
        return n in pool

    def audit(self, line: Line, ssml: str | None = None) -> list[Finding]:
        out: list[Finding] = []

        def found(kind: str, detail: str) -> None:
            out.append(Finding(line.line_id, kind, detail))

        values = parse(line)
        if values is None:
            found("template", f"text is not a reviewed rendering of {line.template_id}")
            return out
        self._parsed = values
        for slot, value in values.items():
            if str(line.slots.get(slot)) != value:
                found("slot", f"{slot}: text says {value!r}, slot says {line.slots.get(slot)!r}")
        lexicon = reviewed(line.language).get("lexicon", {})
        for slot, value in values.items():
            if slot in NAME_SLOTS:
                self._name(slot, value, line, found)
            elif slot in LEXICON_SLOTS:
                words = {w for group in lexicon.values() if isinstance(group, dict) for w in group.values()}
                if value not in words:
                    found("lexicon", f"{slot}={value!r} is not a reviewed lexicon entry")
            else:
                ok = self.allowed(slot, value, line)
                if ok is None:
                    found("underivable", f"{slot}={value!r} has no derivation in the auditor")
                elif not ok:
                    found("number", f"{slot}={value!r} does not match the event log")
        if ssml is not None:
            self._ssml(ssml, line, values, found)
        return out

    def _name(self, slot: str, value: str, line: Line, found) -> None:
        if slot in ("home", "away"):
            want = self.club_names.get(self.home if slot == "home" else self.away)
            if value != want:
                found("name", f"{slot}={value!r}, reviewed {want!r}")
            return
        if slot == "club":
            teams = set(line.club_tags) or {self.home, self.away}
            if value not in {self.club_names.get(t) for t in teams}:
                found("name", f"club={value!r} is not the reviewed name of {sorted(teams)}")
            return
        if slot in ID_SLOTS and line.player_ids:
            i = ID_SLOTS[slot]
            if i < len(line.player_ids):
                want = self.player_names.get(line.player_ids[i])
                if value != want:
                    found("name", f"{slot}={value!r}, reviewed name for {line.player_ids[i]} is {want!r}")
                return
        # recap scorers and anything without ids: the name must belong to this match's reviewed roster
        teams = set(line.club_tags) or {self.home, self.away}
        names = {n for pid, n in self.player_names.items() if self.player_team.get(pid) in teams}
        if value not in names:
            found("name", f"{slot}={value!r} is not in the reviewed roster")

    def _ssml(self, ssml: str, line: Line, values: dict[str, str], found) -> None:
        slotted = {v for v in values.values()}
        spoken_parts = slotted | {p for v in slotted for p in v.split("+")}  # "45+2" is voiced as two numbers
        for inner in SAY_AS.findall(ssml):
            if TAG.sub("", inner).strip() not in spoken_parts:
                found("ssml", f"say-as {inner!r} is not a slot value")
        free = TAG.sub(" ", SAY_AS.sub(" ", ssml))
        for v in slotted:  # names and lexicon words are free text in SSML
            free = free.replace(_escape(v), " ")
        if DIGITS.search(free):
            found("ssml", "a numeral outside any slot")
        words = NUMBER_WORDS.get(line.language, frozenset())
        tokens = {t.strip(".,!?¿¡:;،।").lower() for t in free.split()}
        hit = sorted(tokens & set(words))
        if hit:
            found("ssml", f"number words outside slots: {hit}")


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def reference_blocked(line: Line, reference) -> bool:
    """For the planted-defect roster point: block a line whose text names a player differently from
    the reviewed roster (``reference`` is the reviewed ``Roster``), read from the text, not the slots."""
    values = parse(line)
    if values is None:
        return True
    for slot, i in ID_SLOTS.items():
        if (
            slot in values
            and i < len(line.player_ids)
            and values[slot] != reference.players[line.player_ids[i]].short_name
        ):
            return True
    return False
