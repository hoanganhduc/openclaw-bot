#!/usr/bin/env python3
"""Descriptor-bound snapshots and publication for untrusted workspace queues."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys


MAX_INPUT_BYTES = 64 * 1024 * 1024
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FORBIDDEN_PARTS = frozenset(
    {".config", ".env", ".git", ".local", "_control", "credentials", "secrets"}
)


class QueueBoundaryError(RuntimeError):
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


def _validate_workspace(workspace: Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(workspace))
    descriptor = _open_directory(absolute)
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) & 0o022:
        os.close(descriptor)
        raise QueueBoundaryError("workspace root is not owner-controlled")
    return absolute, descriptor


def _relative_workspace_path(
    workspace: Path, value: str, allowed_roots: tuple[PurePosixPath, ...]
) -> PurePosixPath:
    if not value or value != value.strip() or "\x00" in value:
        raise QueueBoundaryError("queued path is invalid")
    if value == "/workspace":
        relative = PurePosixPath()
    elif value.startswith("/workspace/"):
        relative = PurePosixPath(value[len("/workspace/") :])
    elif os.path.isabs(value):
        try:
            relative = PurePosixPath(
                Path(os.path.abspath(value)).relative_to(workspace).as_posix()
            )
        except ValueError as exc:
            raise QueueBoundaryError("queued path escapes the workspace") from exc
    else:
        relative = PurePosixPath(value)
    if (
        not relative.parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part.casefold() in FORBIDDEN_PARTS for part in relative.parts)
    ):
        raise QueueBoundaryError("queued path crosses a private boundary")
    if not any(
        len(relative.parts) > len(root.parts)
        and relative.parts[: len(root.parts)] == root.parts
        for root in allowed_roots
    ):
        raise QueueBoundaryError("queued path is outside the approved data roots")
    return relative


def _open_relative_regular(root_descriptor: int, relative: PurePosixPath) -> int:
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    parent = os.dup(root_descriptor)
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = next_descriptor
        named = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            os.close(descriptor)
            raise QueueBoundaryError("queued input is not a safe regular file")
        return descriptor
    finally:
        os.close(parent)


def _private_spool(path: Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(path))
    descriptor = _open_directory(absolute)
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) != 0o700:
        os.close(descriptor)
        raise QueueBoundaryError("host queue spool is not owner-private")
    return absolute, descriptor


def snapshot(
    workspace: Path,
    source: str,
    spool: Path,
    label: str,
    allowed_roots: tuple[PurePosixPath, ...],
    max_bytes: int,
) -> Path:
    if SAFE_LABEL_RE.fullmatch(label) is None:
        raise QueueBoundaryError("queue snapshot label is invalid")
    workspace, workspace_descriptor = _validate_workspace(workspace)
    source_descriptor: int | None = None
    spool_descriptor: int | None = None
    output_descriptor: int | None = None
    output_name = ""
    try:
        relative = _relative_workspace_path(workspace, source, allowed_roots)
        source_descriptor = _open_relative_regular(workspace_descriptor, relative)
        before = os.fstat(source_descriptor)
        if before.st_size > max_bytes:
            raise QueueBoundaryError("queued input exceeds its size limit")
        spool, spool_descriptor = _private_spool(spool)
        output_name = f"{label}-{secrets.token_hex(16)}{relative.suffix}"
        output_descriptor = os.open(
            output_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=spool_descriptor,
        )
        remaining = before.st_size
        while remaining:
            chunk = os.read(source_descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise QueueBoundaryError("queued input was truncated")
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                if written <= 0:
                    raise QueueBoundaryError("host queue snapshot was truncated")
                view = view[written:]
            remaining -= len(chunk)
        after = os.fstat(source_descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise QueueBoundaryError("queued input changed during snapshot")
        os.fchmod(output_descriptor, 0o600)
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        os.fsync(spool_descriptor)
        return spool / output_name
    except Exception:
        if output_name and spool_descriptor is not None:
            try:
                os.unlink(output_name, dir_fd=spool_descriptor)
            except FileNotFoundError:
                pass
        raise
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        if spool_descriptor is not None:
            os.close(spool_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(workspace_descriptor)


def publish(
    workspace: Path,
    source: Path,
    destination: str,
    allowed_roots: tuple[PurePosixPath, ...],
) -> Path:
    workspace, workspace_descriptor = _validate_workspace(workspace)
    source_descriptor: int | None = None
    parent_descriptor: int | None = None
    temporary = ""
    try:
        relative = _relative_workspace_path(workspace, destination, allowed_roots)
        if not any(
            len(relative.parts) == len(root.parts) + 1
            and relative.parts[: len(root.parts)] == root.parts
            for root in allowed_roots
        ):
            raise QueueBoundaryError("queued output must be a direct approved-root child")
        if relative.suffix.lower() != ".mp4":
            raise QueueBoundaryError("queued output must be an MP4 file")
        source_parent = _open_directory(source.parent)
        try:
            source_descriptor = _open_relative_regular(
                source_parent, PurePosixPath(source.name)
            )
        finally:
            os.close(source_parent)
        parent_descriptor = os.dup(workspace_descriptor)
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor
        try:
            os.stat(relative.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise QueueBoundaryError("refusing to overwrite an existing queued output")
        temporary = f".{relative.name}.publish-{secrets.token_hex(16)}"
        output_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            while chunk := os.read(source_descriptor, 64 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(output_descriptor, view)
                    if written <= 0:
                        raise QueueBoundaryError("queued output publication was truncated")
                    view = view[written:]
            os.fchmod(output_descriptor, 0o600)
            os.fsync(output_descriptor)
        finally:
            os.close(output_descriptor)
        try:
            os.link(
                temporary,
                relative.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise QueueBoundaryError("queued output appeared during publication") from exc
        os.unlink(temporary, dir_fd=parent_descriptor)
        temporary = ""
        os.fsync(parent_descriptor)
        return workspace.joinpath(*relative.parts)
    finally:
        if temporary and parent_descriptor is not None:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(workspace_descriptor)


def _roots(values: list[str]) -> tuple[PurePosixPath, ...]:
    roots: list[PurePosixPath] = []
    for value in values:
        root = PurePosixPath(value)
        if (
            root.is_absolute()
            or any(part in {"", ".", ".."} for part in root.parts)
            or any(part.casefold() in FORBIDDEN_PARTS for part in root.parts)
        ):
            raise QueueBoundaryError("approved data root is invalid")
        roots.append(root)
    if not roots:
        raise QueueBoundaryError("at least one approved data root is required")
    return tuple(roots)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--workspace", type=Path, required=True)
    snapshot_parser.add_argument("--source", required=True)
    snapshot_parser.add_argument("--spool", type=Path, required=True)
    snapshot_parser.add_argument("--label", required=True)
    snapshot_parser.add_argument("--allow-root", action="append", required=True)
    snapshot_parser.add_argument("--max-bytes", type=int, default=MAX_INPUT_BYTES)
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--workspace", type=Path, required=True)
    publish_parser.add_argument("--source", type=Path, required=True)
    publish_parser.add_argument("--destination", required=True)
    publish_parser.add_argument("--allow-root", action="append", required=True)
    args = parser.parse_args()
    try:
        roots = _roots(args.allow_root)
        if args.command == "snapshot":
            if args.max_bytes < 1 or args.max_bytes > MAX_INPUT_BYTES:
                raise QueueBoundaryError("queue snapshot size limit is invalid")
            path = snapshot(
                args.workspace,
                args.source,
                args.spool,
                args.label,
                roots,
                args.max_bytes,
            )
        else:
            path = publish(
                args.workspace,
                args.source,
                args.destination,
                roots,
            )
        print(path)
        return 0
    except (OSError, QueueBoundaryError) as exc:
        print(f"host queue boundary: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
