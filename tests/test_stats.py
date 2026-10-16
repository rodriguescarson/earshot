"""Stats: xT on a hand-computed toy grid, IRLS, the claim metrics on hand-built events, speeds."""

import numpy as np
import pytest

from earshot.contract.ball import BallSample
from earshot.contract.claim import Metric
from earshot.contract.events import Action, StoppageKind, StoppageStart
from earshot.stats import irls, metrics, passes, xt
from earshot.stats.danger import danger

HOME, AWAY = "h", "a"


def act(i, wall, team, type_="pass", x0=50.0, y0=34.0, x1=60.0, y1=34.0, result="success", player=None,
        near=5.0, flight=800):  # fmt: skip
    from earshot.contract.spadl import FLIGHT_TYPES, ActionType

    return Action.model_validate(
        {
            "event_id": f"e{i}", "match_id": "m", "period_id": 1, "clock_ms": wall, "wall_ms": wall,
            "team_id": team, "player_id": player or f"{team}-p", "type": type_, "result": result,
            "bodypart": "foot", "start_x": x0, "start_y": y0, "end_x": x1, "end_y": y1, "nearest_opp_m": near,
            "flight_ms": flight if ActionType(type_) in FLIGHT_TYPES else None,
        }
    )  # fmt: skip


def test_xt_toy_grid_matches_hand_computation():
    c = xt.Counts.empty(3, 2)
    # Cell 5 (last third, upper band): 10 shots, 3 goals, no moves -> xT = 0.3.
    c.shots[5], c.goals[5] = 10, 3
    # Cell 4: 5 shots (1 goal) and 5 moves, all reaching cell 5 -> 0.5*0.2 + 0.5*1.0*0.3 = 0.25.
    c.shots[4], c.goals[4], c.moves[4], c.trans[4, 5] = 5, 1, 5, 5
    # Cell 2: 4 moves, 2 reach cell 4 and 2 fail -> 1.0 * 0.5 * 0.25 = 0.125.
    c.moves[2], c.trans[2, 4] = 4, 2
    grid = xt.solve(c)
    assert grid[2, 1] == pytest.approx(0.3)
    assert grid[2, 0] == pytest.approx(0.25)
    assert grid[1, 0] == pytest.approx(0.125)
    assert grid[0].tolist() == [0.0, 0.0]


def test_xt_cell_indexing_and_frozen_model():
    assert xt.cell(0, 0) == 0
    assert xt.cell(104.9, 67.9) == xt.NX * xt.NY - 1
    m = xt.XTModel.load(xt.MODEL_PATH)
    assert m.nx == 12
    assert m.ny == 8
    assert m.value(100, 34) > m.value(10, 34)
    assert 0 <= m.danger(50, 34) <= 1


def test_irls_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(20_000, 2))
    beta = np.array([-0.5, 1.2, -0.8])
    p = 1 / (1 + np.exp(-(beta[0] + x @ beta[1:])))
    y = (rng.random(len(p)) < p).astype(float)
    got = irls.fit(x, y)
    assert np.allclose(got, beta, atol=0.08)
    assert irls.log_loss(y, irls.predict(got, x)) < irls.log_loss(y, np.full(len(y), y.mean()))
    assert irls.calibration_error(y, irls.predict(got, x)) < 0.02


def test_pass_difficulty_model_is_frozen_and_ordered():
    m = passes.DifficultyModel.load()
    assert list(m.deciles) == sorted(m.deciles)
    assert len(m.deciles) == 9
    easy = act(1, 0, HOME, x0=40, x1=48, near=9)
    hard = act(2, 0, HOME, x0=60, y0=10, x1=98, y1=34, near=1.0)
    assert m.probability(hard, HOME) > m.probability(easy, HOME)
    assert m.out_of_ten(m.probability(hard, HOME)) >= m.out_of_ten(m.probability(easy, HOME))
    assert m.is_hard(0.99)
    assert not m.is_hard(0.0)


def test_speed_from_flight_time():
    a = act(1, 0, HOME, x0=0, y0=0, x1=30, y1=0, flight=1000)
    assert passes.speed_kmh(a) == pytest.approx(108.0)
    assert passes.speed_kmh(act(2, 0, HOME, type_="dribble")) is None


def test_press_height_uses_challenges_and_pressured_opponent_actions():
    ev = [act(i, i * 1000, HOME, type_="tackle", x0=x, x1=x) for i, x in enumerate((60, 70, 80))]
    # Away attacks left in the first half: broadcast x=40 is 40 m from the home goal.
    ev += [act(10, 5000, AWAY, x0=40, x1=30, near=2.0), act(11, 6000, AWAY, x0=50, x1=40, near=2.5)]
    ev += [act(12, 7000, AWAY, x0=20, x1=10, near=8.0)]  # not under pressure: ignored
    w = metrics.Window(HOME, 0, 10_000)
    assert metrics.press_height(ev, w, HOME) == pytest.approx(60.0)
    assert metrics.press_height(ev[:3], w, HOME) is None  # too little evidence


def test_tempo_is_sixty_over_the_median_gap():
    ev = [act(i, i * 2000, HOME) for i in range(11)]
    assert metrics.tempo(ev, metrics.Window(HOME, 0, 30_000)) == pytest.approx(30.0)
    stop = StoppageStart(
        event_id="s", match_id="m", period_id=1, clock_ms=10_500, wall_ms=10_500,
        kind=StoppageKind.THROW_IN, restart_team_id=HOME, expected_dead_ms=15_000,
    )  # fmt: skip
    broken = [*ev[:6], stop, *ev[6:]]
    assert metrics.tempo(broken, metrics.Window(HOME, 0, 30_000)) is None  # 9 gaps: below the minimum


def test_directness_width_line_height_shots_and_possession():
    ev = [
        act(1, 0, HOME, x0=20, y0=34, x1=50, y1=34),  # 30 m forward: long
        act(2, 2000, HOME, x0=50, y0=34, x1=80, y1=40),  # long forward
        act(3, 4000, HOME, x0=80, y0=40, x1=70, y1=40),  # backwards
        act(4, 6000, HOME, x0=70, y0=40, x1=75, y1=44),
        act(5, 8000, HOME, x0=75, y0=44, x1=80, y1=44),
    ]
    w = metrics.Window(HOME, 0, 10_000)
    assert metrics.directness(ev, w, HOME) == pytest.approx(0.4)
    more = ev + [
        act(10 + i, 9000 + i * 100, HOME, x0=50, y0=y, x1=50, y1=y) for i, y in enumerate((4, 64, 34, 34, 14))
    ]
    assert metrics.width(more, metrics.Window(HOME, 0, 20_000)) == pytest.approx(
        np.mean([0, 0, 6, 6, 10, 30, 30, 0, 0, 20])
    )
    dfs = [
        act(20 + i, 30_000 + i * 1000, HOME, x0=x, x1=x + 5, player=f"h-d{i}")
        for i, x in enumerate((30, 40, 50))
    ]
    pos = {f"h-d{i}": "DF" for i in range(3)}
    assert metrics.line_height(dfs, metrics.Window(HOME, 30_000, 40_000), HOME, pos) == pytest.approx(40.0)
    shots = [act(30, 1000, HOME, type_="shot", x0=95, x1=105), act(31, 2000, AWAY, type_="shot", x0=10, x1=0)]
    assert metrics.shot_count(shots, w) == 1.0
    poss = [
        act(40, 0, HOME),
        act(41, 3000, HOME),
        act(42, 6000, AWAY),
        act(43, 7000, AWAY),
        act(44, 8000, HOME),
    ]
    # Home holds 0-6 s, away 6-8 s: share 6/8.
    assert metrics.possession_share(poss, metrics.Window(HOME, 0, 10_000)) == pytest.approx(0.75)
    assert set(metrics.all_metrics(ev, w, HOME, {})) == set(Metric)


def test_danger_is_zero_when_dead_and_bounded_in_play():
    dead = BallSample(wall_ms=0, period_id=1, clock_ms=0, x=100, y=34, in_play=False, team_id=None)
    live = dead.model_copy(update={"in_play": True, "team_id": HOME})
    assert danger(dead, HOME) == 0.0
    assert 0 < danger(live, HOME) <= 1
    # The same spot is near the away team's own goal in the first half: little danger for them.
    assert danger(live.model_copy(update={"team_id": AWAY}), HOME) < danger(live, HOME)
