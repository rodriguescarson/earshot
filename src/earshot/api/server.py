"""The deployed worker: Stream 1's live broadcaster plus Stream 2's guards, in one app.

Entrypoint: ``uvicorn earshot.api.server:app``.
- Guards (``earshot.api.worker``): ``/healthz``, ``/api/status`` (cached-only notice, spend),
  ``/api/negotiate`` (receive-only Web PubSub tokens, per-IP limits, judge code), the flag poller
  that flips cached-only mode without a restart, the security headers and CORS.
- Live (``earshot.live.app``): ``/api/live``, ``/api/stream`` (SSE), ``/api/ondemand``,
  ``/audio/*``, and the broadcaster loop that runs the shared pipeline in real time and publishes to
  Web PubSub.

The SSE stream gets the negotiate endpoint's limits (``earshot.api.streams``): the browser's
EventSource connects to it directly, so limits that only negotiate applied would bind nobody.
Judges trade their code for a signed ticket at ``POST /api/ticket``. ``/api/ondemand`` gets a
per-IP limit too, because each answer may synthesize speech.

``GET /api/metrics`` (judge code) serves the broadcaster's latency samples for the load test
(``earshot.api.livemetrics``).

Spend (SEC1) for both surfaces runs through one persisted ``SpendMeter``:
- ``Live.cached_only`` reads the kill-switch flag, so the live app's own gate stops new speech and
  Analyst calls the moment the flag flips (it also skips the Analyst while nobody listens);
- each Analyst call is reserved against the daily token cap first and charged its real count after;
- each synthesized line (cache misses only) is charged to the daily character cap, across matches
  and restarts, and never past the live app's own daily speech budget.
"""

from __future__ import annotations

import time
from typing import Any, cast

from starlette.routing import Route

from earshot.api.flags import JudgeCodesFromFlags, SpendMeter
from earshot.api.livemetrics import LiveMetrics, instrumented, metrics_endpoint
from earshot.api.streams import (
    ONDEMAND_PER_MINUTE,
    StreamGuard,
    Tickets,
    guard_stream,
    limit_per_ip,
    ticket_endpoint,
)
from earshot.api.worker import build_app as build_worker

ANALYST_CALL_TOKENS = 6_000
"""Tokens set aside before an Analyst call (a few turns with tool results); the real count is added after."""


class MeteredAnalyst:
    """Wraps the Foundry Analyst: no call once the daily token cap would be passed."""

    def __init__(self, inner: Any, meter: SpendMeter, metrics: LiveMetrics | None = None) -> None:
        self.inner, self.meter, self.metrics = inner, meter, metrics

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        from earshot.agents.analyst import AnalystResult

        if not self.meter.allow(tokens=ANALYST_CALL_TOKENS):
            return AnalystResult(window_id, error="daily token cap or cached-only mode")
        t0 = time.perf_counter()
        res = self.inner.run(view, window_id, expected_dead_ms)
        if self.metrics is not None:
            self.metrics.analyst_ms.append(int((time.perf_counter() - t0) * 1000))
        self.meter.add(tokens=int(getattr(res, "tokens", 0) or 0))
        return res


class MeteredSpeechBudget:
    """Stands in for the live broadcaster's per-match speech budget: one daily count for the process."""

    def __init__(self, meter: SpendMeter, limit: int) -> None:
        self.meter, self.limit = meter, limit

    def allow(self, chars: int) -> bool:
        if self.meter.chars + chars > self.limit or not self.meter.allow(chars=chars):
            return False
        self.meter.add(chars=chars)
        return True


def build_app():
    from earshot.live import app as live
    from earshot.live import broadcast

    guard, tickets = StreamGuard(), Tickets()

    def guarded(r):
        if isinstance(r, Route) and r.path == "/api/stream":
            return Route(r.path, guard_stream(r.endpoint, guard, tickets), methods=["GET"])
        if isinstance(r, Route) and r.path == "/api/ondemand":
            return Route(r.path, limit_per_ip(r.endpoint, ONDEMAND_PER_MINUTE), methods=["POST"])
        return r

    routes = [guarded(r) for r in live.ROUTES]
    assert any(isinstance(r, Route) and r.path == "/api/stream" for r in live.ROUTES), (
        "live stream route moved"
    )
    metrics = LiveMetrics()
    late: dict[str, Any] = {}  # endpoints that need the worker's flags, which build_worker makes

    async def ticket(request):
        return await late["ticket"](request)

    async def metrics_route(request):
        return await late["metrics"](request)

    routes += [Route("/api/ticket", ticket, methods=["POST"]), Route("/api/metrics", metrics_route)]
    worker = build_worker(extra_routes=routes, extra_lifespans=[live.lifespan])
    flags, meter = worker.state.flags, cast(SpendMeter, worker.state.meter)
    codes = JudgeCodesFromFlags(flags)
    late["ticket"] = ticket_endpoint(codes, tickets)
    late["metrics"] = metrics_endpoint(codes, metrics)
    worker.state.stream_guard, worker.state.metrics = guard, metrics
    live.LiveMatch = instrumented(broadcast.LiveMatch, metrics)  # type: ignore[attr-defined]
    limit = broadcast.DAILY_SPEECH_CHARS
    broadcast.SpeechBudget = lambda: MeteredSpeechBudget(meter, limit)  # type: ignore[assignment]
    live.live.cached_only = lambda: flags.cached_only
    if live.live.analyst is not None:
        live.live.analyst = cast(Any, MeteredAnalyst(live.live.analyst, meter, metrics))
    worker.state.live = live.live
    return worker


def __getattr__(name: str):
    if name == "app":
        return build_app()
    raise AttributeError(name)
