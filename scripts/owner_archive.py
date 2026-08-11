#!/usr/bin/env python3
"""Build, crypt, verify, and extract inert owner-data archives.

The native ``openclaw backup create`` archive is the authority for every
``*.sqlite`` member.  Non-SQLite owner data keeps the historical allowlist and
is copied directly, with before/after identity checks.  No credential payload
is decoded or printed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import unicodedata


OWNER_SCHEMA = "openclaw.owner-archive/v5"
MARKER_NAME = "owner-snapshot.json"
MAX_MEMBERS = 100_000
MAX_SCAN_ENTRIES = 200_000
MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
MAX_MEMBER_SIZE = 512 * 1024 * 1024
MAX_MANIFEST_SIZE = 1024 * 1024
MAX_MEMBER_NAME_BYTES = 512
MAX_OPERATION_SECONDS = 300
TRUSTED_GPG_PATH = Path("/usr/bin/gpg")
TRUSTED_GPGCONF_PATH = Path("/usr/bin/gpgconf")
GPG_HOME_RE = re.compile(r"^\.openclaw-gpg-[0-9a-f]{32}$")
MAX_GPG_HOME_ENTRIES = 10_000
PERSISTENT_PLAINTEXT_ACK = "ACKNOWLEDGE_PERSISTENT_OWNER_PLAINTEXT"
SQLITE_EXTENSIONS = (".sqlite", ".sqlite3", ".db")
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
FILE_DELIVERY_POLICY = "file-delivery-policy.json"
ROOT_FILES = {
    "openclaw.json",
    "secrets.json",
    ".env",
    ".stignore",
    FILE_DELIVERY_POLICY,
}
ROOT_TREES = {
    "credentials",
    "devices",
    "identity",
    "state",
    "cron",
    "media",
    "memory",
    "logs",
    "tasks",
    "flows",
}
ARCHIVE_AUTHORITY_ROOTS = ROOT_FILES | {
    "credentials",
    "devices",
    "identity",
    "state",
    "cron",
    "tasks",
    "flows",
}
ARCHIVE_AUTHORITY_PREFIX = PurePosixPath(
    "recovery-quarantine", "archive-authority", "payload"
)
SCAN_ROOT_TREES = ROOT_TREES | {"agents"}
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
AGENT_STATE_FILES = {
    "auth-profiles.json",
    "auth-state.json",
    "auth.json",
    "models.json",
    "openclaw-agent.sqlite",
}
LEGACY_AGENT_AUTHORITY_RE = re.compile(
    r"^\.?(?:auth-profiles|auth-state|auth|models)\.json(?:[._~-].*)?$",
    re.IGNORECASE,
)
AUX_WORKSPACES = {
    "workspace-host",
    "workspace-moltbook",
    "workspace-review",
    "workspace-sanitizer",
    "workspace-moltbook-reviewer",
}
WORKSPACE_TREES = {
    "data",
    "memory",
    "reports",
}
AUX_WORKSPACE_TREES = {"data", "memory", "reports"}
REGENERABLE_COMPONENTS = {
    ".cache",
    "__pycache__",
    "cache",
    "caches",
    "node_modules",
}
WORKSPACE_ACTION_QUEUES = {
    "email-queue",
    "job-queue",
    "manim-queue",
    "send-queue",
}
AGENT_DB_TABLE_COLUMNS = {
    "schema_meta": {
        "meta_key",
        "role",
        "schema_version",
        "agent_id",
        "app_version",
        "created_at",
        "updated_at",
    },
    "auth_profile_store": {"store_key", "store_json", "updated_at"},
    "auth_profile_state": {"state_key", "state_json", "updated_at"},
}
GLOBAL_STATE_PATH = PurePosixPath("state", "openclaw.sqlite")
GLOBAL_DB_TABLE_COLUMNS = {
    "schema_meta": {
        "meta_key",
        "role",
        "schema_version",
        "agent_id",
        "app_version",
        "created_at",
        "updated_at",
    },
    "auth_profile_stores": {"store_key", "store_json", "updated_at"},
    "auth_profile_state": {"store_key", "state_json", "updated_at"},
    "device_identities": {
        "identity_key",
        "device_id",
        "public_key_pem",
        "private_key_pem",
        "created_at_ms",
        "updated_at_ms",
    },
    "device_auth_tokens": {
        "device_id",
        "role",
        "token",
        "scopes_json",
        "updated_at_ms",
    },
    "device_bootstrap_tokens": {
        "token_key",
        "token",
        "device_id",
        "public_key",
        "profile_json",
        "redeemed_profile_json",
        "pending_profile_json",
        "issued_at_ms",
        "last_used_at_ms",
    },
    "web_push_subscriptions": {
        "endpoint_hash",
        "subscription_id",
        "endpoint",
        "p256dh",
        "auth",
        "created_at_ms",
        "updated_at_ms",
    },
    "web_push_vapid_keys": {
        "key_id",
        "public_key",
        "private_key",
        "subject",
        "updated_at_ms",
    },
    "apns_registrations": {
        "node_id",
        "transport",
        "token",
        "relay_handle",
        "send_grant",
        "installation_id",
        "topic",
        "environment",
        "distribution",
        "token_debug_suffix",
        "updated_at_ms",
    },
}


class ArchiveError(RuntimeError):
    pass


class _ClosingDescriptor:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def __enter__(self) -> int:
        return self.descriptor

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        os.close(self.descriptor)


def _open_directory_nofollow(path: Path) -> int:
    """Open an absolute directory one component at a time without links."""

    absolute = Path(os.path.abspath(path.expanduser()))
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
    absolute = Path(os.path.abspath(path))
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


def _validate_private_temp_root(path: Path, *, persistent_allowed: bool) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    descriptor = _open_directory_nofollow(absolute)
    try:
        information = os.fstat(descriptor)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ArchiveError("owner plaintext staging directory is unsafe")
    finally:
        os.close(descriptor)
    if _filesystem_type(absolute) != "tmpfs" and not persistent_allowed:
        raise ArchiveError(
            "owner plaintext staging is not tmpfs and lacks the exact persistent fallback acknowledgement"
        )
    return absolute


def _owner_plaintext_temp_root() -> Path:
    configured = os.environ.get("OPENCLAW_OWNER_PLAINTEXT_TMPDIR")
    persistent_allowed = (
        os.environ.get("OPENCLAW_OWNER_PERSISTENT_PLAINTEXT_ACK")
        == PERSISTENT_PLAINTEXT_ACK
    )
    if configured:
        return _validate_private_temp_root(
            Path(configured), persistent_allowed=persistent_allowed
        )
    runtime = Path(f"/run/user/{os.geteuid()}")
    if runtime.is_dir() and not runtime.is_symlink():
        try:
            return _validate_private_temp_root(runtime, persistent_allowed=False)
        except (ArchiveError, OSError):
            pass
    shared = Path("/dev/shm")
    shared_descriptor = _open_directory_nofollow(shared)
    try:
        information = os.fstat(shared_descriptor)
        if (
            _filesystem_type(shared) != "tmpfs"
            or information.st_uid != 0
            or stat.S_IMODE(information.st_mode) != 0o1777
        ):
            raise ArchiveError("shared tmpfs base is unsafe")
        name = f".openclaw-owner-plaintext-{os.geteuid()}"
        try:
            os.mkdir(name, 0o700, dir_fd=shared_descriptor)
        except FileExistsError:
            pass
        os.fsync(shared_descriptor)
    finally:
        os.close(shared_descriptor)
    return _validate_private_temp_root(shared / name, persistent_allowed=False)


def _open_regular_nofollow(
    path: Path,
    *,
    owner_private: bool,
    write_exclusive: bool = False,
) -> int:
    """Open a final regular file relative to a no-link directory descriptor."""

    absolute = Path(os.path.abspath(path.expanduser()))
    parent_descriptor = _open_directory_nofollow(absolute.parent)
    try:
        if write_exclusive:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(
                absolute.name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        else:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
            raise ArchiveError("secure file input is not a single-link regular file")
        if owner_private and (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) & 0o077
        ):
            raise ArchiveError("secure file input is not owner-private")
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except UnboundLocalError:
            pass
        raise
    finally:
        os.close(parent_descriptor)


def _open_relative_parent(root_descriptor: int, relative: PurePosixPath) -> int:
    descriptor = os.dup(root_descriptor)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _stat_relative_nofollow(
    root_descriptor: int, relative: PurePosixPath
) -> os.stat_result:
    parent_descriptor = _open_relative_parent(root_descriptor, relative)
    try:
        information = os.stat(
            relative.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(information.st_mode):
            raise ArchiveError("owner-data path changed to a link during capture")
        return information
    finally:
        os.close(parent_descriptor)


def _open_live_regular(root_descriptor: int, relative: PurePosixPath) -> int:
    parent_descriptor = _open_relative_parent(root_descriptor, relative)
    try:
        descriptor = os.open(
            relative.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
            os.close(descriptor)
            raise ArchiveError("owner-data file is not a single-link regular file")
        return descriptor
    finally:
        os.close(parent_descriptor)


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or len(value.encode("utf-8", errors="surrogatepass")) > MAX_MEMBER_NAME_BYTES
        or "\x00" in value
        or path.is_absolute()
        or ".." in path.parts
        or "" in path.parts
    ):
        raise ArchiveError("archive contains an unsafe member path")
    return path


def _selected_original(relative: PurePosixPath) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if any(part.casefold() in REGENERABLE_COMPONENTS for part in parts):
        return False
    if (
        len(parts) >= 3
        and parts[0] == "workspace"
        and parts[1] == "data"
        and parts[2] in WORKSPACE_ACTION_QUEUES
    ):
        return False
    if len(parts) == 1 and parts[0] in ROOT_FILES:
        return True
    if parts[0] == "agents":
        if len(parts) == 1:
            return True
        if AGENT_ID_RE.fullmatch(parts[1]) is None:
            return False
        if len(parts) == 2:
            return True
        if parts[2] in {"sessions", "qmd"}:
            return True
        if parts[2] != "agent":
            return False
        if len(parts) == 3:
            return True
        return len(parts) == 4 and (
            parts[3] in AGENT_STATE_FILES
            or LEGACY_AGENT_AUTHORITY_RE.fullmatch(
                unicodedata.normalize("NFKC", parts[3])
            )
            is not None
        )
    if parts[0] in ROOT_TREES:
        return True
    if parts[0] in AUX_WORKSPACES:
        return len(parts) == 1 or (len(parts) >= 2 and parts[1] in AUX_WORKSPACE_TREES)
    return len(parts) >= 2 and parts[0] == "workspace" and parts[1] in WORKSPACE_TREES


def _is_archive_authority(relative: PurePosixPath) -> bool:
    if relative.parts and relative.parts[0] in ARCHIVE_AUTHORITY_ROOTS:
        return True
    return (
        len(relative.parts) == 4
        and relative.parts[0] == "agents"
        and relative.parts[2] == "agent"
        and LEGACY_AGENT_AUTHORITY_RE.fullmatch(
            unicodedata.normalize("NFKC", relative.parts[3])
        )
        is not None
    )


def _stored_relative(relative: PurePosixPath) -> PurePosixPath:
    if _is_archive_authority(relative):
        return PurePosixPath(*ARCHIVE_AUTHORITY_PREFIX.parts, *relative.parts)
    return relative


def _selected(relative: PurePosixPath) -> bool:
    if (
        len(relative.parts) > len(ARCHIVE_AUTHORITY_PREFIX.parts)
        and relative.parts[: len(ARCHIVE_AUTHORITY_PREFIX.parts)]
        == ARCHIVE_AUTHORITY_PREFIX.parts
    ):
        original = PurePosixPath(*relative.parts[len(ARCHIVE_AUTHORITY_PREFIX.parts) :])
        return _is_archive_authority(original) and _selected_original(original)
    return _selected_original(relative)


def _sqlite_base(relative: PurePosixPath) -> PurePosixPath | None:
    value = relative.as_posix()
    for extension in SQLITE_EXTENSIONS:
        if value.endswith(extension):
            return relative
        for suffix in SQLITE_SIDECAR_SUFFIXES:
            if value.endswith(f"{extension}{suffix}"):
                return PurePosixPath(value[: -len(suffix)])
    return None


def _tar_info(name: str, source: os.stat_result, *, directory: bool) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name + ("/" if directory and not name.endswith("/") else ""))
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    # Owner archives are data, never a source of executable provenance.
    info.mode = stat.S_IMODE(source.st_mode) & (0o700 if directory else 0o600)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = int(source.st_mtime)
    if not directory:
        info.size = source.st_size
    return info


def _synthetic_info(name: str, *, mode: int, size: int, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.REGTYPE
    info.mode = mode & 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = mtime
    info.size = size
    return info


def _file_starts_with_sqlite_header(
    root_descriptor: int, relative: PurePosixPath
) -> bool:
    descriptor = _open_live_regular(root_descriptor, relative)
    try:
        return os.read(descriptor, 16) == b"SQLite format 3\x00"
    finally:
        os.close(descriptor)


def _snapshot_sqlite(
    root_descriptor: int,
    relative: PurePosixPath,
    destination: Path,
) -> None:
    """Create a transactionally consistent SQLite backup and validate it."""

    parent_descriptor = _open_relative_parent(root_descriptor, relative)
    descriptor = _open_live_regular(root_descriptor, relative)
    before = os.fstat(descriptor)
    named = os.stat(relative.name, dir_fd=parent_descriptor, follow_symlinks=False)
    if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
        os.close(descriptor)
        os.close(parent_descriptor)
        raise ArchiveError("SQLite source changed while binding its descriptor")
    uri = f"file:/proc/self/fd/{descriptor}?mode=ro"
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(uri, uri=True, timeout=5)
        source.execute("PRAGMA busy_timeout = 5000")
        target = sqlite3.connect(destination)
        source.backup(target)
        target.commit()
        if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ArchiveError("SQLite backup failed quick_check")
    except sqlite3.Error as exc:
        raise ArchiveError("persistent .db/.sqlite3 file is not a readable SQLite database") from exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        try:
            opened_after = os.fstat(descriptor)
            named_after = os.stat(
                relative.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        finally:
            os.close(descriptor)
            os.close(parent_descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_size,
        opened_after.st_mtime_ns,
        opened_after.st_ctime_ns,
    ) or (opened_after.st_dev, opened_after.st_ino) != (
        named_after.st_dev,
        named_after.st_ino,
    ):
        raise ArchiveError("SQLite source changed identity during snapshot")
    os.chmod(destination, 0o600)


def _load_native_manifest(
    archive: tarfile.TarFile, state_dir: Path
) -> tuple[dict[str, object], str, list[tarfile.TarInfo]]:
    members: list[tarfile.TarInfo] = []
    total_size = 0
    deadline = time.monotonic() + MAX_OPERATION_SECONDS
    for member in archive:
        if time.monotonic() > deadline:
            raise ArchiveError("native OpenClaw backup inspection exceeded its time limit")
        if len(members) >= MAX_MEMBERS:
            raise ArchiveError("native OpenClaw backup has too many members")
        _safe_relative(member.name.rstrip("/"))
        if member.isfile():
            if member.size > MAX_MEMBER_SIZE:
                raise ArchiveError("native OpenClaw backup member is oversized")
            total_size += member.size
            if total_size > MAX_TOTAL_SIZE:
                raise ArchiveError("native OpenClaw backup expands beyond 4 GiB")
        members.append(member)
    manifest_members = [
        member
        for member in members
        if PurePosixPath(member.name).name == "manifest.json"
        and len(_safe_relative(member.name).parts) == 2
    ]
    if len(manifest_members) != 1:
        raise ArchiveError("native OpenClaw backup must contain one root manifest")
    member = manifest_members[0]
    if not member.isfile() or member.size > MAX_MANIFEST_SIZE:
        raise ArchiveError("native OpenClaw backup manifest is invalid")
    stream = archive.extractfile(member)
    if stream is None:
        raise ArchiveError("native OpenClaw backup manifest is unreadable")
    try:
        manifest = json.loads(stream.read(MAX_MANIFEST_SIZE + 1))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("native OpenClaw backup manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ArchiveError("unsupported native OpenClaw backup schema")
    root = manifest.get("archiveRoot")
    if not isinstance(root, str) or member.name != f"{root}/manifest.json":
        raise ArchiveError("native OpenClaw backup root does not match its manifest")
    expected_state = os.path.realpath(state_dir)
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise ArchiveError("native OpenClaw backup has no asset inventory")
    state_assets = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("kind") == "state"
        and isinstance(asset.get("sourcePath"), str)
        and os.path.realpath(str(asset["sourcePath"])) == expected_state
        and isinstance(asset.get("archivePath"), str)
    ]
    if len(state_assets) != 1:
        raise ArchiveError("native OpenClaw backup does not bind the requested state directory")
    state_prefix = str(state_assets[0]["archivePath"]).rstrip("/")
    _safe_relative(state_prefix)
    return manifest, state_prefix, members


class _Writer:
    def __init__(self, archive: tarfile.TarFile) -> None:
        self.archive = archive
        self.names: set[str] = set()
        self.count = 0
        self.total_size = 0
        self.digests: dict[str, str] = {}
        self.deadline = time.monotonic() + MAX_OPERATION_SECONDS

    def add(
        self,
        info: tarfile.TarInfo,
        stream: object | None = None,
        *,
        record_digest: bool = True,
    ) -> None:
        name = info.name.rstrip("/")
        _safe_relative(name)
        if name in self.names:
            return
        self.count += 1
        if self.count > MAX_MEMBERS:
            raise ArchiveError("owner archive has too many members")
        if info.isfile():
            if info.size > MAX_MEMBER_SIZE:
                raise ArchiveError("owner archive member exceeds the per-file limit")
            self.total_size += info.size
            if self.total_size > MAX_TOTAL_SIZE:
                raise ArchiveError("owner archive expands beyond 4 GiB")
        if info.isfile() and record_digest:
            if stream is None or not hasattr(stream, "read"):
                raise ArchiveError("owner archive file has no readable stream")
            digest = hashlib.sha256()

            class _DigestingReader:
                def read(self, size: int = -1) -> bytes:
                    if time.monotonic() > writer_deadline:
                        raise ArchiveError("owner archive build exceeded its time limit")
                    chunk = stream.read(size)  # type: ignore[attr-defined]
                    digest.update(chunk)
                    return chunk

            writer_deadline = self.deadline
            self.archive.addfile(info, _DigestingReader())
            self.digests[name] = digest.hexdigest()
        else:
            self.archive.addfile(info, stream)
        self.names.add(name)


def _iter_live_paths(prefix: Path):
    roots = [
        *(prefix / name for name in sorted(ROOT_FILES | SCAN_ROOT_TREES | AUX_WORKSPACES))
    ]
    roots.extend(prefix / "workspace" / name for name in sorted(WORKSPACE_TREES))
    seen: set[str] = set()
    scanned = 0
    deadline = time.monotonic() + MAX_OPERATION_SECONDS

    def account_scan(amount: int = 1) -> None:
        nonlocal scanned
        scanned += amount
        if scanned > MAX_SCAN_ENTRIES:
            raise ArchiveError("owner-data scan exceeds its entry limit")
        if time.monotonic() > deadline:
            raise ArchiveError("owner-data scan exceeded its time limit")

    for root in roots:
        account_scan()
        if not os.path.lexists(root) or root.is_symlink():
            continue
        if root.is_file():
            relative = PurePosixPath(root.relative_to(prefix).as_posix())
            if relative.as_posix() not in seen:
                seen.add(relative.as_posix())
                yield root, relative
            continue
        if not root.is_dir():
            raise ArchiveError("owner-data allowlist contains a special file")
        for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            account_scan(len(dirnames) + len(filenames) + 1)
            base = Path(directory)
            safe_dirs: list[str] = []
            for dirname in sorted(dirnames):
                child = base / dirname
                if child.is_symlink():
                    continue
                information = child.lstat()
                if not stat.S_ISDIR(information.st_mode):
                    raise ArchiveError("owner-data tree contains a special directory entry")
                child_relative = PurePosixPath(child.relative_to(prefix).as_posix())
                if not _selected(child_relative):
                    continue
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs
            for path in [base, *(base / name for name in sorted(filenames))]:
                if path.is_symlink():
                    continue
                relative = PurePosixPath(path.relative_to(prefix).as_posix())
                if not _selected(relative):
                    continue
                if relative.as_posix() in seen:
                    continue
                information = path.lstat()
                if not (stat.S_ISDIR(information.st_mode) or stat.S_ISREG(information.st_mode)):
                    raise ArchiveError("owner-data tree contains a special file")
                seen.add(relative.as_posix())
                yield path, relative


def _add_live_file(
    writer: _Writer,
    root_descriptor: int,
    relative: PurePosixPath,
) -> None:
    before = _stat_relative_nofollow(root_descriptor, relative)
    if not stat.S_ISREG(before.st_mode):
        raise ArchiveError("owner-data file changed type during capture")
    if relative == PurePosixPath(FILE_DELIVERY_POLICY) and (
        before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077
    ):
        raise ArchiveError("file-delivery policy authority is not owner-private")
    descriptor = _open_live_regular(root_descriptor, relative)
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ArchiveError("owner-data file changed while it was opened")
        info = _tar_info(_stored_relative(relative).as_posix(), opened, directory=False)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            writer.add(info, stream)
        after = os.fstat(descriptor)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ArchiveError("owner-data file changed during capture")
    finally:
        os.close(descriptor)


def build_owner_archive(native_path: Path, state_dir: Path, output_path: Path) -> None:
    state_dir = Path(os.path.abspath(state_dir.expanduser()))
    state_descriptor = _open_directory_nofollow(state_dir)
    try:
        if os.fstat(state_descriptor).st_uid != os.geteuid():
            raise ArchiveError("owner state directory has the wrong owner")
    finally:
        os.close(state_descriptor)
    output_path = Path(os.path.abspath(output_path.expanduser()))
    output_parent_descriptor = _open_directory_nofollow(output_path.parent)
    output_parent_information = os.fstat(output_parent_descriptor)
    if (
        output_parent_information.st_uid != os.geteuid()
        or stat.S_IMODE(output_parent_information.st_mode) & 0o077
    ):
        os.close(output_parent_descriptor)
        raise ArchiveError("owner archive output directory is not owner-private")
    try:
        os.stat(output_path.name, dir_fd=output_parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except Exception:
        os.close(output_parent_descriptor)
        raise
    else:
        os.close(output_parent_descriptor)
        raise ArchiveError("refusing to overwrite an owner archive")
    with _ClosingDescriptor(output_parent_descriptor), os.fdopen(
        _open_regular_nofollow(native_path, owner_private=False), "rb"
    ) as native_stream, tarfile.open(fileobj=native_stream, mode="r:gz") as native:
        manifest, state_prefix, native_members = _load_native_manifest(native, state_dir)
        native_sqlite: dict[str, tarfile.TarInfo] = {}
        for member in native_members:
            path = _safe_relative(member.name)
            if member.name == state_prefix:
                continue
            prefix = f"{state_prefix}/"
            if not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name[len(prefix) :])
            if not _selected(relative):
                continue
            sqlite_base = _sqlite_base(relative)
            # OpenClaw 2026.7.3 snapshots only *.sqlite through VACUUM INTO.
            # Raw *.db/*.sqlite3 payloads in its tar are deliberately ignored;
            # those files are snapshotted below with Python's SQLite backup API.
            if sqlite_base is None or not relative.as_posix().endswith(".sqlite"):
                continue
            if relative != sqlite_base:
                raise ArchiveError("native OpenClaw backup contains a SQLite sidecar")
            if not member.isfile():
                raise ArchiveError("native OpenClaw SQLite snapshot is not a regular file")
            key = relative.as_posix()
            if key in native_sqlite:
                raise ArchiveError("native OpenClaw backup contains duplicate SQLite paths")
            native_sqlite[key] = member

        temporary_name = (
            f".{output_path.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
        )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=output_parent_descriptor,
        )
        try:
            os.fchmod(descriptor, 0o600)
            raw_output = os.fdopen(descriptor, "wb")
            descriptor = -1
            with raw_output:
                with tarfile.open(
                    fileobj=raw_output, mode="w:gz", format=tarfile.PAX_FORMAT
                ) as owner:
                    writer = _Writer(owner)
                    live_sqlite: dict[str, PurePosixPath] = {}
                    live_root_descriptor = _open_directory_nofollow(state_dir)
                    try:
                        for _path, relative in _iter_live_paths(state_dir):
                            information = _stat_relative_nofollow(
                                live_root_descriptor, relative
                            )
                            sqlite_base = _sqlite_base(relative)
                            if sqlite_base is not None:
                                if relative == sqlite_base and stat.S_ISREG(information.st_mode):
                                    if information.st_size > MAX_MEMBER_SIZE:
                                        raise ArchiveError(
                                            "live SQLite source exceeds the per-file limit"
                                        )
                                    live_sqlite[relative.as_posix()] = relative
                                continue
                            if stat.S_ISDIR(information.st_mode):
                                writer.add(
                                    _tar_info(
                                        _stored_relative(relative).as_posix(),
                                        information,
                                        directory=True,
                                    )
                                )
                            else:
                                if _file_starts_with_sqlite_header(
                                    live_root_descriptor, relative
                                ):
                                    raise ArchiveError(
                                        "SQLite database uses an unsupported filename extension"
                                    )
                                _add_live_file(writer, live_root_descriptor, relative)
                    except Exception:
                        os.close(live_root_descriptor)
                        raise
                    canonical_live = {
                        name for name in live_sqlite if name.endswith(".sqlite")
                    }
                    missing_snapshots = sorted(canonical_live - set(native_sqlite))
                    if missing_snapshots:
                        raise ArchiveError(
                            "a live *.sqlite database was not present in the native OpenClaw snapshot"
                        )
                    for relative, member in sorted(native_sqlite.items()):
                        stream = native.extractfile(member)
                        if stream is None:
                            raise ArchiveError("native OpenClaw SQLite snapshot is unreadable")
                        info = _synthetic_info(
                            _stored_relative(PurePosixPath(relative)).as_posix(),
                            mode=0o600,
                            size=member.size,
                            mtime=int(member.mtime),
                        )
                        writer.add(info, stream)
                    local_snapshot_count = 0
                    for relative in sorted(
                        name
                        for name in live_sqlite
                        if name.endswith((".db", ".sqlite3"))
                    ):
                        snapshot_descriptor, snapshot_name = tempfile.mkstemp(
                            prefix="openclaw-owner-db-",
                            suffix=".sqlite",
                            dir=_owner_plaintext_temp_root(),
                        )
                        os.close(snapshot_descriptor)
                        snapshot = Path(snapshot_name)
                        try:
                            _snapshot_sqlite(
                                live_root_descriptor,
                                live_sqlite[relative],
                                snapshot,
                            )
                            information = snapshot.stat()
                            with snapshot.open("rb") as stream:
                                writer.add(
                                    _synthetic_info(
                                        _stored_relative(PurePosixPath(relative)).as_posix(),
                                        mode=0o600,
                                        size=information.st_size,
                                        mtime=int(information.st_mtime),
                                    ),
                                    stream,
                                )
                            local_snapshot_count += 1
                        finally:
                            try:
                                snapshot.unlink()
                            except FileNotFoundError:
                                pass
                    os.close(live_root_descriptor)
                    marker = {
                        "schema": OWNER_SCHEMA,
                        "createdAt": manifest.get("createdAt"),
                        "runtimeVersion": manifest.get("runtimeVersion"),
                        "nativeBackupSchemaVersion": manifest.get("schemaVersion"),
                        "sqliteSnapshot": "transactional-backup-only",
                        "sqliteSnapshotCount": len(native_sqlite) + local_snapshot_count,
                        "sqliteSnapshotMethods": {
                            "openclawBackup": len(native_sqlite),
                            "pythonBackupApi": local_snapshot_count,
                        },
                        "excludedActionQueues": sorted(WORKSPACE_ACTION_QUEUES),
                        "executableMembers": False,
                        "authorityDisposition": "quarantine-only",
                        "provenance": "authenticated-passphrase-holder",
                        "memberDigests": dict(sorted(writer.digests.items())),
                    }
                    payload = (json.dumps(marker, sort_keys=True) + "\n").encode("utf-8")
                    writer.add(
                        _synthetic_info(
                            MARKER_NAME,
                            mode=0o600,
                            size=len(payload),
                            mtime=int(time.time()),
                        ),
                        io.BytesIO(payload),
                        record_digest=False,
                    )
                raw_output.flush()
                os.fsync(raw_output.fileno())
            try:
                os.link(
                    temporary_name,
                    output_path.name,
                    src_dir_fd=output_parent_descriptor,
                    dst_dir_fd=output_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ArchiveError("refusing to overwrite an owner archive") from exc
            os.fsync(output_parent_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=output_parent_descriptor)
            except FileNotFoundError:
                pass


def _original_relative(relative: PurePosixPath) -> PurePosixPath:
    if (
        len(relative.parts) > len(ARCHIVE_AUTHORITY_PREFIX.parts)
        and relative.parts[: len(ARCHIVE_AUTHORITY_PREFIX.parts)]
        == ARCHIVE_AUTHORITY_PREFIX.parts
    ):
        return PurePosixPath(
            *relative.parts[len(ARCHIVE_AUTHORITY_PREFIX.parts) :]
        )
    return relative


def _validate_sqlite_database(path: Path, relative: PurePosixPath) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.execute("PRAGMA busy_timeout = 5000")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise ArchiveError("SQLite snapshot failed quick_check")
        original = _original_relative(relative)
        parts = original.parts
        if len(parts) == 4 and parts[0] == "agents" and parts[2] == "agent" and parts[3] == "openclaw-agent.sqlite":
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table, required in AGENT_DB_TABLE_COLUMNS.items():
                if table not in tables:
                    raise ArchiveError("OpenClaw agent SQLite snapshot lacks a required table")
                columns = {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if not required.issubset(columns):
                    raise ArchiveError("OpenClaw agent SQLite snapshot lacks required columns")
            user_version = connection.execute("PRAGMA user_version").fetchone()
            if user_version != (1,):
                raise ArchiveError("unsupported OpenClaw agent SQLite schema version")
            owner = connection.execute(
                "SELECT role, agent_id, schema_version FROM schema_meta WHERE meta_key='primary'"
            ).fetchone()
            if owner != ("agent", parts[1], 1):
                raise ArchiveError("OpenClaw agent SQLite ownership metadata is invalid")
        elif original == GLOBAL_STATE_PATH:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table, required in GLOBAL_DB_TABLE_COLUMNS.items():
                if table not in tables:
                    raise ArchiveError(
                        "OpenClaw global SQLite snapshot lacks a required table"
                    )
                columns = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if not required.issubset(columns):
                    raise ArchiveError(
                        "OpenClaw global SQLite snapshot lacks required columns"
                    )
            user_version = connection.execute("PRAGMA user_version").fetchone()
            if user_version != (1,):
                raise ArchiveError("unsupported OpenClaw global SQLite schema version")
            owner = connection.execute(
                "SELECT role, agent_id, schema_version FROM schema_meta "
                "WHERE meta_key='primary'"
            ).fetchone()
            if owner != ("global", None, 1):
                raise ArchiveError(
                    "OpenClaw global SQLite ownership metadata is invalid"
                )
    except sqlite3.Error as exc:
        raise ArchiveError("SQLite snapshot is unreadable") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass


def _verify_open_archive(
    archive: tarfile.TarFile,
    *,
    allow_legacy: bool,
    expected_runtime_version: str | None,
) -> dict[str, object]:
    deadline = time.monotonic() + MAX_OPERATION_SECONDS
    count = 0
    total = 0
    marker_payload: bytes | None = None
    sqlite_count = 0
    seen_names: set[str] = set()
    actual_digests: dict[str, str] = {}
    active_authorities: list[str] = []
    for member in archive:
        if time.monotonic() > deadline:
            raise ArchiveError("owner archive verification exceeded its time limit")
        count += 1
        if count > MAX_MEMBERS:
            raise ArchiveError("owner archive has too many members")
        path = _safe_relative(member.name.rstrip("/"))
        normalized_name = path.as_posix()
        if normalized_name in seen_names:
            raise ArchiveError("owner archive contains duplicate member paths")
        seen_names.add(normalized_name)
        if path.as_posix() == MARKER_NAME:
            if marker_payload is not None or not member.isfile() or member.size > MAX_MANIFEST_SIZE:
                raise ArchiveError("owner snapshot marker is invalid")
            stream = archive.extractfile(member)
            if stream is None:
                raise ArchiveError("owner snapshot marker is unreadable")
            marker_payload = stream.read(MAX_MANIFEST_SIZE + 1)
            continue
        if not _selected(path):
            raise ArchiveError("owner archive member is outside the allowlist for inert owner data")
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise ArchiveError("owner archive contains an unsupported member type")
        if member.isfile() and member.mode & 0o111:
            raise ArchiveError("owner archive contains an executable member")
        sqlite_base = _sqlite_base(path)
        if sqlite_base is not None and path != sqlite_base:
            raise ArchiveError("owner archive contains a SQLite sidecar")
        if member.isfile():
            if member.size > MAX_MEMBER_SIZE:
                raise ArchiveError("owner archive member exceeds the per-file limit")
            total += member.size
            if total > MAX_TOTAL_SIZE:
                raise ArchiveError("owner archive expands beyond 4 GiB")
            source = archive.extractfile(member)
            if source is None:
                raise ArchiveError("owner archive member is unreadable")
            digest = hashlib.sha256()
            sqlite_descriptor: int | None = None
            sqlite_name: str | None = None
            sqlite_output: object | None = None
            if sqlite_base is not None:
                sqlite_descriptor, sqlite_name = tempfile.mkstemp(
                    prefix="openclaw-owner-db-",
                    suffix=".sqlite",
                    dir=_owner_plaintext_temp_root(),
                )
                os.fchmod(sqlite_descriptor, 0o600)
                sqlite_output = os.fdopen(sqlite_descriptor, "wb")
                sqlite_descriptor = None
            remaining = member.size
            try:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ArchiveError("owner archive member is truncated")
                    digest.update(chunk)
                    if sqlite_output is not None:
                        sqlite_output.write(chunk)  # type: ignore[attr-defined]
                    remaining -= len(chunk)
                    if time.monotonic() > deadline:
                        raise ArchiveError("owner archive verification exceeded its time limit")
                if sqlite_output is not None:
                    sqlite_output.flush()  # type: ignore[attr-defined]
                    os.fsync(sqlite_output.fileno())  # type: ignore[attr-defined]
                    sqlite_output.close()  # type: ignore[attr-defined]
                    sqlite_output = None
                    _validate_sqlite_database(Path(sqlite_name), path)  # type: ignore[arg-type]
                    sqlite_count += 1
            finally:
                if sqlite_output is not None:
                    sqlite_output.close()  # type: ignore[attr-defined]
                if sqlite_descriptor is not None:
                    os.close(sqlite_descriptor)
                if sqlite_name is not None:
                    try:
                        os.unlink(sqlite_name)
                    except FileNotFoundError:
                        pass
            actual_digests[normalized_name] = digest.hexdigest()
        if _is_archive_authority(path):
            active_authorities.append(normalized_name)

    marker: dict[str, object] | None = None
    if marker_payload is not None:
        try:
            parsed = json.loads(marker_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError("owner snapshot marker is invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ArchiveError("owner snapshot marker is not an object")
        marker = parsed
        methods = marker.get("sqliteSnapshotMethods")
        method_counts_valid = (
            isinstance(methods, dict)
            and set(methods) == {"openclawBackup", "pythonBackupApi"}
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in methods.values()
            )
            and sum(methods.values()) == sqlite_count
        )
        if (
            marker.get("schema") != OWNER_SCHEMA
            or marker.get("nativeBackupSchemaVersion") != 1
            or marker.get("sqliteSnapshot") != "transactional-backup-only"
            or not isinstance(marker.get("runtimeVersion"), str)
            or marker.get("sqliteSnapshotCount") != sqlite_count
            or not method_counts_valid
            or marker.get("excludedActionQueues") != sorted(WORKSPACE_ACTION_QUEUES)
            or marker.get("executableMembers") is not False
            or marker.get("authorityDisposition") != "quarantine-only"
            or marker.get("provenance") != "authenticated-passphrase-holder"
            or marker.get("memberDigests") != actual_digests
        ):
            raise ArchiveError("owner snapshot marker contract is invalid")
        if active_authorities:
            raise ArchiveError("modern owner archive contains active authority paths")
        if expected_runtime_version and marker.get("runtimeVersion") != expected_runtime_version:
            raise ArchiveError("owner snapshot OpenClaw version differs from the restore lock")
    elif sqlite_count:
        raise ArchiveError("legacy owner archive contains an unsnapshotted SQLite database")
    elif not allow_legacy:
        raise ArchiveError("owner archive lacks the native-snapshot marker")

    return {
        "schema": OWNER_SCHEMA if marker_payload is not None else "legacy-link-free",
        "memberCount": count,
        "sqliteSnapshotCount": sqlite_count,
        "expandedSize": total,
        # Internal extraction handoff: the public verify command strips this.
        "_memberDigests": actual_digests,
        "_memberNames": sorted(name for name in seen_names if name != MARKER_NAME),
    }


def _open_owner_archive(
    path: Path, *, owner_private: bool = False
) -> tuple[object, tarfile.TarFile]:
    descriptor = _open_regular_nofollow(path, owner_private=owner_private)
    stream = os.fdopen(descriptor, "rb")
    try:
        archive = tarfile.open(fileobj=stream, mode="r|gz")
    except Exception:
        stream.close()
        raise
    return stream, archive


def verify_owner_archive(
    archive_path: Path,
    *,
    allow_legacy: bool,
    expected_runtime_version: str | None,
) -> dict[str, object]:
    stream, archive = _open_owner_archive(archive_path)
    with stream, archive:
        result = _verify_open_archive(
            archive,
            allow_legacy=allow_legacy,
            expected_runtime_version=expected_runtime_version,
        )
        result.pop("_memberDigests", None)
        result.pop("_memberNames", None)
        return result


def _open_or_create_child_directory(parent_descriptor: int, component: str) -> int:
    try:
        os.mkdir(component, 0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    return os.open(
        component,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )


def _open_archive_parent(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for component in parts:
            next_descriptor = _open_or_create_child_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def extract_owner_archive(
    archive_path: Path,
    destination: Path,
    *,
    allow_legacy: bool,
    expected_runtime_version: str | None,
) -> dict[str, object]:
    """Verify and extract from one pinned archive descriptor into a private tree."""

    root_descriptor = _open_directory_nofollow(destination)
    root_information = os.fstat(root_descriptor)
    if (
        root_information.st_uid != os.geteuid()
        or stat.S_IMODE(root_information.st_mode) & 0o077
    ):
        os.close(root_descriptor)
        raise ArchiveError("extraction destination is not owner-private")
    if os.listdir(root_descriptor):
        os.close(root_descriptor)
        raise ArchiveError("extraction destination must be empty")
    stream, archive = _open_owner_archive(archive_path, owner_private=True)
    try:
        with stream:
            with archive:
                result = _verify_open_archive(
                    archive,
                    allow_legacy=allow_legacy,
                    expected_runtime_version=expected_runtime_version,
                )
            expected_digests = result.pop("_memberDigests")
            if not isinstance(expected_digests, dict):
                raise ArchiveError("owner archive digest handoff is invalid")
            expected_names = result.pop("_memberNames")
            if not isinstance(expected_names, list) or not all(
                isinstance(name, str) for name in expected_names
            ):
                raise ArchiveError("owner archive member-set handoff is invalid")
            expected_name_set = set(expected_names)
            required_space = int(result["expandedSize"]) + 64 * 1024 * 1024
            if shutil.disk_usage(destination).free < required_space:
                raise ArchiveError("insufficient free space for bounded owner archive extraction")
            stream.seek(0)
            extraction = tarfile.open(fileobj=stream, mode="r|gz")
            extracted_digests: dict[str, str] = {}
            extracted_names: set[str] = set()
            with extraction:
                deadline = time.monotonic() + MAX_OPERATION_SECONDS
                member_count = 0
                expanded_size = 0
                marker_seen = False
                for member in extraction:
                    if time.monotonic() > deadline:
                        raise ArchiveError("owner archive extraction exceeded its time limit")
                    member_count += 1
                    if member_count > MAX_MEMBERS:
                        raise ArchiveError("owner archive changed its member-count contract")
                    relative = _safe_relative(member.name.rstrip("/"))
                    if relative.as_posix() == MARKER_NAME:
                        if (
                            marker_seen
                            or not member.isfile()
                            or member.size > MAX_MANIFEST_SIZE
                        ):
                            raise ArchiveError(
                                "owner archive marker changed between verification passes"
                            )
                        marker_seen = True
                        continue
                    normalized_name = relative.as_posix()
                    if (
                        normalized_name not in expected_name_set
                        or normalized_name in extracted_names
                        or not _selected(relative)
                        or member.issym()
                        or member.islnk()
                        or not (member.isfile() or member.isdir())
                        or (member.isfile() and member.mode & 0o111)
                    ):
                        raise ArchiveError("owner archive changed contract between verification passes")
                    extracted_names.add(normalized_name)
                    if member.isdir():
                        descriptor = _open_archive_parent(root_descriptor, relative.parts)
                        os.fchmod(descriptor, 0o700)
                        os.close(descriptor)
                        continue
                    if member.size > MAX_MEMBER_SIZE:
                        raise ArchiveError(
                            "owner archive member changed its per-file size contract"
                        )
                    expanded_size += member.size
                    if expanded_size > int(result["expandedSize"]):
                        raise ArchiveError(
                            "owner archive expanded size changed between verification passes"
                        )
                    if normalized_name not in expected_digests:
                        raise ArchiveError(
                            "owner archive file set changed between verification passes"
                        )
                    parent_descriptor = _open_archive_parent(root_descriptor, relative.parts[:-1])
                    try:
                        destination_descriptor = os.open(
                            relative.name,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o600,
                            dir_fd=parent_descriptor,
                        )
                        try:
                            source = extraction.extractfile(member)
                            if source is None:
                                raise ArchiveError("owner archive member is unreadable")
                            remaining = member.size
                            digest = hashlib.sha256()
                            with os.fdopen(
                                destination_descriptor, "wb", closefd=False
                            ) as output:
                                while remaining:
                                    chunk = source.read(min(1024 * 1024, remaining))
                                    if not chunk:
                                        raise ArchiveError("owner archive member is truncated")
                                    digest.update(chunk)
                                    output.write(chunk)
                                    remaining -= len(chunk)
                                    if time.monotonic() > deadline:
                                        raise ArchiveError(
                                            "owner archive extraction exceeded its time limit"
                                        )
                                output.flush()
                            actual_digest = digest.hexdigest()
                            if expected_digests.get(normalized_name) != actual_digest:
                                raise ArchiveError(
                                    "owner archive changed between verification and extraction"
                                )
                            extracted_digests[normalized_name] = actual_digest
                            os.fchmod(destination_descriptor, 0o600)
                            os.fsync(destination_descriptor)
                        finally:
                            os.close(destination_descriptor)
                    finally:
                        os.close(parent_descriptor)
            if (
                extracted_digests != expected_digests
                or extracted_names != expected_name_set
                or expanded_size != int(result["expandedSize"])
                or member_count != int(result["memberCount"])
            ):
                raise ArchiveError(
                    "owner archive member set changed between verification and extraction"
                )
            os.fsync(root_descriptor)
            return result
    finally:
        os.close(root_descriptor)


def _open_trusted_executable(path: Path, *, label: str) -> int:
    """Open one fixed root-owned executable inode without consulting PATH."""

    executable_parent = _open_directory_nofollow(path.parent)
    executable_descriptor: int | None = None
    try:
        named_executable = os.stat(
            path.name,
            dir_fd=executable_parent,
            follow_symlinks=False,
        )
        executable_descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=executable_parent,
        )
        opened_executable = os.fstat(executable_descriptor)
        if (
            not stat.S_ISREG(opened_executable.st_mode)
            or opened_executable.st_nlink != 1
            or opened_executable.st_uid != 0
            or stat.S_IMODE(opened_executable.st_mode) & 0o022
            or not stat.S_IMODE(opened_executable.st_mode) & 0o111
            or (named_executable.st_dev, named_executable.st_ino)
            != (opened_executable.st_dev, opened_executable.st_ino)
        ):
            raise ArchiveError(f"trusted {label} executable is unsafe")
        result = executable_descriptor
        executable_descriptor = None
        return result
    finally:
        if executable_descriptor is not None:
            os.close(executable_descriptor)
        os.close(executable_parent)


def _create_scoped_gpg_home(root: Path) -> Path:
    root = Path(os.path.abspath(root))
    parent = _open_directory_nofollow(root)
    try:
        information = os.fstat(parent)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ArchiveError("GPG staging root is not owner-private")
        for _attempt in range(128):
            name = f".openclaw-gpg-{secrets.token_hex(16)}"
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                continue
            os.fsync(parent)
            home = root / name
            descriptor = _open_directory_nofollow(home)
            try:
                opened = os.fstat(descriptor)
                if (
                    opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o700
                ):
                    raise ArchiveError("scoped GPG home is unsafe")
            finally:
                os.close(descriptor)
            return home
        raise ArchiveError("could not allocate a scoped GPG home")
    finally:
        os.close(parent)


def _remove_scoped_gpg_home(root: Path, home: Path) -> None:
    root = Path(os.path.abspath(root))
    home = Path(os.path.abspath(home))
    if home.parent != root or GPG_HOME_RE.fullmatch(home.name) is None:
        raise ArchiveError("refusing unsafe scoped GPG cleanup")
    parent = _open_directory_nofollow(root)
    descriptor: int | None = None
    budget = [MAX_GPG_HOME_ENTRIES]

    def remove_contents(directory: int) -> None:
        for name in os.listdir(directory):
            budget[0] -= 1
            if budget[0] < 0:
                raise ArchiveError("scoped GPG cleanup exceeded its bound")
            information = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if information.st_uid != os.geteuid():
                raise ArchiveError("scoped GPG home contains foreign ownership")
            if stat.S_ISDIR(information.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory,
                )
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (
                        information.st_dev,
                        information.st_ino,
                    ):
                        raise ArchiveError("scoped GPG cleanup encountered a race")
                    remove_contents(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=directory)
            elif (
                stat.S_ISREG(information.st_mode)
                or stat.S_ISLNK(information.st_mode)
                or stat.S_ISSOCK(information.st_mode)
                or stat.S_ISFIFO(information.st_mode)
            ):
                os.unlink(name, dir_fd=directory)
            else:
                raise ArchiveError("scoped GPG home contains a special file")
        os.fsync(directory)

    try:
        descriptor = os.open(
            home.name,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        information = os.fstat(descriptor)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ArchiveError("scoped GPG cleanup target is unsafe")
        identity = (information.st_dev, information.st_ino)
        remove_contents(descriptor)
        named = os.stat(home.name, dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != identity:
            raise ArchiveError("scoped GPG cleanup target changed")
        os.rmdir(home.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _gpg_environment(home: Path) -> dict[str, str]:
    return {
        "GNUPGHOME": os.fspath(home),
        "HOME": "/",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": os.fspath(home),
    }


def _terminate_scoped_gpg_agent(
    executable_descriptor: int,
    home: Path,
) -> None:
    try:
        result = subprocess.run(
            [
                os.fspath(TRUSTED_GPGCONF_PATH),
                "--homedir",
                os.fspath(home),
                "--kill",
                "gpg-agent",
            ],
            executable=f"/proc/self/fd/{executable_descriptor}",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            pass_fds=(executable_descriptor,),
            timeout=10,
            env=_gpg_environment(home),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArchiveError("could not terminate the scoped GPG agent") from exc
    if result.returncode != 0:
        raise ArchiveError("could not terminate the scoped GPG agent")


def crypt_owner_file(
    *,
    decrypt: bool,
    source: Path,
    destination: Path,
    passphrase_file: Path | None,
    expected_source_sha256: str | None = None,
) -> None:
    """Run GPG with pinned input/passphrase descriptors and private output."""

    source = Path(os.path.abspath(source.expanduser()))
    destination = Path(os.path.abspath(destination.expanduser()))
    if passphrase_file is not None:
        passphrase_file = Path(os.path.abspath(passphrase_file.expanduser()))
    executable_descriptor: int | None = None
    gpgconf_descriptor: int | None = None
    source_descriptor: int | None = None
    passphrase_descriptor: int | None = None
    destination_descriptor: int | None = None
    gpg_home: Path | None = None
    plaintext_tmp_root: Path | None = None
    try:
        executable_descriptor = _open_trusted_executable(
            TRUSTED_GPG_PATH,
            label="gpg",
        )
        gpgconf_descriptor = _open_trusted_executable(
            TRUSTED_GPGCONF_PATH,
            label="gpgconf",
        )
        source_descriptor = _open_regular_nofollow(source, owner_private=True)
        if expected_source_sha256 is not None:
            if re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None:
                raise ArchiveError("signed recovery-set owner digest is invalid")
            digest = hashlib.sha256()
            while chunk := os.read(source_descriptor, 1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected_source_sha256:
                raise ArchiveError(
                    "owner archive differs from the signed recovery-set v2 record"
                )
            os.lseek(source_descriptor, 0, os.SEEK_SET)
        if passphrase_file is not None:
            passphrase_descriptor = _open_regular_nofollow(
                passphrase_file, owner_private=True
            )
        destination_descriptor = _open_regular_nofollow(
            destination,
            owner_private=False,
            write_exclusive=True,
        )
        os.fchmod(destination_descriptor, 0o600)
        plaintext_tmp_root = _owner_plaintext_temp_root()
        gpg_home = _create_scoped_gpg_home(plaintext_tmp_root)
        argv = [
            os.fspath(TRUSTED_GPG_PATH),
            "--no-options",
            "--homedir",
            os.fspath(gpg_home),
            "--batch",
            "--no-tty",
            "--yes",
            "--no-symkey-cache",
        ]
        inherited = [
            executable_descriptor,
            source_descriptor,
            destination_descriptor,
        ]
        if passphrase_descriptor is not None:
            argv.extend(
                [
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase-file",
                    f"/proc/self/fd/{passphrase_descriptor}",
                ]
            )
            inherited.append(passphrase_descriptor)
        argv.extend(["--output", f"/proc/self/fd/{destination_descriptor}"])
        if decrypt:
            argv.extend(["--decrypt", f"/proc/self/fd/{source_descriptor}"])
        else:
            argv.extend(
                [
                    "--compress-algo",
                    "none",
                    "--symmetric",
                    "--cipher-algo",
                    "AES256",
                    f"/proc/self/fd/{source_descriptor}",
                ]
            )
        try:
            result = subprocess.run(
                argv,
                executable=f"/proc/self/fd/{executable_descriptor}",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                pass_fds=tuple(inherited),
                timeout=MAX_OPERATION_SECONDS,
                env=_gpg_environment(gpg_home),
            )
        except subprocess.TimeoutExpired as exc:
            raise ArchiveError("gpg cryptographic operation timed out") from exc
        finally:
            cleanup_error: ArchiveError | None = None
            try:
                _terminate_scoped_gpg_agent(gpgconf_descriptor, gpg_home)
            except ArchiveError as exc:
                cleanup_error = exc
            try:
                _remove_scoped_gpg_home(plaintext_tmp_root, gpg_home)
            except (ArchiveError, OSError) as exc:
                if cleanup_error is None:
                    cleanup_error = ArchiveError("could not remove the scoped GPG home")
                    cleanup_error.__cause__ = exc
            gpg_home = None
            if cleanup_error is not None:
                raise cleanup_error
        if result.returncode != 0:
            raise ArchiveError("gpg cryptographic operation failed")
        if expected_source_sha256 is not None:
            os.lseek(source_descriptor, 0, os.SEEK_SET)
            final_digest = hashlib.sha256()
            while chunk := os.read(source_descriptor, 1024 * 1024):
                final_digest.update(chunk)
            source_parent = _open_directory_nofollow(source.parent)
            try:
                named_after = os.stat(
                    source.name, dir_fd=source_parent, follow_symlinks=False
                )
            finally:
                os.close(source_parent)
            opened_after = os.fstat(source_descriptor)
            if (
                final_digest.hexdigest() != expected_source_sha256
                or (opened_after.st_dev, opened_after.st_ino)
                != (named_after.st_dev, named_after.st_ino)
            ):
                raise ArchiveError(
                    "signed recovery-set owner archive changed during decryption"
                )
        os.fsync(destination_descriptor)
    except Exception:
        if destination_descriptor is not None:
            try:
                try:
                    parent_descriptor = _open_directory_nofollow(destination.parent)
                except OSError:
                    parent_descriptor = None
                if parent_descriptor is not None:
                    try:
                        opened = os.fstat(destination_descriptor)
                        try:
                            current = os.stat(
                                destination.name,
                                dir_fd=parent_descriptor,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            current = None
                        if current is not None and (current.st_dev, current.st_ino) == (
                            opened.st_dev,
                            opened.st_ino,
                        ):
                            os.unlink(destination.name, dir_fd=parent_descriptor)
                    finally:
                        os.close(parent_descriptor)
            except OSError:
                # Preserve the generic cryptographic failure. A raced pathname
                # is never removed unless it still names the pinned output.
                pass
        raise
    finally:
        final_cleanup_error: ArchiveError | None = None
        if gpg_home is not None and plaintext_tmp_root is not None:
            if gpgconf_descriptor is not None:
                try:
                    _terminate_scoped_gpg_agent(gpgconf_descriptor, gpg_home)
                except ArchiveError as exc:
                    final_cleanup_error = exc
            try:
                _remove_scoped_gpg_home(plaintext_tmp_root, gpg_home)
            except (ArchiveError, OSError):
                if final_cleanup_error is None:
                    final_cleanup_error = ArchiveError(
                        "could not remove the scoped GPG home"
                    )
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if passphrase_descriptor is not None:
            os.close(passphrase_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        if executable_descriptor is not None:
            os.close(executable_descriptor)
        if gpgconf_descriptor is not None:
            os.close(gpgconf_descriptor)
        if final_cleanup_error is not None:
            raise final_cleanup_error


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--native-archive", type=Path, required=True)
    build.add_argument("--state-dir", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--allow-legacy", action="store_true")
    verify.add_argument("--expected-runtime-version")
    extract = subparsers.add_parser("verify-extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.add_argument("--allow-legacy", action="store_true")
    extract.add_argument("--expected-runtime-version")
    for name in ("encrypt", "decrypt"):
        crypt = subparsers.add_parser(name)
        crypt.add_argument("--source", type=Path, required=True)
        crypt.add_argument("--output", type=Path, required=True)
        crypt.add_argument("--passphrase-file", type=Path)
        crypt.add_argument("--expected-source-sha256")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build_owner_archive(args.native_archive, args.state_dir, args.output)
        elif args.command == "verify":
            result = verify_owner_archive(
                args.archive,
                allow_legacy=args.allow_legacy,
                expected_runtime_version=args.expected_runtime_version,
            )
            print(json.dumps(result, sort_keys=True))
        elif args.command == "verify-extract":
            result = extract_owner_archive(
                args.archive,
                args.destination,
                allow_legacy=args.allow_legacy,
                expected_runtime_version=args.expected_runtime_version,
            )
            print(json.dumps(result, sort_keys=True))
        else:
            crypt_owner_file(
                decrypt=args.command == "decrypt",
                source=args.source,
                destination=args.output,
                passphrase_file=args.passphrase_file,
                expected_source_sha256=args.expected_source_sha256,
            )
    except (ArchiveError, OSError, tarfile.TarError) as exc:
        print(f"owner archive: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
