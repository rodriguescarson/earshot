"""The deployed worker composition: both surfaces in one app, and spend gated for both."""

import importlib.util
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from earshot.api.flags import FileFlags, Flags, SpendMeter
from earshot.api.server import ANALYST_CALL_TOKENS, MeteredAnalyst, MeteredSpeechBudget

HAS_LIVE = importlib.util.find_spec("earshot.live") is not None


def meter(tmp_path, **kw) -> SpendMeter:
    flags = Flags(FileFlags(tmp_path / "flags.json"))
    flags.refresh()
    return SpendMeter(flags, **kw)


class Inner:
    def __init__(self):
        self.calls = 0

    def run(self, view, window_id, expected_dead_ms):
        self.calls += 1
        return SimpleNamespace(
            claims=["c"], timed_out=False, error=None, invalid=[], latency_ms=900, tokens=3_000
        )


@pytest.mark.skipif(not HAS_LIVE, reason="no agents")
def test_calls_are_metered_and_stop_at_the_cap(tmp_path):
    inner, m = Inner(), meter(tmp_path, token_cap=ANALYST_CALL_TOKENS + 3_000)
    gate = MeteredAnalyst(inner, m)
    assert gate.run(None, "w1", 20_000).claims == ["c"]
    assert m.tokens == 3_000
    assert gate.run(None, "w2", 20_000).claims == ["c"]
    assert m.tokens == 6_000
    res = gate.run(None, "w3", 20_000)  # 6,000 used + 6,000 reserved > the 9,000 cap
    assert res.claims == []
    assert res.window_id == "w3"
    assert inner.calls == 2


@pytest.mark.skipif(not HAS_LIVE, reason="no agents")
def test_cached_only_stops_the_analyst(tmp_path):
    inner, m = Inner(), meter(tmp_path)
    m.flags.set_cached_only(True, "test")
    assert MeteredAnalyst(inner, m).run(None, "w", 20_000).claims == []
    assert inner.calls == 0


def test_speech_budget_is_daily_across_matches(tmp_path):
    m = meter(tmp_path, char_cap=100)
    first, second = MeteredSpeechBudget(m, 1_000), MeteredSpeechBudget(m, 1_000)  # two matches, one meter
    assert first.allow(60)
    assert not second.allow(60)
    assert second.allow(40)
    assert m.flags.cached_only  # the cap was reached: cached-only engaged, no restart


def test_speech_budget_keeps_the_live_apps_lower_limit(tmp_path):
    m = meter(tmp_path)  # the meter allows 400K a day; the live app asks for less
    b = MeteredSpeechBudget(m, 100)
    assert b.allow(100)
    assert not b.allow(1)
    assert m.chars == 100
    assert not m.flags.cached_only  # the live limit only stops speech; the meter's cap is not reached


@pytest.mark.skipif(not HAS_LIVE, reason="no live app")
def test_one_app_serves_both_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("EARSHOT_ADMIN_DIR", str(tmp_path / "admin"))
    monkeypatch.setenv("EARSHOT_MATCH_DIR", str(tmp_path / "matches"))
    monkeypatch.delenv("EARSHOT_STORAGE_URL", raising=False)
    monkeypatch.setenv("EARSHOT_AI_ENDPOINT", "https://example.invalid")  # the live app reads it at import
    from earshot.api.server import build_app

    app = build_app()
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/status").json()["cached_only"] is False
        assert c.get("/api/live").status_code in (200, 503)  # 503 until a stored match kicks off
        assert "strict-transport-security" in c.get("/api/live").headers
        # The SSE stream is behind the per-IP guard, and judges have a ticket endpoint.
        assert c.post("/api/ticket").status_code == 403
        assert c.get("/api/metrics").status_code == 403
        from earshot.live import app as live_app

        assert live_app.LiveMatch.__name__ == "TimedLiveMatch"  # the load test's timings, nothing else
        c.get("/api/stream?group=m00026.en.casual.app_voice.-.-.-")
        assert app.state.stream_guard.total == 0  # whatever the live app answered, the slot was freed
        # The kill-switch flag reaches the live app's own gate, with no restart.
        assert app.state.live.cached_only() is False
        app.state.flags.set_cached_only(True, "drill")
        assert app.state.live.cached_only() is True
