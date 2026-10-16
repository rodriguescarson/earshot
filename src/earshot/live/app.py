"""Live HTTP surface: the current match, an SSE stream per group, on-demand lines and line audio.

Run locally:  uv run uvicorn earshot.live.app:app --port 8787
Configuration:
- ``EARSHOT_MATCH_DIR``: stored match logs (``<match>.events.jsonl`` + ``.info.json``), played in a loop.
- ``EARSHOT_AUDIO_DIR`` / ``EARSHOT_TTS_CACHE``: served line audio and the speech cache.
- ``EARSHOT_WEBPUBSUB_ENDPOINT`` + ``EARSHOT_WEBPUBSUB_HUB``: also publish to Web PubSub (keyless).
- ``EARSHOT_LIVE_SPEED``: 1.0 is real time.
- ``EARSHOT_ALLOWED_ORIGINS``: comma-separated origins for CORS.
The SSE stream is both the local transport and a fallback when Web PubSub is unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from earshot.agents.analyst import Analyst, AnalystResult
from earshot.agents.caller import zone_key
from earshot.agents.catalog import load, placeholders
from earshot.agents.state import spoken_minute
from earshot.contract import Language, Line, LineSource
from earshot.contract.line import SlotValue
from earshot.live.audio import AudioStore
from earshot.live.broadcast import GroupError, GroupKey, LiveMatch
from earshot.sim.io import load_events, load_info
from earshot.tools.snapshots import MatchView
from earshot.world.localize import roster as local_roster

log = logging.getLogger("earshot.live")
ONDEMAND_GAP_S = 3.0


class Hub:
    """Fan-out of published messages: SSE subscribers per group, plus Web PubSub when configured."""

    def __init__(self) -> None:
        self.subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)
        self.wps = None
        endpoint, hub = os.environ.get("EARSHOT_WEBPUBSUB_ENDPOINT"), os.environ.get("EARSHOT_WEBPUBSUB_HUB")
        if endpoint and hub:
            from azure.identity import DefaultAzureCredential
            from azure.messaging.webpubsubservice import WebPubSubServiceClient

            self.wps = WebPubSubServiceClient(endpoint=endpoint, hub=hub, credential=DefaultAzureCredential())

    async def send(self, group: str, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False)
        for q in list(self.subscribers.get(group, ())):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(data)
        if self.wps is not None:
            try:
                await asyncio.to_thread(
                    self.wps.send_to_group, group, message, content_type="application/json"
                )
            except Exception as e:  # a Web PubSub hiccup must not stop the match
                log.warning("web pubsub send failed: %s", e)


class GatedAnalyst:
    """The Analyst, skipped while cached-only mode is on (the window falls back to a summary)."""

    def __init__(self, analyst: Analyst, off: Callable[[], bool]) -> None:
        self.analyst, self.off = analyst, off

    def run(self, view: MatchView, window_id: str, expected_dead_ms: int) -> AnalystResult:
        if self.off():
            return AnalystResult(window_id, error="cached-only mode")
        return self.analyst.run(view, window_id, expected_dead_ms)


class Live:
    def __init__(self) -> None:
        self.match_dir = Path(os.environ.get("EARSHOT_MATCH_DIR", "data/matches"))
        audio_dir = Path(os.environ.get("EARSHOT_AUDIO_DIR", "data/live-audio"))
        cache = Path(os.environ.get("EARSHOT_TTS_CACHE", "data/tts-cache"))
        self.speed = float(os.environ.get("EARSHOT_LIVE_SPEED", "1.0"))
        self.audio = AudioStore(cache, audio_dir)
        self.hub = Hub()
        self.match: LiveMatch | None = None
        self.analyst = (
            Analyst(cache_dir=cache.parent / "analyst-cache")
            if os.environ.get("EARSHOT_AI_ENDPOINT")
            else None
        )
        self.last_ondemand: dict[str, float] = {}
        self.cached_only: Callable[[], bool] = lambda: False
        """The worker points this at its kill-switch flag: no new speech or model calls while set."""

    def logs(self) -> list[Path]:
        return sorted(self.match_dir.glob("*.events.jsonl"))

    async def loop(self) -> None:
        while True:
            for events_path in self.logs():
                info = load_info(
                    events_path.with_name(events_path.name.replace(".events.jsonl", ".info.json"))
                )
                latin = local_roster(Language.EN)
                teams = {info.home.team_id, info.away.team_id}
                clubs = [c for c in latin.clubs if c.team_id in teams]
                players = [p for p in latin.players if p.team_id in teams]
                self.match = LiveMatch(
                    info,
                    load_events(events_path),
                    clubs,
                    players,
                    audio=self.audio,
                    send=self.hub.send,
                    analyst=GatedAnalyst(self.analyst, lambda: self.cached_only()) if self.analyst else None,
                    speed=self.speed,
                    cached_only=lambda: self.cached_only(),
                )
                log.info("live: %s kicks off", info.match_id)
                await self.match.run()
                await asyncio.sleep(30)
            if not self.logs():
                await asyncio.sleep(10)


live = Live()


async def current(request: Request) -> Response:
    if live.match is None:
        return JSONResponse({"error": "no live match yet"}, status_code=503)
    return JSONResponse(live.match.summary())


async def stream(request: Request) -> Response:
    m = live.match
    name = request.query_params.get("group", "")
    if m is None:
        return JSONResponse({"error": "no live match yet"}, status_code=503)
    try:
        key = GroupKey.parse(name)
    except (ValueError, KeyError):
        return JSONResponse(
            {"error": "group must be match.language.mode.output.club.player.stat"}, status_code=400
        )
    if key.match_id != m.info.match_id:
        return JSONResponse({"error": "that match is not live", "live": m.info.match_id}, status_code=409)
    try:
        m.join(key)
    except GroupError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    live.hub.subscribers[key.name].add(q)

    async def events():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    m.join(key)
                    yield ": keep-alive\n\n"
        finally:
            live.hub.subscribers[key.name].discard(q)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


async def ondemand(request: Request) -> Response:
    """'Where's the ball?' and 'Catch me up': answered for the asker only, from code templates."""
    m = live.match
    key: GroupKey | None = None
    try:
        body = await request.json()
        key = GroupKey.parse(str(body.get("group", "")))
        kind = str(body.get("kind"))
    except (ValueError, KeyError, AttributeError):
        kind = ""
    if key is None or kind not in ("where", "catchup"):
        return JSONResponse({"error": "group and kind (where or catchup) are required"}, status_code=400)
    if m is None or key.match_id != m.info.match_id:
        return JSONResponse({"error": "that match is not live"}, status_code=409)
    if key.name not in m.groups:
        return JSONResponse({"error": "join the match stream first"}, status_code=409)
    now = time.monotonic()
    if now - live.last_ondemand.get(key.name + kind, 0) < ONDEMAND_GAP_S:
        return JSONResponse({"error": "once every 3 seconds"}, status_code=429)
    live.last_ondemand[key.name + kind] = now
    line = ondemand_line(m, key, kind)
    speak = await asyncio.to_thread(live.audio.cached_ms, line) is not None or (
        not m.cached_only() and m.budget.allow(len(line.text))
    )
    served = await asyncio.to_thread(live.audio.serve, line, key.output_mode, synthesize=speak)
    return JSONResponse(
        {
            "id": line.line_id,
            "text": line.text,
            "audio_url": f"/audio/{served.name}" if served else None,
            "duration_ms": served.duration_ms if served else None,
        }
    )


def ondemand_line(m: LiveMatch, key: GroupKey, kind: str) -> Line:
    cat = load(key.language)
    clubs, _ = m.views[key.language]
    names = {c.team_id: c.short_name for c in clubs}
    st = m.state
    slots: dict[str, SlotValue] = {}
    if kind == "catchup":
        tid = "catch_up.1"
        slots = {
            "minute": spoken_minute(st.period_id, st.clock_ms),
            "home": names[m.info.home.team_id],
            "away": names[m.info.away.team_id],
            "home_score": st.home_score,
            "away_score": st.away_score,
        }
    else:
        ball = m.ball_now()
        if ball is not None and ball.in_play and ball.team_id:
            tid = "where.1"
            zone = zone_key(ball.x, ball.y, ball.team_id, ball.period_id, m.roster)
            slots = {
                "club": names[ball.team_id],
                "zone": cat.lexicon["zone"][zone],
                "minute": spoken_minute(st.period_id, st.clock_ms),
            }
        else:
            tid = "where_dead.1"
            kind_word = cat.lexicon["stoppage"].get(st.stoppage.value if st.stoppage else "", "")
            team = st.restart_team_id or m.info.home.team_id
            slots = {"stoppage": kind_word or cat.lexicon["stoppage"]["throw_in"], "club": names[team]}
    used = {k: v for k, v in slots.items() if k in placeholders(cat.template(tid, key.listening_mode))}
    text = cat.render(tid, key.listening_mode, used)
    return Line(
        line_id=f"{m.info.match_id}-{kind}-{m.now_ms}-{key.language.value}",
        match_id=m.info.match_id,
        moment_id=None,
        source=LineSource.ON_DEMAND,
        language=key.language,
        listening_mode=key.listening_mode,
        template_id=tid,
        slots=used,
        text=text,
        priority=70,
        wall_ms=m.now_ms,
        deadline_wall_ms=m.now_ms + 10_000,
    )


async def audio(request: Request) -> Response:
    p = live.audio.path(request.path_params["name"])
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        p, media_type="audio/mp4", headers={"Cache-Control": "public, max-age=86400, immutable"}
    )


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    task = asyncio.create_task(live.loop())
    try:
        yield
    finally:
        task.cancel()


ROUTES = [
    Route("/api/live", current),
    Route("/api/stream", stream),
    Route("/api/ondemand", ondemand, methods=["POST"]),
    Route("/audio/{name}", audio),
]

app = Starlette(
    routes=ROUTES,
    lifespan=lifespan,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=[
                o for o in os.environ.get("EARSHOT_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o
            ],
            allow_methods=["GET", "POST"],
            allow_headers=["content-type", "x-earshot-code"],
        )
    ],
)
