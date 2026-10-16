"""Audio for live lines: speech from the per-SSML cache, panned per output mode, served as M4A."""

from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from earshot.contract import Line, OutputMode
from earshot.pitch import PAN_BUCKETS
from earshot.voice.encode import encode_aac
from earshot.voice.tts import Synthesizer, read_mono, ssml_for, write_stereo

EXCITED_PRIORITY = 95
"""Goals, red cards and penalties are voiced with more energy."""


def bucket(pan: float) -> float:
    return min(PAN_BUCKETS, key=lambda b: abs(b - pan))


@dataclass(frozen=True)
class Served:
    name: str
    duration_ms: int


class AudioStore:
    def __init__(self, cache_dir: Path, out_dir: Path, synth: Synthesizer | None = None) -> None:
        self.synth = synth or Synthesizer(cache_dir)
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

    def ssml(self, line: Line) -> str:
        return ssml_for(line, excited=line.priority >= EXCITED_PRIORITY)

    def cached_ms(self, line: Line) -> int | None:
        """The duration if this line's speech is already cached (costs nothing to air)."""
        clip = self.synth.cached(self.ssml(line))
        return clip.duration_ms if clip else None

    def warm(self, line: Line) -> int:
        """Synthesize (or hit the cache) and return the duration: used before a match goes live."""
        return self.synth.synthesize(self.ssml(line)).duration_ms

    def serve(self, line: Line, output: OutputMode, *, synthesize: bool = True) -> Served | None:
        """The M4A for this line in this output mode (screen-reader mode has no app audio).

        With ``synthesize=False`` (cached-only mode) a line whose speech is not cached gets no audio.
        """
        if output is OutputMode.SCREEN_READER:
            return None
        clip = self.synth.synthesize(self.ssml(line)) if synthesize else self.synth.cached(self.ssml(line))
        if clip is None:
            return None
        pan = 0.0 if output is OutputMode.ONE_EAR else bucket(line.pan)
        name = hashlib.sha256(f"{clip.path.name}|{pan}".encode()).hexdigest()[:24] + ".m4a"
        path = self.out_dir / name
        if not path.exists():
            mono = read_mono(clip.path)
            theta = (pan + 1) * np.pi / 4
            stereo = np.stack([mono * np.cos(theta), mono * np.sin(theta)], axis=1)
            wav = path.with_suffix(".wav")
            write_stereo(wav, stereo)
            encode_aac(wav, path)
            wav.unlink()
        return Served(name, clip.duration_ms)

    def path(self, name: str) -> Path | None:
        if len(name) != 28 or not name.endswith(".m4a") or not name[:24].isalnum():
            return None
        p = self.out_dir / name
        return p if p.exists() else None


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()
