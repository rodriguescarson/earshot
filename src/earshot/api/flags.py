"""Runtime flags and the daily spend cap (eng ET10, SEC1).

**Cached-only mode without a restart (ET10).** Live Analyst and TTS generation stop; pre-rendered
clips keep playing from the static site. The flag lives in a small JSON blob (``admin/flags.json``)
that the worker polls every 30 s. Flipping it never touches the Container App's configuration, so
it never creates a revision and never restarts the worker: listeners stay connected.

Who flips it:
- the worker itself, when the daily spend cap below is reached;
- the kill-switch runbook, after a usage or budget alert;
- Carson, by hand (``earshot flags --cached-only on``); live mode is restored by hand too.

**Daily spend cap (SEC1).** The worker counts model tokens and speech characters per UTC day and
switches to cached-only when either passes its cap. The defaults sit below the usage alerts that
fire the kill switch (500K characters and 3M tokens a day): 400K characters and 2.5M tokens.
Counters are written back to the flag blob at most once a minute, so a restart does not reset them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from earshot.api.ratelimit import RELOAD_S, JudgeCodes

log = logging.getLogger("earshot.flags")

POLL_S = 30.0
CHAR_CAP = 400_000
TOKEN_CAP = 2_500_000
PERSIST_S = 60.0
CACHED_ONLY_NOTICE = "Live commentary is paused. Recorded clips still work."


class FlagSource(Protocol):
    def read(self) -> dict[str, Any]: ...
    def write(self, flags: dict[str, Any]) -> None: ...


class FileFlags:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def write(self, flags: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(flags, indent=1, sort_keys=True))
        tmp.replace(self.path)


class BlobFlags:
    """The flag blob, read and written with the worker's managed identity (no keys)."""

    def __init__(self, account_url: str, container: str = "admin", blob: str = "flags.json") -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        self.client = BlobClient(account_url, container, blob, credential=DefaultAzureCredential())

    def read(self) -> dict[str, Any]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return json.loads(self.client.download_blob().readall())
        except ResourceNotFoundError:
            return {}

    def write(self, flags: dict[str, Any]) -> None:
        self.client.upload_blob(json.dumps(flags, indent=1, sort_keys=True), overwrite=True)


def utc_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, UTC).strftime("%Y-%m-%d")


@dataclass
class Flags:
    source: FlagSource
    clock: Callable[[], float] = time.time
    values: dict[str, Any] = field(default_factory=dict)
    loaded_at: float = 0.0

    def refresh(self) -> None:
        try:
            self.values = self.source.read()
            self.loaded_at = self.clock()
        except Exception:  # a failed read keeps the last known flags; logged, retried next poll
            log.exception("flag read failed")

    @property
    def cached_only(self) -> bool:
        return bool(self.values.get("cached_only"))

    @property
    def reason(self) -> str | None:
        return self.values.get("reason")

    def set_cached_only(self, on: bool, reason: str) -> None:
        """Flip the mode for every replica (they pick it up within one poll) and for this one now."""
        fresh = {**self.source.read(), "cached_only": on, "reason": reason, "set_at": utc_day(self.clock())}
        self.source.write(fresh)
        self.values = fresh
        log.warning("cached_only=%s (%s)", on, reason)

    async def poll(self, interval_s: float = POLL_S) -> None:
        while True:
            await asyncio.to_thread(self.refresh)
            await asyncio.sleep(interval_s)


@dataclass
class SpendMeter:
    """Model tokens and speech characters per UTC day, with caps that trip cached-only mode."""

    flags: Flags
    char_cap: int = CHAR_CAP
    token_cap: int = TOKEN_CAP
    clock: Callable[[], float] = time.time
    day: str = ""
    chars: int = 0
    tokens: int = 0
    _persisted_at: float = 0.0

    def __post_init__(self) -> None:
        saved = self.flags.values.get("spend") or {}
        self.day = utc_day(self.clock())
        if saved.get("day") == self.day:
            self.chars, self.tokens = int(saved.get("chars", 0)), int(saved.get("tokens", 0))

    def _roll(self) -> None:
        today = utc_day(self.clock())
        if today != self.day:
            self.day, self.chars, self.tokens = today, 0, 0

    def allow(self, tokens: int = 0, chars: int = 0) -> bool:
        """May a call of this size go ahead? False in cached-only mode or if it would pass a cap."""
        self._roll()
        if self.flags.cached_only:
            return False
        return self.tokens + tokens <= self.token_cap and self.chars + chars <= self.char_cap

    def add(self, tokens: int = 0, chars: int = 0) -> None:
        self._roll()
        self.tokens += tokens
        self.chars += chars
        over = []
        if self.tokens >= self.token_cap:
            over.append(f"tokens {self.tokens} >= {self.token_cap}")
        if self.chars >= self.char_cap:
            over.append(f"speech characters {self.chars} >= {self.char_cap}")
        if over and not self.flags.cached_only:
            self.flags.set_cached_only(True, "daily spend cap: " + ", ".join(over))
        if self.clock() - self._persisted_at >= PERSIST_S:
            self.persist()

    def persist(self) -> None:
        with contextlib.suppress(Exception):  # counting must never break a live line; retried next minute
            fresh = {
                **self.flags.source.read(),
                "spend": {"day": self.day, "chars": self.chars, "tokens": self.tokens},
            }
            self.flags.source.write(fresh)
            self.flags.values = {**self.flags.values, "spend": fresh["spend"]}
        self._persisted_at = self.clock()

    def snapshot(self) -> dict[str, Any]:
        self._roll()
        return {
            "day": self.day,
            "chars": self.chars,
            "char_cap": self.char_cap,
            "tokens": self.tokens,
            "token_cap": self.token_cap,
        }


class JudgeCodesFromFlags(JudgeCodes):
    """Judge codes live as SHA-256 hashes in the flag store (``judge_code_sha256``), so rotating one
    is a blob edit: no redeploy, no restart."""

    def __init__(self, flags: Flags, refresh: bool = False) -> None:
        """``refresh``: re-read the flag store when it is older than 30 s (for apps without a poller)."""
        self.flags, self.refresh = flags, refresh
        super().__init__(None)

    def valid(self, code: str | None) -> bool:
        if self.refresh and self.clock() - self.flags.loaded_at > RELOAD_S:
            self.flags.refresh()
        self._hashes = {h.lower() for h in self.flags.values.get("judge_code_sha256", [])}
        self._loaded_at = self.clock()
        return super().valid(code)
