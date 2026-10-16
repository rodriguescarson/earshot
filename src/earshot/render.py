"""Render a fixture clip end to end: events -> Spotter -> Caller -> Checker -> Director -> Voice -> mix.

Output is a folder the web client plays: ``audio.m4a`` plus ``manifest.json`` (aired lines with
times, a score/minute timeline, and a 5 Hz ball track for the screen).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

from pydantic import TypeAdapter

from earshot.agents.analyst import Analyst, timeout_for
from earshot.agents.caller import Caller
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
from earshot.contract import Event, Language, Line, ListeningMode, MomentKind, Restart, StoppageStart
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.pitch import attacks_right, to_attacking
from earshot.tools.match import MatchView, Teams
from earshot.voice.mix import Arm, BallPoint, mix
from earshot.voice.tts import Synthesizer, ssml_for, write_stereo

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


def full_match_events(d: dict) -> list[Event]:
    """The whole match up to the clip, so the Analyst can compare windows before the clip started.

    Offline tooling only: it uses the simulator's events and never its truth log.
    """
    from earshot.sim.match import simulate

    events = simulate(d["seed"]).events
    clip_ids = {e["event_id"] for e in d["events"]}
    assert clip_ids <= {e.event_id for e in events}, "fixture does not match the engine: regenerate it"
    return events


def render_fixture(
    fixture: Path,
    out_dir: Path,
    *,
    language: Language,
    mode: ListeningMode,
    arms: list[Arm],
    cache: Path,
    analyst: bool = True,
) -> dict:
    d = json.loads(fixture.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, [ClubInfo(**c) for c in d["clubs"]], [PlayerInfo(**p) for p in d["players"]])
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
    teams = Teams(roster.home_id, roster.away_id, {t: c.short_name for t, c in roster.clubs.items()})
    positions = {p.player_id: p.position for p in roster.players.values()}
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

    def analyse_window(ev: StoppageStart) -> list[Line]:
        assert the_analyst is not None
        view = MatchView(all_events, teams, positions, now_wall_ms=ev.wall_ms)
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
            label = f"{view.teams.names[c.team_id]} {c.metric.value.replace('_', ' ')} {c.direction.value}"
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

    synth = Synthesizer(cache)
    clips = {ln.line_id: synth.synthesize(ssml_for(ln, excited=kinds[ln.line_id] in EXCITED)) for ln in lines}
    director = Director(
        Profile(listening_mode=mode), duration_ms=lambda line: clips[line.line_id].duration_ms
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

    ball = [BallPoint(b["wall_ms"], b["x"], b["y"], b["in_play"], danger(b, roster)) for b in d["ball"]]
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
                "pan": a.line.pan,
            }
            for a in aired
        ],
        "timeline": timeline,
        "ball": [
            {"t_ms": b.wall_ms - t0, "x": round(b.x, 1), "y": round(b.y, 1), "in_play": b.in_play}
            for b in ball[::2]
        ],
        "stats": {
            "lines_checked": len(lines) + len(blocked),
            "lines_blocked": len(blocked),
            "aired": len(aired),
            "dropped": len(director.dropped),
            "tts_characters_sent": synth.characters,
        },
        "blocked": blocked,
        "agents": agents,
        "analyst_windows": windows,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def encode_aac(wav: Path, m4a: Path, bitrate: int = 128_000) -> None:
    """AAC in an M4A container (plays on iOS Safari). ffmpeg where it works, else macOS afconvert."""
    if shutil.which("ffmpeg"):
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav),
                "-c:a",
                "aac",
                "-b:a",
                str(bitrate),
                str(m4a),
            ],
            capture_output=True,
        )
        if r.returncode == 0:
            return
    if shutil.which("afconvert"):
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", "-b", str(bitrate), str(wav), str(m4a)], check=True
        )
        return
    raise RuntimeError("no working AAC encoder: install ffmpeg")


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
    a = p.parse_args(argv)
    m = render_fixture(
        a.fixture,
        a.out,
        language=a.lang,
        mode=a.mode,
        arms=[Arm(x) for x in a.arms.split(",")],
        cache=a.cache,
    )
    print(json.dumps(m["stats"]), json.dumps(m["audio"]))


if __name__ == "__main__":
    main()
