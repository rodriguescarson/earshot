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

from earshot.agents.caller import Caller
from earshot.agents.checker import check_caller_line
from earshot.agents.director import Director, Profile
from earshot.agents.spotter import Spotter
from earshot.agents.state import MatchState, Roster, is_goal, spoken_minute
from earshot.contract import Event, Language, Line, ListeningMode, MomentKind
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo
from earshot.pitch import attacks_right, to_attacking
from earshot.voice.mix import Arm, BallPoint, mix
from earshot.voice.tts import Synthesizer, ssml_for, write_stereo

EV = TypeAdapter(Event)
EXCITED = frozenset({MomentKind.GOAL, MomentKind.OWN_GOAL, MomentKind.RED_CARD, MomentKind.PENALTY_AWARDED})


def danger(x: float, y: float, team_id: str | None, period_id: int, roster: Roster) -> float:
    """Placeholder danger until the xT model lands: how deep into the attacking half the ball is."""
    if team_id is None:
        return 0.0
    ax, _ = to_attacking(x, y, attacks_right(team_id == roster.home_id, period_id))
    return max(0.0, (ax - 52.5) / 52.5) ** 2


def render_fixture(
    fixture: Path, out_dir: Path, *, language: Language, mode: ListeningMode, arms: list[Arm], cache: Path
) -> dict:
    d = json.loads(fixture.read_text())
    match = MatchInfo(**d["match"])
    roster = Roster.build(match, [ClubInfo(**c) for c in d["clubs"]], [PlayerInfo(**p) for p in d["players"]])
    clip = d["clip"]
    state = replace(
        MatchState.start(match),
        period_id=clip["period_id"],
        home_score=clip["home_score_at_start"],
        away_score=clip["away_score_at_start"],
        status="live",
    )
    spotter, caller = Spotter(roster), Caller(roster)
    lines: list[Line] = []
    kinds: dict[str, MomentKind] = {}
    blocked: list[dict] = []
    timeline: list[dict] = []
    goals: list[int] = []
    t0 = clip["start_wall_ms"]
    by_id: dict[str, Event] = {}
    states: dict[str, MatchState] = {}

    def voice_moments(moments: list, st: MatchState) -> None:
        for m in moments:
            trigger = by_id[m.event_ids[0]]
            for ln in caller.lines(m, trigger, st, language, mode):
                v = check_caller_line(ln, m, trigger, st, roster)
                if v.ok:
                    lines.append(ln)
                    kinds[ln.line_id] = m.kind
                else:
                    blocked.append({"line": ln.text, "reason": v.reason})

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
    voice_moments(spotter.flush(), state)

    synth = Synthesizer(cache)
    clips = {ln.line_id: synth.synthesize(ssml_for(ln, excited=kinds[ln.line_id] in EXCITED)) for ln in lines}
    director = Director(
        Profile(listening_mode=mode), duration_ms=lambda line: clips[line.line_id].duration_ms
    )
    aired = director.run(lines)
    # Late never-drop lines carry the "earlier" marker: re-voice those with the prefix.
    for i, a in enumerate(aired):
        if a.late:
            late_line = a.line.model_copy(update={"late": True})
            clips[a.line.line_id] = synth.synthesize(ssml_for(late_line, excited=True))
            aired[i] = replace(a, line=late_line)

    ball = [
        BallPoint(
            b["wall_ms"],
            b["x"],
            b["y"],
            b["in_play"],
            danger(b["x"], b["y"], b["team_id"], b["period_id"], roster),
        )
        for b in d["ball"]
    ]
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
    p.add_argument("--arms", default="speech,pan,full")
    p.add_argument("--cache", type=Path, default=Path("data/tts-cache"))
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
