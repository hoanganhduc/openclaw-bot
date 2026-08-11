#!/usr/bin/env python3
"""Acquire a descriptor-anchored owner-state lock and exec a child command."""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import stat
import sys


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(absolute.anchor or os.sep, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component,
                flags | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_inherited_lock(lock_path: Path, mode: str) -> None:
    marker = os.environ.get("OPENCLAW_OWNER_STATE_LOCK")
    descriptor_text = os.environ.get("OPENCLAW_OWNER_STATE_LOCK_FD", "")
    if marker != os.fspath(lock_path):
        raise OSError("owner-state lock marker does not match the requested lock")
    if os.environ.get("OPENCLAW_OWNER_STATE_LOCK_MODE") != mode:
        raise OSError("owner-state lock mode does not match the requested mode")
    try:
        descriptor = int(descriptor_text)
    except ValueError as exc:
        raise OSError("owner-state lock descriptor is missing") from exc
    if descriptor < 0:
        raise OSError("owner-state lock descriptor is invalid")

    parent_descriptor = _open_directory_nofollow(lock_path.parent)
    try:
        information = os.fstat(descriptor)
        pathname_information = os.stat(
            lock_path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o077
            or (information.st_dev, information.st_ino)
            != (pathname_information.st_dev, pathname_information.st_ino)
        ):
            raise OSError("inherited owner-state lock is unsafe")
        # Re-locking an inherited open-file description succeeds only while the
        # descriptor remains usable.  A fabricated marker without the pinned
        # descriptor therefore cannot bypass serialization.
        requested = fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX
        fcntl.flock(descriptor, requested | fcntl.LOCK_NB)
    finally:
        os.close(parent_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-path", type=Path, required=True)
    parser.add_argument("--mode", choices=("shared", "exclusive"), default="exclusive")
    parser.add_argument("--validate-inherited", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    lock_path = Path(os.path.abspath(args.lock_path.expanduser()))
    if args.validate_inherited:
        if args.command:
            parser.error("--validate-inherited does not accept a child command")
        try:
            _validate_inherited_lock(lock_path, args.mode)
        except (BlockingIOError, OSError) as exc:
            print(f"owner-state lock failed: {exc}", file=sys.stderr)
            return 2
        return 0
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a child command is required")
    parent_descriptor: int | None = None
    lock_descriptor: int | None = None
    try:
        parent_descriptor = _open_directory_nofollow(lock_path.parent)
        lock_descriptor = os.open(
            lock_path.name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        information = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o077
        ):
            raise OSError("owner-state lock file is unsafe")
        requested = fcntl.LOCK_SH if args.mode == "shared" else fcntl.LOCK_EX
        fcntl.flock(lock_descriptor, requested | fcntl.LOCK_NB)
        os.set_inheritable(lock_descriptor, True)
        environment = dict(os.environ)
        environment["OPENCLAW_OWNER_STATE_LOCK"] = os.fspath(lock_path)
        environment["OPENCLAW_OWNER_STATE_LOCK_FD"] = str(lock_descriptor)
        environment["OPENCLAW_OWNER_STATE_LOCK_MODE"] = args.mode
        os.execvpe(command[0], command, environment)
    except BlockingIOError:
        print("owner-state lock is already held incompatibly", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"owner-state lock failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
