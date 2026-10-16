"""Latency samples from the deployed broadcaster, for the load test (``earshot loadtest``).

The live broadcaster's own classes are used as they are; a subclass of ``LiveMatch`` only times
what it already does:

- ``ready_ms``: one offer, from the moment a line reaches its group to the moment the Director has
  it (the speech cache, or a synthesis call on a miss);
- ``analyst_ms``: one Analyst call, wall time;
- per published line: ``start_ms`` (the event's match time to the Director starting the line; the
  live clock runs at 1x, so match milliseconds are real milliseconds), ``publish_ms`` (serving its
  audio and sending the message) and ``sent_ms`` (epoch time it was sent). The load-test client
  joins these with its own receive time and audio first-byte time per line.

``GET /api/metrics`` returns them to a caller with a judge code (header ``X-Earshot-Code``). Samples
are bounded (``SAMPLES`` each) and hold no listener data: group names and line ids only.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from earshot.api.ratelimit import JudgeCodes, SlidingLimit, client_ip

SAMPLES = 20_000


def _q() -> deque[Any]:
    return deque(maxlen=SAMPLES)


@dataclass
class LiveMetrics:
    ready_ms: deque[int] = field(default_factory=_q)
    analyst_ms: deque[int] = field(default_factory=_q)
    lines: deque[dict[str, Any]] = field(default_factory=_q)
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def snapshot(self, since_ms: int = 0) -> dict[str, Any]:
        return {
            "started_at_ms": self.started_at_ms,
            "now_ms": int(time.time() * 1000),
            "ready_ms": list(self.ready_ms),
            "analyst_ms": list(self.analyst_ms),
            "lines": [x for x in self.lines if x["sent_ms"] >= since_ms],
        }


def instrumented(base: type, metrics: LiveMetrics) -> type:
    """A ``LiveMatch`` subclass that records timings and changes nothing else."""

    class Timed(base):  # type: ignore[valid-type, misc]
        async def _offer(self, g: Any, line: Any) -> None:
            t0 = time.perf_counter()
            await super()._offer(g, line)
            metrics.ready_ms.append(int((time.perf_counter() - t0) * 1000))

        async def _publish_line(self, g: Any, start: Any) -> None:
            start_ms = self.match_ms() - start.line.wall_ms
            t0 = time.perf_counter()
            await super()._publish_line(g, start)
            metrics.lines.append(
                {
                    "group": g.key.name,
                    "id": start.line.line_id,
                    "source": start.line.source.value,
                    "never_drop": start.line.never_drop,
                    "start_ms": start_ms,
                    "publish_ms": int((time.perf_counter() - t0) * 1000),
                    "sent_ms": int(time.time() * 1000),
                }
            )

    Timed.__name__ = Timed.__qualname__ = f"Timed{base.__name__}"
    return Timed


def metrics_endpoint(codes: JudgeCodes, metrics: LiveMetrics) -> Callable[[Request], Awaitable[Response]]:
    from earshot.api.streams import TICKET_TRIES_PER_MINUTE

    tries = SlidingLimit(TICKET_TRIES_PER_MINUTE)

    async def endpoint(request: Request) -> Response:
        ip = client_ip(dict(request.headers), request.client.host if request.client else None)
        if not tries.allow(ip):
            return JSONResponse({"error": "too many tries"}, status_code=429)
        if not codes.valid(request.headers.get("x-earshot-code")):
            return JSONResponse({"error": "not a judge code"}, status_code=403)
        try:
            since = int(request.query_params.get("since_ms", "0"))
        except ValueError:
            return JSONResponse({"error": "since_ms must be an integer"}, status_code=400)
        return JSONResponse(metrics.snapshot(since))

    return endpoint
