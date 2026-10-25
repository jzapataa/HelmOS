"""Command line entry point for the local Core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .approvals import ALLOWED_ACTIONS, create_proposal, decide_proposal, list_proposals, read_proposal
from .core import REPOSITORY_ROOT, execute, read_execution
from .social_import import list_posts, summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m helmos")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=REPOSITORY_ROOT / "var",
        help="Local directory for execution records (default: ./var)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Execute a supported local operation")
    run.add_argument("operation", help="Supported: core.status, social.sample, social.import, social.analyze, core.agent")
    run.add_argument("--csv", type=Path, help="CSV input for social.sample or social.import")
    run.add_argument("--task", help="One read-only repository task for core.agent")
    run.add_argument("--context", type=Path, help="README.md or docs/NAME.md for core.agent")
    run.add_argument("--platform", choices=("Instagram", "TikTok", "LinkedIn"), help="Platform filter for social.analyze")
    run.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD for social.analyze")
    run.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD for social.analyze")
    show = commands.add_parser("show", help="Recover an execution by ID")
    show.add_argument("identifier")
    social = commands.add_parser("social", help="Read locally imported publications")
    social_commands = social.add_subparsers(dest="social_command", required=True)
    for name in ("posts", "summary"):
        command = social_commands.add_parser(name)
        command.add_argument("--platform", choices=("Instagram", "TikTok", "LinkedIn"))
    approvals = commands.add_parser("approvals", help="Manage local approval proposals")
    approval_commands = approvals.add_subparsers(dest="approval_command", required=True)
    create = approval_commands.add_parser("create", help="Create a proposal from a saved execution")
    create.add_argument("--run-id", required=True)
    create.add_argument("--action", required=True, choices=sorted(ALLOWED_ACTIONS))
    create.add_argument("--description", required=True)
    create.add_argument("--scope", required=True)
    approval_commands.add_parser("list", help="List saved proposals")
    for name in ("show", "approve", "reject"):
        command = approval_commands.add_parser(name)
        command.add_argument("identifier")
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            record = execute(args.operation, args.state_dir, args.csv, args.task, args.context,
                             args.platform, args.from_date, args.to_date)
        elif args.command == "show":
            record = read_execution(args.identifier, args.state_dir)
        elif args.command == "social":
            posts = list_posts(args.state_dir, args.platform)
            record = posts if args.social_command == "posts" else summarize(posts)
        elif args.approval_command == "create":
            record = create_proposal(
                args.run_id, args.action, args.description, args.scope, args.state_dir,
            )
        elif args.approval_command == "list":
            record = list_proposals(args.state_dir)
        elif args.approval_command == "show":
            record = read_proposal(args.identifier, args.state_dir)
        else:
            decision = "approved" if args.approval_command == "approve" else "rejected"
            record = decide_proposal(args.identifier, decision, args.state_dir)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(record, ensure_ascii=True, indent=2))
    if args.command in ("run", "show"):
        return 0 if record["status"] == "succeeded" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
