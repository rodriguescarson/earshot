"""StatWatcher (CEO-7): code-made lines for the five stats a listener can follow.

Listener-neutral, like the rest of the pipeline: it reports a crossing for every stat, and the
Director airs a stat line only to Analyst-mode listeners who follow that stat. Every number comes
from code (``earshot.stats``), and ``check_stat_line`` recomputes it from the events.

Thresholds (design doc, "Follow one stat"):
- **Press height**: a change of 5 m or more between the last 5 minutes and the 5 before.
- **Danger (xT)**: a team's xT added over the last 5 minutes in the top decile of the reference
  distribution (``models/statwatch.json``, dev seeds 1-40).
- **Pass difficulty**: every open-play pass in the top difficulty decile.
- **Shot speed**: every shot with a recorded flight time.
- **Possession**: a swing of 10 points or more in 5-minute share, reported for the side that gained.
The windowed stats (press height, danger, possession) are checked at stoppages, so they fit in
dead time, and at most once per team per stat every 5 minutes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from earshot.agents.catalog import load, placeholders
from earshot.agents.checker import Verdict, ssml_numeric_leak
from earshot.contract import FocusStat, Language, Line, LineSource, ListeningMode
from earshot.contract.events import Action, Event, StoppageStart
from earshot.contract.line import SlotValue, VoiceRole
from earshot.pitch import speech_pan
from earshot.stats import metrics
from earshot.stats.danger import model as xt_model
from earshot.stats.passes import OPEN_PLAY_PASS, DifficultyModel, is_shot, speed_kmh
from earshot.stats.xt import XTModel
from earshot.tools.snapshots import MatchView

WINDOW_MS = 5 * 60_000
PRESS_DELTA_M = 5.0
POSSESSION_SWING = 0.10
REPEAT_MS = 5 * 60_000
PRIORITY = 50
"""Below Caller lines for shots and saves, above routine play: screen-reader order puts focus-stat
lines after never-drop and Caller lines."""
TTL_MS = {
    FocusStat.SHOT_SPEED: 4_000,
    FocusStat.PASS_DIFFICULTY: 3_000,
    FocusStat.PRESS_HEIGHT: 12_000,
    FocusStat.DANGER: 12_000,
    FocusStat.POSSESSION: 12_000,
}
REFERENCE_PATH = Path(__file__).parent.parent / "stats" / "models" / "statwatch.json"
MODE = ListeningMode.ANALYST


@dataclass(frozen=True)
class Crossing:
    """One stat crossing, language-neutral: the numbers and who, not the words."""

    stat: FocusStat
    event_id: str
    wall_ms: int
    team_id: str
    family: str
    """Template family under ``stat.``, e.g. ``press_height.up``."""
    numbers: dict[str, SlotValue] = field(default_factory=dict)
    player_id: str | None = None
    x: float | None = None


@cache
def difficulty_model() -> DifficultyModel:
    return DifficultyModel.load()


@cache
def danger_reference() -> float:
    """Top-decile cut point of a team's 5-minute xT added (frozen with the other reference stats)."""
    return float(json.loads(REFERENCE_PATH.read_text())["danger_p90"])


def xt_added(
    events: Sequence[Event], team_id: str, start_ms: int, end_ms: int, home_id: str, m: XTModel
) -> float:
    """xT gained by a team's completed passes and carries in ``[start_ms, end_ms)``; losses count as zero."""
    total = 0.0
    for e in events:
        if not (start_ms <= e.wall_ms < end_ms) or not isinstance(e, Action) or e.team_id != team_id:
            continue
        if e.type not in ("pass", "cross", "dribble") or e.result != "success":
            continue
        sx, sy = metrics.attacking(e, home_id)
        ex, ey = metrics.attacking(e, home_id, end=True)
        total += max(0.0, m.value(ex, ey) - m.value(sx, sy))
    return total


def _window(stat: FocusStat, events: Sequence[Event], team: str, end: int, home: str) -> tuple:
    w_now = metrics.Window(team, end - WINDOW_MS, end)
    w_before = metrics.Window(team, end - 2 * WINDOW_MS, end - WINDOW_MS)
    if stat is FocusStat.PRESS_HEIGHT:
        return metrics.press_height(events, w_before, home), metrics.press_height(events, w_now, home)
    if stat is FocusStat.POSSESSION:
        return metrics.possession_share(events, w_before), metrics.possession_share(events, w_now)
    return None, xt_added(events, team, end - WINDOW_MS, end, home, xt_model())


def windowed(stat: FocusStat, events: Sequence[Event], team: str, end_ms: int, home: str) -> Crossing | None:
    """The crossing for one windowed stat at ``end_ms``, if the threshold is met (pure: no repeat rule)."""
    if end_ms < 2 * WINDOW_MS:
        return None
    before, after = _window(stat, events, team, end_ms, home)
    if after is None:
        return None
    window_min = WINDOW_MS // 60_000
    if stat is FocusStat.DANGER:
        if after < danger_reference():
            return None
        return Crossing(stat, "", end_ms, team, "danger", {"window_min": window_min})
    if before is None:
        return None
    if stat is FocusStat.PRESS_HEIGHT:
        b, a = round(before), round(after)
        if abs(after - before) < PRESS_DELTA_M or a == b:
            return None
    else:
        b, a = round(before * 100), round(after * 100)
        # Shares mirror each other, so only the side that gained is reported.
        if after - before < POSSESSION_SWING or a == b:
            return None
    family = f"{stat.value}.{'up' if a > b else 'down'}"
    return Crossing(stat, "", end_ms, team, family, {"before": b, "after": a, "window_min": window_min})


def on_action(a: Action, home: str) -> Crossing | None:
    """Per-action crossings: every shot's speed, and passes in the top difficulty decile."""
    if not a.team_id or not a.player_id:
        return None
    if is_shot(a):
        kmh = speed_kmh(a)
        if kmh is None:
            return None
        return Crossing(
            FocusStat.SHOT_SPEED, a.event_id, a.wall_ms, a.team_id, "shot_speed",
            {"speed": round(kmh)}, a.player_id, a.start_x,
        )  # fmt: skip
    if a.type in OPEN_PLAY_PASS:
        m = difficulty_model()
        if m.out_of_ten(m.probability(a, home)) < 10:
            return None
        made = "made" if a.result == "success" else "missed"
        return Crossing(
            FocusStat.PASS_DIFFICULTY, a.event_id, a.wall_ms, a.team_id, f"pass_difficulty.{made}",
            {"value": 10}, a.player_id, a.start_x,
        )  # fmt: skip
    return None


WINDOWED = (FocusStat.PRESS_HEIGHT, FocusStat.DANGER, FocusStat.POSSESSION)


def danger_samples(events: Sequence[Event], home: str, away: str) -> list[float]:
    """Each team's 5-minute xT added at every stoppage: the population the danger decile is cut from."""
    m = xt_model()
    return [
        xt_added(events, team, ev.wall_ms - WINDOW_MS, ev.wall_ms, home, m)
        for ev in events
        if isinstance(ev, StoppageStart) and ev.wall_ms >= 2 * WINDOW_MS
        for team in (home, away)
    ]


class StatWatcher:
    """Feeds on the event stream in order; returns crossings as they happen."""

    def __init__(self, home_team_id: str, away_team_id: str) -> None:
        self.home, self.teams = home_team_id, (home_team_id, away_team_id)
        self.events: list[Event] = []
        self.last: dict[tuple[FocusStat, str], int] = {}

    def push(self, ev: Event) -> list[Crossing]:
        self.events.append(ev)
        out: list[Crossing] = []
        if isinstance(ev, Action):
            c = on_action(ev, self.home)
            if c is not None:
                out.append(c)
        elif isinstance(ev, StoppageStart):
            for stat in WINDOWED:
                for team in self.teams:
                    if ev.wall_ms - self.last.get((stat, team), -REPEAT_MS) < REPEAT_MS:
                        continue
                    c = windowed(stat, self.events, team, ev.wall_ms, self.home)
                    if c is not None:
                        self.last[(stat, team)] = ev.wall_ms
                        out.append(Crossing(c.stat, ev.event_id, ev.wall_ms, c.team_id, c.family, c.numbers))
        return out


def stat_slots(c: Crossing, view: MatchView) -> dict[str, SlotValue]:
    slots: dict[str, SlotValue] = {"club": view.clubs[c.team_id].short_name, **c.numbers}
    if c.player_id is not None:
        slots["player"] = view.players[c.player_id].short_name
    return slots


def stat_line(c: Crossing, view: MatchView, language: Language) -> Line:
    cat = load(language)
    tid = f"stat.{c.family}.1"
    slots = stat_slots(c, view)
    used = {k: v for k, v in slots.items() if k in placeholders(cat.template(tid, MODE))}
    text = cat.render(tid, MODE, used)
    return Line(
        line_id=f"{view.match_id}-stat-{c.stat.value}-{c.team_id}-{c.wall_ms}-{language.value}",
        match_id=view.match_id,
        moment_id=c.event_id or None,
        source=LineSource.STATWATCHER,
        language=language,
        listening_mode=MODE,
        template_id=tid,
        slots=used,
        text=text,
        priority=PRIORITY,
        wall_ms=c.wall_ms,
        deadline_wall_ms=c.wall_ms + TTL_MS[c.stat],
        voice_role=VoiceRole.FACTS,
        pan=speech_pan(c.x) if c.x is not None else 0.0,
        est_ms=int(len(text) / 14 * 1000) + 300,
        club_tags=[c.team_id],
        player_ids=[c.player_id] if c.player_id else [],
        stat_tag=c.stat,
    )


def check_stat_line(line: Line, view: MatchView) -> Verdict:
    """Recompute the crossing from the events up to the line and compare every slot and the text."""
    if line.source is not LineSource.STATWATCHER or line.stat_tag is None:
        return Verdict(False, "not a StatWatcher line")
    events = [e for e in view.events if e.wall_ms <= line.wall_ms]
    home = view.info.home.team_id
    if line.stat_tag in WINDOWED:
        team = line.club_tags[0] if line.club_tags else ""
        c = windowed(line.stat_tag, events, team, line.wall_ms, home)
    else:
        ev = next((e for e in events if e.event_id == line.moment_id), None)
        c = on_action(ev, home) if isinstance(ev, Action) else None
    if c is None or c.stat is not line.stat_tag:
        return Verdict(False, "no crossing in the events")
    expected = stat_line(c, view, line.language)
    if (line.template_id, dict(line.slots), line.text) != (
        expected.template_id,
        dict(expected.slots),
        expected.text,
    ):
        return Verdict(False, f"recomputed {expected.slots} != {line.slots}")
    cat = load(line.language)
    leak = ssml_numeric_leak(
        line.language, cat.ssml_body(line.template_id, MODE, dict(line.slots)), dict(line.slots)
    )
    return Verdict(False, f"numeric text outside a slot: {leak}") if leak else Verdict(True)
