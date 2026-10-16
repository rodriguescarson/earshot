import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from earshot.contract import (
    Action,
    Claim,
    Event,
    Kickoff,
    Restart,
    StoppageKind,
    StoppageStart,
    check_stream,
)
from earshot.contract.schemas import VERSION, all_schemas

EV = TypeAdapter(Event)
SCHEMAS = Path(__file__).parent.parent / "schemas"


def base(i: int, clock: int, **kw):
    return {"event_id": f"e{i}", "match_id": "m1", "period_id": 1, "clock_ms": clock, "wall_ms": clock, **kw}


def test_action_round_trips_through_the_union():
    raw = base(1, 1000, team_id="kes", player_id="p9", type="pass", result="success", bodypart="foot",
               start_x=50, start_y=30, end_x=70, end_y=20, flight_ms=900, nearest_opp_m=2.5)  # fmt: skip
    ev = EV.validate_python(raw)
    assert isinstance(ev, Action)
    assert EV.validate_json(EV.dump_json(ev)) == ev


def test_flight_ms_rejected_on_a_tackle():
    with pytest.raises(ValidationError):
        EV.validate_python(base(1, 0, type="tackle", result="success", bodypart="foot", start_x=1, start_y=1,
                                end_x=1, end_y=1, flight_ms=10))  # fmt: skip


def test_positions_outside_the_pitch_are_rejected():
    with pytest.raises(ValidationError):
        EV.validate_python(base(1, 0, type="pass", result="fail", bodypart="foot", start_x=106, start_y=1,
                                end_x=1, end_y=1))  # fmt: skip


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        EV.validate_python(base(1, 0, type="kickoff", surprise=1))


def test_pairing_invariant_accepts_a_valid_stream():
    stream = [
        Kickoff(**base(1, 0)),
        StoppageStart(
            **base(2, 5000), kind=StoppageKind.THROW_IN, restart_team_id="kes", expected_dead_ms=12000
        ),
        Restart(**base(3, 17000), kind=StoppageKind.THROW_IN),
        StoppageStart(
            **base(4, 90000), kind=StoppageKind.FULL_TIME, restart_team_id=None, expected_dead_ms=0
        ),
    ]
    assert check_stream(stream) == []


def test_pairing_invariant_catches_missing_and_mismatched_restarts():
    s1 = StoppageStart(**base(1, 0), kind=StoppageKind.CORNER, restart_team_id="kes", expected_dead_ms=20000)
    s2 = StoppageStart(**base(2, 10), kind=StoppageKind.FREE_KICK,
                       restart_team_id="cop", expected_dead_ms=20000)  # fmt: skip
    wrong = Restart(**base(3, 20), kind=StoppageKind.GOAL_KICK)
    problems = check_stream([s1, s2, wrong])
    assert any("while" in p for p in problems)
    assert any("kind" in p for p in problems)
    assert any("never restarted" in p for p in check_stream([s1]))


def test_clock_going_backwards_is_flagged():
    assert check_stream([Kickoff(**base(1, 500)), Kickoff(**base(2, 100))])


def test_claim_window_must_be_ordered_and_cite_events():
    ok = {"team_id": "kes", "metric": "press_height", "direction": "up", "window_start_ms": 0,
          "window_end_ms": 300000, "cited_event_ids": ["e1"]}  # fmt: skip
    Claim(**ok)
    with pytest.raises(ValidationError):
        Claim(**{**ok, "window_end_ms": 0})
    with pytest.raises(ValidationError):
        Claim(**{**ok, "cited_event_ids": []})
    with pytest.raises(ValidationError):
        Claim(**{**ok, "cause": "MADE_UP_LABEL"})


@pytest.mark.parametrize("name", sorted(all_schemas()))
def test_committed_schemas_match_the_models(name):
    path = SCHEMAS / f"{name}.{VERSION}.json"
    assert path.exists(), f"run `uv run earshot schemas` to write {path.name}"
    committed = json.loads(path.read_text())
    assert committed == json.loads(json.dumps(all_schemas()[name], sort_keys=True)), f"{path.name} drifted"


def test_event_schema_validates_a_model_dump():
    schema = json.loads((SCHEMAS / f"event.{VERSION}.json").read_text())
    ev = StoppageStart(**base(1, 0), kind=StoppageKind.CORNER, restart_team_id="kes", expected_dead_ms=20000)
    jsonschema.validate(json.loads(ev.model_dump_json()), schema)
