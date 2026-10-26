"""Shared test setup: no live player-track threads (12 s of frame rendering per match) unless a
test turns them on itself."""

import pytest


@pytest.fixture(autouse=True)
def _no_live_tracks(monkeypatch):
    monkeypatch.setenv("EARSHOT_LIVE_TRACKS", "0")
