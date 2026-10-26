"""Live player tracks: positions at the nearest frame, -1 while off the pitch, metadata once."""

import numpy as np

from earshot.live.broadcast import LiveTracks


def tracks() -> LiveTracks:
    t = np.array([0, 100, 200, 300])
    a = np.array([[10.0, 20.0], [11.0, 21.0], [12.0, 22.0], [13.0, 23.0]])
    sub = np.array([[np.nan, np.nan], [np.nan, np.nan], [50.0, 30.0], [51.0, 31.0]])
    return LiveTracks(ids=["h-1", "a-9"], home=[True, False], names=["One", "Nine"], t_ms=t, xy=[a, sub])


def test_positions_follow_the_nearest_frame_and_subs_are_minus_one_until_they_come_on():
    tr = tracks()
    assert tr.at(0) == [10.0, 20.0, -1.0, -1.0]
    assert tr.at(199) == [11.0, 21.0, -1.0, -1.0]
    assert tr.at(250) == [12.0, 22.0, 50.0, 30.0]
    assert tr.at(10_000) == [13.0, 23.0, 51.0, 31.0]
    assert tr.at(-5) == []


def test_meta_names_the_order_the_positions_use():
    assert tracks().meta() == {"ids": ["h-1", "a-9"], "home": [True, False], "names": ["One", "Nine"]}
