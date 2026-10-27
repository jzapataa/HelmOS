"""Command line entry point for the local Core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .approvals import ALLOWED_ACTIONS, create_proposal, decide_proposal, list_proposals, read_proposal
from .core import REPOSITORY_ROOT, execute, read_execution
from .social_import import list_posts
from .social_connectors import ConnectorError
from .instagram_sync import sync_instagram
from .instagram_credentials import InstagramCredentials, KINDS, secure_input
from .scheduled_instagram import SyncConfig, run_scheduled, sync_status
from .windows_tasks import manage_task
from .recommendation_evaluations import evaluate_recommendation, list_evaluations, read_evaluation
from .social_history import evolution, latest, observations, publication, summarize_history
from .recommendations import (associate_post, associated_posts, decide_recommendation,
                              list_recommendations, read_recommendation, register_run)


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
    social = commands.add_parser("social", help="Inspect local Social data and editorial follow-up")
    social_commands = social.add_subparsers(dest="social_command", required=True)
    scheduled = social_commands.add_parser("scheduled-sync", help="DPAPI-only jorgebytes synchronization")
    scheduled.add_argument("scheduled_action", choices=("run", "status", "task"))
    scheduled.add_argument("task_action", nargs="?", choices=(
        "install", "query", "last-run", "run", "disable", "remove"))
    scheduled.add_argument("--at", default="09:00", help="Local daily task time HH:MM")
    for key, value in vars(SyncConfig()).items():
        scheduled.add_argument("--" + key.replace("_", "-"), type=type(value), default=value)
    credentials = social_commands.add_parser("credentials", help="Manage protected Instagram credentials")
    credentials.add_argument("platform", choices=("instagram",))
    credentials.add_argument("credential_action", choices=("import", "status", "check", "refresh", "preflight"))
    credentials.add_argument("--api-version", required=True)
    credentials.add_argument("--expected-username", required=True)
    credentials.add_argument("--kind", choices=KINDS, default="unknown", help="Import only: verified token origin/type")
    credentials.add_argument("--issued-at", help="Import only: known issuance ISO timestamp with timezone")
    credentials.add_argument("--expires-at", help="Import only: known expiry ISO timestamp with timezone")
    sync = social_commands.add_parser("sync", help="Explicit read-only platform API synchronization")
    sync.add_argument("platform", choices=("instagram",))
    sync.add_argument("--api-version", required=True, help="Explicit Graph version verified in Meta")
    sync.add_argument("--expected-username", required=True, help="Authenticated account to verify")
    sync.add_argument("--metadata", type=Path, help="JSON editorial classifications by official post ID")
    sync_mode = sync.add_mutually_exclusive_group()
    sync_mode.add_argument("--preview", action="store_true", help="Read API and preflight without writing")
    sync_mode.add_argument("--prevalidate-metadata", action="store_true",
                      help="Compare listed metadata without Insights or Social writes")
    sync.add_argument("--page-size", type=int, default=5)
    sync.add_argument("--max-pages", type=int, default=1)
    sync.add_argument("--max-posts", type=int, default=5)
    sync.add_argument("--max-requests", type=int, default=100)
    sync.add_argument("--timeout", type=float, default=10)
    sync.add_argument("--deadline-seconds", type=float, default=120)
    for name in ("posts", "summary"):
        command = social_commands.add_parser(name)
        command.add_argument("--platform", choices=("Instagram", "TikTok", "LinkedIn"))
    for name in ("observations", "latest", "evolution"):
        command = social_commands.add_parser(name)
        command.add_argument("--platform", required=True, choices=("Instagram", "TikTok", "LinkedIn"))
        command.add_argument("--post-id", required=True)
    recommendations = social_commands.add_parser("recommendations", help="Track editorial recommendations")
    recommendation_commands = recommendations.add_subparsers(dest="recommendation_command", required=True)
    listing = recommendation_commands.add_parser("list")
    listing.add_argument("--status", choices=("pending", "accepted", "rejected"))
    for name in ("show", "accept", "reject", "posts", "evaluate", "evaluations"):
        recommendation_commands.add_parser(name).add_argument("identifier")
    evaluations = social_commands.add_parser("evaluations", help="Recover an editorial evaluation")
    evaluation_commands = evaluations.add_subparsers(dest="evaluation_command", required=True)
    evaluation_commands.add_parser("show").add_argument("identifier")
    association = recommendation_commands.add_parser("associate")
    association.add_argument("identifier")
    association.add_argument("--platform", required=True, choices=("Instagram", "TikTok", "LinkedIn"))
    association.add_argument("--post-id", required=True)
    recovery = recommendation_commands.add_parser("register-run", help="Retry registration for a saved analysis")
    recovery.add_argument("run_id")
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
    if args.command == "social" and args.social_command == "scheduled-sync":
        if (args.scheduled_action == "task") != (args.task_action is not None):
            parser.error("task requires a task action; other commands do not accept one")
    if (args.command == "social" and args.social_command == "credentials"
            and args.credential_action != "import"
            and (args.kind != "unknown" or args.issued_at or args.expires_at)):
        parser.error("--kind, --issued-at and --expires-at apply only to credentials import")

    try:
        if args.command == "run":
            record = execute(args.operation, args.state_dir, args.csv, args.task, args.context,
                             args.platform, args.from_date, args.to_date)
        elif args.command == "show":
            record = read_execution(args.identifier, args.state_dir)
        elif args.command == "social":
            if args.social_command == "scheduled-sync":
                config = SyncConfig(**{key: getattr(args, key) for key in vars(SyncConfig())})
                if args.scheduled_action == "run":
                    record, code = run_scheduled(args.state_dir, config)
                    print(json.dumps(record, ensure_ascii=True, indent=2))
                    return code
                if args.scheduled_action == "status":
                    record = sync_status(args.state_dir)
                else:
                    record = manage_task(args.task_action, state_dir=args.state_dir,
                                         repository=REPOSITORY_ROOT, config=config, at=args.at)
            elif args.social_command == "credentials":
                manager = InstagramCredentials(api_version=args.api_version,
                                               expected_username=args.expected_username)
                if args.credential_action == "import":
                    record = manager.import_token(secure_input(), kind=args.kind,
                                                  issued_at=args.issued_at, expires_at=args.expires_at)
                elif args.credential_action == "status":
                    record = manager.status()
                elif args.credential_action == "check":
                    record = manager.check()
                elif args.credential_action == "refresh":
                    record = manager.refresh()
                else:
                    record = sync_instagram(
                        args.state_dir, api_version=args.api_version,
                        expected_username=args.expected_username, preview=True,
                        page_size=1, max_pages=1, max_posts=1, max_requests=10)
                    record["preflight"] = "bounded_preview"
            elif args.social_command == "sync":
                record = sync_instagram(
                    args.state_dir, api_version=args.api_version, expected_username=args.expected_username,
                    metadata_path=args.metadata, preview=args.preview, page_size=args.page_size,
                    max_pages=args.max_pages, max_posts=args.max_posts, max_requests=args.max_requests,
                    timeout=args.timeout, deadline_seconds=args.deadline_seconds,
                    prevalidate_metadata=args.prevalidate_metadata)
            elif args.social_command == "recommendations":
                name = args.recommendation_command
                if name == "list":
                    record = list_recommendations(args.state_dir, args.status)
                elif name == "show":
                    record = read_recommendation(args.identifier, args.state_dir)
                elif name in ("accept", "reject"):
                    record = decide_recommendation(args.identifier,
                                                   "accepted" if name == "accept" else "rejected",
                                                   args.state_dir)
                elif name == "associate":
                    record = associate_post(args.identifier, args.platform, args.post_id, args.state_dir)
                elif name == "posts":
                    record = associated_posts(args.identifier, args.state_dir)
                elif name == "evaluate":
                    record = evaluate_recommendation(args.identifier, args.state_dir)
                elif name == "evaluations":
                    record = list_evaluations(args.identifier, args.state_dir)
                else:
                    record = register_run(read_execution(args.run_id, args.state_dir), args.state_dir)
            elif args.social_command == "evaluations":
                record = read_evaluation(args.identifier, args.state_dir)
            elif args.social_command == "posts":
                record = list_posts(args.state_dir, args.platform)
            elif args.social_command == "summary":
                record = summarize_history(args.state_dir, list_posts(args.state_dir, args.platform))
            else:
                post = publication(args.state_dir, args.platform, args.post_id)
                if args.social_command == "observations":
                    record = {"platform": post["platform"], "post_id": post["post_id"],
                              "observations": observations(args.state_dir, post)}
                elif args.social_command == "latest":
                    record = latest(args.state_dir, post)
                else:
                    record = evolution(args.state_dir, post)
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
    except ConnectorError as error:
        print(json.dumps({"error": error.code, "status": error.status,
                          "retry_after": error.retry_after,
                          "reauthorization_required": error.code in {
                              "authentication_required", "instagram_token_expired_reauthorize",
                              "instagram_permissions_required"}}), file=sys.stderr)
        return 2
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(record, ensure_ascii=True, indent=2))
    if args.command in ("run", "show"):
        return 0 if record["status"] == "succeeded" else 1
    if args.command == "social" and args.social_command == "sync":
        if args.prevalidate_metadata:
            return 2 if record["conflicts"] else (1 if record["bounded_listing"] else 0)
        return 1 if record["partial_errors"] else 0
    if (args.command == "social" and args.social_command == "credentials"
            and args.credential_action == "preflight"):
        return 1 if record["partial_errors"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
