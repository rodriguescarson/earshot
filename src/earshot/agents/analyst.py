"""Analyst: during a dead ball, asks a model WHY the game is changing, through read-only match tools.

The model may only call the match tools (bound in-process: the MCP server exposes the same
functions) and return claims in a strict JSON schema. It never writes a sentence. Every claim is
then verified by recomputation (``claims.verify``) before code voices it.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from openai import OpenAI, RateLimitError
from pydantic import ValidationError

from earshot.contract import AnalystResponse, CauseLabel, Claim, Metric
from earshot.tools import match as tools

SCOPE = "https://cognitiveservices.azure.com/.default"
MAX_TURNS = 3
MIN_TIMEOUT_S, MAX_TIMEOUT_S, RESTART_MARGIN_S = 3.0, 8.0, 4.0

SYSTEM = """You are the Analyst for Earshot: live audio description of football for blind and partially sighted fans.
The ball is dead for a few seconds. Explain how the game is changing, using only the match tools.

Rules:
- Call metric_table first. Call metric_change or recent_events only if you need more.
- Return claims only for metrics where the tool says notable=true. At most 3 per team, rank 1 is the biggest change relative to its threshold.
- Copy window_start_ms and window_end_ms exactly from the tool output. Copy before and after into value_before and value_after.
- cited_event_ids: 1 to 8 ids copied from evidence_event_ids or recent_events. Never invent an id.
- cause: one label only when the match context supports it, otherwise null.
  CHASING_GAME: behind and pushing forward. PROTECTING_LEAD: ahead and dropping deeper or slowing down.
  RED_CARD_REORG: the team had a red card in the last 15 minutes. PRESS_HIGHER, DROP_DEEPER, GO_DIRECT, RAISE_TEMPO,
  SLOW_TEMPO, WIDEN, NARROW: a deliberate change matching that metric. PARK_THE_BUS: deep, slow and narrow together.
  ALL_OUT_ATTACK: high, fast and direct together.
- If nothing is notable, return an empty claims list.
You never write sentences. Code turns your claims into speech and checks every number."""


def _tool_specs(home_id: str, away_id: str) -> list[dict]:
    team = {"type": "string", "enum": [home_id, away_id]}
    minutes = {"type": "integer", "enum": list(tools.WINDOW_MINUTES)}
    return [
        _fn("match_context", "Score, minute, red cards and the last goal.", {}),
        _fn(
            "metric_table",
            "All seven metrics for both teams: this window vs the one before.",
            {"minutes": minutes},
        ),
        _fn(
            "metric_change",
            "One metric for one team: this window vs the one before.",
            {
                "team_id": team,
                "metric": {"type": "string", "enum": [m.value for m in Metric]},
                "minutes": minutes,
            },
        ),
        _fn(
            "recent_events",
            "Recent events (ids, types, minutes), optionally for one team.",
            {"team_id": {"anyOf": [team, {"type": "null"}]}, "minutes": minutes},
        ),
    ]


def _fn(name: str, description: str, props: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        },
    }


def response_schema(home_id: str, away_id: str) -> dict:
    claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "team_id": {"type": "string", "enum": [home_id, away_id]},
            "metric": {"type": "string", "enum": [m.value for m in Metric]},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "window_start_ms": {"type": "integer"},
            "window_end_ms": {"type": "integer"},
            "value_before": {"type": ["number", "null"]},
            "value_after": {"type": ["number", "null"]},
            "cause": {"anyOf": [{"type": "string", "enum": [c.value for c in CauseLabel]}, {"type": "null"}]},
            "cited_event_ids": {"type": "array", "items": {"type": "string"}},
            "rank": {"type": "integer", "enum": [1, 2, 3]},
        },
    }
    claim["required"] = list(claim["properties"])
    return {
        "name": "analyst_claims",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"claims": {"type": "array", "items": claim}},
            "required": ["claims"],
        },
    }


def timeout_for(expected_dead_ms: int) -> float:
    """The real dead time is unknown at the start: aim to finish 4 s before the expected restart."""
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, expected_dead_ms / 1000 - RESTART_MARGIN_S))


@dataclass
class AnalystResult:
    window_id: str
    claims: list[Claim] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    """Claims the schema let through but the contract rejected (logged, never aired)."""
    timed_out: bool = False
    error: str | None = None
    latency_ms: int = 0
    tool_calls: list[str] = field(default_factory=list)
    tokens: int = 0
    cached: bool = False
    waited_ms: int = 0
    """Time spent waiting out rate limits in patient (offline) mode; not part of the latency."""


class Analyst:
    def __init__(
        self,
        cache_dir: Path | None = None,
        deployment: str | None = None,
        endpoint: str | None = None,
        *,
        patient: bool = False,
    ) -> None:
        """``patient`` (offline renders and evaluation): wait out rate limits instead of failing."""
        self.patient = patient
        self.deployment = deployment or os.environ.get("EARSHOT_FAST_DEPLOYMENT", "fast")
        self.endpoint = (endpoint or os.environ["EARSHOT_AI_ENDPOINT"]).rstrip("/")
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self._cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._token: tuple[str, float] | None = None

    def _bearer(self) -> str:
        """Cached Entra token: fetching one can take seconds, which the dead-ball budget cannot spare."""
        if self._token is None or self._token[1] - time.time() < 120:
            tok = self._cred.get_token(SCOPE)
            self._token = (tok.token, tok.expires_on)
        return self._token[0]

    def warm(self) -> None:
        """Fetch the token before the first window (call at match start)."""
        self._bearer()

    def _client(self, timeout: float) -> OpenAI:
        return OpenAI(
            base_url=f"{self.endpoint}/openai/v1/", api_key=self._bearer(), timeout=timeout, max_retries=0
        )

    def _call(self, request: dict, timeout: float, res: AnalystResult) -> tuple[dict, bool]:
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:40]
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            return json.loads(path.read_text()), True
        for attempt in range(8):
            try:
                r = self._client(timeout).chat.completions.create(**request)
                break
            except RateLimitError as e:
                if not self.patient or attempt == 7:
                    raise
                after = e.response.headers.get("retry-after") if e.response is not None else None
                wait = min(60.0, float(after) if after else 10.0)
                time.sleep(wait)
                res.waited_ms += int(wait * 1000)
        else:  # pragma: no cover - the loop either breaks or raises
            raise RuntimeError("unreachable")
        out = {
            "message": r.choices[0].message.model_dump(exclude_none=True),
            "tokens": r.usage.total_tokens if r.usage else 0,
        }
        if path:
            path.write_text(json.dumps(out))
        return out, False

    def run(self, view: tools.MatchView, window_id: str, expected_dead_ms: int) -> AnalystResult:
        res = AnalystResult(window_id)
        budget = timeout_for(expected_dead_ms)
        t0 = time.perf_counter()
        ctx = tools.match_context(view)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"window_id": window_id, "now_wall_ms": view.now_wall_ms, **ctx}),
            },
        ]
        specs = _tool_specs(view.teams.home_id, view.teams.away_id)
        schema = response_schema(view.teams.home_id, view.teams.away_id)
        all_cached = True
        try:
            for turn in range(MAX_TURNS):
                remaining = budget - (time.perf_counter() - t0 - res.waited_ms / 1000)
                if remaining <= 0.3:
                    res.timed_out = True
                    break
                request = {
                    "model": self.deployment,
                    "messages": messages,
                    "tools": specs,
                    "tool_choice": "auto" if turn < MAX_TURNS - 1 else "none",
                    "response_format": {"type": "json_schema", "json_schema": schema},
                    "max_completion_tokens": 700,
                }
                out, cached = self._call(request, remaining, res)
                all_cached &= cached
                res.tokens += out["tokens"]
                msg = out["message"]
                calls = msg.get("tool_calls") or []
                if calls:
                    messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
                    for call in calls:
                        name, args = (
                            call["function"]["name"],
                            json.loads(call["function"]["arguments"] or "{}"),
                        )
                        res.tool_calls.append(name)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps(_dispatch(view, name, args)),
                            }
                        )
                    continue
                self._parse(msg.get("content") or "", res)
                break
            else:
                res.timed_out = True
        except Exception as e:  # network, quota, kill switch: the Director falls back to a code line
            res.error = f"{type(e).__name__}: {e}"[:300]
        res.latency_ms = int((time.perf_counter() - t0) * 1000) - res.waited_ms
        res.cached = all_cached
        if not res.cached and res.latency_ms > budget * 1000:
            res.timed_out = True
        return res

    @staticmethod
    def _parse(content: str, res: AnalystResult) -> None:
        _parse(content, res)


def _parse(content: str, res: AnalystResult) -> None:
    """Keep the claims the contract accepts; log the rest. Never more than six per window."""
    raw = json.loads(content)
    for c in raw.get("claims", []):
        try:
            res.claims.append(Claim(**c))
        except ValidationError as e:
            res.invalid.append(f"{c.get('metric')}/{c.get('team_id')}: {e.errors()[0]['msg']}")
    res.claims = AnalystResponse(window_id=res.window_id, claims=res.claims[:6]).claims


def _dispatch(view: tools.MatchView, name: str, args: dict) -> Any:
    try:
        if name == "match_context":
            return tools.match_context(view)
        if name == "metric_table":
            return tools.metric_table(view, args.get("minutes", 5))
        if name == "metric_change":
            return tools.metric_change(view, args["team_id"], Metric(args["metric"]), args.get("minutes", 5))
        if name == "recent_events":
            return tools.recent_events(view, args.get("team_id"), args.get("minutes", 5))
        return {"error": f"unknown tool {name}"}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
