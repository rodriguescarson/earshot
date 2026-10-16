"""Web PubSub negotiate (eng E-1b, design "Cost and abuse controls", R3-12).

A listener's phone asks for a token to join its listening group. Before any token is issued:
- **cached-only mode** answers 503 with the notice (recorded clips still play from the static site);
- **anonymous callers** get at most 3 live sessions at once and 150 live minutes a UTC day per IP;
- **judges** (header ``X-Earshot-Code``, read by the client from the ``#code=`` link fragment,
  DR-6) skip the per-IP limits, because a judging panel may share one corporate IP; the global
  Analyst and speech caps still apply to them, and codes rotate without a redeploy.

Tokens are **receive-only** (the single role ``webpubsub.joinLeaveGroup.<group>``, no send role),
scoped to one group, and expire after at most 60 minutes. The worker asks the service for them
with its managed identity: Web PubSub has local auth disabled, so no access key exists.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from earshot.api.flags import CACHED_ONLY_NOTICE, Flags, utc_day
from earshot.api.ratelimit import JudgeCodes, SlidingLimit
from earshot.contract.line import FocusStat, Language, ListeningMode, OutputMode

TOKEN_MINUTES = 60
SESSIONS_PER_IP = 3
MINUTES_PER_IP_PER_DAY = 150
JUDGE_NEGOTIATIONS_PER_MINUTE = 60
MATCH_ID = re.compile(r"^m\d{5}$")
TEAM_ID = re.compile(r"^[a-z]{3}$")
PLAYER_ID = re.compile(r"^[a-z]{3}-\d{2}$")


class TokenIssuer(Protocol):
    def get_client_access_token(self, **kwargs: Any) -> dict[str, Any]: ...


class NegotiateError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


@dataclass(frozen=True)
class GroupKey:
    match_id: str
    language: Language
    listening_mode: ListeningMode
    output_mode: OutputMode
    club: str | None = None
    player: str | None = None
    stat: FocusStat | None = None

    @property
    def name(self) -> str:
        """The Web PubSub group the Director publishes to (design "Runtime split")."""
        parts = [
            self.match_id,
            self.language.value,
            self.listening_mode.value,
            self.output_mode.value,
            self.club or "-",
            self.player or "-",
            self.stat.value if self.stat else "-",
        ]
        return ".".join(parts)

    @classmethod
    def parse(cls, body: dict[str, Any]) -> GroupKey:
        try:
            match_id = str(body["match_id"])
            key = cls(
                match_id=match_id,
                language=Language(body.get("language", "en")),
                listening_mode=ListeningMode(body.get("listening_mode", "casual")),
                output_mode=OutputMode(body.get("output_mode", "app_voice")),
                club=body.get("club") or None,
                player=body.get("player") or None,
                stat=FocusStat(body["stat"]) if body.get("stat") else None,
            )
        except (KeyError, ValueError) as exc:
            raise NegotiateError(400, f"bad request: {exc}") from None
        if not MATCH_ID.match(match_id):
            raise NegotiateError(400, "match_id must look like m00026")
        if key.club and not TEAM_ID.match(key.club):
            raise NegotiateError(400, "club must be a team id")
        if key.player and not PLAYER_ID.match(key.player):
            raise NegotiateError(400, "player must be a player id")
        if key.stat and key.listening_mode is not ListeningMode.ANALYST:
            raise NegotiateError(400, "a followed stat needs Analyst mode")
        return key


@dataclass
class IpBudget:
    """Live sessions per IP: started-at times of tokens still alive, and minutes granted today."""

    clock: Callable[[], float] = time.time
    live: dict[str, deque[float]] = field(default_factory=dict)
    minutes: dict[tuple[str, str], int] = field(default_factory=dict)

    def grant(self, ip: str) -> int:
        """Minutes for a new token, or raise if this IP is out of sessions or minutes."""
        now = self.clock()
        q = self.live.setdefault(ip, deque())
        while q and now - q[0] >= TOKEN_MINUTES * 60:
            q.popleft()
        if len(q) >= SESSIONS_PER_IP:
            raise NegotiateError(
                429, "Earshot is busy. Try again in a few minutes, or play a recorded clip.", limit="sessions"
            )
        day_key = (ip, utc_day(now))
        used = self.minutes.get(day_key, 0)
        left = MINUTES_PER_IP_PER_DAY - used
        if left <= 0:
            raise NegotiateError(
                429, "Earshot is busy. Try again in a few minutes, or play a recorded clip.", limit="minutes"
            )
        granted = min(TOKEN_MINUTES, left)
        q.append(now)
        self.minutes[day_key] = used + granted
        # IPs live only as long as a limit needs them (SEC6): drop yesterday's counters.
        for k in [k for k in self.minutes if k[1] != day_key[1]]:
            del self.minutes[k]
        return granted


@dataclass
class Negotiator:
    issuer: TokenIssuer
    flags: Flags
    codes: JudgeCodes
    budget: IpBudget = field(default_factory=IpBudget)
    judge_limit: SlidingLimit = field(default_factory=lambda: SlidingLimit(JUDGE_NEGOTIATIONS_PER_MINUTE))

    def negotiate(self, body: dict[str, Any], ip: str, code: str | None) -> dict[str, Any]:
        key = GroupKey.parse(body)
        if self.flags.cached_only:
            raise NegotiateError(503, CACHED_ONLY_NOTICE, cached_only=True)
        if self.codes.valid(code):
            if not self.judge_limit.allow(code or ""):
                raise NegotiateError(429, "too many requests for this access code", limit="judge")
            minutes, judge = TOKEN_MINUTES, True
        else:
            minutes, judge = self.budget.grant(ip), False
        group = key.name
        token = self.issuer.get_client_access_token(
            roles=[f"webpubsub.joinLeaveGroup.{group}"], groups=[group], minutes_to_expire=minutes
        )
        return {"url": token["url"], "group": group, "expires_in_s": minutes * 60, "judge": judge}
