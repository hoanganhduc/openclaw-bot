#!/usr/bin/env python3
"""Remove group/world write from reviewed OpenClaw credential-reader ancestors."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys


SKILL_DIRECTORIES = (
    "axiom-axle-mcp",
    "calibre",
    "lean-explore-cli",
    "lean-explore-mcp",
    "research-digest-wrapper",
    "submission-venue-selector",
    "vnthuquan",
    "zotero",
)


class AncestorError(RuntimeError):
    pass


def _open_directory(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _harden(path: Path, *, required: bool) -> None:
    try:
        descriptor = _open_directory(path)
    except FileNotFoundError:
        if required:
            raise AncestorError(f"required runtime ancestor is missing: {path}")
        return
    try:
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid():
            raise AncestorError(f"runtime ancestor has the wrong owner: {path}")
        os.fchmod(descriptor, stat.S_IMODE(information.st_mode) & ~0o022)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    prefix = Path(os.path.abspath(args.prefix.expanduser()))
    if prefix == Path("/"):
        parser.error("unsafe OpenClaw prefix")
    workspace = prefix / "workspace"
    try:
        for path in (prefix, workspace, workspace / "skills"):
            _harden(path, required=True)
        for name in SKILL_DIRECTORIES:
            _harden(workspace / "skills" / name, required=False)
        for path in (
            prefix / "skills",
            workspace / ".config",
            workspace / ".config/ai-agents-skills",
        ):
            _harden(path, required=False)
    except (AncestorError, OSError) as exc:
        print(f"runtime ancestor hardening: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
