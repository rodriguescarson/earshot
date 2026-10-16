"""The deployed worker: Stream 1's live broadcaster plus Stream 2's guards, in one app.

Entrypoint: ``uvicorn earshot.api.server:app``.
- Guards (``earshot.api.worker``): ``/healthz``, ``/api/status`` (cached-only notice, spend),
  ``/api/negotiate`` (receive-only Web PubSub tokens, per-IP limits, judge code), the flag poller
  that flips cached-only mode without a restart, the security headers and CORS.
- Live (``earshot.live.app``): ``/api/live``, ``/api/stream`` (SSE), ``/api/ondemand``,
  ``/audio/*``, and the broadcaster loop that runs the shared pipeline in real time and publishes to
  Web PubSub.

Spend (SEC1) is enforced here, for both surfaces, by one persisted ``SpendMeter``:
- the Analyst runs only while at least one listening group is joined, and only while the meter
  allows another call (cached-only mode and the daily token cap both stop it);
- every spoken line's characters count against the same daily character cap, across matches.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from earshot.api.flags import SpendMeter
from earshot.api.worker import build_app as build_worker

ANALYST_CALL_TOKENS = 6_000
"""Tokens set aside before an Analyst call (a few turns with tool results); the real count is added after."""


def skipped(reason: str) -> SimpleNamespace:
    """An Analyst result with no claims: the window-summary line airs instead."""
    return SimpleNamespace(claims=[], timed_out=False, error=reason, invalid=[], latency_ms=0, tokens=0)


class GatedAnalyst:
    """Wraps the Foundry Analyst: no listeners or no budget means no call."""

    def __init__(self, inner: Any, live: Any, meter: SpendMeter) -> None:
        self.inner, self.live, self.meter = inner, live, meter

    def run(self, view: Any, window_id: str, expected_dead_ms: int) -> Any:
        match = getattr(self.live, "match", None)
        if match is None or not getattr(match, "groups", None):
            return skipped("skipped: no listeners")
        if not self.meter.allow(tokens=ANALYST_CALL_TOKENS):
            return skipped("skipped: cached-only mode or the daily token cap")
        res = self.inner.run(view, window_id, expected_dead_ms)
        self.meter.add(tokens=int(getattr(res, "tokens", 0) or 0))
        return res


class MeteredSpeechBudget:
    """Stands in for the live broadcaster's per-match speech budget: one daily cap for the process."""

    def __init__(self, meter: SpendMeter) -> None:
        self.meter = meter

    def allow(self, chars: int) -> bool:
        if not self.meter.allow(chars=chars):
            return False
        self.meter.add(chars=chars)
        return True


def build_app():
    from earshot.live import app as live
    from earshot.live import broadcast

    worker = build_worker(extra_routes=live.ROUTES, extra_lifespans=[live.lifespan])
    meter: SpendMeter = worker.state.meter
    broadcast.SpeechBudget = lambda: MeteredSpeechBudget(meter)  # type: ignore[assignment]
    if live.live.analyst is not None:
        live.live.analyst = cast(Any, GatedAnalyst(live.live.analyst, live.live, meter))
    worker.state.live = live.live
    return worker


def __getattr__(name: str):
    if name == "app":
        return build_app()
    raise AttributeError(name)
