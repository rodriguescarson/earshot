"""Offline mixer: the listening-test arms, the judge clips and the described-audio track.

Speech clips are placed at their aired times with constant-power pan. The continuous layer is
synthesized procedurally (no samples): a soft ball tone panned along the pitch and a crowd bed
that rises with danger and roars on a goal. Everything ducks under speech.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from earshot.agents.director import Aired
from earshot.voice.tts import SAMPLE_RATE, Clip, read_mono

CONTROL_HZ = 100


class Arm(StrEnum):
    SPEECH = "speech"
    """Speech only, centred: the listening test's control arm."""
    PAN = "pan"
    """Speech panned to where the ball is."""
    FULL = "full"
    """Speech with pan plus the continuous ball tone and crowd layer."""
    BED = "bed"
    """The continuous layer alone: screen-reader mode, where the reader speaks the lines."""
    MONO = "mono"
    """One-ear mode: everything centred; the ball tone's pitch says which third of the pitch it is in."""


@dataclass(frozen=True)
class BallPoint:
    wall_ms: int
    x: float
    y: float
    in_play: bool
    danger: float


def _db(db: float) -> float:
    return float(10 ** (db / 20))


def _pan_gains(p: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    theta = (np.asarray(p) + 1.0) * np.pi / 4
    return np.cos(theta), np.sin(theta)


def _smooth(x: np.ndarray, attack_s: float, release_s: float) -> np.ndarray:
    """One-pole envelope follower at CONTROL_HZ (short arrays, so a plain loop is fine)."""
    a_up = 1 - np.exp(-1 / (attack_s * CONTROL_HZ))
    a_dn = 1 - np.exp(-1 / (release_s * CONTROL_HZ))
    out = np.empty_like(x)
    y = float(x[0]) if len(x) else 0.0
    for i, v in enumerate(x):
        y += (a_up if v > y else a_dn) * (v - y)
        out[i] = y
    return out


def _upsample(control: np.ndarray, n: int) -> np.ndarray:
    src = np.linspace(0, n - 1, num=len(control))
    return np.interp(np.arange(n), src, control).astype(np.float32)


def _brown(rng: np.random.Generator, n: int) -> np.ndarray:
    b = np.cumsum(rng.standard_normal(n).astype(np.float32))
    k = SAMPLE_RATE // 20
    c = np.cumsum(np.concatenate([np.zeros(1, np.float32), b]))
    mov = (c[k:] - c[:-k]) / k
    b = b[: len(mov)] - mov
    b = np.concatenate([b, np.zeros(n - len(b), np.float32)])
    return b / (np.abs(b).max() + 1e-9)


def mix(
    aired: Sequence[Aired],
    clips: dict[str, Clip],
    *,
    start_ms: int,
    end_ms: int,
    arm: Arm,
    ball: Sequence[BallPoint] = (),
    goal_wall_ms: Sequence[int] = (),
    seed: int = 7,
) -> np.ndarray:
    n = int((end_ms - start_ms) / 1000 * SAMPLE_RATE) + SAMPLE_RATE * 2
    out = np.zeros((n, 2), np.float32)
    speech_ctl = np.zeros(n // (SAMPLE_RATE // CONTROL_HZ) + 1, np.float32)
    fade = int(0.03 * SAMPLE_RATE)
    for a in aired if arm is not Arm.BED else ():
        x = read_mono(clips[a.line.line_id].path)
        if a.cut_at_ms is not None:
            keep = max(0, int((a.cut_at_ms - a.start_ms) / 1000 * SAMPLE_RATE))
            x = x[:keep].copy()
            if len(x) > fade:
                x[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        i0 = int((a.start_ms - start_ms) / 1000 * SAMPLE_RATE)
        if i0 < 0 or i0 >= n:
            continue
        x = x[: n - i0]
        pan = 0.0 if arm in (Arm.SPEECH, Arm.MONO) else a.line.pan
        gl, gr = _pan_gains(pan)
        out[i0 : i0 + len(x), 0] += x * float(gl)
        out[i0 : i0 + len(x), 1] += x * float(gr)
        c0, c1 = i0 // (SAMPLE_RATE // CONTROL_HZ), (i0 + len(x)) // (SAMPLE_RATE // CONTROL_HZ)
        speech_ctl[c0 : c1 + 1] = 1.0

    if arm in (Arm.FULL, Arm.BED, Arm.MONO) and ball:
        rng = np.random.default_rng(seed)
        t_ctl = start_ms + np.arange(len(speech_ctl)) * (1000 / CONTROL_HZ)
        bt = np.array([b.wall_ms for b in ball], np.float64)
        bx = np.interp(t_ctl, bt, [b.x for b in ball])
        live = np.interp(t_ctl, bt, [1.0 if b.in_play else 0.0 for b in ball])
        danger = np.interp(t_ctl, bt, [b.danger for b in ball])
        live = _smooth((live > 0.5).astype(np.float32), 0.08, 0.35)
        danger = _smooth(danger.astype(np.float32), 0.4, 1.2)
        roar = np.zeros_like(danger)
        for g in goal_wall_ms:
            dt = (t_ctl - g - 150) / 1000
            roar = np.maximum(roar, np.where(dt >= 0, np.exp(-dt / 3.5), 0))
        duck = 1 - 0.65 * _smooth(speech_ctl, 0.06, 0.3)
        # Ball tone: soft G3 with a slow roll, panned along the full width of the pitch.
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        if arm is Arm.MONO:
            # Pitch earcon instead of pan: low in the left third, G3 in the middle, high in the right third.
            hz_ctl = np.where(bx < 35, 165.0, np.where(bx < 70, 196.0, 247.0)).astype(np.float32)
            phase = 2 * np.pi * np.cumsum(_upsample(hz_ctl, n)) / SAMPLE_RATE
            tone = np.sin(phase) + 0.22 * np.sin(2 * phase)
            p = np.zeros(n, np.float32)
        else:
            tone = np.sin(2 * np.pi * 196 * t) + 0.22 * np.sin(2 * np.pi * 392 * t)
            p = _upsample(np.clip(bx / 52.5 - 1, -1, 1).astype(np.float32), n)
        tone *= 0.8 + 0.2 * np.sin(2 * np.pi * 2.6 * t)
        g_tone = _upsample((live * duck * _db(-25)).astype(np.float32), n)
        gl, gr = _pan_gains(p)
        out[:, 0] += tone * gl * g_tone
        out[:, 1] += tone * gr * g_tone
        # Crowd: decorrelated brown noise per ear, rising with danger, roaring on goals.
        level = _db(-31) * (1 + 3.0 * danger + 5.0 * roar) * (0.55 + 0.45 * duck)
        g_crowd = _upsample(level.astype(np.float32), n)
        left = _brown(rng, n)
        right = left if arm is Arm.MONO else _brown(rng, n)
        out[:, 0] += left * g_crowd
        out[:, 1] += right * g_crowd

    peak = float(np.abs(out).max())
    if peak > 0:
        out *= 0.89 / peak
    return out
