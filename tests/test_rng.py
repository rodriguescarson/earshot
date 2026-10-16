from earshot.rng import stream


def test_same_keys_same_numbers():
    assert (stream(2026, "match", 3, "fouls").random(5) == stream(2026, "match", 3, "fouls").random(5)).all()


def test_different_keys_differ():
    assert not (
        stream(2026, "match", 3, "fouls").random(5) == stream(2026, "match", 4, "fouls").random(5)
    ).all()
    assert not (
        stream(2026, "match", 3, "fouls").random(5) == stream(2026, "match", 3, "shots").random(5)
    ).all()
