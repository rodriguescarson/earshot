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

    s = sub.add_parser("gate", help="run the pre-registered P1 gate (see EVALUATION.md)")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))
    s.add_argument("--workers", type=int, default=8)
    s.add_argument(
        "--model-seeds",
        type=seed_range,
        default=None,
        help="held-out seeds (a subset of --seeds) for the G7 pass-difficulty check",
    )
    s.add_argument("--report", type=Path, default=None)

    s = sub.add_parser("clip", help="pick an action-dense 3-minute clip and write it as a fixture")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("ball", help="write the 10 Hz ball path for one match as JSON lines")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser("fit", help="fit xT and pass difficulty on development seeds and save the models")
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-40"))

    s = sub.add_parser("flags", help="show or set the runtime flags (cached-only mode, judge codes)")
    s.add_argument("--storage-url", default=None, help="Blob account URL (default: EARSHOT_STORAGE_URL)")
    s.add_argument("--cached-only", choices=["on", "off"], default=None)
    s.add_argument("--reason", default="set by hand")
    s.add_argument("--add-judge-code", default=None, help="add a judge code (only its SHA-256 is stored)")
    s.add_argument("--rotate-judge-codes", action="store_true", help="remove every existing judge code first")
    s.add_argument("--remove-judge-code", default=None, help="remove one judge code (matched by its SHA-256)")

    s = sub.add_parser(
        "frames", help="player frames at 10 Hz for the render match (CEO-4), and its G8 numbers"
    )
    s.add_argument("--seed", type=int, default=26)
    s.add_argument(
        "--out", type=Path, required=True, help="an .npz file: t_ms, period and one (n, 2) array per player"
    )

    s = sub.add_parser("roster", help="write the league roster (clubs and players) as JSON")
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser(
        "evaluate", help="the accelerated evaluation run (ET11): matches through the live broadcaster"
    )
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-380"))
    s.add_argument("--workers", type=int, default=8)
    s.add_argument(
        "--latency", type=Path, default=None, help="load-test latency samples (JSON); default: provisional"
    )
    s.add_argument(
        "--windows",
        choices=["inline", "background"],
        default="background",
        help="background: as built (window runs on their own task); inline: the feeder waits for each",
    )
    s.add_argument(
        "--real-analyst", action="store_true", help="real Analyst calls (Foundry) in every analysis window"
    )
    s.add_argument(
        "--tokens-per-hour", type=int, default=400_000, help="pace for --real-analyst (alert: 500K)"
    )
    s.add_argument("--max-calls", type=int, default=None, help="cap Analyst calls per match (trial runs)")
    s.add_argument("--resume", action="store_true", help="keep finished rows next to --out and run the rest")
    s.add_argument(
        "--analyst-cache", type=Path, default=None, help="cache Analyst responses (re-grading is free)"
    )
    s.add_argument("--out", type=Path, required=True, help="summary JSON; per-match rows go next to it")

    s = sub.add_parser(
        "planted", help="the planted-defect harness: every injection point, Checker and auditor"
    )
    s.add_argument("--seeds", type=seed_range, default=seed_range("1-10"))
    s.add_argument("--languages", default="en", help="comma-separated: en,es,ar,hi")
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser(
        "template-eval", help="Foundry fluency and coherence evaluators over every template, per language"
    )
    s.add_argument("--seeds", type=seed_range, default=seed_range("26,28,31"))
    s.add_argument("--tokens-per-hour", type=int, default=400_000, help="judge pace (alert: 500K)")
    s.add_argument("--out", type=Path, required=True)

    s = sub.add_parser(
        "loadtest", help="N listeners on the deployed live stream: event to first audio byte, per line"
    )
    s.add_argument("--url", required=True, help="the worker's base URL")
    s.add_argument("--listeners", type=int, default=20)
    s.add_argument("--minutes", type=float, default=10.0)
    s.add_argument("--out", type=Path, required=True, help="summary JSON; the latency model goes next to it")

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
        from earshot.sim.runner import run_many, write_outputs

        outs = run_many(args.seeds, workers=args.workers)
        manifest = write_outputs(outs, args.out, run=args.out.name)
        print(json.dumps({"matches": manifest["matches"], "out": str(args.out)}, indent=1))
    elif args.cmd == "gate":
        import time

        from earshot.eval import gate_p1 as g
        from earshot.eval.realism import g3_realism
        from earshot.sim.io import write_json
        from earshot.sim.runner import environment, run_many

        t0 = time.perf_counter()
        outs = run_many(args.seeds, workers=args.workers)
        wall = time.perf_counter() - t0
        results = [
            g.g1_determinism(args.seeds, args.workers, first=outs),
            g.g2_completeness(outs, len(args.seeds)),
            g3_realism([o.aggregates for o in outs]),
            g.g4_truth(outs),
            g.g5_planted_signal(outs),
            g.g6_baseline(outs),
        ]
        if args.model_seeds:
            held = [o for o in outs if o.seed in set(args.model_seeds)]
            results.append(g.g7_pass_model(held))
        results += [g.g9_speed(outs, wall, args.workers), g.g10_dead_ball(outs)]
        season = len(args.seeds) >= 380
        informational = set() if season else {"G3"}  # G3 gates on the full season only (EVALUATION.md)
        for r in results:
            line = (
                r.line()
                if r.gate not in ("G3", "G5")
                else f"{r.gate} {r.name}: {'PASS' if r.passed else 'FAIL'}"
            )
            print(line + (" (informational on a slice)" if r.gate in informational else ""))
            for f in r.failures[:10]:
                print("   ", f)
        if args.report:
            seeds = f"{args.seeds[0]}-{args.seeds[-1]}" if len(args.seeds) > 1 else str(args.seeds[0])
            write_json(args.report, {"seeds": seeds, "environment": environment(),
                                     "results": [r.as_dict() for r in results]})  # fmt: skip
        sys.exit(0 if all(r.passed for r in results if r.gate not in informational) else 1)
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
    elif args.cmd == "fit":
        from earshot.sim.match import ENGINE_VERSION, simulate
        from earshot.stats import passes, xt

        label = f"seeds {args.seeds[0]}-{args.seeds[-1]}, engine {ENGINE_VERSION}"
        results = [simulate(s) for s in args.seeds]
        pairs = [(r.events, r.info.home.team_id) for r in results]
        xt.fit(pairs, label).save(xt.MODEL_PATH)
        passes.fit(pairs, label).save(passes.MODEL_PATH)
        print(
            json.dumps(
                {"xt": str(xt.MODEL_PATH), "pass_difficulty": str(passes.MODEL_PATH), "fitted_on": label}
            )
        )
    elif args.cmd == "flags":
        import os

        from earshot.api.flags import BlobFlags, Flags
        from earshot.api.ratelimit import code_hash

        url = args.storage_url or os.environ.get("EARSHOT_STORAGE_URL")
        if not url:
            sys.exit("set --storage-url or EARSHOT_STORAGE_URL")
        flags = Flags(BlobFlags(url))
        flags.refresh()
        if args.rotate_judge_codes or args.add_judge_code or args.remove_judge_code:
            added = [code_hash(args.add_judge_code)] if args.add_judge_code else []
            removed = {code_hash(args.remove_judge_code)} if args.remove_judge_code else set()

            def codes(cur: dict) -> dict:
                kept = [] if args.rotate_judge_codes else list(cur.get("judge_code_sha256", []))
                return {**cur, "judge_code_sha256": sorted(set(kept + added) - removed)}

            flags.source.update(codes)
            flags.refresh()
        if args.cached_only:
            flags.set_cached_only(args.cached_only == "on", args.reason)
        shown = {k: v for k, v in flags.values.items() if k != "judge_code_sha256"}
        shown["judge_codes"] = len(flags.values.get("judge_code_sha256", []))
        print(json.dumps(shown, indent=1, sort_keys=True))
    elif args.cmd == "frames":
        import numpy as np

        from earshot.sim.frames import g8, render_frames

        fr = render_frames(args.seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {"t_ms": fr.t_ms, "period": fr.period, **fr.players}
        np.savez_compressed(args.out, allow_pickle=False, **arrays)
        ok, numbers = g8(fr)
        print(json.dumps({"out": str(args.out), "g8_passed": ok, **numbers}, indent=1))
    elif args.cmd == "roster":
        from earshot.world.clubs import world

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(world().roster().model_dump_json(indent=1) + "\n")
    elif args.cmd == "evaluate":
        from earshot.eval.run import LatencyModel, run_season

        latency = LatencyModel.load(args.latency) if args.latency else LatencyModel.provisional_default()
        analyst = (
            {
                "tokens_per_hour": args.tokens_per_hour,
                "max_calls": args.max_calls,
                "cache_dir": str(args.analyst_cache) if args.analyst_cache else None,
            }
            if args.real_analyst
            else None
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        rows = args.out.with_suffix(".matches.jsonl")
        if not args.resume:
            rows.unlink(missing_ok=True)
        summary, _ = run_season(
            args.seeds, latency, workers=args.workers, windows=args.windows, analyst=analyst, rows_path=rows
        )
        summary["command"] = "uv run earshot " + " ".join(argv if argv is not None else sys.argv[1:])
        args.out.write_text(json.dumps(summary, indent=1) + "\n")
        brief = {k: summary[k] for k in ("matches", "ok", "failed", "workers", "wall_s")}
        print(json.dumps(brief | {"out": str(args.out), "rows": str(rows)}, indent=1))
    elif args.cmd == "planted":
        from earshot.contract import Language
        from earshot.eval.planted import run_all

        result = run_all(args.seeds, [Language(x) for x in args.languages.split(",")])
        result["command"] = "uv run earshot " + " ".join(argv if argv is not None else sys.argv[1:])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
        for pt in result["points"]:
            where = f"{pt.get('language', '')} {pt.get('mode', '')}".strip()
            print(f"{pt['point']:28} {where:14} injected {pt['defects_injected']:5} recall {pt['recall']}"
                  f" false-block {pt.get('false_block_rate')}")  # fmt: skip
    elif args.cmd == "template-eval":
        from earshot.eval.templates_eval import run as judge_templates

        result = judge_templates(args.seeds, args.tokens_per_hour)
        result["command"] = "uv run earshot " + " ".join(argv if argv is not None else sys.argv[1:])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
        print(
            json.dumps({k: result[k] for k in ("coverage", "judge_calls", "tokens")} | {"out": str(args.out)})
        )
    elif args.cmd == "loadtest":
        import asyncio
        import os

        from earshot.eval.loadtest import run as load
        from earshot.eval.loadtest import temporary_judge_code

        url = os.environ.get("EARSHOT_STORAGE_URL")
        if not url:
            sys.exit("set EARSHOT_STORAGE_URL (the flag blob's account) for the run's temporary judge code")
        with temporary_judge_code(url) as code:
            result = asyncio.run(load(args.url.rstrip("/"), code, args.listeners, args.minutes))
        result["command"] = "uv run earshot " + " ".join(argv if argv is not None else sys.argv[1:])
        model = result.pop("latency_model")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1) + "\n")
        latency = args.out.with_suffix(".latency.json")
        latency.write_text(json.dumps(model) + "\n")
        keys = (
            "listeners",
            "minutes",
            "received_lines",
            "event_to_first_audio_byte_ms",
            "p95_under_2s",
            "errors",
        )
        print(
            json.dumps(
                {k: result[k] for k in keys} | {"out": str(args.out), "latency": str(latency)}, indent=1
            )
        )


if __name__ == "__main__":
    main()
