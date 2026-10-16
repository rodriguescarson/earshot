"""Limits for the live SSE stream (``/api/stream``), the path the web client actually listens on.

The negotiate endpoint's limits (design R3-12) only bind callers who negotiate, and the browser's
``EventSource`` connects to the stream directly, so the same limits are applied here:

- **anonymous callers:** at most ``SESSIONS_PER_IP`` open streams per IP, and
  ``MINUTES_PER_IP_PER_DAY`` live minutes per IP per UTC day. A stream reserves up to one
  session's worth of minutes when it opens; when the time runs out the stream ends, and it refunds
  what it did not use when it closes;
- **judges** (DR-6) skip the per-IP limits. ``EventSource`` cannot send headers, so the client
  trades the code (header ``X-Earshot-Code``, never a URL) for a short-lived signed ticket at
  ``POST /api/ticket`` and adds ``&ticket=`` to the stream URL;
- **everyone:** at most ``MAX_STREAMS`` open streams on the worker.

Tickets are signed with a key made when the process starts, so a restart invalidates them and the
client asks again. The worker runs one replica, so one key is enough. IPs live only in memory
(SEC6).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from earshot.api.flags import utc_day
from earshot.api.negotiate import MINUTES_PER_IP_PER_DAY, SESSIONS_PER_IP, TOKEN_MINUTES, NegotiateError
from earshot.api.ratelimit import JudgeCodes, SlidingLimit, client_ip

MAX_STREAMS = 150
"""Open SSE streams on the worker, all callers together (judges included)."""
TICKET_S = TOKEN_MINUTES * 60
TICKET_TRIES_PER_MINUTE = 10
"""Per IP: enough for a panel that mistypes, too few to guess a code."""
BUSY = "Earshot is busy. Try again in a few minutes, or play a recorded clip."


@dataclass
class StreamGuard:
    clock: Callable[[], float] = time.time
    max_streams: int = MAX_STREAMS
    open_by_ip: Counter[str] = field(default_factory=Counter)
    reserved_s: dict[tuple[str, str], float] = field(default_factory=dict)
    total: int = 0

    def admit(self, ip: str, judge: bool) -> tuple[float, Callable[[float], None]]:
        """Seconds this stream may run, and the release to call with the seconds it ran."""
        if self.total >= self.max_streams:
            raise NegotiateError(503, BUSY, limit="streams")
        if judge:
            self.total += 1
            return float(TICKET_S), self._release(None, None, 0.0)
        if self.open_by_ip[ip] >= SESSIONS_PER_IP:
            raise NegotiateError(429, BUSY, limit="sessions")
        day = utc_day(self.clock())
        for k in [k for k in self.reserved_s if k[1] != day]:  # yesterday's IPs go (SEC6)
            del self.reserved_s[k]
        left = MINUTES_PER_IP_PER_DAY * 60 - self.reserved_s.get((ip, day), 0.0)
        if left <= 0:
            raise NegotiateError(429, BUSY, limit="minutes")
        grant = min(float(TOKEN_MINUTES * 60), left)
        self.reserved_s[(ip, day)] = self.reserved_s.get((ip, day), 0.0) + grant
        self.open_by_ip[ip] += 1
        self.total += 1
        return grant, self._release(ip, day, grant)

    def _release(self, ip: str | None, day: str | None, grant: float) -> Callable[[float], None]:
        done = False

        def release(used_s: float) -> None:
            nonlocal done
            if done:
                return
            done = True
            self.total -= 1
            if ip is None or day is None:
                return
            self.open_by_ip[ip] -= 1
            if self.open_by_ip[ip] <= 0:
                del self.open_by_ip[ip]
            if (ip, day) in self.reserved_s:  # refund what the stream did not use
                self.reserved_s[(ip, day)] -= max(0.0, grant - used_s)

        return release


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass
class Tickets:
    """Signed, expiring proof that the caller presented a judge code (no code inside)."""

    key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    clock: Callable[[], float] = time.time

    def issue(self) -> str:
        body = _b64(json.dumps({"judge": True, "exp": int(self.clock()) + TICKET_S}).encode())
        return body + "." + _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())

    def judge(self, ticket: str | None) -> bool:
        if not ticket or "." not in ticket:
            return False
        body, sig = ticket.rsplit(".", 1)
        want = hmac.new(self.key, body.encode(), hashlib.sha256).digest()
        try:
            if not hmac.compare_digest(want, _unb64(sig)):
                return False
            claims = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError):
            return False
        return bool(claims.get("judge")) and int(claims.get("exp", 0)) > self.clock()


async def _limited(
    body: AsyncIterable[Any], seconds: float, release: Callable[[float], None], clock: Callable[[], float]
) -> AsyncIterator[Any]:
    start = clock()
    try:
        async for chunk in body:
            yield chunk
            if clock() - start >= seconds:
                yield ": session over; reconnect to keep listening\n\n"
                break
    finally:
        release(clock() - start)
        aclose = getattr(body, "aclose", None)
        if aclose is not None:
            await aclose()


def guard_stream(
    endpoint: Callable[[Request], Awaitable[Response]], guard: StreamGuard, tickets: Tickets
) -> Callable[[Request], Awaitable[Response]]:
    async def guarded(request: Request) -> Response:
        ip = client_ip(dict(request.headers), request.client.host if request.client else None)
        try:
            seconds, release = guard.admit(ip, tickets.judge(request.query_params.get("ticket")))
        except NegotiateError as e:
            return JSONResponse({"error": e.message, **e.extra}, status_code=e.status)
        try:
            resp = await endpoint(request)
        except BaseException:
            release(0.0)
            raise
        if not isinstance(resp, StreamingResponse):
            release(0.0)  # refused (bad group, match not live): nothing was used
            return resp
        resp.body_iterator = _limited(resp.body_iterator, seconds, release, guard.clock)
        return resp

    return guarded


def ticket_endpoint(codes: JudgeCodes, tickets: Tickets) -> Callable[[Request], Awaitable[Response]]:
    tries = SlidingLimit(TICKET_TRIES_PER_MINUTE)

    async def ticket(request: Request) -> Response:
        ip = client_ip(dict(request.headers), request.client.host if request.client else None)
        if not tries.allow(ip):
            return JSONResponse(
                {"error": "too many tries"},
                status_code=429,
                headers={"retry-after": str(tries.retry_after_s(ip))},
            )
        if not codes.valid(request.headers.get("x-earshot-code")):
            return JSONResponse({"error": "not a judge code"}, status_code=403)
        return JSONResponse({"ticket": tickets.issue(), "expires_in_s": TICKET_S})

    return ticket
