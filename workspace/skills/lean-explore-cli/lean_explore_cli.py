#!/usr/bin/env python3
"""Direct-CLI Lean declaration search for the OpenClaw sandboxed agent (non-MCP).

OpenClaw is not an MCP client, so this wraps the lean_explore API client directly
and prints JSON. The bounded launcher projects LEANEXPLORE_API_KEY directly; no
shared secret-file selector is exposed to this process. The key is never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os


def _load_key() -> str | None:
    # Keep the credential out of the ambient environment before importing the
    # third-party client or entering its event loop. The ApiClient receives the
    # capability explicitly and no child process can inherit it accidentally.
    return os.environ.pop("LEANEXPLORE_API_KEY", "") or None


def cmd_search(args: argparse.Namespace) -> int:
    key = _load_key()
    if not key:
        print(json.dumps({"ok": False, "error": "no LEANEXPLORE_API_KEY in the managed environment"}))
        return 1
    from lean_explore.api.client import ApiClient

    client = ApiClient(api_key=key)
    resp = asyncio.run(client.search(query=args.query, limit=args.limit, packages=args.package or None))
    print(resp.model_dump_json(indent=2))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    key = _load_key()
    try:
        import lean_explore  # noqa: F401

        importable = True
    except Exception:  # noqa: BLE001
        importable = False
    print(json.dumps({
        "ok": importable and bool(key),
        "lean_explore_importable": importable,
        "auth_status": "present" if key else "missing",
        "venv": os.environ.get("VIRTUAL_ENV", ""),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-explore-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search Lean declarations (Mathlib etc.)")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=5)
    s.add_argument("-p", "--package", action="append", help="restrict to package(s); repeatable")
    s.set_defaults(func=cmd_search)
    d = sub.add_parser("doctor", help="offline readiness check")
    d.set_defaults(func=cmd_doctor)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
