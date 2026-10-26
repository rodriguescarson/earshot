"""Cache-miss cost per line: the file path (WAV from the service, pan, ffmpeg AAC) against the
streamed path (MP3 straight from the service). Same 20 English lines, fresh caches, one at a time.

    uv run python scripts/bench_stream.py        # needs EARSHOT_AI_ENDPOINT and an Azure login

Reported in EVALUATION.md ("Changes after eval-freeze"). The service call dominates both paths;
the encode and the second file are what the streamed path removes.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.live.audio import AudioStore
from earshot.voice.tts import Synthesizer

MANIFEST = Path("data/clips/demo-en-casual/manifest.json")
N = 20


def texts() -> list[str]:
    with MANIFEST.open() as f:
        manifest = json.load(f)
    return [row["text"] for row in manifest["lines"] if not row.get("replay")][:N]


def line(i: int, text: str, pan: float) -> Line:
    # The text rides in the club slot of a one-slot template, so the SSML carries it verbatim.
    return Line(
        line_id=f"b{i}", match_id="m", moment_id=None, source=LineSource.NOTICE, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id="kickoff.1", slots={"club": text.rstrip(".")},
        text=text, priority=50, wall_ms=0, deadline_wall_ms=10_000, pan=pan,
    )  # fmt: skip


def run(stream: bool) -> dict[str, int]:
    os.environ["EARSHOT_AUDIO_STREAM"] = "1" if stream else "0"
    d = Path(tempfile.mkdtemp(prefix="bench-"))
    store = AudioStore(d / "cache", d / "out", Synthesizer(d / "cache"))
    times: list[float] = []
    sizes: list[int] = []
    try:
        for i, t in enumerate(texts()):
            ln = line(i, t, -1 + 2 * (i % 5) / 4)
            t0 = time.perf_counter()
            served = store.serve(ln, OutputMode.APP_VOICE)
            times.append((time.perf_counter() - t0) * 1000)
            p = store.path(served.name) if served else None
            sizes.append(p.stat().st_size if p else 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    s = sorted(times)
    return {
        "n": len(s),
        "p50_ms": round(s[len(s) // 2]),
        "p95_ms": round(s[int(0.95 * (len(s) - 1))]),
        "mean_ms": round(statistics.mean(s)),
        "bytes_mean": round(statistics.mean(sizes)),
    }


if __name__ == "__main__":
    print(json.dumps({"file_path_wav_pan_aac": run(False), "streamed_mp3": run(True)}, indent=1))
