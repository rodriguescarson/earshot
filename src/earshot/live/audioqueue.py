"""One priority queue for the live broadcaster's audio work: speech synthesis and M4A encoding.

Before this, every line's speech and encode went to the shared default thread pool in arrival
order, and a line was encoded only once the Director had started it. On a small worker the encode
was the longest part of delivery (load test, 30 listeners, 0.5 vCPU: start to sent p95 2.3 s), and
a goal's encode could wait behind lower lines'. Here:

- jobs run in rank order: never-drop lines first, then lines that may preempt, then by priority,
  then first come;
- at most ``AUDIO_WORKERS`` jobs run at once, on threads of their own;
- identical jobs (same key) share one result, so two groups never encode the same file at once
  (they used to share one temporary WAV path);
- a queued job can be skipped if its line has gone stale by the time a slot is free, so a small
  worker does not spend CPU on lines that will never air.

The broadcaster starts a line's encode as soon as the line is offered (it waits in the Director's
queue meanwhile), and publishing then awaits the same job: usually already done.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from earshot.contract import Line

AUDIO_WORKERS = 2
"""One job can wait on the speech service while the other encodes."""

Rank = tuple[int, int, int]


def rank(line: Line) -> Rank:
    return (0 if line.never_drop else 1, 0 if line.preempt else 1, -line.priority)


class Skipped(Exception):
    """The job's line was no longer wanted when its turn came."""


class AudioQueue:
    def __init__(self, workers: int = AUDIO_WORKERS) -> None:
        self.workers = workers
        self._free = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="audio")
        self._waiting: list[tuple[Rank, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self.ran = 0
        self.skipped = 0

    async def _acquire(self, r: Rank) -> None:
        if self._free > 0 and not self._waiting:
            self._free -= 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiting, (r, next(self._seq), waiter))
        await waiter  # the slot is handed over directly by _release

    def _release(self) -> None:
        while self._waiting:
            _, _, waiter = heapq.heappop(self._waiting)
            if not waiter.done():  # a cancelled waiter gave up its place
                waiter.set_result(None)
                return
        self._free += 1

    async def run(
        self,
        key: str,
        r: Rank,
        fn: Callable[..., Any],
        *args: Any,
        wanted: Callable[[], bool] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run ``fn(*args, **kwargs)`` in rank order; a job with the same key in flight is shared.
        Raises ``Skipped`` when ``wanted()`` is false at the job's turn."""
        loop = asyncio.get_running_loop()
        shared = self._inflight.get(key)
        if shared is not None:
            return await asyncio.shield(shared)
        result: asyncio.Future[Any] = loop.create_future()
        result.add_done_callback(lambda f: f.cancelled() or f.exception())  # never "never retrieved"
        self._inflight[key] = result
        try:
            await self._acquire(r)
            try:
                if wanted is not None and not wanted():
                    self.skipped += 1
                    raise Skipped(key)
                value = await loop.run_in_executor(self._pool, lambda: fn(*args, **kwargs))
                self.ran += 1
            finally:
                self._release()
            result.set_result(value)
            return value
        except BaseException as e:
            if not result.done():
                if isinstance(e, asyncio.CancelledError):
                    result.cancel()
                else:
                    result.set_exception(e)
            raise
        finally:
            self._inflight.pop(key, None)
