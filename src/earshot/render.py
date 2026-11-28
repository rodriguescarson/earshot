"""Render a fixture clip end to end: events -> Spotter -> Caller -> Checker -> Director -> Voice -> mix.

Output is a folder the web client plays: ``audio.m4a`` plus ``manifest.json`` (aired lines with
times, a score/minute timeline, and a 5 Hz ball track for the screen).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from earshot.agents.analyst import Analyst, timeout_for
from earshot.agents.auditor import Auditor
from earshot.agents.caller import Caller
from earshot.agents.catalog import load
from earshot.agents.checker import check_caller_line
from earshot.agents.claims import (
    check_claim_line,
    claim_key,
    claim_line,
    fresh,
    gate_cause,
    select_for_mode,
    verify,
    window_summary_line,
)
from earshot.agents.director import Director, Profile
from earshot.agents.spotter import Spotter
from earshot.agents.state import MatchState, Roster, is_goal, spoken_minute
from earshot.agents.statwatcher import StatWatcher, check_stat_line, stat_line
from earshot.contract import (
    Event,
    FocusStat,
    Language,
    Line,
    LineSource,
    ListeningMode,
    MomentKind,
    Restart,
    StoppageStart,
)
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.pitch import attacks_right, to_attacking
from earshot.tools.snapshots import MatchView
from earshot.voice.encode import encode_aac
from earshot.voice.mix import REPLAY_TEMPLATE, Arm, BallPoint, mix
from earshot.voice.replay import REPLAY_MS, replay_audio
from earshot.voice.tts import SAMPLE_RATE, Clip, Synthesizer, read_mono, ssml_for, write_stereo

EV = TypeAdapter(Event)
EXCITED = frozenset({MomentKind.GOAL, MomentKind.OWN_GOAL, MomentKind.RED_CARD, MomentKind.PENALTY_AWARDED})


def danger(b: dict, roster: Roster) -> float:
    """xT danger from the engine's ball track; a positional fallback for tracks rendered without it."""
    if b.get("danger") is not None:
        return float(b["danger"])
    if b.get("team_id") is None or not b.get("in_play", True):
        return 0.0
    ax, _ = to_attacking(b["x"], b["y"], attacks_right(b["team_id"] == roster.home_id, b["period_id"]))
    return max(0.0, (ax - 52.5) / 52.5) ** 2


ANALYST_MIN_DEAD_MS = 15_000
CACHED_ANALYST_LATENCY_MS = 2_500
"""Replayed (cached) Analyst answers are scheduled as if they took this long, so renders are stable."""


def replay_clip(
    synth: Synthesizer, line: Line, goal_wall_ms: int, ball: list[BallPoint], cache: Path
) -> Clip:
    """The spoken intro, a short breath, then the sonified build-up and the roar (one stereo file)."""
    intro = synth.synthesize(ssml_for(line))
    voice = read_mono(intro.path)
    gap = np.zeros(int(0.3 * SAMPLE_RATE), np.float32)
    head = np.stack([np.concatenate([voice, gap])] * 2, axis=1)
    audio = np.concatenate([head, replay_audio(ball, goal_wall_ms)])
    key = hashlib.sha256(f"{intro.path.name}|{goal_wall_ms}|{len(ball)}".encode()).hexdigest()[:24]
    path = cache / f"replay-{key}.wav"
    write_stereo(path, audio)
    return Clip(path, int(len(audio) / SAMPLE_RATE * 1000))


def localized(d: dict, language: Language) -> tuple[list[ClubInfo], list[PlayerInfo]]:
    """Club and player names in the language's script (Arabic, Devanagari); Latin otherwise.

    Names come from the reviewed roster tables (``earshot.world.localize``) by id, never from a
    model at runtime, so the Checker's slot rule still holds.
    """
    clubs = [ClubInfo(**c) for c in d["clubs"]]
    players = [PlayerInfo(**p) for p in d["players"]]
    if language not in (Language.AR, Language.HI):
        return clubs, players
    from earshot.world.localize import roster as local_roster

    table = local_roster(language)
    by_club = {c.team_id: c for c in table.clubs}
    by_player = {p.player_id: p for p in table.players}
    return [by_club.get(c.team_id, c) for c in clubs], [by_player.get(p.player_id, p) for p in players]


def full_match_events(d: dict) -> list[Event]:
    """The whole match up to the clip, so the Analyst can compare windows before the clip started.

    Offline tooling only: it uses the simulator's events and never its truth log.
    """
    from earshot.sim.match import simulate

    events = simulate(d["seed"]).events
    clip_ids = {e["event_id"] for e in d["events"]}
    assert clip_ids <= {e.event_id for e in events}, "fixture does not match the engine: regenerate it"
    return events


def player_tracks(d: dict, t0: int, t_end: int, home_id: str, hz: int = 2) -> dict:
    """The engine's player frames inside the clip window, thinned to ``hz``, for the match view.

    Players off the pitch (substitutes) have NaN positions and are left out. Shape kept small:
    one row per frame, x and y per player in ``ids`` order, tenths of a metre.
    """
    import numpy as np

    from earshot.sim.frames import HZ, render_frames

    fr = render_frames(d["seed"])
    idx = np.flatnonzero((fr.t_ms >= t0) & (fr.t_ms <= t_end))[:: max(1, HZ // hz)]
    ids = [pid for pid, xy in fr.players.items() if not np.isnan(xy[idx]).any()]
    names = {p["player_id"]: p["short_name"] for p in d["players"]}
    return {
        "hz": hz,
        "ids": ids,
        "home": [pid.startswith(f"{home_id}-") for pid in ids],
        "names": [names.get(pid, pid) for pid in ids],
        "t_ms": [int(fr.t_ms[i] - t0) for i in idx],
        "xy": [[round(float(v), 1) for pid in ids for v in fr.players[pid][i]] for i in idx],
    }


def render_fixture(
    fixture: Path,
    out_dir: Path,
    *,
    language: Language,
    mode: ListeningMode,
    arms: list[Arm],
    cache: Path,
    analyst: bool = True,
    replay: bool = True,
    stat: FocusStat | None = None,
) -> dict:
    d = json.loads(fixture.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, *localized(d, language))
    clip = d["clip"]
    t0 = clip["start_wall_ms"]
    all_events = full_match_events(d) if analyst else [EV.validate_python(r) for r in d["events"]]
    state = MatchState.start(match)
    for ev in all_events:
        if ev.wall_ms >= t0:
            break
        state = state.apply(ev, roster)
    if not analyst:
        state = replace(
            state,
            period_id=clip["period_id"],
            home_score=clip["home_score_at_start"],
            away_score=clip["away_score_at_start"],
            status="live",
        )
    the_analyst = Analyst(cache_dir=cache.parent / "analyst-cache", patient=True) if analyst else None
    if the_analyst:
        the_analyst.warm()
    pending: list[Line] = []
    windows: list[dict] = []
    said_summaries: set[str] = set()
    aired_claims: dict[tuple[str, str, str], int] = {}

    spotter, caller = Spotter(roster), Caller(roster)
    lines: list[Line] = []
    kinds: dict[str, MomentKind] = {}
    blocked: list[dict] = []
    timeline: list[dict] = []
    goals: list[int] = []
    by_id: dict[str, Event] = {}
    states: dict[str, MatchState] = {}

    agents: list[dict] = []

    def log(wall_ms: int, agent: str, text: str, kind: str = "info") -> None:
        """One decision, for the producer view: who decided what, and when (clip time)."""
        agents.append({"t_ms": wall_ms - t0, "agent": agent, "kind": kind, "text": text})

    def voice_moments(moments: list, st: MatchState) -> None:
        for m in moments:
            trigger = by_id[m.event_ids[0]]
            log(m.wall_ms, "Spotter", f"{m.kind.value.replace('_', ' ')}, priority {m.priority}")
            for ln in caller.lines(m, trigger, st, language, mode):
                log(ln.wall_ms, "Caller", f"{ln.template_id}: {ln.text}")
                v = check_caller_line(ln, m, trigger, st, roster)
                if v.ok:
                    lines.append(ln)
                    kinds[ln.line_id] = m.kind
                    log(ln.wall_ms, "Checker", "passed: every name and number re-derived", "ok")
                else:
                    blocked.append({"line": ln.text, "reason": v.reason})
                    log(ln.wall_ms, "Checker", f"blocked: {v.reason}", "block")

    def replay_line(goal: Event) -> Line:
        cat = load(language)
        text = cat.render(REPLAY_TEMPLATE, mode, {})
        return Line(
            line_id=f"{goal.event_id}-replay-{language.value}-{mode.value}",
            match_id=match.match_id,
            moment_id=None,
            source=LineSource.NOTICE,
            language=language,
            listening_mode=mode,
            template_id=REPLAY_TEMPLATE,
            slots={},
            text=text,
            priority=48,  # after the goal line, before any Analyst line in the celebration window
            wall_ms=goal.wall_ms + 200,
            deadline_wall_ms=goal.wall_ms + 60_000,
            est_ms=REPLAY_MS + 3_500,
        )

    def analyse_window(ev: StoppageStart) -> list[Line]:
        assert the_analyst is not None
        view = MatchView.of(
            match, list(roster.clubs.values()), list(roster.players.values()), all_events, ev.wall_ms
        )
        window_id = f"{match.match_id}-win-{ev.event_id}"
        res = the_analyst.run(view, window_id, ev.expected_dead_ms)
        budget_ms = int(timeout_for(ev.expected_dead_ms) * 1000)
        ready = ev.wall_ms + (CACHED_ANALYST_LATENCY_MS if res.cached else min(res.latency_ms, budget_ms))
        deadline = ev.wall_ms + ev.expected_dead_ms
        checked = [(c, verify(c, view)) for c in res.claims]
        gated = [(gate_cause(c, view), k) for c, k in checked]
        if res.error or res.timed_out:
            log(
                ready,
                "Analyst",
                "timed out: the window summary airs instead"
                if res.timed_out
                else "unavailable: the window summary airs instead",
                "block",
            )
        else:
            tools_used = ", ".join(dict.fromkeys(res.tool_calls)) or "no tools"
            log(ready, "Analyst", f"{len(res.claims)} claims via {tools_used} ({res.tokens} tokens)")
        for (c, why), k in gated:
            label = (
                f"{roster.clubs[c.team_id].short_name} {c.metric.value.replace('_', ' ')} {c.direction.value}"
            )
            if k.ok:
                log(ready, "Checker", f"claim verified: {label} ({k.before:.2f} to {k.after:.2f})", "ok")
            else:
                log(ready, "Checker", f"claim rejected: {label}: {k.reason}", "block")
            if why:
                log(ready, "Checker", f"cause dropped: {why}", "block")
        ok = fresh([(c, k) for (c, _why), k in gated if k.ok], aired_claims, ev.wall_ms)
        out: list[Line] = []
        if ok and not res.timed_out and not res.error:
            for c, k in select_for_mode(ok, mode):
                ln = claim_line(
                    c,
                    k,
                    view=view,
                    window_id=window_id,
                    language=language,
                    mode=mode,
                    wall_ms=ready,
                    deadline_wall_ms=deadline,
                )
                if check_claim_line(ln, c, view).ok:
                    aired_claims[claim_key(c)] = ev.wall_ms
                    out.append(ln)
                    kinds[ln.line_id] = MomentKind.STOPPAGE
        if not out:
            fb = window_summary_line(
                view,
                window_id=window_id,
                language=language,
                mode=mode,
                wall_ms=ready,
                deadline_wall_ms=deadline,
            )
            if fb is not None and fb.text not in said_summaries:
                said_summaries.add(fb.text)
                out.append(fb)
                kinds[fb.line_id] = MomentKind.STOPPAGE
        windows.append(
            {
                "window_id": window_id,
                "t_ms": ev.wall_ms - t0,
                "kind": ev.kind.value,
                "expected_dead_ms": ev.expected_dead_ms,
                "latency_ms": res.latency_ms,
                "cached": res.cached,
                "timed_out": res.timed_out,
                "error": res.error,
                "tool_calls": res.tool_calls,
                "tokens": res.tokens,
                "claims": [
                    {
                        **c.model_dump(mode="json"),
                        "verified": k.ok,
                        "reason": k.reason,
                        "recomputed": [k.before, k.after],
                        "cause_dropped": why,
                    }
                    for (c, k), ((_, why), _) in zip(checked, gated, strict=True)
                ],
                "invalid": res.invalid,
                "aired_lines": [ln.text for ln in out],
            }
        )
        return out

    for raw in d["events"]:
        ev = EV.validate_python(raw)
        by_id[ev.event_id] = ev
        before, state = state, state.apply(ev, roster)
        states[ev.event_id] = state
        if is_goal(ev):
            goals.append(ev.wall_ms)
            if replay:
                pending.append(replay_line(ev))
        timeline.append(
            {
                "t_ms": ev.wall_ms - t0,
                "home_score": state.home_score,
                "away_score": state.away_score,
                "minute": spoken_minute(state.period_id, state.clock_ms),
                "status": state.status,
            }
        )
        voice_moments(spotter.observe(ev, before, state), state)
        if isinstance(ev, StoppageStart) and the_analyst and ev.expected_dead_ms >= ANALYST_MIN_DEAD_MS:
            pending.extend(analyse_window(ev))
        if isinstance(ev, Restart) and pending:
            for ln in pending:
                lines.append(
                    ln.model_copy(update={"deadline_wall_ms": max(ln.wall_ms, ev.wall_ms - ln.est_ms)})
                )
            pending.clear()
    voice_moments(spotter.flush(), state)
    lines.extend(pending)
    if stat is not None and mode is ListeningMode.ANALYST:
        clubs, players = localized(d, language)
        watcher = StatWatcher(match.home.team_id, match.away.team_id)
        for ev in all_events:
            if ev.wall_ms > clip["end_wall_ms"]:
                break
            for c in watcher.push(ev):
                if c.stat is not stat or c.wall_ms < t0:
                    continue
                view = MatchView.of(match, clubs, players, all_events, c.wall_ms + 1)
                ln = stat_line(c, view, language)
                log(ln.wall_ms, "StatWatcher", f"{c.stat.value} crossing: {ln.text}")
                v = check_stat_line(ln, view)
                if v.ok:
                    lines.append(ln)
                    log(ln.wall_ms, "Checker", "passed: recomputed from the events", "ok")
                else:
                    blocked.append({"line": ln.text, "reason": v.reason})
                    log(ln.wall_ms, "Checker", f"blocked: {v.reason}", "block")

    # The independent auditor (a separate code path) re-reads every line against the reviewed
    # templates and roster and the whole event log; anything it finds never airs.
    clubs_l, players_l = localized(d, language)
    auditor = Auditor(match, full_match_events(d), clubs_l, players_l)
    audit = {"lines": 0, "findings": []}
    kept: list[Line] = []
    for ln in lines:
        audit["lines"] += 1
        findings = auditor.audit(ln, ssml_for(ln))
        if findings:
            audit["findings"] += [{"line": ln.text, "kind": f.kind, "detail": f.detail} for f in findings]
            blocked.append({"line": ln.text, "reason": f"auditor: {findings[0].detail}"})
            log(ln.wall_ms, "Auditor", f"blocked: {findings[0].detail}", "block")
        else:
            kept.append(ln)
            if ln.template_id != REPLAY_TEMPLATE:
                log(
                    ln.wall_ms,
                    "Auditor",
                    "passed: re-read against the reviewed templates, roster and event log",
                    "ok",
                )
    lines = kept

    ball = [BallPoint(b["wall_ms"], b["x"], b["y"], b["in_play"], danger(b, roster)) for b in d["ball"]]
    players = player_tracks(d, t0, clip["end_wall_ms"], roster.home_id)
    synth = Synthesizer(cache)
    clips = {
        ln.line_id: synth.synthesize(ssml_for(ln, excited=kinds.get(ln.line_id) in EXCITED))
        for ln in lines
        if ln.template_id != REPLAY_TEMPLATE
    }
    for ln in lines:
        if ln.template_id == REPLAY_TEMPLATE:
            goal_id = ln.line_id.split("-replay-")[0]
            clips[ln.line_id] = replay_clip(synth, ln, by_id[goal_id].wall_ms, ball, cache)
    director = Director(
        Profile(listening_mode=mode, stat=stat), duration_ms=lambda line: clips[line.line_id].duration_ms
    )
    aired = director.run(lines)
    for x in director.dropped:
        log(x.at_ms, "Director", f"dropped ({x.reason}): {x.line.text}", "block")
    # Late never-drop lines carry the "earlier" marker: re-voice those with the prefix.
    for i, a in enumerate(aired):
        if a.late:
            late_line = a.line.model_copy(update={"late": True})
            clips[a.line.line_id] = synth.synthesize(ssml_for(late_line, excited=True))
            aired[i] = replace(a, line=late_line)

    for a in aired:
        role = "explanation voice" if a.line.voice_role.value == "explanation" else "facts voice"
        log(a.start_ms, "Director", f"on air: {a.line.text}", "air")
        log(
            a.start_ms,
            "Voice",
            f"{role}, {clips[a.line.line_id].duration_ms / 1000:.1f} s, pan {a.line.pan:+.1f}",
        )
        if a.cut_at_ms is not None:
            log(a.cut_at_ms, "Director", f"cut for a more important line: {a.line.text}", "block")
    agents.sort(key=lambda e: e["t_ms"])

    out_dir.mkdir(parents=True, exist_ok=True)
    end_ms = clip["end_wall_ms"]
    files = {}
    for arm in arms:
        audio = mix(aired, clips, start_ms=t0, end_ms=end_ms, arm=arm, ball=ball, goal_wall_ms=goals)
        wav = out_dir / f"{arm.value}.wav"
        write_stereo(wav, audio)
        m4a = wav.with_suffix(".m4a")
        encode_aac(wav, m4a)
        wav.unlink()
        files[arm.value] = m4a.name
    manifest = {
        "clip_id": f"{d['match']['match_id']}-{language.value}-{mode.value}",
        "description": d["description"],
        "language": language.value,
        "listening_mode": mode.value,
        "duration_ms": end_ms - t0,
        "audio": files,
        "home": {"team_id": roster.home_id, "name": roster.clubs[roster.home_id].short_name},
        "away": {"team_id": roster.away_id, "name": roster.clubs[roster.away_id].short_name},
        "start": {
            "home_score": clip["home_score_at_start"],
            "away_score": clip["away_score_at_start"],
            "minute": spoken_minute(clip["period_id"], clip["start_clock_ms"]),
        },
        "lines": [
            {
                "t_ms": a.start_ms - t0,
                "end_ms": (a.cut_at_ms or a.end_ms) - t0,
                "text": ("Earlier: " if a.late else "") + a.line.text,
                "voice_role": a.line.voice_role.value,
                "priority": a.line.priority,
                "never_drop": a.line.never_drop,
                "cut": a.cut_at_ms is not None,
                "template_id": a.line.template_id,
                "source": a.line.source.value,
                "replay": a.line.template_id == REPLAY_TEMPLATE,
                "pan": a.line.pan,
            }
            for a in aired
        ],
        "timeline": timeline,
        "ball": [
            {"t_ms": b.wall_ms - t0, "x": round(b.x, 1), "y": round(b.y, 1), "in_play": b.in_play}
            for b in ball[::2]
        ],
        "players": players,
        "stats": {
            "lines_checked": len(lines) + len(blocked),
            "lines_blocked": len(blocked),
            "aired": len(aired),
            "dropped": len(director.dropped),
            "tts_characters_sent": synth.characters,
        },
        "blocked": blocked,
        "audit": audit,
        "agents": agents,
        "analyst_windows": windows,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="python -m earshot.render", description=__doc__)
    p.add_argument("--fixture", type=Path, default=Path("fixtures/clip-demo.json"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--lang", type=Language, default=Language.EN)
    p.add_argument("--mode", type=ListeningMode, default=ListeningMode.CASUAL)
    p.add_argument("--arms", default="speech,pan,full,bed,mono")
    p.add_argument("--cache", type=Path, default=Path("data/tts-cache"))
    p.add_argument("--no-analyst", action="store_true", help="Caller only (no model calls)")
    p.add_argument("--stat", type=FocusStat, default=None, help="follow one stat (Analyst mode)")
    a = p.parse_args(argv)
    m = render_fixture(
        a.fixture,
        a.out,
        language=a.lang,
        mode=a.mode,
        arms=[Arm(x) for x in a.arms.split(",")],
        cache=a.cache,
        analyst=not a.no_analyst,
        stat=a.stat,
    )
    print(json.dumps(m["stats"]), json.dumps(m["audio"]))


if __name__ == "__main__":
    main()
