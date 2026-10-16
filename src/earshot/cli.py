"""Command line entry point: ``earshot <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def seed_range(text: str) -> list[int]:
    """``1-40`` or ``1,5,9`` or ``7``."""
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="earshot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schemas", help="write versioned JSON Schemas for the contract")
    s.add_argument("--out", type=Path, default=Path("schemas"))

    s = sub.add_parser("sim", help="simulate one match and write its events and header")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--out", type=Path, default=Path("data/matches"))

    s = sub.add_parser("slice", help="simulate a set of seeds in parallel, with a sha256 manifest")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--out", type=Path, default=Path("data/slice"))

    s = sub.add_parser("gate", help="run the P1 gate checks that apply to a slice (G1, G2, G10)")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--report", type=Path, default=None)

    s = sub.add_parser("clip", help="pick an action-dense 3-minute clip and write it as a fixture")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("ball", help="write the 10 Hz ball path for one match as JSON lines")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("roster", help="write the league roster (clubs and players) as JSON")
    s.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.cmd == "schemas":
        from earshot.contract.schemas import write

        for p in write(args.out):
            print(p)
    elif args.cmd == "sim":
        from earshot.sim.io import events_jsonl, info_json
        from earshot.sim.match import simulate
        from earshot.stats.aggregates import aggregate

        r = simulate(args.seed)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{r.info.match_id}.events.jsonl").write_bytes(events_jsonl(r.events))
        (args.out / f"{r.info.match_id}.info.json").write_bytes(info_json(r.info))
        agg = aggregate(r.events, r.info.home.team_id, r.info.away.team_id)
        print(json.dumps({"match_id": r.info.match_id, **agg.as_dict()}, indent=1))
    elif args.cmd == "slice":
        from earshot.season.run import run_many, write_outputs

        outs = run_many(args.seeds, workers=args.workers)
        manifest = write_outputs(outs, args.out)
        print(json.dumps({"matches": manifest["matches"], "out": str(args.out)}, indent=1))
    elif args.cmd == "gate":
        from earshot.eval.gate_p1 import g1_determinism, g2_completeness, g10_dead_ball
        from earshot.season.run import environment, run_many

        outs = run_many(args.seeds, workers=args.workers)
        results = [
            g1_determinism(args.seeds, args.workers, first=outs),
            g2_completeness(outs, len(args.seeds)),
            g10_dead_ball(outs),
        ]
        for r in results:
            print(r.line())
            for f in r.failures[:10]:
                print("   ", f)
        if args.report:
            from earshot.sim.io import write_json

            seeds = f"{args.seeds[0]}-{args.seeds[-1]}" if len(args.seeds) > 1 else str(args.seeds[0])
            write_json(
                args.report,
                {"seeds": seeds, "environment": environment(), "results": [r.as_dict() for r in results]},
            )
        sys.exit(0 if all(r.passed for r in results) else 1)
    elif args.cmd == "clip":
        from earshot.sim.clip import dumps, fixture, pick

        r, c = pick(args.seeds)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(dumps(fixture(r, c)))
        print(
            json.dumps(
                {
                    "seed": c.seed,
                    "events": c.end_index - c.start_index + 1,
                    "score": round(c.score, 1),
                    "out": str(args.out),
                }
            )
        )
    elif args.cmd == "ball":
        from earshot.sim.ballpath import ball_path
        from earshot.sim.io import ball_jsonl
        from earshot.sim.match import simulate

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(ball_jsonl(ball_path(simulate(args.seed).events)))
    elif args.cmd == "roster":
        from earshot.world.clubs import world

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(world().roster().model_dump_json(indent=1) + "\n")


if __name__ == "__main__":
    main()
