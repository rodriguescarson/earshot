"""Export versioned JSON Schemas (schemas/*.v1.json); a test fails if they drift from the models."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from earshot.contract.ball import BallSample
from earshot.contract.claim import AnalystResponse, Claim
from earshot.contract.events import EVENT_MODELS, Event
from earshot.contract.line import Line
from earshot.contract.match import ClubInfo, MatchInfo, PlayerInfo, Roster, TeamSheet
from earshot.contract.moment import Moment
from earshot.contract.truth import TruthChange
from earshot.contract.wire import WireMsg

VERSION = "v1"


def all_schemas() -> dict[str, dict]:
    out: dict[str, dict] = {
        "event": TypeAdapter(Event).json_schema(),
        "wire": TypeAdapter(WireMsg).json_schema(),
    }
    models = (*EVENT_MODELS, Moment, Line, Claim, AnalystResponse, MatchInfo, TeamSheet, PlayerInfo, ClubInfo,
              Roster, BallSample, TruthChange)  # fmt: skip
    for model in models:
        assert issubclass(model, BaseModel)
        out[f"{model.__name__.lower()}"] = model.model_json_schema()
    return out


def write(dir_: Path) -> list[Path]:
    dir_.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in sorted(all_schemas().items()):
        p = dir_ / f"{name}.{VERSION}.json"
        p.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        paths.append(p)
    return paths
