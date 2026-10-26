"""The streamed audio path (EARSHOT_AUDIO_STREAM=1): the service's mono MP3 is served as it is,
the pan rides on the message for the browser, and the audio route resolves the cache name."""

from pathlib import Path

from earshot.contract import Language, Line, LineSource, ListeningMode, OutputMode
from earshot.contract.wire import LineMsg
from earshot.live.audio import AudioStore, streaming
from earshot.voice.tts import MP3_BITRATE, Clip, mp3_duration_ms


class FakeMp3Synth:
    def __init__(self, d: Path):
        self.cache_dir = d
        self.path = d / ("v3-" + "a" * 32 + ".mp3")
        self.path.write_bytes(b"\0" * (MP3_BITRATE // 8))  # exactly one second at the constant bitrate
        self.wav_calls = 0

    def synthesize_mp3(self, ssml):
        return Clip(self.path, mp3_duration_ms(self.path))

    def cached_mp3(self, ssml):
        return Clip(self.path, mp3_duration_ms(self.path))

    def synthesize(self, ssml):
        self.wav_calls += 1
        raise AssertionError("the streamed path must not synthesize WAV")

    cached = synthesize


def line(pan: float) -> Line:
    return Line(
        line_id="L1", match_id="m1", moment_id=None, source=LineSource.CALLER, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id="var_stands.1", slots={},
        text="The decision stands.", priority=80, wall_ms=0, deadline_wall_ms=2500, pan=pan,
    )  # fmt: skip


def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("EARSHOT_AUDIO_STREAM", raising=False)
    assert streaming() is False


def test_streamed_serve_returns_the_mp3_with_the_pan_on_the_message(tmp_path, monkeypatch):
    monkeypatch.setenv("EARSHOT_AUDIO_STREAM", "1")
    synth = FakeMp3Synth(tmp_path)
    store = AudioStore(tmp_path, tmp_path / "out", synth)  # type: ignore[arg-type]
    served = store.serve(line(0.9), OutputMode.APP_VOICE)
    assert served is not None
    assert served.codec == "mp3"
    assert served.name == synth.path.name
    assert served.duration_ms == 1000
    assert 0.5 < served.pan <= 1.0  # bucketed toward the right
    assert store.path(served.name) == synth.path
    one_ear = store.serve(line(0.9), OutputMode.ONE_EAR)
    assert one_ear is not None
    assert one_ear.pan == 0.0
    assert store.serve(line(0.0), OutputMode.SCREEN_READER) is None
    assert synth.wav_calls == 0
    assert store.cached_ms(line(0.0)) == 1000


def test_audio_route_name_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("EARSHOT_AUDIO_STREAM", "1")
    store = AudioStore(tmp_path, tmp_path / "out", FakeMp3Synth(tmp_path))  # type: ignore[arg-type]
    assert store.path("v3-" + "a" * 32 + ".mp3") is not None
    assert store.path("v3-" + "b" * 32 + ".mp3") is None  # not in the cache
    assert store.path("../v3-" + "a" * 28 + ".mp3") is None
    assert store.path("v3-" + "a" * 32 + ".wav") is None


def test_line_message_defaults_keep_old_clients_working():
    msg = LineMsg(
        id="x", priority=50, preempt=False, never_drop=False, audio_url=None, text="t", ssml_hash="",
        deadline_ms=0, voice_role="facts",
    )  # fmt: skip
    assert msg.codec == "m4a"
    assert msg.pan == 0.0
