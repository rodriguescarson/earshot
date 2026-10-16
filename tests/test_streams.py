"""Limits on the live SSE stream (the path the web client listens on) and the judge ticket."""

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from earshot.api.negotiate import MINUTES_PER_IP_PER_DAY, SESSIONS_PER_IP, NegotiateError
from earshot.api.ratelimit import JudgeCodes, code_hash
from earshot.api.streams import (
    TICKET_S,
    TICKET_TRIES_PER_MINUTE,
    StreamGuard,
    Tickets,
    guard_stream,
    ticket_endpoint,
)


class Clock:
    def __init__(self, t: float = 1_760_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_per_ip_streams_then_busy_and_release_frees_a_slot():
    g = StreamGuard()
    releases = [g.admit("203.0.113.9", judge=False)[1] for _ in range(SESSIONS_PER_IP)]
    with pytest.raises(NegotiateError) as e:
        g.admit("203.0.113.9", judge=False)
    assert e.value.status == 429
    assert e.value.extra["limit"] == "sessions"
    g.admit("198.51.100.4", judge=False)  # another listener is unaffected
    releases[0](10.0)
    releases[0](10.0)  # a second release of the same stream changes nothing
    g.admit("203.0.113.9", judge=False)
    assert g.total == SESSIONS_PER_IP + 1


def test_daily_minutes_are_reserved_and_refunded():
    clock = Clock()
    g = StreamGuard(clock=clock)
    ip = "203.0.113.9"
    used = 0.0
    while used < MINUTES_PER_IP_PER_DAY * 60:
        seconds, release = g.admit(ip, judge=False)
        release(seconds)  # listened to the end of the grant
        used += seconds
    assert used == MINUTES_PER_IP_PER_DAY * 60
    with pytest.raises(NegotiateError) as e:
        g.admit(ip, judge=False)
    assert e.value.extra["limit"] == "minutes"
    clock.t += 86_400  # a new UTC day: yesterday's count is gone
    seconds, release = g.admit(ip, judge=False)
    release(30.0)  # left after 30 s: the rest comes back
    assert g.reserved_s == {(ip, next(iter(g.reserved_s))[1]): 30.0}


def test_judges_skip_the_per_ip_limits_but_not_the_worker_cap():
    g = StreamGuard(max_streams=SESSIONS_PER_IP + 2)
    for _ in range(SESSIONS_PER_IP + 2):
        g.admit("203.0.113.9", judge=True)
    with pytest.raises(NegotiateError) as e:
        g.admit("198.51.100.4", judge=True)
    assert e.value.status == 503


def test_tickets_expire_and_cannot_be_forged():
    clock = Clock()
    t = Tickets(clock=clock)
    good = t.issue()
    assert t.judge(good)
    assert not t.judge(None)
    assert not t.judge("garbage")
    body, sig = good.rsplit(".", 1)
    assert not t.judge(body + "." + sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB"))
    assert not Tickets(clock=clock).judge(good)  # another key (a restart)
    clock.t += TICKET_S + 1
    assert not t.judge(good)


def stream_app(guard: StreamGuard, tickets: Tickets, chunks: int = 3):
    async def endpoint(request: Request):
        if request.query_params.get("group") == "bad":
            return JSONResponse({"error": "bad group"}, status_code=400)

        async def gen():
            for i in range(chunks):
                yield f"data: {i}\n\n"
                await asyncio.sleep(0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    codes = JudgeCodes.__new__(JudgeCodes)
    codes._hashes = {code_hash("panel-7f3")}  # type: ignore[attr-defined]
    codes.valid = lambda code: code is not None and code_hash(code) in codes._hashes  # type: ignore[method-assign]
    return Starlette(
        routes=[
            Route("/api/stream", guard_stream(endpoint, guard, tickets)),
            Route("/api/ticket", ticket_endpoint(codes, tickets), methods=["POST"]),
        ]
    )


def test_the_stream_route_counts_streams_and_frees_them_when_they_end():
    guard, tickets = StreamGuard(), Tickets()
    ip = {"x-forwarded-for": "203.0.113.9"}
    with TestClient(stream_app(guard, tickets)) as c:
        for _ in range(SESSIONS_PER_IP + 2):  # each stream ends, so its slot comes back
            r = c.get("/api/stream?group=g", headers=ip)
            assert r.status_code == 200
            assert r.text.count("data:") == 3
        assert guard.total == 0
        assert c.get("/api/stream?group=bad", headers=ip).status_code == 400
        assert guard.total == 0  # a refused stream holds nothing
        assert guard.reserved_s[next(iter(guard.reserved_s))] < 1.0  # only the seconds actually used


def test_a_stream_ends_when_its_grant_runs_out():
    clock = Clock()
    guard, tickets = StreamGuard(clock=clock), Tickets()

    async def endpoint(request: Request):
        async def gen():
            for i in range(100):
                clock.t += 60  # each chunk takes a minute
                yield f"data: {i}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/api/stream", guard_stream(endpoint, guard, tickets))])
    with TestClient(app) as c:
        text = c.get("/api/stream", headers={"x-forwarded-for": "203.0.113.9"}).text
    assert text.count("data:") == 60  # one session's worth of minutes
    assert "session over" in text


def test_judge_ticket_flow_and_its_rate_limit():
    guard, tickets = StreamGuard(max_streams=50), Tickets()
    ip = {"x-forwarded-for": "203.0.113.9"}
    with TestClient(stream_app(guard, tickets, chunks=10_000)) as c:
        assert c.post("/api/ticket", headers=ip).status_code == 403
        r = c.post("/api/ticket", headers={**ip, "x-earshot-code": "panel-7f3"})
        assert r.status_code == 200
        ticket = r.json()["ticket"]
        assert "panel" not in ticket  # the code itself never travels in the URL
        assert tickets.judge(ticket)
        codes = [c.post("/api/ticket", headers=ip).status_code for _ in range(TICKET_TRIES_PER_MINUTE)]
        assert codes[-1] == 429
