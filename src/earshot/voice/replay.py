"""Sonified goal replay (CEO-6): the last seconds before a goal, replayed as sound.

The ball tone retraces the build-up across the stereo field, its pitch rising as the move goes
on, and the crowd swells into the roar at the moment the ball goes in. App voice and one-ear
modes only; never in screen-reader mode. Synthesized procedurally: no samples.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from earshot.voice.mix import BallPoint
from earshot.voice.tts import SAMPLE_RATE

REPLAY_MS = 7_000


def replay_audio(ball: Sequence[BallPoint], goal_wall_ms: int, seed: int = 11) -> np.ndarray:
    """Stereo float32 audio for the replay: REPLAY_MS of build-up plus a two-second roar."""
    lead = [b for b in ball if goal_wall_ms - REPLAY_MS <= b.wall_ms <= goal_wall_ms]
    n_build = int(REPLAY_MS / 1000 * SAMPLE_RATE)
    n = n_build + 2 * SAMPLE_RATE
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    if lead:
        bt = np.array([b.wall_ms for b in lead], np.float64)
        bx = np.array([b.x for b in lead], np.float64)
        when = goal_wall_ms - REPLAY_MS + t[:n_build] * 1000
        x = np.interp(when, bt, bx)
    else:
        x = np.full(n_build, 52.5)
    x = np.concatenate([x, np.full(n - n_build, x[-1])])
    pan = np.clip(x / 52.5 - 1, -1, 1)
    progress = np.clip(t / (REPLAY_MS / 1000), 0, 1)
    hz = 196 * (1 + 0.5 * progress)  # rises from G3 towards D4 as the move builds
    phase = 2 * np.pi * np.cumsum(hz) / SAMPLE_RATE
    tone = (np.sin(phase) + 0.22 * np.sin(2 * phase)) * np.where(t < REPLAY_MS / 1000, 0.35, 0.0)
    rng = np.random.default_rng(seed)
    crowd = np.cumsum(rng.standard_normal(n).astype(np.float32))
    crowd -= np.convolve(crowd, np.ones(1200) / 1200, mode="same")
    crowd /= np.abs(crowd).max() + 1e-9
    after = np.clip(t - REPLAY_MS / 1000, 0, None)
    swell = 0.08 + 0.25 * progress**2 + np.where(after > 0, 0.6 * np.exp(-after / 1.2), 0)
    fade = np.minimum(1, (n - np.arange(n)) / (0.3 * SAMPLE_RATE))
    theta = (pan + 1) * np.pi / 4
    left = (tone * np.cos(theta) + crowd * swell) * fade
    right = (tone * np.sin(theta) + np.roll(crowd, 37) * swell) * fade
    out = np.stack([left, right], axis=1).astype(np.float32)
    return out * (0.7 / (np.abs(out).max() + 1e-9))
