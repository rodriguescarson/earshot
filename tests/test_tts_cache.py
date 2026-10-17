"""The speech cache under concurrency: two writers of one SSML must both succeed."""

import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

from earshot.voice.tts import SAMPLE_RATE, Synthesizer


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.sin(np.arange(4800) / 10) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


class FakeHttp:
    def post(self, url, **_):
        return httpx.Response(200, content=_wav(), request=httpx.Request("POST", url))


def test_two_syntheses_of_the_same_ssml_at_once_both_succeed(tmp_path, monkeypatch):
    # Found in the worker's logs (load test, 2026-10-17): both writers used one ".part" file, so the
    # second rename found it gone and its line aired as text. Both writes now land before either
    # rename, the interleaving that failed.
    s = Synthesizer(tmp_path, endpoint="http://speech.invalid")
    s._http = FakeHttp()  # type: ignore[assignment]
    s._bearer = lambda: "token"  # type: ignore[method-assign]
    both_written = threading.Barrier(2, timeout=5)
    write = Path.write_bytes

    def write_then_wait(self, data):
        n = write(self, data)
        both_written.wait()
        return n

    monkeypatch.setattr(Path, "write_bytes", write_then_wait)
    ssml = "<speak>Throw-in, Sparkwheel.</speak>"
    with ThreadPoolExecutor(2) as pool:
        clips = list(pool.map(lambda _: s.synthesize(ssml), range(2)))
    assert clips[0].path == clips[1].path
    assert clips[0].path.exists()
    assert clips[0].duration_ms == 200
    assert [p.name for p in tmp_path.iterdir()] == [clips[0].path.name]  # no scratch file left
