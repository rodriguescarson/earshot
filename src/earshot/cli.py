"""Command line entry point: ``earshot <command>``."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="earshot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schemas", help="write versioned JSON Schemas for the contract")
    s.add_argument("--out", type=Path, default=Path("schemas"))
    args = parser.parse_args(argv)
    if args.cmd == "schemas":
        from earshot.contract.schemas import write

        for p in write(args.out):
            print(p)


if __name__ == "__main__":
    main()
