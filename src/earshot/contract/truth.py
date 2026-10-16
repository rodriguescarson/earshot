"""Hidden tactical truth: scripted changes the engine applies (data lives under hidden/).

Only the simulator (which writes it) and the evaluation (which grades against it) may load
truth data. The model is here so both agree on its shape.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from earshot.contract.claim import CauseLabel


class TruthChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    team_id: str
    label: CauseLabel
    wall_ms: Annotated[int, Field(ge=0)]
    ramp_ms: Annotated[int, Field(ge=0)]
    setting: str
    delta: float
