"""AudioStore.serve with the real encoder: centred lines are mono, panned lines stereo, and two
concurrent writers of one file never corrupt it."""

import shutil
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.live.audio import AudioStore
from earshot.voice.tts import SAMPLE_RATE, Clip

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


class FakeSynth:
    def __init__(self, d: Path):
        self.wav = d / "v2-test.wav"
        t = np.arange(int(1.0 * SAMPLE_RATE)) / SAMPLE_RATE
        pcm = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())

    def synthesize(self, ssml):
        return Clip(self.wav, 1000)

    def cached(self, ssml):
        return Clip(self.wav, 1000)


def line(pan: float) -> Line:
    return Line(
        line_id="L1", match_id="m1", moment_id=None, source=LineSource.CALLER, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id="var_stands.1", slots={},
        text="The decision stands.", priority=80, wall_ms=0, deadline_wall_ms=2500, pan=pan,
    )  # fmt: skip


def channels(path: Path) -> int:
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout  # fmt: skip
    return int(out.strip())


def test_centred_lines_are_mono_and_panned_lines_stereo(tmp_path):
    store = AudioStore(tmp_path / "cache", tmp_path / "out", synth=FakeSynth(tmp_path))  # type: ignore[arg-type]
    one_ear = store.serve(line(0.6), OutputMode.ONE_EAR)
    panned = store.serve(line(0.6), OutputMode.APP_VOICE)
    assert one_ear is not None
    assert panned is not None
    assert channels(tmp_path / "out" / one_ear.name) == 1
    assert channels(tmp_path / "out" / panned.name) == 2
    assert not list((tmp_path / "out").glob("*.wav"))  # no scratch files left behind


def test_concurrent_writers_of_one_file_leave_one_good_file(tmp_path):
    store = AudioStore(tmp_path / "cache", tmp_path / "out", synth=FakeSynth(tmp_path))  # type: ignore[arg-type]
    with ThreadPoolExecutor(6) as pool:
        names = list(pool.map(lambda _: store.serve(line(0.0), OutputMode.ONE_EAR).name, range(6)))  # type: ignore[union-attr]
    assert len(set(names)) == 1
    assert channels(tmp_path / "out" / names[0]) == 1
    assert sorted(p.suffix for p in (tmp_path / "out").iterdir()) == [".m4a"]


def test_an_ffmpeg_failure_reports_ffmpegs_error_not_a_missing_encoder(tmp_path, monkeypatch):
    # The worker's logs said "no working AAC encoder: install ffmpeg" on a machine with ffmpeg,
    # which hid the real failure (load test, 2026-10-17).
    from earshot.voice import encode

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    which = shutil.which
    monkeypatch.setattr(encode.shutil, "which", lambda name: which(name) if name == "ffmpeg" else None)
    with pytest.raises(RuntimeError, match=r"^ffmpeg failed on gone\.wav: .+"):
        encode.encode_aac(tmp_path / "gone.wav", tmp_path / "out.m4a")
