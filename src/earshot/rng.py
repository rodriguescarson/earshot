"""Separate, reproducible random streams per match and per purpose.

Every stream is derived from the season seed plus stable string keys, never from Python's
salted ``hash()``, so the same inputs give the same numbers on every machine and process.
"""

from __future__ import annotations

import hashlib

import numpy as np


def _key(*parts: str | int) -> list[int]:
    digest = hashlib.blake2b("\x1f".join(map(str, parts)).encode(), digest_size=16).digest()
    return [int.from_bytes(digest[i : i + 4], "little") for i in range(0, 16, 4)]


def stream(seed: int, *keys: str | int) -> np.random.Generator:
    """A generator for ``seed`` and an ordered key path, e.g. ``stream(2026, "match", 17, "fouls")``."""
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, *_key(*keys)])))
