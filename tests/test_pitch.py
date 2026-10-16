import pytest
from hypothesis import given
from hypothesis import strategies as st

from earshot.pitch import (
    PAN_BUCKETS,
    PITCH_LENGTH,
    PITCH_WIDTH,
    Channel,
    Third,
    attacks_right,
    channel,
    pan,
    pan_bucket,
    speech_pan,
    third,
    to_attacking,
)

xs = st.floats(0, PITCH_LENGTH, allow_nan=False)
ys = st.floats(0, PITCH_WIDTH, allow_nan=False)


def test_pan_endpoints():
    assert pan(0) == -1.0
    assert pan(PITCH_LENGTH / 2) == pytest.approx(0.0)
    assert pan(PITCH_LENGTH) == 1.0


@given(xs)
def test_speech_pan_is_capped_and_bucketed(x):
    assert -0.6 <= speech_pan(x) <= 0.6
    assert pan_bucket(x) in PAN_BUCKETS


@given(xs, ys, st.booleans())
def test_attacking_frame_round_trips(x, y, right):
    ax, ay = to_attacking(x, y, right)
    bx, by = to_attacking(ax, ay, right)
    assert (bx, by) == pytest.approx((x, y))


def test_ends_switch_at_half_time():
    assert attacks_right(home=True, period_id=1)
    assert not attacks_right(home=True, period_id=2)
    assert not attacks_right(home=False, period_id=1)


def test_zones():
    assert third(10) is Third.DEFENSIVE
    assert third(60) is Third.MIDDLE
    assert third(100) is Third.ATTACKING
    assert channel(60) is Channel.LEFT
    assert channel(34) is Channel.CENTRE
    assert channel(5) is Channel.RIGHT
