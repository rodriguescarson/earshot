"""AAC encoding for every audio Earshot serves (M4A plays on iOS Safari, including on a locked screen)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def encode_aac(wav: Path, m4a: Path, bitrate: int = 128_000) -> None:
    """ffmpeg where it works, else macOS afconvert."""
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
