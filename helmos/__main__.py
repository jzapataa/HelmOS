"""Command line entry point for the local Core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import REPOSITORY_ROOT, execute, read_execution


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m helmos")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=REPOSITORY_ROOT / "var",
        help="Local directory for execution records (default: ./var)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Execute a supported Core operation")
    run.add_argument("operation", help="Currently supported: core.status")
    show = commands.add_parser("show", help="Recover an execution by ID")
    show.add_argument("identifier")
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            record = execute(args.operation, args.state_dir)
        else:
            record = read_execution(args.identifier, args.state_dir)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(record, ensure_ascii=True, indent=2))
    return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
