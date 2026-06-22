#!/usr/bin/env python3
"""Direct-CLI Lean declaration search for the OpenClaw sandboxed agent (non-MCP).

OpenClaw is not an MCP client, so this wraps the lean_explore API client directly
and prints JSON. The API key is read from LEANEXPLORE_API_KEY or, failing that,
from the JSON secrets file named by OPENCLAW_SECRETS_FILE / AAS_SECRETS_FILE
(key: "LEANEXPLORE_API_KEY"). The key value is never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


def _load_key() -> str | None:
    key = os.environ.get("LEANEXPLORE_API_KEY")
    if key:
        return key
    for env in ("OPENCLAW_SECRETS_FILE", "AAS_SECRETS_FILE"):
        sf = os.environ.get(env)
        if sf and Path(sf).is_file():
            try:
                data = json.loads(Path(sf).read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(data, dict) and data.get("LEANEXPLORE_API_KEY"):
                return str(data["LEANEXPLORE_API_KEY"])
    return None


def cmd_search(args: argparse.Namespace) -> int:
    key = _load_key()
    if not key:
        print(json.dumps({"ok": False, "error": "no LEANEXPLORE_API_KEY (env or OPENCLAW_SECRETS_FILE)"}))
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
