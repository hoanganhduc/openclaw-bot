#!/usr/bin/env python3
"""Create/remove owner-private plaintext staging on tmpfs by default."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import sys


ACKNOWLEDGEMENT = "ACKNOWLEDGE_PERSISTENT_OWNER_PLAINTEXT"
NAME_PREFIX = "openclaw-owner-plaintext-"


class PrivateTmpError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_directory_nofollow(path: Path) -> int:
    absolute = _absolute(path)
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


def _unescape_mount(value: str) -> str:
    for encoded, decoded in (
        ("\\040", " "),
        ("\\011", "\t"),
        ("\\012", "\n"),
        ("\\134", "\\"),
    ):
        value = value.replace(encoded, decoded)
    return value


def _filesystem_type(path: Path) -> str | None:
    absolute = _absolute(path)
    selected: tuple[int, str] | None = None
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        for line in stream:
            left, separator, right = line.rstrip("\n").partition(" - ")
            if not separator:
                continue
            fields = left.split()
            after = right.split()
            if len(fields) < 5 or not after:
                continue
            mount = Path(_unescape_mount(fields[4]))
            try:
                absolute.relative_to(mount)
            except ValueError:
                continue
            depth = len(mount.parts)
            if selected is None or depth > selected[0]:
                selected = (depth, after[0])
    return selected[1] if selected else None


def _validated_base(path: Path, *, shared_sticky: bool) -> int:
    descriptor = _open_directory_nofollow(path)
    information = os.fstat(descriptor)
    mode = stat.S_IMODE(information.st_mode)
    if _filesystem_type(path) != "tmpfs":
        os.close(descriptor)
        raise PrivateTmpError("plaintext staging base is not tmpfs")
    if shared_sticky:
        safe = information.st_uid == 0 and mode == 0o1777
    else:
        safe = information.st_uid == os.geteuid() and mode & 0o077 == 0
    if not safe:
        os.close(descriptor)
        raise PrivateTmpError("plaintext staging base permissions are unsafe")
    return descriptor


def _private_persistent_base() -> Path:
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    current = home
    try:
        home_descriptor = _open_directory_nofollow(current)
    except OSError as exc:
        raise PrivateTmpError("account home is unsafe") from exc
    try:
        home_information = os.fstat(home_descriptor)
        if (
            home_information.st_uid != os.geteuid()
            or stat.S_IMODE(home_information.st_mode) & 0o022
        ):
            raise PrivateTmpError("account home is unsafe")
    finally:
        os.close(home_descriptor)
    for component in (".local", "state", "openclaw-bot", "private-tmp"):
        current /= component
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        descriptor = _open_directory_nofollow(current)
        try:
            information = os.fstat(descriptor)
            if information.st_uid != os.geteuid():
                raise PrivateTmpError("persistent staging base has the wrong owner")
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    return current


def create(*, acknowledgement: str | None = None) -> Path:
    candidates = (
        (Path(f"/run/user/{os.geteuid()}"), False),
        (Path("/dev/shm"), True),
    )
    base_descriptor: int | None = None
    base: Path | None = None
    for candidate, shared in candidates:
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            base_descriptor = _validated_base(candidate, shared_sticky=shared)
        except (OSError, PrivateTmpError):
            continue
        base = candidate
        break
    if base_descriptor is None or base is None:
        if acknowledgement != ACKNOWLEDGEMENT:
            raise PrivateTmpError(
                "tmpfs plaintext staging is unavailable; persistent fallback requires the exact acknowledgement"
            )
        base = _private_persistent_base()
        base_descriptor = _open_directory_nofollow(base)
    try:
        for _attempt in range(64):
            name = f"{NAME_PREFIX}{os.getpid()}-{secrets.token_hex(16)}"
            try:
                os.mkdir(name, 0o700, dir_fd=base_descriptor)
            except FileExistsError:
                continue
            descriptor = os.open(
                name,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=base_descriptor,
            )
            try:
                information = os.fstat(descriptor)
                if (
                    information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) != 0o700
                ):
                    raise PrivateTmpError("created plaintext staging directory is unsafe")
                os.fsync(base_descriptor)
                return base / name
            finally:
                os.close(descriptor)
        raise PrivateTmpError("could not allocate a unique plaintext staging directory")
    finally:
        os.close(base_descriptor)


def remove(path: Path, *, acknowledgement: str | None = None) -> None:
    path = _absolute(path)
    if not path.name.startswith(NAME_PREFIX):
        raise PrivateTmpError("refusing to remove an unrecognized staging path")
    allowed = [Path(f"/run/user/{os.geteuid()}"), Path("/dev/shm")]
    if acknowledgement == ACKNOWLEDGEMENT:
        allowed.append(_private_persistent_base())
    if path.parent not in allowed:
        raise PrivateTmpError("refusing to remove staging outside an approved base")
    descriptor = _open_directory_nofollow(path)
    try:
        information = os.fstat(descriptor)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise PrivateTmpError("refusing to remove unsafe plaintext staging")
    finally:
        os.close(descriptor)
    shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--allow-persistent")
    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--path", type=Path, required=True)
    remove_parser.add_argument("--allow-persistent")
    args = parser.parse_args()
    try:
        if args.command == "create":
            print(create(acknowledgement=args.allow_persistent))
        else:
            remove(args.path, acknowledgement=args.allow_persistent)
    except (OSError, PrivateTmpError) as exc:
        print(f"private plaintext staging: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
