"""The planted-defect control (design "Line Safety", R3-1): defects injected at a known rate at the
four points where a wrong name or number could still reach a listener.

- **Analyst JSON:** fabricated value, wrong direction, cited event that does not exist, cited
  event outside the window, window in the future. Caught by the claim verifier
  (``earshot.stats.claims``).
- **Slot values:** out-of-set (an unknown name, an impossible score) and real-but-wrong (another
  real player, the scores swapped, the other club). The Checker re-derives every slot; the auditor
  recomputes them independently.
- **Roster entries:** a wrong name for a player id. **Not catchable by the Checker by design** (it
  reads the same roster); the auditor's job, against the reviewed roster.
- **Template strings:** a stray numeral in fixed text (the Checker's SSML numeric scan), and a club
  name typed into fixed text (only the auditor's exact match against reviewed templates).

For each point the harness reports recall (defective items blocked / defective items) and the
false-block rate (clean items blocked / clean items), per defect class. A Checker that blocks
everything scores perfect recall, so the false-block rate is reported next to it, always.

The Analyst-JSON point runs on this package alone. The other three drive Stream 1's Caller and
Checker (``earshot.agents``), imported only when those points run.
"""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from earshot.contract import Language, ListeningMode
from earshot.contract.claim import Claim, Direction, Metric
from earshot.contract.events import Event, StoppageStart
from earshot.contract.match import MatchInfo
from earshot.stats.claims import THRESHOLD, verify_claim, window_values

SPAN_MS = 10 * 60_000
DEFECT_RATE = 0.25
"""Share of items that receive a defect; the rest are the clean control."""


@dataclass
class PointResult:
    point: str
    clean: int = 0
    clean_blocked: int = 0
    defective: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    caught: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    examples: list[str] = field(default_factory=list)

    def add(self, cls: str | None, blocked: bool, why: str = "") -> None:
        if cls is None:
            self.clean += 1
            self.clean_blocked += blocked
            if blocked and len(self.examples) < 5:
                self.examples.append(f"false block: {why}")
        else:
            self.defective[cls] += 1
            self.caught[cls] += blocked
            if not blocked and len(self.examples) < 5:
                self.examples.append(f"missed {cls}: {why}")

    def summary(self) -> dict:
        per = {c: {"injected": n, "blocked": self.caught[c], "recall": round(self.caught[c] / n, 4)}
               for c, n in sorted(self.defective.items())}  # fmt: skip
        total = sum(self.defective.values())
        return {
            "point": self.point,
            "defects_injected": total,
            "recall": round(sum(self.caught.values()) / total, 4) if total else None,
            "per_class": per,
            "clean": self.clean,
            "false_block_rate": round(self.clean_blocked / self.clean, 4) if self.clean else None,
            "examples": self.examples,
        }


# ----- point 1: Analyst JSON ----------------------------------------------------------------------


def honest_claims(events: Sequence[Event], home_id: str, away_id: str, positions: Mapping[str, str],
                  stoppage: StoppageStart) -> list[Claim]:  # fmt: skip
    """Claims that state only what the numbers show: per club, every metric whose change over the
    last 10 minutes clears its threshold. The clean control for the Analyst-JSON point."""
    end, start = stoppage.wall_ms, stoppage.wall_ms - SPAN_MS
    if start - SPAN_MS < 0:
        return []
    past = [e for e in events if e.wall_ms <= stoppage.wall_ms]
    out: list[Claim] = []
    for team in (home_id, away_id):
        cited = [e.event_id for e in past if e.team_id == team and start <= e.wall_ms < end][-3:]
        if not cited:
            continue
        for m in Metric:
            before, after = window_values(m, past, team, start, end, home_id, positions)
            if before is None or after is None or abs(after - before) < THRESHOLD[m] or after == before:
                continue
            direction = Direction.UP if after > before else Direction.DOWN
            out.append(
                Claim(
                    team_id=team, metric=m, direction=direction, window_start_ms=start, window_end_ms=end,
                    value_before=round(before, 3), value_after=round(after, 3), cited_event_ids=cited,
                )
            )  # fmt: skip
    return out


CLAIM_DEFECTS = (
    "fabricated_value",
    "wrong_direction",
    "missing_event",
    "out_of_window_event",
    "future_window",
)


def corrupt_claim(c: Claim, cls: str, events: Sequence[Event]) -> Claim:
    if cls == "fabricated_value":
        return c.model_copy(update={"value_after": (c.value_after or 0.0) + 3 * THRESHOLD[c.metric]})
    if cls == "wrong_direction":
        return c.model_copy(
            update={"direction": Direction.DOWN if c.direction is Direction.UP else Direction.UP}
        )
    if cls == "missing_event":
        return c.model_copy(
            update={"cited_event_ids": [*c.cited_event_ids[:-1], f"{c.cited_event_ids[0]}-x"]}
        )
    if cls == "out_of_window_event":
        early = next((e.event_id for e in events if e.wall_ms < c.window_start_ms - 2 * SPAN_MS), None)
        return c.model_copy(update={"cited_event_ids": [early or "none"]})
    if cls == "future_window":
        shift = 5 * 60_000
        return c.model_copy(
            update={"window_start_ms": c.window_start_ms + shift, "window_end_ms": c.window_end_ms + shift}
        )
    raise ValueError(cls)


def analyst_json_point(matches: Sequence[tuple[list[Event], MatchInfo]], positions: Mapping[str, str],
                       seed: int = 1, rate: float = DEFECT_RATE) -> PointResult:  # fmt: skip
    rng = random.Random(seed)
    res = PointResult("analyst_json")
    for events, info in matches:
        for stop in (e for e in events if isinstance(e, StoppageStart) and e.expected_dead_ms >= 15_000):
            for c in honest_claims(events, info.home.team_id, info.away.team_id, positions, stop):
                cls = rng.choice(CLAIM_DEFECTS) if rng.random() < rate else None
                claim = corrupt_claim(c, cls, events) if cls else c
                v = verify_claim(claim, events, info.home.team_id, positions, stop.wall_ms + 1)
                res.add(cls, not v.ok, v.reason)
    return res


# ----- points 2-4: Caller lines (Stream 1's Caller and Checker) -----------------------------------

SLOT_DEFECTS = (
    "out_of_set_name",
    "out_of_set_number",
    "real_wrong_player",
    "real_wrong_score",
    "real_wrong_club",
)


def _caller_items(events: list[Event], info: MatchInfo, clubs, players, language, mode, roster=None,
                  ) -> Iterator[tuple[Any, Any, Event, Any, Any]]:  # fmt: skip
    """(line, moment, trigger, state, roster) for every Caller line over a match."""
    from earshot.agents.caller import Caller  # pyright: ignore[reportMissingImports]
    from earshot.agents.spotter import Spotter  # pyright: ignore[reportMissingImports]
    from earshot.agents.state import MatchState, Roster  # pyright: ignore[reportMissingImports]

    roster = roster or Roster.build(info, list(clubs), list(players))
    spotter, caller = Spotter(roster), Caller(roster)
    state, seen = MatchState.start(info), {}
    for i, ev in enumerate(events):
        seen[ev.event_id] = ev
        before, state = state, state.apply(ev, roster)
        moments = list(spotter.observe(ev, before, state))
        if i == len(events) - 1:
            moments += spotter.flush()
        for m in moments:
            trigger = seen[m.event_ids[0]]
            for ln in caller.lines(m, trigger, state, language, mode):
                yield ln, m, trigger, state, roster


def corrupt_slots(line, cls: str, roster, rng: random.Random):
    """A line whose slots carry the defect and whose text is the honest rendering of those slots,
    exactly as a buggy slot filler would produce it. None if the line has no slot of that kind."""
    from earshot.agents.catalog import load  # pyright: ignore[reportMissingImports]

    slots = dict(line.slots)
    if cls == "out_of_set_name" and "player" in slots:
        slots["player"] = "Zarqowitz"
    elif cls == "out_of_set_number" and "home_score" in slots:
        slots["home_score"] = 17
    elif cls == "real_wrong_player" and "player" in slots:
        others = sorted({p.short_name for p in roster.players.values()} - {slots["player"]})
        slots["player"] = rng.choice(others)
    elif (
        cls == "real_wrong_score" and "home_score" in slots and slots["home_score"] != slots.get("away_score")
    ):
        slots["home_score"], slots["away_score"] = slots["away_score"], slots["home_score"]
    elif cls == "real_wrong_club" and "club" in slots:
        names = sorted(c.short_name for c in roster.clubs.values())
        slots["club"] = next(n for n in names if n != slots["club"])
    else:
        return None
    text = load(line.language).render(line.template_id, line.listening_mode, slots)
    return line.model_copy(update={"slots": slots, "text": text})


def slot_values_point(matches, clubs_of, players_of, language, mode, seed: int = 2,
                      rate: float = DEFECT_RATE) -> PointResult:  # fmt: skip
    from earshot.agents.checker import check_caller_line  # pyright: ignore[reportMissingImports]

    rng = random.Random(seed)
    res = PointResult("slot_values")
    for events, info in matches:
        for ln, m, trig, st, roster in _caller_items(
            events, info, clubs_of(info), players_of(info), language, mode
        ):
            cls = rng.choice(SLOT_DEFECTS) if rng.random() < rate else None
            bad = corrupt_slots(ln, cls, roster, rng) if cls else None
            if cls and bad is None:
                cls = None
            v = check_caller_line(bad or ln, m, trig, st, roster)
            res.add(cls, not v.ok, v.reason)
    return res


def roster_point(matches, clubs_of, players_of, language, mode, seed: int = 3,
                 auditor: Callable[[Any, Any], bool] | None = None) -> PointResult:  # fmt: skip
    """Swap two players' names in the roster the Caller and Checker share.

    The Checker cannot catch this by design (the wrong name came from a slot it re-derives from the
    same roster), so its recall here is expected to be about zero and is reported as such. An
    ``auditor(line, reference_roster) -> blocked`` with the reviewed roster can be passed in.
    """
    from earshot.agents.checker import check_caller_line  # pyright: ignore[reportMissingImports]
    from earshot.agents.state import Roster  # pyright: ignore[reportMissingImports]

    rng = random.Random(seed)
    res = PointResult("roster_entries" if auditor is None else "roster_entries_auditor")
    for events, info in matches:
        players = list(players_of(info))
        a, b = rng.sample(range(len(players)), 2)
        other = {"short_name": players[b].short_name, "name": players[b].name}
        swapped = [p.model_copy(update=other) if i == a else p for i, p in enumerate(players)]
        reference = Roster.build(info, list(clubs_of(info)), players)
        wrong = Roster.build(info, list(clubs_of(info)), swapped)
        target = players[a].player_id
        for ln, m, trig, st, roster in _caller_items(
            events, info, clubs_of(info), swapped, language, mode, wrong
        ):
            # Defective when the line names the swapped player in any role (actor, keeper, on, off),
            # not only as the actor: "X comes on for <wrong name>" is as wrong as the actor case.
            names_target = target in m.player_ids and other["short_name"] in {
                str(v) for v in ln.slots.values()
            }
            cls = "wrong_name_for_id" if names_target else None
            if auditor is not None:
                res.add(cls, auditor(ln, reference), "auditor")
            else:
                v = check_caller_line(ln, m, trig, st, roster)
                res.add(cls, not v.ok, v.reason)
    return res


@contextmanager
def mutated_templates(language, mutate: Callable[[dict], set[str]]):
    """Load a copy of the language's catalogue with ``mutate(templates) -> changed ids`` applied, for
    both the Caller and the Checker (as a corrupted template file would be)."""
    import earshot.agents.caller as caller_mod  # pyright: ignore[reportMissingImports]
    import earshot.agents.catalog as catalog_mod  # pyright: ignore[reportMissingImports]
    import earshot.agents.checker as checker_mod  # pyright: ignore[reportMissingImports]

    original = catalog_mod.load
    cat = copy.deepcopy(original(language))
    changed = mutate(cat.templates)

    def load(lang):
        return cat if lang == language else original(lang)

    saved = {mod: mod.load for mod in (caller_mod, checker_mod, catalog_mod)}
    checker_mod._bad_templates.cache_clear()
    for mod in saved:
        setattr(mod, "load", load)  # noqa: B010 (modules of an optional package)
    try:
        yield changed
    finally:
        for mod, fn in saved.items():
            setattr(mod, "load", fn)  # noqa: B010
        checker_mod._bad_templates.cache_clear()


TEMPLATE_DEFECTS = ("stray_numeral", "name_in_fixed_text")


def template_point(matches, clubs_of, players_of, language, mode, seed: int = 4,
                   rate: float = DEFECT_RATE,
                   auditor: Callable[..., Callable[[Any], Any]] | None = None) -> PointResult:  # fmt: skip
    """Plant a stray numeral or a club name in the fixed text of some templates in the catalogue the
    Caller and Checker share. The Checker sees the numeral but not the name (it renders from the same
    catalogue), so names are the auditor's job: with ``auditor(info, events, clubs, players)`` returning
    an ``audit(line)`` callable (findings mean blocked), each line is judged by the auditor instead,
    which reads the reviewed templates from disk."""
    from earshot.agents.checker import check_caller_line  # pyright: ignore[reportMissingImports]

    rng = random.Random(seed)
    res = PointResult("template_strings" if auditor is None else "template_strings_auditor")
    # Built before the catalogue is patched, so nothing the auditor caches can see the planted text.
    audits = {info.match_id: auditor(info, events, clubs_of(info), players_of(info))
              for events, info in matches} if auditor is not None else {}  # fmt: skip
    plan: dict[str, str] = {}

    def mutate(templates: dict) -> set[str]:
        for tid in sorted(templates):
            if rng.random() < rate:
                cls = rng.choice(TEMPLATE_DEFECTS)
                plan[tid] = cls
                for md, text in templates[tid].items():
                    templates[tid][md] = text.rstrip(".!") + (
                        " 3." if cls == "stray_numeral" else ", Kestrels."
                    )
        return set(plan)

    with mutated_templates(language, mutate):
        for events, info in matches:
            for ln, m, trig, st, roster in _caller_items(
                events, info, clubs_of(info), players_of(info), language, mode
            ):
                if auditor is not None:
                    res.add(plan.get(ln.template_id), bool(audits[info.match_id](ln)), "auditor")
                    continue
                v = check_caller_line(ln, m, trig, st, roster)
                res.add(plan.get(ln.template_id), not v.ok, v.reason)
    return res


def run_all(
    seeds: Sequence[int],
    languages: Sequence[Language] = (Language.EN,),
    modes: Sequence[ListeningMode] = tuple(ListeningMode),
    rate: float = DEFECT_RATE,
) -> dict[str, Any]:
    """Every injection point over ``seeds``, per language and mode. The roster and template points run
    twice: against the Checker alone (which reads the same roster and catalogue, so names planted
    there are invisible to it by design) and with the independent auditor, which checks the reviewed
    roster (``reference_blocked``) and the reviewed template files (``Auditor.audit``)."""
    from earshot.agents.auditor import Auditor, reference_blocked  # pyright: ignore[reportMissingImports]
    from earshot.sim.match import simulate
    from earshot.world.localize import roster as local_roster

    results = [simulate(s) for s in seeds]
    matches = [(list(r.events), r.info) for r in results]
    positions = {p.player_id: p.position for p in local_roster(Language.EN).players}
    points: list[dict[str, Any]] = [analyst_json_point(matches, positions, rate=rate).summary()]
    for language in languages:
        table = local_roster(language)

        def clubs_of(info: MatchInfo, table=table) -> list:
            return [c for c in table.clubs if c.team_id in (info.home.team_id, info.away.team_id)]

        def players_of(info: MatchInfo, table=table) -> list:
            return [p for p in table.players if p.team_id in (info.home.team_id, info.away.team_id)]

        for mode in modes:
            tag = {"language": language.value, "mode": mode.value}
            for res in (
                slot_values_point(matches, clubs_of, players_of, language, mode, rate=rate),
                roster_point(matches, clubs_of, players_of, language, mode),
                roster_point(matches, clubs_of, players_of, language, mode, auditor=reference_blocked),
                template_point(matches, clubs_of, players_of, language, mode, rate=rate),
                template_point(
                    matches,
                    clubs_of,
                    players_of,
                    language,
                    mode,
                    rate=rate,
                    auditor=lambda i, e, c, p: Auditor(i, e, c, p).audit,
                ),
            ):
                points.append(tag | res.summary())
    return {"seeds": list(seeds), "rate": rate, "points": points}
