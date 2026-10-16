"""Listening test material (pre-registered in EVALUATION.md): three matched clips, three arms each.

Arms: speech only (centred), speech with pan, speech with pan plus the continuous layer.
Within-subject, three arm orders (a Latin square over the three clips), four probes per clip
at in-play moments; each probe asks the score, who has the ball, and where it is
(left, middle or right of the pitch as heard). Offline tooling: uses the simulator's events
and ball path only, never its truth log.

    python -m earshot.listening --out data/clips/listen-test
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from earshot.contract import Language, ListeningMode
from earshot.render import render_fixture
from earshot.sim.clip import best_clip, dumps, fixture
from earshot.sim.match import simulate
from earshot.voice.mix import Arm

DEMO_SEED = 26
PROBE_OFFSETS_MS = (30_000, 60_000, 115_000, 160_000)
PROBE_GAP_MS = 20_000
"""Probes at least this far apart, so each one asks about a different moment."""
ORDERS = (("speech", "pan", "full"), ("pan", "full", "speech"), ("full", "speech", "pan"))


def matched_clips(seeds: range, n: int = 3) -> list[tuple[int, dict]]:
    """The n clips (distinct matches, not the demo) whose density scores are closest together."""
    found = []
    for seed in seeds:
        if seed == DEMO_SEED:
            continue
        r = simulate(seed)
        c = best_clip(r)
        if c is not None:
            found.append((c.score, seed, r, c))
    found.sort()
    best = min(range(len(found) - n + 1), key=lambda i: found[i + n - 1][0] - found[i][0])
    return [(seed, fixture(r, c)) for _, seed, r, c in found[best : best + n]]


def probes(fx: dict) -> list[dict]:
    """Four in-play moments with the true answers a listener could know from what they heard."""
    t0 = fx["clip"]["start_wall_ms"]
    names = {c["team_id"]: c["short_name"] for c in fx["clubs"]}
    home, away = fx["match"]["home"]["team_id"], fx["match"]["away"]["team_id"]
    score = {home: fx["clip"]["home_score_at_start"], away: fx["clip"]["away_score_at_start"]}
    goals_at = []
    for e in fx["events"]:
        if (e["type"].startswith("shot") and e.get("result") == "success") or e.get("result") == "owngoal":
            scorer = (
                e["team_id"] if e.get("result") != "owngoal" else (away if e["team_id"] == home else home)
            )
            goals_at.append((e["wall_ms"], scorer))
    out: list[dict] = []
    last = -PROBE_GAP_MS
    for off in PROBE_OFFSETS_MS:
        earliest = max(t0 + off, t0 + last + PROBE_GAP_MS)
        sample = next(
            (b for b in fx["ball"] if b["wall_ms"] >= earliest and b["in_play"] and b["team_id"]),
            None,
        )
        if sample is None:
            continue
        last = sample["wall_ms"] - t0
        s = dict(score)
        for when, team in goals_at:
            if when <= sample["wall_ms"]:
                s[team] += 1
        x = sample["x"]
        out.append(
            {
                "t_ms": sample["wall_ms"] - t0,
                "score": f"{names[home]} {s[home]}, {names[away]} {s[away]}",
                "possession": names[sample["team_id"]],
                "where": "left" if x < 35 else "middle" if x < 70 else "right",
            }
        )
    return out


def headphone_check(out: Path, cache: Path) -> str:
    """'Left.' hard left, then 'Right.' hard right: the left/right check before every session."""
    import numpy as np

    from earshot.voice.encode import encode_aac
    from earshot.voice.tts import SAMPLE_RATE, Synthesizer, read_mono, write_stereo

    synth = Synthesizer(cache)
    parts = []
    for word, side in (("Left.", 0), ("Right.", 1)):
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-GB">'
            f'<voice name="en-GB-RyanNeural">{word}</voice></speak>'
        )
        mono = read_mono(synth.synthesize(ssml).path)
        stereo = np.zeros((len(mono) + SAMPLE_RATE // 2, 2), np.float32)
        stereo[: len(mono), side] = mono
        parts.append(stereo)
    wav = out / "headphones.wav"
    write_stereo(wav, np.concatenate(parts) * 0.9)
    encode_aac(wav, out / "headphones.m4a")
    wav.unlink()
    return "headphones.m4a"


def build(out: Path, cache: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    check = headphone_check(out, cache)
    clips = []
    for i, (seed, fx) in enumerate(matched_clips(range(1, 41))):
        name = "abc"[i]
        fx_path = out / f"fixture-{name}.json"
        fx_path.write_text(dumps(fx))
        manifest = render_fixture(
            fx_path,
            out / f"clip-{name}",
            language=Language.EN,
            mode=ListeningMode.CASUAL,
            arms=[Arm.SPEECH, Arm.PAN, Arm.FULL],
            cache=cache,
            analyst=False,
            replay=False,
        )
        clips.append(
            {
                "id": name,
                "seed": seed,
                "dir": f"clip-{name}",
                "duration_ms": manifest["duration_ms"],
                "home": manifest["home"]["name"],
                "away": manifest["away"]["name"],
                "probes": probes(fx),
            }
        )
    test = {
        "protocol": "earshot-listening-v1",
        "arms": {
            "speech": "Speech only, centred",
            "pan": "Speech panned to the ball",
            "full": "Pan plus ball tone and crowd",
        },
        "orders": [list(o) for o in ORDERS],
        "headphones": check,
        "clips": clips,
    }
    (out / "test.json").write_text(json.dumps(test, indent=2))
    return test


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/clips/listen-test"))
    ap.add_argument("--cache", type=Path, default=Path("data/tts-cache"))
    a = ap.parse_args()
    t = build(a.out, a.cache)
    print(
        json.dumps(
            [
                {k: c[k] for k in ("id", "seed", "home", "away")} | {"probes": len(c["probes"])}
                for c in t["clips"]
            ]
        )
    )
