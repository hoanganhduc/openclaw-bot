#!/usr/bin/env python3
"""Run the installed attested OpenClaw health or cron-export API."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import re
import stat
import sys


HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_UNIT_BYTES = 256 * 1024


class CommandError(RuntimeError):
    pass


def _read_unit(path: Path) -> str:
    parent = os.open(
        path.parent,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    descriptor: int | None = None
    try:
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o022
            or information.st_size > MAX_UNIT_BYTES
            or (named.st_dev, named.st_ino) != (information.st_dev, information.st_ino)
        ):
            raise CommandError("installed OpenClaw service unit is unsafe")
        chunks: list[bytes] = []
        remaining = information.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise CommandError("installed OpenClaw service unit changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            information.st_dev,
            information.st_ino,
            information.st_size,
            information.st_mtime_ns,
            information.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise CommandError("installed OpenClaw service unit changed while reading")
        payload = b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CommandError("installed OpenClaw service unit is malformed") from exc


def _installed_paths(home: Path) -> tuple[Path, Path]:
    text = _read_unit(home / ".config/systemd/user/openclaw-gateway.service")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("Environment="):
            continue
        value = line[len("Environment=") :]
        name, separator, content = value.partition("=")
        if separator and name in {"OPENCLAW_LIBEXEC", "OPENCLAW_STATE_DIR"}:
            if name in values:
                raise CommandError("installed OpenClaw service unit has duplicate authority")
            values[name] = content
    generation = Path(values.get("OPENCLAW_LIBEXEC", ""))
    state = Path(values.get("OPENCLAW_STATE_DIR", ""))
    expected_parent = home / ".local/libexec/openclaw-bot/generations"
    if (
        not generation.is_absolute()
        or generation.parent != expected_parent
        or HEX64.fullmatch(generation.name) is None
        or not state.is_absolute()
    ):
        raise CommandError("installed OpenClaw service authority is invalid")
    return generation, state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("health", "cron-export"))
    parser.add_argument("--timeout", type=int, default=10_000)
    args = parser.parse_args()
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        generation, state = _installed_paths(home)
        child = [args.command]
        if args.command == "health":
            child.extend(("--timeout", str(args.timeout)))
        argv = [
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            os.fspath(generation / "host_exec.py"),
            "--generation",
            os.fspath(generation),
            "--artifact",
            "openclaw_host_cli.py",
            "--",
            *child,
        ]
        os.execve(
            argv[0],
            argv,
            {
                "HOME": os.fspath(home),
                "PATH": "/usr/bin:/bin",
                "OPENCLAW_LIBEXEC": os.fspath(generation),
                "OPENCLAW_STATE_DIR": os.fspath(state),
                "OPENCLAW_CONFIG_PATH": os.fspath(state / "openclaw.json"),
            },
        )
    except (CommandError, OSError) as exc:
        print(f"OpenClaw host command: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
