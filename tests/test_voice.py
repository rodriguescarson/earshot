"""Mixer and replay: which arms carry what (no speech service calls)."""

import numpy as np
import pytest

from earshot.agents.director import Aired
from earshot.contract import Language, Line, LineSource, ListeningMode
from earshot.voice.mix import REPLAY_TEMPLATE, Arm, BallPoint, mix
from earshot.voice.replay import REPLAY_MS, replay_audio
from earshot.voice.tts import SAMPLE_RATE, Clip, write_stereo


def _line(tid: str, line_id: str, pan: float = 0.0) -> Line:
    return Line(
        line_id=line_id, match_id="m", moment_id=None, source=LineSource.NOTICE, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id=tid, slots={}, text="x", priority=48,
        wall_ms=0, deadline_wall_ms=10_000, pan=pan,
    )  # fmt: skip


@pytest.fixture
def clips(tmp_path):
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    speech = tmp_path / "speech.wav"
    import wave

    with wave.open(str(speech), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((tone * 32767).astype("<i2").tobytes())
    replay = tmp_path / "replay.wav"
    write_stereo(replay, np.stack([tone, tone * 0.2], axis=1))
    return {"speech": Clip(speech, 1000), "replay": Clip(replay, 1000)}


def _energy(audio: np.ndarray, start_s: float, end_s: float) -> float:
    a, b = int(start_s * SAMPLE_RATE), int(end_s * SAMPLE_RATE)
    return float(np.abs(audio[a:b]).mean())


@pytest.mark.parametrize(
    ("arm", "has_replay"),
    [(Arm.FULL, True), (Arm.MONO, True), (Arm.PAN, False), (Arm.SPEECH, False), (Arm.BED, False)],
)
def test_replay_only_in_full_and_one_ear(clips, arm, has_replay):
    aired = [
        Aired(_line("foul.1", "speech"), 0, 1000),
        Aired(_line(REPLAY_TEMPLATE, "replay"), 2000, 3000),
    ]
    audio = mix(aired, clips, start_ms=0, end_ms=4000, arm=arm)
    assert (_energy(audio, 2.1, 2.9) > 0.01) is has_replay


def test_one_ear_mix_is_identical_in_both_ears(clips):
    aired = [
        Aired(_line("foul.1", "speech", pan=0.6), 0, 1000),
        Aired(_line(REPLAY_TEMPLATE, "replay"), 1000, 2000),
    ]
    ball = [BallPoint(i * 100, 10.0 + i, 34.0, True, 0.5) for i in range(40)]
    audio = mix(aired, clips, start_ms=0, end_ms=4000, arm=Arm.MONO, ball=ball)
    assert np.allclose(audio[:, 0], audio[:, 1], atol=1e-4)


def test_speech_is_panned_only_in_the_pan_arms(clips):
    aired = [Aired(_line("foul.1", "speech", pan=0.6), 0, 1000)]
    left, right = (mix(aired, clips, start_ms=0, end_ms=2000, arm=a) for a in (Arm.PAN, Arm.SPEECH))
    assert _energy(left[:, 1:], 0, 1) > _energy(left[:, :1], 0, 1)
    assert _energy(right[:, :1], 0, 1) == pytest.approx(_energy(right[:, 1:], 0, 1), rel=1e-3)


def test_replay_moves_with_the_ball_and_ends_in_a_roar():
    ball = [BallPoint(i * 100, 5.0 + i * 1.4, 34.0, True, 0.2) for i in range(71)]
    audio = replay_audio(ball, goal_wall_ms=7_000)
    assert len(audio) == int(REPLAY_MS / 1000 * SAMPLE_RATE) + 2 * SAMPLE_RATE
    start_bias = _energy(audio[:, 1:], 0, 1) / _energy(audio[:, :1], 0, 1)
    end_bias = _energy(audio[:, 1:], 6, 7) / _energy(audio[:, :1], 6, 7)
    assert end_bias > start_bias  # the ball runs left to right, so the right ear grows
