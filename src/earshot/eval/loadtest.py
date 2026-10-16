"""The deployed-stack load test: N listeners on the live SSE stream, each fetching every line's audio.

Per line, event to first audio byte is the sum of what the server measured (``/api/metrics``:
event to the Director's start, then serving and sending) and what each listener measured (the
message's arrival after it was sent, then the audio request's first byte). The two clocks are the
worker's and this machine's; both are NTP-synchronised, and the skew is stated as a limit, not
corrected.

The run also writes the latency model the accelerated evaluation draws from
(``earshot evaluate --latency``): audio-ready samples, Analyst samples, and delivery samples (the
Director's start to the listener's first audio byte).

Listeners spread round-robin over the evaluation profiles' groups in English. One judge code
(``X-Earshot-Code``, read from the environment, never written anywhere) buys the ticket that lifts
the per-IP stream limit for a load from one machine, and reads ``/api/metrics``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from earshot.eval.run import PROFILES, pct

TARGET_P95_MS = 2_000


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Listener:
    group: str
    received: list[dict[str, Any]] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)
    kinds: Counter[str] = field(default_factory=Counter)


def followed_forward(match_id: str) -> str | None:
    """P2's player, picked as the evaluation picks it: the home side's first starting forward.
    Stored matches are development seeds (``m00026`` is seed 26), so the match is re-simulated."""
    from earshot.sim.match import simulate
    from earshot.world.clubs import world

    info = simulate(int(match_id.lstrip("m"))).info
    players = world().players
    return next((pid for pid in info.home.starting if players[pid].position == "FW"), None)


def groups_for(match: dict[str, Any], roster_fw: str | None) -> list[str]:
    out = []
    for p in PROFILES:
        club = match["home"]["team_id"] if p.follow else None
        player = roster_fw if p.follow else None
        parts = [match["match_id"], "en", p.mode.value, p.output.value, club or "-", player or "-", "-"]
        out.append(".".join(parts))  # P4's focus stat is not live; it listens without one
    return out


async def listen(client: httpx.AsyncClient, base: str, lst: Listener, ticket: str, until: float) -> None:
    params = {"group": lst.group, "ticket": ticket}
    try:
        async with client.stream("GET", f"{base}/api/stream", params=params, timeout=None) as r:
            if r.status_code != 200:
                lst.errors[f"stream {r.status_code}"] += 1
                return
            async for raw in r.aiter_lines():
                if time.time() >= until:
                    break
                if not raw.startswith("data: "):
                    continue
                msg = json.loads(raw[6:])
                lst.kinds[msg.get("kind", "?")] += 1
                if msg.get("kind") != "line":
                    continue
                rec: dict[str, Any] = {"id": msg["id"], "recv_ms": now_ms(), "audio": msg.get("audio_url")}
                if rec["audio"]:
                    t0 = time.perf_counter()
                    try:
                        async with client.stream("GET", f"{base}{rec['audio']}", timeout=10) as a:
                            async for _ in a.aiter_bytes():
                                rec["ttfb_ms"] = int((time.perf_counter() - t0) * 1000)
                                break
                            rec["audio_status"] = a.status_code
                    except httpx.HTTPError as e:
                        lst.errors[type(e).__name__] += 1
                lst.received.append(rec)
    except httpx.HTTPError as e:
        lst.errors[type(e).__name__] += 1


async def run(base: str, code: str, listeners: int, minutes: float) -> dict[str, Any]:
    started = now_ms()
    async with httpx.AsyncClient(
        http2=False, limits=httpx.Limits(max_connections=listeners * 2 + 10)
    ) as client:
        match = (await client.get(f"{base}/api/live")).json()
        t = await client.post(f"{base}/api/ticket", headers={"x-earshot-code": code})
        t.raise_for_status()
        ticket = t.json()["ticket"]
        groups = groups_for(match, followed_forward(match["match_id"]))
        lsts = [Listener(groups[i % len(groups)]) for i in range(listeners)]
        until = time.time() + minutes * 60
        await asyncio.gather(*(listen(client, base, x, ticket, until) for x in lsts))
        m = await client.get(
            f"{base}/api/metrics", params={"since_ms": started}, headers={"x-earshot-code": code}
        )
        m.raise_for_status()
        server = m.json()
    return summarize(base, match, listeners, minutes, lsts, server)


def summarize(
    base: str,
    match: dict[str, Any],
    listeners: int,
    minutes: float,
    lsts: list[Listener],
    server: dict[str, Any],
) -> dict[str, Any]:
    by_line = {(x["group"], x["id"]): x for x in server["lines"]}
    e2e, delivery, ttfb, transit, unmatched = [], [], [], [], 0
    never: list[int] = []
    for lst in lsts:
        for rec in lst.received:
            srv = by_line.get((lst.group, rec["id"]))
            if srv is None or "ttfb_ms" not in rec:
                unmatched += srv is None
                continue
            arrive = rec["recv_ms"] - srv["sent_ms"]
            total = srv["start_ms"] + srv["publish_ms"] + arrive + rec["ttfb_ms"]
            if srv["source"] == "caller":  # live calls; window lines have no single event time
                e2e.append(total)
                if srv["never_drop"]:
                    never.append(total)
            transit.append(arrive)
            ttfb.append(rec["ttfb_ms"])
            delivery.append(srv["publish_ms"] + arrive + rec["ttfb_ms"])

    def dist(xs: list[int]) -> dict[str, Any]:
        return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "max": max(xs) if xs else None}

    p95 = pct(e2e, 95)
    return {
        "base": base,
        "match_id": match.get("match_id"),
        "listeners": listeners,
        "groups": sorted({x.group for x in lsts}),
        "minutes": minutes,
        "event_to_first_audio_byte_ms": dist(e2e),
        "never_drop_event_to_first_audio_byte_ms": dist(never),
        "p95_under_2s": p95 is not None and p95 < TARGET_P95_MS,
        "components_ms": {
            "server_ready": dist(server["ready_ms"]),
            "server_start": dist([x["start_ms"] for x in server["lines"] if x["source"] == "caller"]),
            "server_publish": dist([x["publish_ms"] for x in server["lines"]]),
            "transit": dist(transit),
            "audio_ttfb": dist(ttfb),
            "analyst": dist(server["analyst_ms"]),
        },
        "received_lines": sum(len(x.received) for x in lsts),
        "unmatched_lines": unmatched,
        "message_kinds": dict(sum((x.kinds for x in lsts), Counter())),
        "errors": dict(sum((x.errors for x in lsts), Counter())),
        "latency_model": {
            "ready_ms": server["ready_ms"],
            "analyst_ms": server["analyst_ms"],
            "delivery_ms": delivery,
        },
        "limits": [
            "two clocks (the worker's and the load generator's), both NTP-synchronised; skew not corrected",
            "one load-generator machine and network path",
        ],
    }
