"""Worker guards: negotiate limits and token scope, the judge code, the daily spend cap, and the
cached-only drill (ET10: the flip lands without a restart)."""

import os
import time

import pytest
from starlette.testclient import TestClient

from earshot.api.flags import CHAR_CAP, FileFlags, Flags, SpendMeter
from earshot.api.negotiate import MINUTES_PER_IP_PER_DAY, SESSIONS_PER_IP, GroupKey, IpBudget, NegotiateError
from earshot.api.ratelimit import code_hash
from earshot.api.worker import build_app

BODY = {"match_id": "m00026", "language": "en", "listening_mode": "casual", "output_mode": "app_voice"}


class FakeIssuer:
    def __init__(self):
        self.calls = []

    def get_client_access_token(self, **kw):
        self.calls.append(kw)
        return {"url": "wss://example.webpubsub.azure.com/client/hubs/earshot?access_token=x", "token": "x"}


def app_with(tmp_path, judge_code: str | None = None, poll_s: float = 30.0):
    src = FileFlags(tmp_path / "flags.json")
    src.write({"judge_code_sha256": [code_hash(judge_code)] if judge_code else []})
    flags = Flags(src)
    flags.refresh()
    issuer = FakeIssuer()
    return build_app(flags, issuer, poll_s=poll_s), issuer, src


def test_tokens_are_receive_only_group_scoped_and_expire(tmp_path):
    app, issuer, _ = app_with(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/negotiate", json=BODY)
        assert r.status_code == 200
        assert r.json()["group"] == "m00026.en.casual.app_voice.-.-.-"
        assert r.json()["expires_in_s"] <= 3600
    kw = issuer.calls[0]
    assert kw["roles"] == ["webpubsub.joinLeaveGroup.m00026.en.casual.app_voice.-.-.-"]
    assert not any("send" in role.lower() for role in kw["roles"])
    assert kw["groups"] == ["m00026.en.casual.app_voice.-.-.-"]
    assert kw["minutes_to_expire"] <= 60


def test_anonymous_ip_limits_and_the_judge_bypass(tmp_path):
    app, _, _ = app_with(tmp_path, judge_code="panel-7f3")
    with TestClient(app) as c:
        codes = [c.post("/api/negotiate", json=BODY).status_code for _ in range(SESSIONS_PER_IP + 1)]
        assert codes == [200] * SESSIONS_PER_IP + [429]
        busy = c.post("/api/negotiate", json=BODY).json()
        assert "recorded clip" in busy["error"]
        judge = {"x-earshot-code": "panel-7f3"}
        assert all(c.post("/api/negotiate", json=BODY, headers=judge).status_code == 200 for _ in range(10))
        assert c.post("/api/negotiate", json=BODY, headers={"x-earshot-code": "wrong"}).status_code == 429


def test_daily_minutes_per_ip():
    clock = [0.0]
    b = IpBudget(clock=lambda: clock[0])
    granted = []
    for _ in range(5):
        clock[0] += 3601  # each earlier session has expired, so only the daily minutes bind
        try:
            granted.append(b.grant("1.2.3.4"))
        except NegotiateError:
            break
    assert sum(granted) == MINUTES_PER_IP_PER_DAY
    assert granted == [60, 60, 30]


def test_bad_requests_are_rejected_before_any_token(tmp_path):
    app, issuer, _ = app_with(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/negotiate", json={**BODY, "match_id": "../x"}).status_code == 400
        assert c.post("/api/negotiate", json={**BODY, "language": "fr"}).status_code == 400
        assert (
            c.post("/api/negotiate", json={**BODY, "stat": "danger"}).status_code == 400
        )  # stat needs Analyst
        assert c.post("/api/negotiate", content=b"not json").status_code == 400
    assert issuer.calls == []


def test_group_key_includes_every_personal_choice():
    key = GroupKey.parse(
        {**BODY, "listening_mode": "analyst", "club": "kes", "player": "kes-09", "stat": "danger"}
    )
    assert key.name == "m00026.en.analyst.app_voice.kes.kes-09.danger"


def test_spend_cap_trips_cached_only_and_survives_a_restart(tmp_path):
    src = FileFlags(tmp_path / "flags.json")
    flags = Flags(src)
    flags.refresh()
    meter = SpendMeter(flags, char_cap=1_000)
    assert meter.allow(chars=500)
    meter.add(chars=999)
    assert not flags.cached_only
    meter.add(chars=1)
    assert flags.cached_only
    assert "daily spend cap" in (flags.reason or "")
    assert not meter.allow(tokens=1)
    meter.persist()
    again = Flags(src)
    again.refresh()
    assert SpendMeter(again, char_cap=1_000).chars == 1_000
    assert CHAR_CAP == 400_000


def test_drill_cached_only_flip_lands_without_a_restart(tmp_path):
    """ET10 drill: flip the flag file mid-session; within one poll the same process (no restart, no new
    revision) refuses new live sessions with the notice, and flipping back restores them."""
    app, _, src = app_with(tmp_path, poll_s=0.05)
    with TestClient(app) as c:
        pid = os.getpid()
        assert c.post("/api/negotiate", json=BODY).status_code == 200
        assert c.get("/api/status").json()["cached_only"] is False
        src.write({**src.read(), "cached_only": True, "reason": "drill"})
        deadline = time.time() + 2
        while not c.get("/api/status").json()["cached_only"] and time.time() < deadline:
            time.sleep(0.05)
        status = c.get("/api/status").json()
        assert status["cached_only"] is True
        assert "Recorded clips still work" in status["notice"]
        r = c.post("/api/negotiate", json=BODY)
        assert r.status_code == 503
        assert r.json()["cached_only"] is True
        src.write({**src.read(), "cached_only": False, "reason": "restored by hand"})
        deadline = time.time() + 2
        while c.get("/api/status").json()["cached_only"] and time.time() < deadline:
            time.sleep(0.05)
        assert c.post("/api/negotiate", json=BODY).status_code == 200
        assert os.getpid() == pid


def test_security_headers_and_health(tmp_path):
    app, _, _ = app_with(tmp_path)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        expected = ("strict-transport-security", "x-content-type-options", "referrer-policy",
                    "permissions-policy", "content-security-policy")  # fmt: skip
        for h in expected:
            assert h in r.headers
        assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("path", ["/api/negotiate"])
def test_get_is_not_allowed_on_negotiate(tmp_path, path):
    app, _, _ = app_with(tmp_path)
    with TestClient(app) as c:
        assert c.get(path).status_code == 405
