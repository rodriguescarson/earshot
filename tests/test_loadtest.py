"""The load test's per-line join: server timings plus listener timings, one line at a time."""

from earshot.eval.loadtest import Listener, groups_for, summarize

G = "m00026.en.casual.app_voice.-.-.-"


def test_event_to_first_audio_byte_adds_server_and_listener_parts():
    lst = Listener(G)
    lst.received = [
        {"id": "L1", "recv_ms": 1_000_120, "audio": "/audio/x.m4a", "ttfb_ms": 80},
        {"id": "L2", "recv_ms": 1_000_500, "audio": None},  # screen-reader style: no audio, not counted
        {"id": "L9", "recv_ms": 1_000_600, "audio": "/audio/y.m4a", "ttfb_ms": 50},  # not in the server log
    ]
    server = {
        "ready_ms": [300, 900],
        "analyst_ms": [2_400],
        "lines": [
            {"group": G, "id": "L1", "source": "caller", "never_drop": True, "start_ms": 600,
             "publish_ms": 40, "sent_ms": 1_000_000},
            {"group": G, "id": "L2", "source": "caller", "never_drop": False, "start_ms": 700,
             "publish_ms": 5, "sent_ms": 1_000_400},
        ],
    }  # fmt: skip
    s = summarize("https://w", {"match_id": "m00026"}, 1, 1.0, [lst], server)
    e2e = s["event_to_first_audio_byte_ms"]
    assert e2e["n"] == 1
    assert e2e["p95"] == 600 + 40 + 120 + 80
    assert s["never_drop_event_to_first_audio_byte_ms"]["n"] == 1
    assert s["p95_under_2s"] is True
    assert s["unmatched_lines"] == 1
    assert s["latency_model"] == {
        "ready_ms": [300, 900],
        "analyst_ms": [2_400],
        "delivery_ms": [40 + 120 + 80],
    }


def test_listeners_cover_every_profile():
    match = {"match_id": "m00026", "home": {"team_id": "spa"}}
    groups = groups_for(match, "spa-09")
    assert len(groups) == 5
    assert "m00026.en.analyst.app_voice.spa.spa-09.-" in groups
    assert "m00026.en.casual.screen_reader.-.-.-" in groups
