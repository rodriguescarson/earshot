"""The worker Container App's HTTP surface: health, status and Web PubSub negotiate.

Entrypoint: ``uvicorn earshot.api.worker:app``. Configuration (all keyless):
- ``EARSHOT_STORAGE_URL``: Blob account URL; flags and judge codes are read with the managed
  identity (``AZURE_CLIENT_ID`` selects it). Without it, files under ``EARSHOT_ADMIN_DIR``.
- ``EARSHOT_WEBPUBSUB_ENDPOINT`` and ``EARSHOT_WEBPUBSUB_HUB``: the Web PubSub service (local auth off).
- ``EARSHOT_ALLOWED_ORIGINS``: comma-separated origins allowed to call the API (the static site).

Public surfaces mutate nothing on the server: negotiate only issues receive-only tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from earshot.api.flags import CACHED_ONLY_NOTICE, BlobFlags, FileFlags, Flags, JudgeCodesFromFlags, SpendMeter
from earshot.api.negotiate import NegotiateError, Negotiator, TokenIssuer
from earshot.api.ratelimit import client_ip

log = logging.getLogger("earshot.worker")
VERSION = os.environ.get("EARSHOT_VERSION", "dev")

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


class SecurityHeaders:
    """ASGI middleware: the SEC5 headers on every response the worker sends."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [
                    (k.lower().encode(), v.encode())
                    for k, v in SECURITY_HEADERS.items()
                    if k.lower().encode() not in present
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def default_issuer() -> TokenIssuer:
    from azure.identity import DefaultAzureCredential
    from azure.messaging.webpubsubservice import WebPubSubServiceClient

    client = WebPubSubServiceClient(
        endpoint=os.environ["EARSHOT_WEBPUBSUB_ENDPOINT"],
        hub=os.environ.get("EARSHOT_WEBPUBSUB_HUB", "earshot"),
        credential=DefaultAzureCredential(),
    )
    return cast(TokenIssuer, client)


def default_flags() -> Flags:
    url = os.environ.get("EARSHOT_STORAGE_URL")
    source = (
        BlobFlags(url)
        if url
        else FileFlags(Path(os.environ.get("EARSHOT_ADMIN_DIR", "data/admin")) / "flags.json")
    )
    flags = Flags(source)
    flags.refresh()
    return flags


def build_app(
    flags: Flags | None = None,
    issuer: TokenIssuer | None = None,
    poll_s: float = 30.0,
    extra_routes: Sequence[BaseRoute] = (),
    extra_lifespans: Sequence[Callable[[Starlette], Any]] = (),
) -> Starlette:
    """The worker app. ``extra_routes`` and ``extra_lifespans`` let another surface (the live
    match broadcaster) share this app's headers, CORS and flag poller (see earshot.api.server)."""
    flags = flags or default_flags()
    meter = SpendMeter(flags)
    state: dict[str, Any] = {"issuer": issuer}

    def negotiator() -> Negotiator:
        if state["issuer"] is None:
            state["issuer"] = default_issuer()
        if "negotiator" not in state:
            state["negotiator"] = Negotiator(state["issuer"], flags, JudgeCodesFromFlags(flags))
        return state["negotiator"]

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": VERSION})

    async def status(request: Request) -> Response:
        return JSONResponse(
            {
                "version": VERSION,
                "cached_only": flags.cached_only,
                "notice": CACHED_ONLY_NOTICE if flags.cached_only else None,
                "flags_age_s": None if not flags.loaded_at else round(flags.clock() - flags.loaded_at, 1),
                "spend": meter.snapshot(),
            }
        )

    async def negotiate(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:  # an unreadable body is a client error, not a crash
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        headers = {k.lower(): v for k, v in request.headers.items()}
        ip = client_ip(headers, request.client.host if request.client else None)
        try:
            out = await asyncio.to_thread(negotiator().negotiate, body, ip, headers.get("x-earshot-code"))
        except NegotiateError as e:
            return JSONResponse({"error": e.message, **e.extra}, status_code=e.status)
        return JSONResponse(out)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        task = asyncio.create_task(flags.poll(poll_s))
        async with contextlib.AsyncExitStack() as stack:
            for extra in extra_lifespans:
                await stack.enter_async_context(extra(app))
            try:
                yield
            finally:
                task.cancel()
                meter.persist()

    origins = [o for o in os.environ.get("EARSHOT_ALLOWED_ORIGINS", "").split(",") if o]
    app = Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/api/status", status),
            Route("/api/negotiate", negotiate, methods=["POST"]),
            *extra_routes,
        ],
        middleware=[
            Middleware(SecurityHeaders),
            Middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST"],
                allow_headers=["content-type", "x-earshot-code"],
                max_age=600,
            ),
        ],
        lifespan=lifespan,
    )

    app.state.flags, app.state.meter = flags, meter
    return app


def __getattr__(name: str):
    # ``uvicorn earshot.api.worker:app`` builds the app on first access, from the environment.
    if name == "app":
        return build_app()
    raise AttributeError(name)
