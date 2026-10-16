from collections import Counter

from earshot.world.clubs import SQUAD_SHAPE, fixture_for_seed, season_fixtures, world
from earshot.world.names import banned_words_in


def test_twenty_clubs_with_full_squads():
    w = world()
    assert len(w.clubs) == 20
    for c in w.clubs:
        assert Counter(p.position for p in c.players) == dict(SQUAD_SHAPE)
        assert len({p.shirt for p in c.players}) == 25


def test_ids_and_names_are_unique():
    w = world()
    ids = [p.player_id for c in w.clubs for p in c.players]
    assert len(ids) == len(set(ids)) == 500
    people = [p.name.full for c in w.clubs for p in c.players] + [c.coach.full for c in w.clubs]
    people += [r.full for r in w.referees]
    assert len(people) == len(set(people))
    assert len({c.name for c in w.clubs}) == 20


def test_no_banned_words_in_club_or_stadium_names():
    for c in world().clubs:
        assert banned_words_in(c.name) == [], c.name
        assert banned_words_in(c.stadium) == [], c.stadium


def test_banned_word_rule_catches_real_club_patterns():
    assert banned_words_in("North London Rovers")
    assert banned_words_in("Kestrel City")
    assert banned_words_in("Ashbridge")  # place-name ending
    assert not banned_words_in("Copperline")


def test_world_is_reproducible():
    from earshot.world import clubs

    a = clubs.world()
    clubs.world.cache_clear()
    b = clubs.world()
    assert a.roster() == b.roster()


def test_double_round_robin():
    fx = season_fixtures()
    assert len(fx) == 380
    assert {f.round for f in fx} == set(range(1, 39))
    home = Counter(f.home for f in fx)
    away = Counter(f.away for f in fx)
    assert set(home.values()) == {19}
    assert set(away.values()) == {19}
    pairs = Counter((f.home, f.away) for f in fx)
    assert len(pairs) == 380
    assert set(pairs.values()) == {1}
    for r in range(1, 39):
        teams = [t for f in fx if f.round == r for t in (f.home, f.away)]
        assert len(teams) == len(set(teams)) == 20


def test_seed_to_fixture_mapping():
    assert fixture_for_seed(1).index == 0
    assert fixture_for_seed(40).round == 4
    assert fixture_for_seed(9001).round == 27
    assert fixture_for_seed(9040).round == 30
