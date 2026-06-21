from __future__ import annotations

import argparse
import sys

from auth import authenticate


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gmailtools",
        description="Gmail label automation tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth_p = sub.add_parser("auth", help="Authenticate a Gmail account")
    auth_p.add_argument("account", help="Account name (matches config.yml)")
    auth_p.set_defaults(func=cmd_auth)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
