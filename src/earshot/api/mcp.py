"""The public, read-only MCP match-tools server (CEO-3, eng E-4, ET5, ET9).

Entrypoint for the MCP Container App: ``uvicorn earshot.api.mcp:app`` (or ``python -m earshot.api.mcp``).
It serves the tools in ``earshot.tools.match_tools`` over streamable HTTP at ``/mcp``:

- Data: ``EARSHOT_MCP_SNAPSHOTS`` (a directory the worker writes, E-4) or, when no worker is
  publishing, ``EARSHOT_MCP_REPLAY`` (a directory holding ``<match>.events.jsonl``,
  ``<match>.info.json`` and ``roster.json``) replayed on the real clock.
- Limits: 60 calls a minute per judge code (header ``X-Earshot-Code``), 10 a minute per anonymous
  IP. Codes are SHA-256 hashes in ``EARSHOT_JUDGE_CODES_FILE``, rotatable without a redeploy.
- Read-only by construction: no tool writes anything, and every answer is bounded to the match
  clock of its snapshot (``as_of``).

This module may import only tools, stats, contract and pitch code (``tests/test_import_boundaries.py``):
it can never reach the agents, the simulator or the hidden truth log.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from earshot.api.ratelimit import JudgeCodes, SlidingLimit, client_ip
from earshot.contract.match import MatchInfo, Roster
from earshot.tools import match_tools as t
from earshot.tools.snapshots import MatchSource, ScheduledReplay, SnapshotDir, load_dir_events

MATCH_ID = re.compile(r"^m\d{5}$")
TEAM_ID = re.compile(r"^[a-z]{3}$")
PLAYER_ID = re.compile(r"^[a-z]{3}-\d{2}$")
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
JUDGE_PER_MINUTE = 60
ANON_PER_MINUTE = 10

INSTRUCTIONS = (
    "Read-only tools over Earshot's synthetic football matches (every club and player is made up). "
    "Every answer carries an as_of block: the match clock it covers and how stale it is. "
    "Call list_matches first, then catch_up, key_moments, team_window, player_report or recap."
)


def source_from_env() -> MatchSource:
    snap = os.environ.get("EARSHOT_MCP_SNAPSHOTS")
    if snap:
        return SnapshotDir(Path(snap))
    replay = Path(os.environ.get("EARSHOT_MCP_REPLAY", "data/mcp-replay"))
    info_path = next(replay.glob("*.info.json"), None)
    if info_path is None:
        raise RuntimeError(f"no match to serve: set EARSHOT_MCP_SNAPSHOTS or put a match in {replay}")
    info = MatchInfo.model_validate_json(info_path.read_text())
    roster = Roster.model_validate_json((replay / "roster.json").read_text())
    teams = {info.home.team_id, info.away.team_id}
    clubs = [c for c in roster.clubs if c.team_id in teams]
    players = [p for p in roster.players if p.team_id in teams]
    events = load_dir_events(replay / f"{info.match_id}.events.jsonl")
    kickoff = float(os.environ.get("EARSHOT_MCP_KICKOFF_UNIX", time.time()))
    return ScheduledReplay(info, clubs, players, events, kickoff)


def build_server(source: MatchSource) -> MCPServer:
    server = MCPServer(
        name="earshot-match-tools", title="Earshot match tools", instructions=INSTRUCTIONS, version="1"
    )

    def guard(fn, *args):
        """Bad input comes back as a tool error the caller can read, never as a crash."""
        try:
            return fn(*args)
        except t.ToolInputError as exc:
            raise ToolError(str(exc)) from None

    def view(match_id: str):
        if not MATCH_ID.match(match_id):
            raise t.ToolInputError("match_id must look like m00026")
        try:
            return source.view(match_id)
        except KeyError:
            raise t.ToolInputError(f"no live match {match_id!r}; call list_matches") from None

    def check(pattern: re.Pattern[str], value: str, name: str) -> str:
        if not pattern.match(value):
            raise t.ToolInputError(f"{name} {value!r} is not valid")
        return value

    @server.tool(annotations=READ_ONLY)
    def list_matches() -> dict[str, Any]:
        """Matches being served now, with their clubs and current score."""
        out = []
        for mid in source.match_ids():
            v = source.view(mid)
            out.append({"match_id": mid, "score": t.catch_up(v)["score"], "as_of": t.as_of(v)})
        return {"matches": out}

    @server.tool(annotations=READ_ONLY)
    def key_moments(match_id: str, limit: int = 10) -> dict[str, Any]:
        """The latest key moments (goals, saves, cards, penalties, substitutions, VAR checks), newest last."""
        return guard(lambda: t.key_moments(view(match_id), limit))

    @server.tool(annotations=READ_ONLY)
    def team_window(match_id: str, team_id: str, minutes: int = 10) -> dict[str, Any]:
        """Press height, tempo, directness, width, line height, possession share and shots for one club,
        over the last `minutes` and the `minutes` before."""
        return guard(lambda: t.team_window(view(match_id), check(TEAM_ID, team_id, "team_id"), minutes))

    @server.tool(annotations=READ_ONLY)
    def player_report(match_id: str, player_id: str) -> dict[str, Any]:
        """One player's match so far: touches, passing accuracy, carries, shots and shot speed, cards."""
        return guard(lambda: t.player_report(view(match_id), check(PLAYER_ID, player_id, "player_id")))

    @server.tool(annotations=READ_ONLY)
    def catch_up(match_id: str) -> dict[str, Any]:
        """Score, minute and the last five key moments."""
        return guard(lambda: t.catch_up(view(match_id)))

    @server.tool(annotations=READ_ONLY)
    def recap(match_id: str) -> dict[str, Any]:
        """Code statistics for a recap: score, scorers, cards and team totals so far."""
        return guard(lambda: t.recap(view(match_id)))

    @server.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "matches": len(source.match_ids())})

    return server


class RateLimited:
    """ASGI middleware: judge codes get 60 calls a minute, anonymous IPs 10. Health checks are free."""

    def __init__(self, app, codes: JudgeCodes) -> None:
        self.app, self.codes = app, codes
        self.judge = SlidingLimit(JUDGE_PER_MINUTE)
        self.anon = SlidingLimit(ANON_PER_MINUTE)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        code = headers.get("x-earshot-code")
        if self.codes.valid(code):
            limit, key = self.judge, "code:" + (code or "")
        else:
            peer = scope.get("client")
            limit, key = self.anon, "ip:" + client_ip(headers, peer[0] if peer else None)
        if not limit.allow(key):
            body = json.dumps({"error": "rate limit", "per_minute": limit.per_minute}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(limit.retry_after_s(key)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_app(source: MatchSource | None = None, codes: JudgeCodes | None = None):
    server = build_server(source or source_from_env())
    inner = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    codes_path = os.environ.get("EARSHOT_JUDGE_CODES_FILE")
    return RateLimited(inner, codes or JudgeCodes(Path(codes_path) if codes_path else None))


def __getattr__(name: str):
    # ``uvicorn earshot.api.mcp:app`` builds the app on first access, from the environment.
    if name == "app":
        return build_app()
    raise AttributeError(name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
