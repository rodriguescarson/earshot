"""The deployed worker composition: both surfaces in one app, and spend gated for both."""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from earshot.api.flags import FileFlags, Flags, SpendMeter
from earshot.api.server import ANALYST_CALL_TOKENS, GatedAnalyst, MeteredSpeechBudget


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


def test_no_listeners_no_analyst_call(tmp_path):
    inner, m = Inner(), meter(tmp_path)
    live = SimpleNamespace(match=SimpleNamespace(groups={}))
    res = GatedAnalyst(inner, live, m).run(None, "w", 20_000)
    assert inner.calls == 0
    assert res.claims == []
    assert "no listeners" in res.error


def test_calls_are_metered_and_stop_at_the_cap(tmp_path):
    inner, m = Inner(), meter(tmp_path, token_cap=ANALYST_CALL_TOKENS + 3_000)
    live = SimpleNamespace(match=SimpleNamespace(groups={"g": object()}))
    gate = GatedAnalyst(inner, live, m)
    assert gate.run(None, "w1", 20_000).claims == ["c"]
    assert m.tokens == 3_000
    assert gate.run(None, "w2", 20_000).claims == ["c"]
    assert m.tokens == 6_000
    res = gate.run(None, "w3", 20_000)  # 6,000 used + 6,000 reserved > the 9,000 cap
    assert res.claims == []
    assert inner.calls == 2


def test_cached_only_stops_the_analyst(tmp_path):
    inner, m = Inner(), meter(tmp_path)
    m.flags.set_cached_only(True, "test")
    live = SimpleNamespace(match=SimpleNamespace(groups={"g": object()}))
    assert GatedAnalyst(inner, live, m).run(None, "w", 20_000).claims == []
    assert inner.calls == 0


def test_speech_budget_is_daily_across_matches(tmp_path):
    m = meter(tmp_path, char_cap=100)
    first, second = MeteredSpeechBudget(m), MeteredSpeechBudget(m)  # two matches, one meter
    assert first.allow(60)
    assert not second.allow(60)
    assert second.allow(40)
    assert m.flags.cached_only  # the cap was reached: cached-only engaged, no restart


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("earshot.live") is None, reason="no live app")
def test_one_app_serves_both_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("EARSHOT_ADMIN_DIR", str(tmp_path / "admin"))
    monkeypatch.setenv("EARSHOT_MATCH_DIR", str(tmp_path / "matches"))
    monkeypatch.delenv("EARSHOT_STORAGE_URL", raising=False)
    monkeypatch.setenv("EARSHOT_AI_ENDPOINT", "https://example.invalid")  # the live app reads it at import
    from earshot.api.server import build_app

    with TestClient(build_app()) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/status").json()["cached_only"] is False
        assert c.get("/api/live").status_code in (200, 503)  # 503 until a stored match kicks off
        assert "strict-transport-security" in c.get("/api/live").headers
