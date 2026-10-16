"""Load-test instrumentation: timings recorded around the live broadcaster, nothing else changed."""

import asyncio
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from earshot.api.livemetrics import LiveMetrics, instrumented, metrics_endpoint


class Base:
    def __init__(self):
        self.offered, self.published = [], []

    def match_ms(self) -> int:
        return 10_500

    async def _offer(self, g, line):
        await asyncio.sleep(0.01)
        self.offered.append(line)

    async def _publish_line(self, g, start):
        self.published.append(start)


def test_timings_are_recorded_and_behaviour_is_unchanged():
    m = LiveMetrics()
    Timed = instrumented(Base, m)
    match = Timed()
    line = SimpleNamespace(
        wall_ms=10_000, line_id="L1", source=SimpleNamespace(value="caller"), never_drop=True
    )
    g = SimpleNamespace(key=SimpleNamespace(name="m00026.en.casual.app_voice.-.-.-"))
    asyncio.run(match._offer(g, line))
    asyncio.run(match._publish_line(g, SimpleNamespace(line=line)))
    assert match.offered == [line]
    assert len(match.published) == 1
    assert m.ready_ms[0] >= 10
    rec = m.lines[0]
    assert rec["start_ms"] == 500
    assert rec["id"] == "L1"
    assert rec["never_drop"] is True
    assert set(rec) == {"group", "id", "source", "never_drop", "start_ms", "publish_ms", "sent_ms"}


def test_metrics_need_a_judge_code():
    m = LiveMetrics()
    codes = SimpleNamespace(valid=lambda c: c == "panel-7f3")
    app = Starlette(routes=[Route("/api/metrics", metrics_endpoint(codes, m))])  # type: ignore[arg-type]
    with TestClient(app) as c:
        assert c.get("/api/metrics").status_code == 403
        r = c.get("/api/metrics", headers={"x-earshot-code": "panel-7f3"})
        assert r.status_code == 200
        assert set(r.json()) == {"started_at_ms", "now_ms", "ready_ms", "analyst_ms", "lines"}
        assert c.get("/api/metrics?since_ms=x", headers={"x-earshot-code": "panel-7f3"}).status_code == 400
