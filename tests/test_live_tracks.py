"""Live player tracks: positions at the nearest frame, -1 while off the pitch, metadata once."""

import numpy as np

from earshot.live.broadcast import LiveTracks, tracks_from_npz


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


def test_tracks_load_from_the_frames_file_without_the_simulator(tmp_path):
    path = tmp_path / "m00026.frames.npz"
    arrays = {
        "t_ms": np.array([0, 100]),
        "period": np.array([1, 1]),
        "h-1": np.array([[1.0, 2.0], [3.0, 4.0]]),
        "a-9": np.array([[np.nan, np.nan], [9.0, 9.0]]),
    }
    np.savez_compressed(path, allow_pickle=False, **arrays)
    tr = tracks_from_npz(path, "h", {"h-1": "One"})
    assert tr.ids == ["h-1", "a-9"]
    assert tr.home == [True, False]
    assert tr.names == ["One", "a-9"]
    assert tr.at(0) == [1.0, 2.0, -1.0, -1.0]
    assert tr.at(150) == [3.0, 4.0, 9.0, 9.0]
