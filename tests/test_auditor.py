"""The independent auditor: zero findings on clean lines, and every planted defect class caught.

Development seeds only. The run of record is on the held-out seeds (EVALUATION.md section 4).
"""

import random

import pytest

from earshot.agents.auditor import Auditor, reference_blocked
from earshot.agents.claims import window_summary_line
from earshot.agents.recap import recap_lines
from earshot.agents.state import Roster
from earshot.agents.statwatcher import StatWatcher, stat_line
from earshot.contract import Language, ListeningMode, Metric, PeriodEnd, StoppageStart
from earshot.eval.planted import (
    SLOT_DEFECTS,
    _caller_items,
    corrupt_slots,
    mutated_templates,
    roster_point,
)
from earshot.stats.claims import window_values
from earshot.tools.snapshots import MatchView
from earshot.voice.tts import ssml_for

SEED = 4


def match(seed, lang):
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    r = simulate(seed)
    teams = {r.info.home.team_id, r.info.away.team_id}
    t = roster(lang)
    clubs = [c for c in t.clubs if c.team_id in teams]
    players = [p for p in t.players if p.team_id in teams]
    return r, clubs, players, Auditor(r.info, r.events, clubs, players)


@pytest.mark.parametrize("lang", [Language.EN, Language.AR])
def test_clean_lines_have_no_findings(lang):
    r, clubs, players, aud = match(SEED, lang)
    lines = []
    for mode in ListeningMode:
        lines += [ln for ln, *_ in _caller_items(r.events, r.info, clubs, players, lang, mode)]
        for ev in r.events:
            if isinstance(ev, PeriodEnd) and ev.period_id in (1, 2):
                v = MatchView.of(r.info, clubs, players, r.events, ev.wall_ms + 1)
                period = "half_time" if ev.period_id == 1 else "full_time"
                lines += recap_lines(v, period, lang, mode, wall_ms=ev.wall_ms + 1)
            if isinstance(ev, StoppageStart) and ev.expected_dead_ms >= 15_000:
                v = MatchView.of(r.info, clubs, players, r.events, ev.wall_ms + 1)
                wid = f"{r.info.match_id}-win-{ev.event_id}"
                fb = window_summary_line(
                    v,
                    window_id=wid,
                    language=lang,
                    mode=mode,
                    wall_ms=ev.wall_ms,
                    deadline_wall_ms=ev.wall_ms + 1,
                )
                lines += [fb] if fb else []
    w = StatWatcher(r.info.home.team_id, r.info.away.team_id)
    for c in (c for ev in r.events for c in w.push(ev)):
        lines.append(stat_line(c, MatchView.of(r.info, clubs, players, r.events, c.wall_ms + 1), lang))
    assert len(lines) > 1000
    findings = [f for ln in lines for f in aud.audit(ln, ssml_for(ln))]
    assert findings == []


def test_every_slot_defect_class_is_caught():
    r, clubs, players, aud = match(SEED, Language.EN)
    ref = Roster.build(r.info, clubs, players)
    rng = random.Random(1)
    caught = dict.fromkeys(SLOT_DEFECTS, 0)
    total = dict.fromkeys(SLOT_DEFECTS, 0)
    for ln, *_ in _caller_items(r.events, r.info, clubs, players, Language.EN, ListeningMode.ANALYST):
        for cls in SLOT_DEFECTS:
            bad = corrupt_slots(ln, cls, ref, rng)
            if bad is not None:
                total[cls] += 1
                caught[cls] += bool(aud.audit(bad, ssml_for(bad)))
    assert all(total[c] > 0 for c in SLOT_DEFECTS), total
    assert caught == total


def test_a_wrong_roster_entry_is_caught_against_the_reviewed_roster():
    r, clubs, players, _ = match(SEED, Language.EN)
    res = roster_point(
        [(r.events, r.info)], lambda _i: clubs, lambda _i: players, Language.EN, ListeningMode.CASUAL,
        auditor=reference_blocked,
    ).summary()  # fmt: skip
    assert res["defects_injected"] > 0
    assert res["recall"] == 1.0
    assert res["false_block_rate"] == 0.0


@pytest.mark.parametrize("defect", [" 3.", ", Kestrels."])
def test_template_text_the_shared_catalogue_was_given_is_caught(defect):
    r, clubs, players, aud = match(SEED, Language.EN)

    def mutate(templates):
        for tid in ("foul.1", "shot.1"):
            for md, text in templates[tid].items():
                templates[tid][md] = text.rstrip(".!") + defect
        return {"foul.1", "shot.1"}

    with mutated_templates(Language.EN, mutate):
        lines = [
            ln
            for ln, *_ in _caller_items(r.events, r.info, clubs, players, Language.EN, ListeningMode.CASUAL)
            if ln.template_id in ("foul.1", "shot.1")
        ]
    assert lines
    # Caught as a template mismatch, or as a slot that no longer reads back as the reviewed name.
    assert all(aud.audit(ln) for ln in lines)


def test_a_numeral_in_the_ssml_outside_a_slot_is_caught():
    r, clubs, players, aud = match(SEED, Language.EN)
    ln = next(
        ln for ln, *_ in _caller_items(r.events, r.info, clubs, players, Language.EN, ListeningMode.CASUAL)
    )
    ssml = ssml_for(ln)
    assert aud.audit(ln, ssml) == []
    assert any(f.kind == "ssml" for f in aud.audit(ln, ssml.replace("</voice>", " 7 </voice>")))
    assert any(f.kind == "ssml" for f in aud.audit(ln, ssml.replace("</voice>", " seven </voice>")))


def test_fallback_possession_is_rounded_once_from_the_unrounded_share():
    # Regression: the tool rounded to 3 places, then speech rounded half-to-even: 52.55% was said as 52.
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    r = simulate(1)
    teams = {r.info.home.team_id, r.info.away.team_id}
    t = roster(Language.EN)
    clubs = [c for c in t.clubs if c.team_id in teams]
    players = [p for p in t.players if p.team_id in teams]
    pos = {p.player_id: p.position for p in players}
    checked = 0
    for ev in r.events:
        if isinstance(ev, StoppageStart) and ev.expected_dead_ms >= 15_000:
            v = MatchView.of(r.info, clubs, players, r.events, ev.wall_ms + 1)
            ln = window_summary_line(
                v, window_id=f"{r.info.match_id}-win-{ev.event_id}", language=Language.EN,
                mode=ListeningMode.ANALYST, wall_ms=ev.wall_ms, deadline_wall_ms=ev.wall_ms + 1,
            )  # fmt: skip
            if ln is None:
                continue
            now = ev.wall_ms + 1
            past = [e for e in v.events if e.wall_ms < now]
            _, after = window_values(
                Metric.POSSESSION_SHARE, past, ln.club_tags[0], now - 300_000, now, r.info.home.team_id, pos
            )
            assert after is not None
            assert ln.slots["percent"] == round(after * 100)
            checked += 1
    assert checked > 10


def test_a_substitution_naming_the_wrong_teammate_is_caught():
    # Stream 2's planted run found substitutions slipped through: on/off were checked for team
    # membership only, so swapping in a real teammate passed. They are now bound to [on, off] ids.
    from earshot.agents.catalog import load

    r, clubs, players, aud = match(SEED, Language.EN)
    ref = Roster.build(r.info, clubs, players)
    subs = [
        ln
        for ln, *_ in _caller_items(r.events, r.info, clubs, players, Language.EN, ListeningMode.CASUAL)
        if "on" in ln.slots
    ]
    assert subs
    for ln in subs:
        assert aud.audit(ln) == []
        assert not reference_blocked(ln, ref)
        team = ln.club_tags[0]
        other = next(p.short_name for p in players if p.team_id == team and p.player_id not in ln.player_ids)
        for slot in ("on", "off"):
            slots = {**ln.slots, slot: other}
            text = load(Language.EN).render(ln.template_id, ln.listening_mode, slots)
            bad = ln.model_copy(update={"slots": slots, "text": text})
            assert any(f.kind == "name" for f in aud.audit(bad)), slot
            assert reference_blocked(bad, ref), slot
