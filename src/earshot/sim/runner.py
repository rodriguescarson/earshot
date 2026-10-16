"""Run many matches in a process pool and write them with a sha256 manifest.

Every match depends only on its own seed, so the output is identical whatever the worker count
(gate G1 checks 1 worker against 8). Events and headers go under ``data/<run>/`` (gitignored);
truth logs go only to the hidden store (``data/hidden/<run>/``) through ``earshot.hidden``.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from earshot.hidden.store import TRUTH_ROOT, truth_bytes, write_truth
from earshot.sim.io import events_jsonl, info_json, sha256, write_json
from earshot.sim.match import ENGINE_VERSION, simulate
from earshot.stats.aggregates import aggregate


@dataclass(frozen=True)
class MatchOutput:
    seed: int
    match_id: str
    events: bytes
    info: bytes
    truth: bytes
    aggregates: dict
    seconds: float


def run_one(seed: int) -> MatchOutput:
    t0 = time.perf_counter()
    r = simulate(seed)
    elapsed = time.perf_counter() - t0
    truth = truth_bytes(r.truth)
    agg = aggregate(r.events, r.info.home.team_id, r.info.away.team_id).as_dict()
    return MatchOutput(seed, r.info.match_id, events_jsonl(r.events), info_json(r.info), truth, agg, elapsed)


def run_many(seeds: Iterable[int], workers: int = 8) -> list[MatchOutput]:
    seeds = list(seeds)
    if workers <= 1:
        return [run_one(s) for s in seeds]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run_one, seeds, chunksize=max(1, len(seeds) // (workers * 4))))


def environment() -> dict[str, str]:
    return {
        "engine_version": ENGINE_VERSION,
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "pydantic": version("pydantic"),
    }


def write_outputs(outputs: list[MatchOutput], out_dir: Path, run: str, truth_root: Path = TRUTH_ROOT) -> dict:
    """Write events and headers to ``out_dir`` and each truth log to the hidden store."""
    from earshot.contract.truth import TruthChange

    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for o in sorted(outputs, key=lambda o: o.seed):
        for name, data in ((f"{o.match_id}.events.jsonl", o.events), (f"{o.match_id}.info.json", o.info)):
            (out_dir / name).write_bytes(data)
            files[name] = sha256(data)
        changes = [TruthChange.model_validate_json(x) for x in o.truth.decode().splitlines() if x]
        write_truth(truth_root, run, o.match_id, changes)
        files[f"hidden/{o.match_id}.truth.jsonl"] = sha256(o.truth)
    manifest = {"environment": environment(), "run": run, "matches": len(outputs), "files": files}
    write_json(out_dir / "manifest.json", manifest)
    return manifest
