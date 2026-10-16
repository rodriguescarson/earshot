"""Per-key rate limits for the public endpoints, and the judge access code (design R3-12, DR-6).

Limits are sliding one-minute windows kept in memory per replica (enough at judging scale; a
restart forgets them, which only ever loosens a limit). Judge codes are stored as SHA-256 hashes,
never in plain text, and are re-read from their file on every check that is more than 30 s after
the last read, so rotating a code needs no redeploy: replace the file (deployed: a Blob the app's
identity reads) and old codes stop working within 30 s.

IP addresses are used only as limiter keys, kept in memory for at most one minute, and never
logged or stored (SEC6).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

RELOAD_S = 30.0


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


class JudgeCodes:
    def __init__(
        self,
        path: Path | None = None,
        hashes: set[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path, self.clock = path, clock
        self._hashes = set(hashes or ())
        self._loaded_at = -1e9
        self._load()

    def _load(self) -> None:
        if self.path is not None and self.path.exists():
            lines = self.path.read_text().split()
            self._hashes = {x.strip().lower() for x in lines if len(x.strip()) == 64}
        self._loaded_at = self.clock()

    def valid(self, code: str | None) -> bool:
        if not code:
            return False
        if self.clock() - self._loaded_at > RELOAD_S:
            self._load()
        h = code_hash(code)
        return any(hmac.compare_digest(h, k) for k in self._hashes)


class SlidingLimit:
    """At most ``per_minute`` hits per key in any 60 s."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.clock = per_minute, clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= 60.0:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        # Forget idle keys so IPs never outlive their minute.
        if len(self._hits) > 10_000:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= 60.0]:
                del self._hits[k]
        return True

    def retry_after_s(self, key: str) -> int:
        q = self._hits.get(key)
        if not q:
            return 0
        return max(1, int(60.0 - (self.clock() - q[0])) + 1)


def client_ip(headers: dict[str, str], peer: str | None) -> str:
    """The caller's address as the platform saw it: the last X-Forwarded-For entry, else the peer.

    The Container Apps ingress appends the address it accepted the connection from. Every entry
    before it came from the caller and can be anything, so the first entry is never trusted (a
    made-up header there used to buy a fresh rate limit per request).
    """
    fwd = headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() or (peer or "unknown")
