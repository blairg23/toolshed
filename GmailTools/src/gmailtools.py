from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .auth import authenticate
from .runner import DEFAULT_CATEGORIES, run_plan

_DEFAULT_CONFIG = Path(__file__).parent.parent / "config.yml"
_DEFAULT_LIMIT = 100


def cmd_auth(args: argparse.Namespace) -> int:
    print(f"Authenticating account: {args.account}")
    try:
        creds = authenticate(args.account)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Authenticated. Token stored for '{args.account}'.")
    print(f"  valid={creds.valid}  expiry={creds.expiry}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    return run_plan(
        account_name=args.account,
        config_path=args.config,
        categories=args.categories,
        limit=args.limit,
        dry_run=True,
    )


def cmd_run(args: argparse.Namespace) -> int:
    return run_plan(
        account_name=args.account,
        config_path=args.config,
        categories=args.categories,
        limit=args.limit,
        dry_run=False,
    )


def _add_plan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("account", help="Account name (matches name: in config.yml)")
    p.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"Path to config.yml (default: {_DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--categories",
        nargs="+",
        default=DEFAULT_CATEGORIES,
        metavar="CAT",
        help="Gmail categories to scan: promotions social updates (default: all three)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        metavar="N",
        help=f"Max messages to fetch per category (default: {_DEFAULT_LIMIT})",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gmailtools",
        description="Gmail label automation tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth_p = sub.add_parser("auth", help="Authenticate a Gmail account")
    auth_p.add_argument("account", help="Account name (matches config.yml)")
    auth_p.set_defaults(func=cmd_auth)

    plan_p = sub.add_parser(
        "plan",
        help="Fetch emails, match rules, show what would happen -- no writes",
    )
    _add_plan_args(plan_p)
    plan_p.set_defaults(func=cmd_plan)

    run_p = sub.add_parser(
        "run",
        help="Fetch emails, match rules, confirm, then apply all actions",
    )
    _add_plan_args(run_p)
    run_p.set_defaults(func=cmd_run)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
