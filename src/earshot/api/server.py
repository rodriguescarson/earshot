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
Judges trade their code for a signed ticket at ``POST /api/ticket``.

Spend (SEC1) for both surfaces runs through one persisted ``SpendMeter``:
- ``Live.cached_only`` reads the kill-switch flag, so the live app's own gate stops new speech and
  Analyst calls the moment the flag flips (it also skips the Analyst while nobody listens);
- each Analyst call is reserved against the daily token cap first and charged its real count after;
- each synthesized line (cache misses only) is charged to the daily character cap, across matches
  and restarts, and never past the live app's own daily speech budget.
"""

from __future__ import annotations

from typing import Any, cast

from starlette.routing import Route

from earshot.api.flags import JudgeCodesFromFlags, SpendMeter
from earshot.api.streams import StreamGuard, Tickets, guard_stream, ticket_endpoint
from earshot.api.worker import build_app as build_worker

ANALYST_CALL_TOKENS = 6_000
"""Tokens set aside before an Analyst call (a few turns with tool results); the real count is added after."""


class MeteredAnalyst:
    """Wraps the Foundry Analyst: no call once the daily token cap would be passed."""

    def __init__(self, inner: Any, meter: SpendMeter) -> None:
        self.inner, self.meter = inner, meter

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        from earshot.agents.analyst import AnalystResult

        if not self.meter.allow(tokens=ANALYST_CALL_TOKENS):
            return AnalystResult(window_id, error="daily token cap or cached-only mode")
        res = self.inner.run(view, window_id, expected_dead_ms)
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
    routes = [
        Route(r.path, guard_stream(r.endpoint, guard, tickets), methods=["GET"])
        if isinstance(r, Route) and r.path == "/api/stream"
        else r
        for r in live.ROUTES
    ]
    assert any(isinstance(r, Route) and r.path == "/api/stream" for r in live.ROUTES), (
        "live stream route moved"
    )
    holder: dict[str, Any] = {}

    async def ticket(request):  # the judge codes come from the worker's flags, built below
        return await holder["ticket"](request)

    routes.append(Route("/api/ticket", ticket, methods=["POST"]))
    worker = build_worker(extra_routes=routes, extra_lifespans=[live.lifespan])
    flags, meter = worker.state.flags, cast(SpendMeter, worker.state.meter)
    holder["ticket"] = ticket_endpoint(JudgeCodesFromFlags(flags), tickets)
    worker.state.stream_guard = guard
    limit = broadcast.DAILY_SPEECH_CHARS
    broadcast.SpeechBudget = lambda: MeteredSpeechBudget(meter, limit)  # type: ignore[assignment]
    live.live.cached_only = lambda: flags.cached_only
    if live.live.analyst is not None:
        live.live.analyst = cast(Any, MeteredAnalyst(live.live.analyst, meter))
    worker.state.live = live.live
    return worker


def __getattr__(name: str):
    if name == "app":
        return build_app()
    raise AttributeError(name)
