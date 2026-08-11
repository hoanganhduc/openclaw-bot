#!/usr/bin/env python3
"""Publish RSS summaries without following sandbox-controlled paths."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import stat


MAX_DIGESTS = 64
MAX_DIGEST_BYTES = 8 * 1024 * 1024
MAX_SUMMARY_BYTES = 1024 * 1024
SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
DIGEST_RE = re.compile(r"^rss-([a-z0-9][a-z0-9_-]{0,63})\.md$")
ITEM_RE = re.compile(r"^## [0-9]+\. (.*)$")


class PublishError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_directory(path: Path) -> int:
    path = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.anchor or os.sep, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in path.parts[1:]:
            child = os.open(
                component,
                flags | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid():
            raise PublishError("RSS directory has the wrong owner")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_child_directory(parent: int, name: str) -> int:
    if SAFE_SESSION_RE.fullmatch(name) is None or name in {".", ".."}:
        raise PublishError("unsafe RSS directory name")
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
    information = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.geteuid()
        or (before.st_dev, before.st_ino) != (information.st_dev, information.st_ino)
    ):
        os.close(descriptor)
        raise PublishError("unsafe RSS directory")
    return descriptor


def _select_session(sessions: int) -> tuple[int, str]:
    selected: tuple[int, str, int] | None = None
    for name in sorted(os.listdir(sessions)):
        if SAFE_SESSION_RE.fullmatch(name) is None or name in {".", ".."}:
            continue
        try:
            information = os.stat(name, dir_fd=sessions, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(information.st_mode) or information.st_uid != os.geteuid():
            continue
        try:
            descriptor = _open_child_directory(sessions, name)
        except (FileNotFoundError, NotADirectoryError, OSError, PublishError):
            continue
        opened = os.fstat(descriptor)
        candidate = (opened.st_mtime_ns, name, descriptor)
        if selected is None or candidate[:2] > selected[:2]:
            if selected is not None:
                os.close(selected[2])
            selected = candidate
        else:
            os.close(descriptor)
    if selected is not None:
        return selected[2], selected[1]
    try:
        os.mkdir("unspecified_session", 0o700, dir_fd=sessions)
        os.fsync(sessions)
    except FileExistsError:
        pass
    return _open_child_directory(sessions, "unspecified_session"), "unspecified_session"


def _read_regular(parent: int, name: str) -> str:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
        dir_fd=parent,
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > MAX_DIGEST_BYTES
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise PublishError("unsafe RSS digest")
        chunks: list[bytes] = []
        remaining = MAX_DIGEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if len(payload) > MAX_DIGEST_BYTES or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino):
            raise PublishError("RSS digest changed while reading")
        return payload.decode("utf-8", errors="replace")
    finally:
        os.close(descriptor)


def _summary(digests: int, now: datetime) -> bytes:
    lines = [f"# RSS Digest Summary - {now:%Y-%m-%d %H:%M:%S UTC}"]
    names = sorted(name for name in os.listdir(digests) if DIGEST_RE.fullmatch(name))
    if len(names) > MAX_DIGESTS:
        raise PublishError("too many RSS digests")
    for name in names:
        match = DIGEST_RE.fullmatch(name)
        assert match is not None
        tag = match.group(1)
        if tag == "all":
            continue
        try:
            text = _read_regular(digests, name)
        except (FileNotFoundError, OSError, PublishError):
            continue
        items = [item.group(1) for line in text.splitlines() if (item := ITEM_RE.fullmatch(line))]
        lines.extend(("", f"## {tag}", *(f"- {item}" for item in items[:5])))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    if len(payload) > MAX_SUMMARY_BYTES:
        raise PublishError("RSS summary is oversized")
    return payload


def _write_atomic(parent: int, name: str, payload: bytes) -> None:
    if re.fullmatch(r"(?:last-summary|summary-[0-9]{8}T[0-9]{6}Z)\.md", name) is None:
        raise PublishError("unsafe RSS summary name")
    temporary = f".{name}.rss-stage-{secrets.token_hex(12)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PublishError("RSS summary write was truncated")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary = ""
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def publish(workspace: Path, *, now: datetime | None = None) -> str:
    workspace = _absolute(workspace)
    if workspace == Path(workspace.anchor):
        raise PublishError("unsafe RSS workspace")
    digest_descriptor = _open_directory(workspace / "data" / "research" / "rss" / "digests")
    sessions_descriptor = _open_directory(workspace / "data" / "sessions")
    session_descriptor: int | None = None
    try:
        session_descriptor, session_name = _select_session(sessions_descriptor)
        instant = now or datetime.now(timezone.utc)
        instant = instant.astimezone(timezone.utc)
        payload = _summary(digest_descriptor, instant)
        timestamped = f"summary-{instant:%Y%m%dT%H%M%SZ}.md"
        _write_atomic(session_descriptor, "last-summary.md", payload)
        _write_atomic(digest_descriptor, timestamped, payload)
        _write_atomic(session_descriptor, timestamped, payload)
        return f"data/sessions/{session_name}/last-summary.md"
    finally:
        if session_descriptor is not None:
            os.close(session_descriptor)
        os.close(sessions_descriptor)
        os.close(digest_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    arguments = parser.parse_args()
    print(f"WROTE_SUMMARY:{publish(arguments.workspace)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PublishError) as exc:
        print(f"rss summary publish: {exc}", file=os.sys.stderr)
        raise SystemExit(2) from exc
