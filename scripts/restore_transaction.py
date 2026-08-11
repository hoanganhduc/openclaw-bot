#!/usr/bin/env python3
"""Stage and atomically publish an OpenClaw owner-state restore.

The helper never executes restored content.  It keeps archive data in a private
candidate tree, preserves pre-restore action queues only under an inert
quarantine path, and uses Linux ``renameat2(RENAME_EXCHANGE)`` for an atomic
upgrade when a target already exists.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import time
import unicodedata


ACTION_QUEUES = ("email-queue", "job-queue", "manim-queue", "send-queue")
OVERLAY_ONLY_AUTHORITIES = {
    ".env",
    "credentials",
    "cron",
    "devices",
    "identity",
    "state",
    "openclaw.json",
    "secrets.json",
    "tasks",
    "flows",
    "file-delivery-policy.json",
}
ARCHIVE_AUTHORITY_ROOTS = frozenset(OVERLAY_ONLY_AUTHORITIES)
LEGACY_AGENT_AUTHORITY_RE = re.compile(
    r"^\.?(?:auth-profiles|auth-state|auth|models)\.json(?:[._~-].*)?$",
    re.IGNORECASE,
)
AUTHORITY_ACTIVATION_CONFIRMATION = "ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY"
REPLAY_CONFIRMATION = "REPLAY_QUARANTINED_ACTIONS"
REPLAY_REVIEW_CONFIRMATION = "REVIEW_QUARANTINED_ACTIONS"


class RestoreTransactionError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_directory_nofollow(path: Path) -> int:
    absolute = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor or os.sep, flags | nofollow)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component,
                flags | nofollow,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_safe_directory(
    path: Path,
    *,
    empty: bool = False,
    owner_private: bool = True,
) -> Path:
    absolute = _absolute(path)
    descriptor = _open_directory_nofollow(absolute)
    try:
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid():
            raise RestoreTransactionError("restore directory has the wrong owner")
        if owner_private and stat.S_IMODE(information.st_mode) & 0o077:
            raise RestoreTransactionError("restore directory is not owner-private")
        if empty and os.listdir(descriptor):
            raise RestoreTransactionError("restore candidate must be empty")
    finally:
        os.close(descriptor)
    return absolute


def inspect_directory(path: Path, *, owner_private: bool) -> dict[str, int]:
    absolute = _absolute(path)
    descriptor = _open_directory_nofollow(absolute)
    try:
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid():
            raise RestoreTransactionError("restore directory has the wrong owner")
        forbidden = 0o077 if owner_private else 0o022
        if stat.S_IMODE(information.st_mode) & forbidden:
            raise RestoreTransactionError("restore directory permissions are unsafe")
        return {
            "device": information.st_dev,
            "inode": information.st_ino,
            "uid": information.st_uid,
            "mode": stat.S_IMODE(information.st_mode),
        }
    finally:
        os.close(descriptor)


def _require_identity(
    information: os.stat_result,
    *,
    expected_device: int | None,
    expected_inode: int | None,
    label: str,
) -> None:
    if (expected_device is None) != (expected_inode is None):
        raise RestoreTransactionError(f"{label} identity is incomplete")
    if expected_device is not None and (information.st_dev, information.st_ino) != (
        expected_device,
        expected_inode,
    ):
        raise RestoreTransactionError(f"{label} identity changed during restore")


def _copy_regular(source: Path, destination: Path, mode: int) -> None:
    before = source.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    source_descriptor = os.open(source, flags)
    try:
        opened = os.fstat(source_descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        if identity != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise RestoreTransactionError("restore source changed while opening")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        try:
            with os.fdopen(source_descriptor, "rb", closefd=False) as input_stream, os.fdopen(
                destination_descriptor, "wb", closefd=False
            ) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
            os.fchmod(destination_descriptor, mode)
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        after = os.fstat(source_descriptor)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RestoreTransactionError("restore source changed during copy")
    finally:
        os.close(source_descriptor)


def _is_action_queue(relative: PurePosixPath) -> bool:
    return (
        len(relative.parts) >= 3
        and relative.parts[0] == "workspace"
        and relative.parts[1] == "data"
        and relative.parts[2] in ACTION_QUEUES
    )


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    skip_action_queues: bool,
    allow_symlinks: bool,
) -> None:
    for root, directories, files in os.walk(source, topdown=True, followlinks=False):
        source_root = Path(root)
        relative_root = PurePosixPath(source_root.relative_to(source).as_posix())
        if relative_root == PurePosixPath("."):
            relative_root = PurePosixPath()
        destination_root = destination.joinpath(*relative_root.parts)
        destination_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_directories: list[str] = []
        for name in sorted(directories):
            child = source_root / name
            relative = PurePosixPath(*relative_root.parts, name)
            if skip_action_queues and _is_action_queue(relative):
                continue
            information = child.lstat()
            target = destination.joinpath(*relative.parts)
            if stat.S_ISLNK(information.st_mode):
                if not allow_symlinks:
                    raise RestoreTransactionError("action queue contains a symlink")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.symlink(os.readlink(child), target)
                continue
            if not stat.S_ISDIR(information.st_mode):
                raise RestoreTransactionError("restore source contains a special directory entry")
            safe_directories.append(name)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
        directories[:] = safe_directories
        for name in sorted(files):
            child = source_root / name
            relative = PurePosixPath(*relative_root.parts, name)
            if skip_action_queues and _is_action_queue(relative):
                continue
            information = child.lstat()
            target = destination.joinpath(*relative.parts)
            if stat.S_ISLNK(information.st_mode):
                if not allow_symlinks:
                    raise RestoreTransactionError("action queue contains a symlink")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.symlink(os.readlink(child), target)
            elif stat.S_ISREG(information.st_mode):
                _copy_regular(
                    child,
                    target,
                    stat.S_IMODE(information.st_mode) & ~0o022,
                )
            elif stat.S_ISSOCK(information.st_mode):
                # Runtime sockets are not persistent state and must not be staged.
                continue
            else:
                raise RestoreTransactionError("restore source contains a special file")


def prepare_candidate(source: Path, candidate: Path, *, clone_existing: bool) -> dict[str, object]:
    source = _absolute(source)
    candidate = _ensure_safe_directory(candidate, empty=True)
    source_exists = os.path.lexists(source)
    if source_exists:
        if source.is_symlink() or not source.is_dir():
            raise RestoreTransactionError("restore target is missing or unsafe")
        _ensure_safe_directory(source)
        for name in OVERLAY_ONLY_AUTHORITIES:
            authority = source / name
            if not os.path.lexists(authority):
                continue
            information = authority.lstat()
            expected_directory = name in {
                "credentials",
                "cron",
                "devices",
                "flows",
                "identity",
                "tasks",
            }
            if stat.S_ISLNK(information.st_mode) or (
                expected_directory and not stat.S_ISDIR(information.st_mode)
            ) or (not expected_directory and not stat.S_ISREG(information.st_mode)):
                raise RestoreTransactionError("pre-existing authority/config path is unsafe")
    if clone_existing and source_exists:
        _copy_tree(
            source,
            candidate,
            skip_action_queues=True,
            allow_symlinks=True,
        )

    stamp = (
        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{os.getpid()}-{secrets.token_hex(12)}"
    )
    quarantine_parent = candidate / "recovery-quarantine" / "action-queues"
    candidate_descriptor = _open_directory_nofollow(candidate)
    try:
        quarantine_parent_descriptor = _open_parent(
            candidate_descriptor,
            ("recovery-quarantine", "action-queues"),
        )
        try:
            os.mkdir(stamp, 0o700, dir_fd=quarantine_parent_descriptor)
            os.fsync(quarantine_parent_descriptor)
        finally:
            os.close(quarantine_parent_descriptor)
    finally:
        os.close(candidate_descriptor)
    quarantine = quarantine_parent / stamp
    quarantined: list[str] = []
    if source_exists:
        for queue in ACTION_QUEUES:
            queue_source = source / "workspace" / "data" / queue
            if not os.path.lexists(queue_source):
                continue
            information = queue_source.lstat()
            if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
                raise RestoreTransactionError("pre-existing action queue is unsafe")
            queue_destination = quarantine / queue
            queue_destination.mkdir(parents=True, mode=0o700)
            _copy_tree(
                queue_source,
                queue_destination,
                skip_action_queues=False,
                allow_symlinks=False,
            )
            quarantined.append(queue)
    if quarantined:
        inventory: list[dict[str, str]] = []
        for queue in sorted(quarantined):
            queue_root = quarantine / queue
            for child in sorted(queue_root.iterdir(), key=lambda path: path.name):
                if not child.name.endswith(".json"):
                    continue
                information = child.lstat()
                if not stat.S_ISREG(information.st_mode) or stat.S_ISLNK(information.st_mode):
                    raise RestoreTransactionError("quarantined action queue contains an unsafe entry")
                child.chmod(0o600, follow_symlinks=False)
                inventory.append(
                    {
                        "queue": queue,
                        "name": child.name,
                        "sha256": _hash_regular(child),
                    }
                )
        inventory_digest = hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        metadata = quarantine / "QUARANTINE.json"
        payload = {
            "schema": "openclaw.action-queue-quarantine/v2",
            "createdAt": stamp,
            "quarantineId": secrets.token_hex(32),
            "queues": quarantined,
            "inventory": inventory,
            "inventorySha256": inventory_digest,
            "replayRequires": REPLAY_CONFIRMATION,
            "reviewRequires": REPLAY_REVIEW_CONFIRMATION,
        }
        _write_private_exclusive(
            metadata,
            (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        )
    else:
        # Do not leave empty provenance directories when no queue existed.
        os.rmdir(quarantine)
    return {"quarantinedQueues": quarantined, "quarantine": str(quarantine) if quarantined else None}


def _open_child_directory(parent_descriptor: int, component: str, mode: int = 0o700) -> int:
    try:
        os.mkdir(component, mode, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    return os.open(
        component,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )


def _open_parent(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for component in parts:
            next_descriptor = _open_child_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _legacy_agent_authority(relative: PurePosixPath) -> bool:
    return (
        len(relative.parts) == 4
        and relative.parts[0] == "agents"
        and relative.parts[2] == "agent"
        and LEGACY_AGENT_AUTHORITY_RE.fullmatch(
            unicodedata.normalize("NFKC", relative.parts[3])
        )
        is not None
    )


def _is_under(relative: PurePosixPath, prefix: PurePosixPath) -> bool:
    return len(relative.parts) >= len(prefix.parts) and relative.parts[: len(prefix.parts)] == prefix.parts


def _overlay_protected(
    relative: PurePosixPath,
    preserved: set[str],
    quarantined: set[PurePosixPath],
) -> bool:
    return (
        bool(relative.parts)
        and relative.parts[0] in preserved
        or any(_is_under(relative, prefix) for prefix in quarantined)
    )


def _hash_regular(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
            raise RestoreTransactionError("archive authority contains an unsafe file")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RestoreTransactionError("private metadata write was truncated")
        view = view[written:]


def _write_private_exclusive(path: Path, payload: bytes) -> None:
    """Create one private metadata file without following any path component."""

    parent = _open_directory_nofollow(path.parent)
    descriptor: int | None = None
    try:
        parent_information = os.fstat(parent)
        if (
            parent_information.st_uid != os.geteuid()
            or stat.S_IMODE(parent_information.st_mode) & 0o077
        ):
            raise RestoreTransactionError("private metadata parent is unsafe")
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent,
        )
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _quarantine_archive_authorities(
    stage: Path,
    candidate: Path,
    *,
    activate_reviewed: bool,
) -> set[PurePosixPath]:
    """Copy archive authority to inert storage and return overlay exclusions.

    Legacy agent JSON/model files never become active.  Top-level provider,
    plugin/scheduler, SecretRef, identity, and credential authorities require
    the exact reviewed-activation token at restore entry; otherwise they are
    retained only under recovery quarantine.
    """

    roots: list[tuple[PurePosixPath, str]] = []
    for name in sorted(ARCHIVE_AUTHORITY_ROOTS):
        source = stage / name
        if os.path.lexists(source):
            roots.append((PurePosixPath(name), "review-required"))
    agent_root = stage / "agents"
    if os.path.lexists(agent_root):
        if agent_root.is_symlink() or not agent_root.is_dir():
            raise RestoreTransactionError("verified stage has an unsafe agent root")
        for agent in os.scandir(agent_root):
            if agent.is_symlink() or not agent.is_dir(follow_symlinks=False):
                raise RestoreTransactionError("verified stage has an unsafe agent entry")
            state = Path(agent.path) / "agent"
            if not os.path.lexists(state):
                continue
            if state.is_symlink() or not state.is_dir():
                raise RestoreTransactionError("verified stage has an unsafe agent state path")
            for entry in os.scandir(state):
                relative = PurePosixPath("agents", agent.name, "agent", entry.name)
                if LEGACY_AGENT_AUTHORITY_RE.fullmatch(
                    unicodedata.normalize("NFKC", entry.name)
                ):
                    roots.append((relative, "legacy-never-active"))

    quarantined: set[PurePosixPath] = {
        relative
        for relative, reason in roots
        if reason == "legacy-never-active" or not activate_reviewed
    }
    if not quarantined:
        return quarantined

    stamp = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    quarantine = candidate / "recovery-quarantine" / "archive-authority" / stamp
    payload_root = quarantine / "payload"
    inventory: list[dict[str, object]] = []
    for relative, reason in roots:
        if relative not in quarantined:
            continue
        source = stage.joinpath(*relative.parts)
        destination = payload_root.joinpath(*relative.parts)
        information = source.lstat()
        if stat.S_ISDIR(information.st_mode) and not stat.S_ISLNK(information.st_mode):
            destination.mkdir(parents=True, mode=0o700)
            _copy_tree(
                source,
                destination,
                skip_action_queues=False,
                allow_symlinks=False,
            )
            for root, directories, files in os.walk(source, topdown=True, followlinks=False):
                directories.sort()
                for name in sorted(files):
                    child = Path(root) / name
                    child_relative = PurePosixPath(child.relative_to(stage).as_posix())
                    inventory.append(
                        {
                            "path": child_relative.as_posix(),
                            "reason": reason,
                            "sha256": _hash_regular(child),
                        }
                    )
        elif stat.S_ISREG(information.st_mode) and not stat.S_ISLNK(information.st_mode):
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _copy_regular(source, destination, 0o600)
            inventory.append(
                {
                    "path": relative.as_posix(),
                    "reason": reason,
                    "sha256": _hash_regular(source),
                }
            )
        else:
            raise RestoreTransactionError("archive authority contains an unsafe entry")
    metadata = quarantine / "QUARANTINE.json"
    candidate_descriptor = _open_directory_nofollow(candidate)
    try:
        quarantine_descriptor = _open_parent(
            candidate_descriptor,
            ("recovery-quarantine", "archive-authority", stamp),
        )
        os.close(quarantine_descriptor)
    finally:
        os.close(candidate_descriptor)
    _write_private_exclusive(
        metadata,
        (
            json.dumps(
                {
                    "schema": "openclaw.archive-authority-quarantine/v1",
                    "createdAt": stamp,
                    "activationRequires": AUTHORITY_ACTIVATION_CONFIRMATION,
                    "defaultDisposition": "inert",
                    "inventory": inventory,
                },
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    )
    return quarantined


def apply_overlay(
    stage: Path,
    candidate: Path,
    *,
    preserve_authorities: bool,
    isolate_authorities: bool,
    activate_reviewed_authorities: bool = False,
) -> None:
    stage = _ensure_safe_directory(stage)
    candidate = _ensure_safe_directory(candidate)
    embedded_authority = (
        stage
        / "recovery-quarantine"
        / "archive-authority"
        / "payload"
    )
    if activate_reviewed_authorities and os.path.lexists(embedded_authority):
        if embedded_authority.is_symlink() or not embedded_authority.is_dir():
            raise RestoreTransactionError("embedded archive authority payload is unsafe")
        apply_overlay(
            embedded_authority,
            candidate,
            preserve_authorities=False,
            isolate_authorities=False,
            activate_reviewed_authorities=True,
        )
    if isolate_authorities:
        preserved = set(OVERLAY_ONLY_AUTHORITIES)
    else:
        preserved = {
            name
            for name in OVERLAY_ONLY_AUTHORITIES
            if preserve_authorities and os.path.lexists(candidate / name)
        }
    quarantined = _quarantine_archive_authorities(
        stage,
        candidate,
        activate_reviewed=activate_reviewed_authorities,
    )
    root_descriptor = _open_directory_nofollow(candidate)
    try:
        for root, directories, files in os.walk(stage, topdown=True, followlinks=False):
            source_root = Path(root)
            relative_root = PurePosixPath(source_root.relative_to(stage).as_posix())
            if relative_root == PurePosixPath("."):
                relative_root = PurePosixPath()
            if _overlay_protected(relative_root, preserved, quarantined):
                directories[:] = []
                continue
            safe_directories: list[str] = []
            for name in sorted(directories):
                relative = PurePosixPath(*relative_root.parts, name)
                if _overlay_protected(relative, preserved, quarantined):
                    continue
                information = (source_root / name).lstat()
                if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
                    raise RestoreTransactionError("verified stage contains a non-directory")
                descriptor = _open_parent(root_descriptor, relative.parts)
                os.fchmod(descriptor, 0o700)
                os.close(descriptor)
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in sorted(files):
                relative = PurePosixPath(*relative_root.parts, name)
                if _overlay_protected(relative, preserved, quarantined):
                    continue
                source = source_root / name
                information = source.lstat()
                if not stat.S_ISREG(information.st_mode) or stat.S_ISLNK(information.st_mode):
                    raise RestoreTransactionError("verified stage contains a non-regular file")
                parent_descriptor = _open_parent(root_descriptor, relative.parts[:-1])
                temporary_name = f".{name}.restore.{secrets.token_hex(8)}"
                destination_descriptor: int | None = None
                try:
                    try:
                        existing = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None and not stat.S_ISREG(existing.st_mode):
                        raise RestoreTransactionError("restore file collides with an unsafe destination")
                    destination_descriptor = os.open(
                        temporary_name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_descriptor,
                    )
                    source_descriptor = os.open(
                        source,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        with os.fdopen(source_descriptor, "rb", closefd=False) as input_stream, os.fdopen(
                            destination_descriptor, "wb", closefd=False
                        ) as output_stream:
                            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                            output_stream.flush()
                        os.fchmod(destination_descriptor, 0o600)
                        os.fsync(destination_descriptor)
                    finally:
                        os.close(source_descriptor)
                    os.rename(
                        temporary_name,
                        name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                    temporary_name = ""
                finally:
                    if destination_descriptor is not None:
                        os.close(destination_descriptor)
                    if temporary_name:
                        try:
                            os.unlink(temporary_name, dir_fd=parent_descriptor)
                        except FileNotFoundError:
                            pass
                    os.close(parent_descriptor)
        os.fsync(root_descriptor)
    finally:
        os.close(root_descriptor)


def create_empty_action_queues(candidate: Path) -> None:
    candidate = _ensure_safe_directory(candidate)
    root_descriptor = _open_directory_nofollow(candidate)
    try:
        data_descriptor = _open_parent(root_descriptor, ("workspace", "data"))
        try:
            for queue in ACTION_QUEUES:
                try:
                    information = os.stat(queue, dir_fd=data_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    information = None
                if information is not None:
                    if not stat.S_ISDIR(information.st_mode):
                        raise RestoreTransactionError("candidate action queue is unsafe")
                descriptor = _open_child_directory(data_descriptor, queue)
                if os.listdir(descriptor):
                    os.close(descriptor)
                    raise RestoreTransactionError("candidate action queue is not empty")
                os.fchmod(descriptor, 0o700)
                os.close(descriptor)
            os.fsync(data_descriptor)
        finally:
            os.close(data_descriptor)
    finally:
        os.close(root_descriptor)


def _rename_exchange(parent_descriptor: int, left: str, right: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RestoreTransactionError("atomic rename exchange is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(parent_descriptor, os.fsencode(left), parent_descriptor, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise RestoreTransactionError(f"atomic restore exchange failed ({errno.errorcode.get(error, error)})")


def commit_candidate(
    candidate: Path,
    target: Path,
    rollback: Path,
    *,
    expected_parent_device: int | None = None,
    expected_parent_inode: int | None = None,
    expected_candidate_device: int | None = None,
    expected_candidate_inode: int | None = None,
    expected_target_device: int | None = None,
    expected_target_inode: int | None = None,
    expected_target_missing: bool = False,
) -> dict[str, object]:
    candidate = _ensure_safe_directory(candidate)
    os.chmod(candidate, 0o700, follow_symlinks=False)
    create_empty_action_queues(candidate)
    target = _absolute(target)
    rollback = _absolute(rollback)
    if candidate.parent != target.parent or rollback.parent != target.parent:
        raise RestoreTransactionError("candidate, target, and rollback must share one parent")
    parent_descriptor = _open_directory_nofollow(target.parent)
    try:
        parent_information = os.fstat(parent_descriptor)
        if parent_information.st_uid != os.geteuid() or stat.S_IMODE(parent_information.st_mode) & 0o022:
            raise RestoreTransactionError("restore parent is not owner-controlled")
        _require_identity(
            parent_information,
            expected_device=expected_parent_device,
            expected_inode=expected_parent_inode,
            label="restore parent",
        )
        candidate_information = os.stat(
            candidate.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if not stat.S_ISDIR(candidate_information.st_mode):
            raise RestoreTransactionError("restore candidate is unsafe")
        if (
            candidate_information.st_uid != os.geteuid()
            or stat.S_IMODE(candidate_information.st_mode) & 0o077
        ):
            raise RestoreTransactionError("restore candidate is not owner-private")
        _require_identity(
            candidate_information,
            expected_device=expected_candidate_device,
            expected_inode=expected_candidate_inode,
            label="restore candidate",
        )
        try:
            target_information = os.stat(
                target.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            target_information = None
        if expected_target_missing and target_information is not None:
            raise RestoreTransactionError("restore target appeared during restore")
        if not expected_target_missing and expected_target_device is not None:
            if target_information is None:
                raise RestoreTransactionError("restore target disappeared during restore")
            _require_identity(
                target_information,
                expected_device=expected_target_device,
                expected_inode=expected_target_inode,
                label="restore target",
            )
        if target_information is not None and not stat.S_ISDIR(target_information.st_mode):
            raise RestoreTransactionError("restore target is unsafe")
        if target_information is not None and (
            target_information.st_uid != os.geteuid()
            or stat.S_IMODE(target_information.st_mode) & 0o077
        ):
            raise RestoreTransactionError("restore target is not owner-private")
        try:
            os.stat(rollback.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RestoreTransactionError("restore rollback path already exists")
        if target_information is None:
            os.rename(
                candidate.name,
                target.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            rollback_path: str | None = None
        else:
            os.rename(
                candidate.name,
                rollback.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            # Put the complete new candidate at its durable rollback pathname
            # first, then exchange that pathname with the live target.  The
            # single exchange is the publication point: before it, the old
            # target is unchanged; after it, the new target and old rollback
            # both already have their final names.
            _rename_exchange(parent_descriptor, rollback.name, target.name)
            os.chmod(rollback.name, 0o700, dir_fd=parent_descriptor, follow_symlinks=False)
            rollback_path = os.fspath(rollback)
        os.fsync(parent_descriptor)
        return {"rollback": rollback_path, "published": os.fspath(target)}
    finally:
        os.close(parent_descriptor)


def _load_private_json(path: Path, *, maximum: int = 8 * 1024 * 1024) -> dict[str, object]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o077
            or information.st_size > maximum
        ):
            raise RestoreTransactionError("queue replay metadata is unsafe")
        chunks: list[bytes] = []
        remaining = information.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise RestoreTransactionError("queue replay metadata is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > maximum:
        raise RestoreTransactionError("queue replay metadata is oversized")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreTransactionError("queue replay metadata is invalid") from exc
    if not isinstance(value, dict):
        raise RestoreTransactionError("queue replay metadata is not an object")
    return value


def _validated_quarantine(prefix: Path, quarantine: Path) -> tuple[Path, dict[str, object]]:
    prefix = _ensure_safe_directory(prefix)
    quarantine = _ensure_safe_directory(quarantine)
    recovery_root = prefix / "recovery-quarantine" / "action-queues"
    if os.path.commonpath((os.fspath(recovery_root), os.fspath(quarantine))) != os.fspath(
        recovery_root
    ):
        raise RestoreTransactionError("queue quarantine is outside the selected owner state")
    metadata = _load_private_json(quarantine / "QUARANTINE.json")
    inventory = metadata.get("inventory")
    encoded_inventory = json.dumps(
        inventory, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if (
        metadata.get("schema") != "openclaw.action-queue-quarantine/v2"
        or metadata.get("replayRequires") != REPLAY_CONFIRMATION
        or metadata.get("reviewRequires") != REPLAY_REVIEW_CONFIRMATION
        or not isinstance(metadata.get("quarantineId"), str)
        or not isinstance(inventory, list)
        or metadata.get("inventorySha256")
        != hashlib.sha256(encoded_inventory).hexdigest()
    ):
        raise RestoreTransactionError("queue quarantine provenance metadata is invalid")
    return quarantine, metadata


def approve_queue_replay(
    prefix: Path,
    quarantine: Path,
    *,
    confirmation: str,
) -> None:
    if confirmation != REPLAY_REVIEW_CONFIRMATION:
        raise RestoreTransactionError("queue review requires the exact confirmation token")
    quarantine, metadata = _validated_quarantine(prefix, quarantine)
    approval = quarantine / "APPROVAL.json"
    payload = json.dumps(
        {
            "schema": "openclaw.action-queue-replay-approval/v1",
            "approvedAt": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
            "quarantineId": metadata["quarantineId"],
            "inventorySha256": metadata["inventorySha256"],
            "scope": "exact-inventory-only",
        },
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _write_private_exclusive(approval, payload)


def _rename_noreplace(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RestoreTransactionError("no-overwrite queue replay is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(
        source_descriptor,
        os.fsencode(source_name),
        destination_descriptor,
        os.fsencode(destination_name),
        1,
    ) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise RestoreTransactionError("queue replay refused to overwrite a concurrent job")
        raise RestoreTransactionError(
            f"no-overwrite queue replay failed ({errno.errorcode.get(error, error)})"
        )


def _open_lock(path: Path, *, exclusive: bool = True) -> int:
    parent = _open_directory_nofollow(path.parent)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    information = os.fstat(descriptor)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_nlink != 1
        or information.st_uid != os.geteuid()
        or stat.S_IMODE(information.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise RestoreTransactionError("queue replay lock is unsafe")
    fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    return descriptor


def _load_replay_journal(
    path: Path,
    *,
    metadata: dict[str, object],
    expected: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    if not os.path.lexists(path):
        return {}
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o077
            or information.st_size > 8 * 1024 * 1024
        ):
            raise RestoreTransactionError("queue replay journal is unsafe")
        chunks: list[bytes] = []
        remaining = information.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise RestoreTransactionError("queue replay journal is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    states: dict[tuple[str, str], str] = {}
    for raw_line in b"".join(chunks).splitlines():
        try:
            record = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestoreTransactionError("queue replay journal is invalid") from exc
        if not isinstance(record, dict):
            raise RestoreTransactionError("queue replay journal record is invalid")
        queue = record.get("queue")
        name = record.get("name")
        digest = record.get("sha256")
        status_value = record.get("status")
        if (
            record.get("schema") != "openclaw.action-queue-replay-journal/v3"
            or record.get("quarantineId") != metadata.get("quarantineId")
            or record.get("inventorySha256") != metadata.get("inventorySha256")
            or not isinstance(queue, str)
            or not isinstance(name, str)
            or status_value not in {"prepared", "published"}
        ):
            raise RestoreTransactionError("queue replay journal provenance is invalid")
        key = (queue, name)
        if key not in expected or digest != expected[key]:
            raise RestoreTransactionError("queue replay journal provenance is invalid")
        previous = states.get(key)
        if status_value == "prepared" and previous is not None:
            raise RestoreTransactionError("queue replay journal transition is invalid")
        if status_value == "published" and previous != "prepared":
            raise RestoreTransactionError("queue replay journal transition is invalid")
        states[key] = status_value
    return states


def _append_replay_journal(
    descriptor: int,
    *,
    metadata: dict[str, object],
    queue: str,
    name: str,
    digest: str,
    status_value: str,
) -> None:
    record = json.dumps(
        {
            "schema": "openclaw.action-queue-replay-journal/v3",
            "quarantineId": metadata["quarantineId"],
            "inventorySha256": metadata["inventorySha256"],
            "queue": queue,
            "name": name,
            "sha256": digest,
            "status": status_value,
        },
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _write_all(descriptor, record)
    os.fsync(descriptor)


def _open_private_child(parent: int, name: str) -> int:
    descriptor = _open_child_directory(parent, name, 0o700)
    information = os.fstat(descriptor)
    if (
        information.st_uid != os.geteuid()
        or stat.S_IMODE(information.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise RestoreTransactionError("queue replay staging directory is unsafe")
    return descriptor


def _exists_at(parent: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _copy_replay_source(
    source_parent: int,
    name: str,
    stage_parent: int,
    expected_digest: str,
) -> None:
    named = os.stat(name, dir_fd=source_parent, follow_symlinks=False)
    source = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=source_parent,
    )
    stage: int | None = None
    try:
        before = os.fstat(source)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > 8 * 1024 * 1024
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RestoreTransactionError("queue replay source is unsafe")
        stage = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=stage_parent,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source, 64 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > 8 * 1024 * 1024:
                raise RestoreTransactionError("queue replay source is oversized")
            digest.update(chunk)
            _write_all(stage, chunk)
        os.fchmod(stage, 0o600)
        os.fsync(stage)
        after = os.fstat(source)
        named_after = os.stat(name, dir_fd=source_parent, follow_symlinks=False)
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
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise RestoreTransactionError("queue replay source changed while staging")
        if copied != before.st_size or digest.hexdigest() != expected_digest:
            raise RestoreTransactionError("queue replay source digest changed")
        os.fsync(stage_parent)
    except Exception:
        try:
            os.unlink(name, dir_fd=stage_parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        if stage is not None:
            os.close(stage)
        os.close(source)


def _verify_regular_at(
    parent: int,
    name: str,
    expected_digest: str,
    *,
    private: bool = True,
) -> tuple[int, int]:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & (0o077 if private else 0o022)
            or before.st_size > 8 * 1024 * 1024
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RestoreTransactionError("queue replay file is unsafe")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
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
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ) or digest.hexdigest() != expected_digest:
            raise RestoreTransactionError("queue replay file identity changed")
        return before.st_dev, before.st_ino
    finally:
        os.close(descriptor)


def _unlink_verified_source(parent: int, name: str, expected_digest: str) -> None:
    expected_identity = _verify_regular_at(
        parent, name, expected_digest, private=False
    )
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != expected_identity:
        raise RestoreTransactionError("queue replay source changed before cleanup")
    os.unlink(name, dir_fd=parent)
    os.fsync(parent)


def replay_queues(prefix: Path, quarantine: Path, *, confirmation: str) -> None:
    if confirmation != REPLAY_CONFIRMATION:
        raise RestoreTransactionError("queue replay requires the exact confirmation token")
    prefix = _ensure_safe_directory(prefix)
    quarantine, metadata = _validated_quarantine(prefix, quarantine)
    approval = _load_private_json(quarantine / "APPROVAL.json")
    if (
        approval.get("schema") != "openclaw.action-queue-replay-approval/v1"
        or approval.get("quarantineId") != metadata.get("quarantineId")
        or approval.get("inventorySha256") != metadata.get("inventorySha256")
        or approval.get("scope") != "exact-inventory-only"
    ):
        raise RestoreTransactionError("queue replay approval does not bind this inventory")

    owner_lock = prefix.parent / f".{prefix.name}.owner-state.lock"
    owner_descriptor: int | None = None
    queue_lock = prefix / "workspace" / "data" / ".action-queue-replay.lock"
    queue_descriptor: int | None = None
    journal_descriptor: int | None = None
    try:
        owner_descriptor = _open_lock(owner_lock)
        queue_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        queue_descriptor = _open_lock(queue_lock)
        expected: dict[tuple[str, str], str] = {}
        inventory = metadata["inventory"]
        for item in inventory:  # type: ignore[assignment]
            if not isinstance(item, dict):
                raise RestoreTransactionError("queue replay inventory entry is invalid")
            queue = item.get("queue")
            name = item.get("name")
            digest = item.get("sha256")
            if (
                queue not in ACTION_QUEUES
                or not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or "/" in name
                or not name.endswith(".json")
                or len(os.fsencode(name)) > 255
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or (queue, name) in expected
            ):
                raise RestoreTransactionError("queue replay inventory entry is invalid")
            expected[(queue, name)] = digest

        journal_path = quarantine / ".replay-journal.jsonl"
        states = _load_replay_journal(
            journal_path,
            metadata=metadata,
            expected=expected,
        )
        journal_parent = _open_directory_nofollow(quarantine)
        try:
            journal_descriptor = os.open(
                journal_path.name,
                os.O_WRONLY
                | os.O_APPEND
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=journal_parent,
            )
        finally:
            os.close(journal_parent)
        journal_information = os.fstat(journal_descriptor)
        if (
            not stat.S_ISREG(journal_information.st_mode)
            or journal_information.st_nlink != 1
            or journal_information.st_uid != os.geteuid()
            or stat.S_IMODE(journal_information.st_mode) & 0o077
        ):
            raise RestoreTransactionError("queue replay journal is unsafe")

        quarantine_descriptor = _open_directory_nofollow(quarantine)
        try:
            stage_root_descriptor = _open_private_child(
                quarantine_descriptor, ".replay-stage"
            )
        finally:
            os.close(quarantine_descriptor)

        queue_paths: dict[str, tuple[Path, Path]] = {}
        for queue in ACTION_QUEUES:
            source = quarantine / queue
            queue_expected = {
                name: digest
                for (inventory_queue, name), digest in expected.items()
                if inventory_queue == queue
            }
            if not os.path.lexists(source):
                if queue_expected:
                    raise RestoreTransactionError("queue replay inventory source is missing")
                continue
            source = _ensure_safe_directory(source)
            destination = _ensure_safe_directory(prefix / "workspace" / "data" / queue)
            queue_paths[queue] = (source, destination)
            actual_source_names = {
                name for name in os.listdir(source) if name.endswith(".json")
            }
            if not actual_source_names.issubset(queue_expected):
                raise RestoreTransactionError("queue replay inventory gained an unreviewed job")
        try:
            for queue in ACTION_QUEUES:
                if queue not in queue_paths:
                    continue
                source, destination = queue_paths[queue]
                source_descriptor = _open_directory_nofollow(source)
                destination_descriptor = _open_directory_nofollow(destination)
                stage_descriptor = _open_private_child(stage_root_descriptor, queue)
                try:
                    for (inventory_queue, name), digest in sorted(expected.items()):
                        if inventory_queue != queue:
                            continue
                        key = (queue, name)
                        source_exists = _exists_at(source_descriptor, name)
                        stage_exists = _exists_at(stage_descriptor, name)
                        destination_exists = _exists_at(destination_descriptor, name)
                        state_value = states.get(key)

                        if state_value == "published":
                            if not destination_exists:
                                raise RestoreTransactionError(
                                    "published queue replay destination is missing"
                                )
                            _verify_regular_at(destination_descriptor, name, digest)
                            if stage_exists:
                                raise RestoreTransactionError(
                                    "published queue replay retained ambiguous staging"
                                )
                            if source_exists:
                                _unlink_verified_source(source_descriptor, name, digest)
                            continue

                        if destination_exists:
                            if state_value != "prepared" or stage_exists:
                                raise RestoreTransactionError(
                                    "queue replay has an ambiguous destination"
                                )
                            _verify_regular_at(destination_descriptor, name, digest)
                        else:
                            if state_value is None and not stage_exists:
                                if not source_exists:
                                    raise RestoreTransactionError(
                                        "queue replay inventory source is missing"
                                    )
                                _copy_replay_source(
                                    source_descriptor,
                                    name,
                                    stage_descriptor,
                                    digest,
                                )
                                stage_exists = True
                            if not stage_exists:
                                raise RestoreTransactionError(
                                    "prepared queue replay staging is missing"
                                )
                            _verify_regular_at(stage_descriptor, name, digest)
                            if state_value is None:
                                _append_replay_journal(
                                    journal_descriptor,
                                    metadata=metadata,
                                    queue=queue,
                                    name=name,
                                    digest=digest,
                                    status_value="prepared",
                                )
                                states[key] = "prepared"
                            _rename_noreplace(
                                stage_descriptor,
                                name,
                                destination_descriptor,
                                name,
                            )
                            os.fsync(stage_descriptor)
                            os.fsync(destination_descriptor)
                            _verify_regular_at(destination_descriptor, name, digest)

                        _append_replay_journal(
                            journal_descriptor,
                            metadata=metadata,
                            queue=queue,
                            name=name,
                            digest=digest,
                            status_value="published",
                        )
                        states[key] = "published"
                        if source_exists:
                            _unlink_verified_source(source_descriptor, name, digest)
                finally:
                    os.close(stage_descriptor)
                    os.close(source_descriptor)
                    os.close(destination_descriptor)
        finally:
            os.close(stage_root_descriptor)
    except BlockingIOError as exc:
        raise RestoreTransactionError("queue replay is blocked by an active state writer") from exc
    finally:
        if journal_descriptor is not None:
            os.close(journal_descriptor)
        if queue_descriptor is not None:
            os.close(queue_descriptor)
        if owner_descriptor is not None:
            os.close(owner_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--clone-existing", action="store_true")
    overlay = subparsers.add_parser("overlay")
    overlay.add_argument("--stage", type=Path, required=True)
    overlay.add_argument("--candidate", type=Path, required=True)
    overlay.add_argument("--preserve-authorities", action="store_true")
    overlay.add_argument("--isolate-authorities", action="store_true")
    overlay.add_argument("--activate-reviewed-authorities")
    queues = subparsers.add_parser("empty-queues")
    queues.add_argument("--candidate", type=Path, required=True)
    commit = subparsers.add_parser("commit")
    commit.add_argument("--candidate", type=Path, required=True)
    commit.add_argument("--target", type=Path, required=True)
    commit.add_argument("--rollback", type=Path, required=True)
    commit.add_argument("--expected-parent-device", type=int)
    commit.add_argument("--expected-parent-inode", type=int)
    commit.add_argument("--expected-candidate-device", type=int)
    commit.add_argument("--expected-candidate-inode", type=int)
    commit.add_argument("--expected-target-device", type=int)
    commit.add_argument("--expected-target-inode", type=int)
    commit.add_argument("--expected-target-missing", action="store_true")
    inspect = subparsers.add_parser("inspect-directory")
    inspect.add_argument("--path", type=Path, required=True)
    inspect.add_argument("--owner-private", action="store_true")
    replay = subparsers.add_parser("replay-queues")
    replay.add_argument("--prefix", type=Path, required=True)
    replay.add_argument("--quarantine", type=Path, required=True)
    replay.add_argument("--confirm", required=True)
    approve = subparsers.add_parser("approve-queues")
    approve.add_argument("--prefix", type=Path, required=True)
    approve.add_argument("--quarantine", type=Path, required=True)
    approve.add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_candidate(
                args.source,
                args.candidate,
                clone_existing=args.clone_existing,
            )
            print(json.dumps(result, sort_keys=True))
        elif args.command == "overlay":
            if args.activate_reviewed_authorities not in {
                None,
                AUTHORITY_ACTIVATION_CONFIRMATION,
            }:
                raise RestoreTransactionError(
                    "archive authority activation requires the exact review token"
                )
            apply_overlay(
                args.stage,
                args.candidate,
                preserve_authorities=args.preserve_authorities,
                isolate_authorities=args.isolate_authorities,
                activate_reviewed_authorities=(
                    args.activate_reviewed_authorities
                    == AUTHORITY_ACTIVATION_CONFIRMATION
                ),
            )
        elif args.command == "empty-queues":
            create_empty_action_queues(args.candidate)
        elif args.command == "commit":
            print(
                json.dumps(
                    commit_candidate(
                        args.candidate,
                        args.target,
                        args.rollback,
                        expected_parent_device=args.expected_parent_device,
                        expected_parent_inode=args.expected_parent_inode,
                        expected_candidate_device=args.expected_candidate_device,
                        expected_candidate_inode=args.expected_candidate_inode,
                        expected_target_device=args.expected_target_device,
                        expected_target_inode=args.expected_target_inode,
                        expected_target_missing=args.expected_target_missing,
                    ),
                    sort_keys=True,
                )
            )
        elif args.command == "inspect-directory":
            print(
                json.dumps(
                    inspect_directory(args.path, owner_private=args.owner_private),
                    sort_keys=True,
                )
            )
        elif args.command == "approve-queues":
            approve_queue_replay(
                args.prefix,
                args.quarantine,
                confirmation=args.confirm,
            )
        else:
            replay_queues(args.prefix, args.quarantine, confirmation=args.confirm)
    except (OSError, RestoreTransactionError) as exc:
        print(f"restore transaction: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
