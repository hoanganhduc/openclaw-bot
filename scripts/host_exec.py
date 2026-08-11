#!/usr/bin/env python3
"""Execute one artifact from an owner-private, aggregate-attested generation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import sys


MANIFEST_SCHEMA = "openclaw.host-runtime/v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
SAFE_PATH = "/usr/bin:/bin"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class HostExecError(RuntimeError):
    pass


def _parent_is_controlled(
    path: Path, information: os.stat_result, *, home: Path, euid: int
) -> bool:
    mode = stat.S_IMODE(information.st_mode)
    if path == Path(path.anchor):
        # An unprivileged systemd mount namespace maps the host root owner to
        # the overflow uid.  A non-writable root is still a safe path anchor.
        return mode & 0o022 == 0
    if information.st_uid in {0, euid} and mode & 0o022 == 0:
        return True
    # ProtectHome=tmpfs creates a private, sticky synthetic home parent owned
    # by the service uid.  Nothing outside the unit can mutate this namespace.
    return (
        path == home.parent
        and information.st_uid in {0, euid}
        and mode == 0o1777
    )


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


def _read_regular(parent: int, name: str, limit: int) -> tuple[int, bytes, os.stat_result]:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size > limit
        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(descriptor)
        raise HostExecError("host runtime artifact is unsafe")
    chunks: list[bytes] = []
    remaining = before.st_size
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            os.close(descriptor)
            raise HostExecError("host runtime artifact was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
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
    ) or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino):
        os.close(descriptor)
        raise HostExecError("host runtime artifact changed while opening")
    return descriptor, b"".join(chunks), before


def _validate_generation_path(generation: Path) -> Path:
    euid = os.geteuid()
    home = Path(pwd.getpwuid(euid).pw_dir)
    generation = Path(os.path.abspath(generation))
    expected_parent = home / ".local/libexec/openclaw-bot/generations"
    if generation.parent != expected_parent or HEX64.fullmatch(generation.name) is None:
        raise HostExecError("host runtime generation path is outside the reviewed root")
    checks: list[Path] = []
    current = Path(generation.anchor)
    checks.append(current)
    for component in generation.parts[1:]:
        current /= component
        checks.append(current)
    for path in checks:
        descriptor = _open_directory(path)
        try:
            information = os.fstat(descriptor)
            if not _parent_is_controlled(
                path, information, home=home, euid=euid
            ):
                raise HostExecError("host runtime parent is not owner-controlled")
            if path in {expected_parent, generation} and (
                information.st_uid != euid
                or stat.S_IMODE(information.st_mode) != 0o700
            ):
                raise HostExecError("host runtime generation directory is not private")
        finally:
            os.close(descriptor)
    return generation


def _load_manifest(generation: Path) -> dict[str, object]:
    generation_descriptor = _open_directory(generation)
    try:
        descriptor, payload, information = _read_regular(
            generation_descriptor, "MANIFEST.json", MAX_MANIFEST_BYTES
        )
        os.close(descriptor)
        if stat.S_IMODE(information.st_mode) != 0o400:
            raise HostExecError("host runtime manifest permissions are invalid")
    finally:
        os.close(generation_descriptor)
    try:
        manifest = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostExecError("host runtime manifest is malformed") from exc
    if not isinstance(manifest, dict) or frozenset(manifest) != {
        "schema",
        "generation",
        "artifacts",
        "externalRuntime",
    }:
        raise HostExecError("host runtime manifest shape is invalid")
    artifacts = manifest.get("artifacts")
    external = manifest.get("externalRuntime")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("generation") != generation.name
        or not isinstance(artifacts, dict)
        or not isinstance(external, dict)
    ):
        raise HostExecError("host runtime generation identity is invalid")
    aggregate = hashlib.sha256(
        json.dumps(
            {"artifacts": artifacts, "externalRuntime": external},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if aggregate != generation.name:
        raise HostExecError("host runtime aggregate attestation failed")
    return manifest


def _artifact_record(artifacts: dict[str, object], name: str) -> tuple[PurePosixPath, dict[str, object]]:
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise HostExecError("host runtime artifact name is invalid")
    record = artifacts.get(relative.as_posix())
    if not isinstance(record, dict) or frozenset(record) != {
        "sha256",
        "mode",
        "interpreter",
    }:
        raise HostExecError("host runtime artifact is not in the reviewed manifest")
    digest = record.get("sha256")
    mode = record.get("mode")
    interpreter = record.get("interpreter")
    if (
        not isinstance(digest, str)
        or HEX64.fullmatch(digest) is None
        or mode not in {0o400, 0o500}
        or interpreter not in {"bash", "python-isolated", "data"}
        or (interpreter == "data") != (mode == 0o400)
    ):
        raise HostExecError("host runtime artifact manifest record is invalid")
    return relative, record


def _open_artifact(
    generation: Path, relative: PurePosixPath, record: dict[str, object]
) -> int:
    parent = _open_directory(generation.joinpath(*relative.parts[:-1]))
    try:
        parent_information = os.fstat(parent)
        if (
            parent_information.st_uid != os.geteuid()
            or stat.S_IMODE(parent_information.st_mode) != 0o700
        ):
            raise HostExecError("host runtime artifact directory is unsafe")
        descriptor, payload, information = _read_regular(
            parent, relative.name, MAX_ARTIFACT_BYTES
        )
    finally:
        os.close(parent)
    if (
        stat.S_IMODE(information.st_mode) != record["mode"]
        or hashlib.sha256(payload).hexdigest() != record["sha256"]
    ):
        os.close(descriptor)
        raise HostExecError("host runtime artifact attestation failed")
    return descriptor


def _verify_and_select(generation: Path, selected: str) -> tuple[int, str]:
    manifest = _load_manifest(generation)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    selected_descriptor: int | None = None
    selected_interpreter = ""
    self_identity = os.stat(__file__, follow_symlinks=False)
    try:
        for name in sorted(artifacts):
            relative, record = _artifact_record(artifacts, name)
            descriptor = _open_artifact(generation, relative, record)
            if name == "host_exec.py":
                information = os.fstat(descriptor)
                if (information.st_dev, information.st_ino) != (
                    self_identity.st_dev,
                    self_identity.st_ino,
                ):
                    os.close(descriptor)
                    raise HostExecError("host runtime launcher self-attestation failed")
            if name == selected:
                selected_descriptor = descriptor
                selected_interpreter = str(record["interpreter"])
            else:
                os.close(descriptor)
        if selected_descriptor is None:
            raise HostExecError("requested host runtime artifact is not reviewed")
        if selected_interpreter == "data":
            raise HostExecError("host runtime data artifact is not executable")
        return selected_descriptor, selected_interpreter
    except Exception:
        if selected_descriptor is not None:
            os.close(selected_descriptor)
        raise


def _safe_environment(generation: Path) -> dict[str, str]:
    keep = {
        "DISPLAY",
        "GNUPGHOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "OPENCLAW_CONFIG_PATH",
        "OPENCLAW_DELIVERY_CHANNEL",
        "OPENCLAW_DELIVERY_POLICY",
        "OPENCLAW_DELIVERY_STATE",
        "OPENCLAW_EXPECTED_OWNER_STATE_LOCK",
        "OPENCLAW_HOME",
        "OPENCLAW_OWNER_STATE_LOCK",
        "OPENCLAW_QUEUE_KIND",
        "OPENCLAW_STATE_DIR",
        "OPENCLAW_TELEGRAM_CREDENTIAL",
        "OPENCLAW_WORKSPACE",
        "SEND_EMAIL_SECRETS_FILE",
        "TMPDIR",
        "TZ",
        "XDG_RUNTIME_DIR",
    }
    child = {name: os.environ[name] for name in keep if os.environ.get(name)}
    child["HOME"] = pwd.getpwuid(os.geteuid()).pw_dir
    child["OPENCLAW_LIBEXEC"] = os.fspath(generation)
    child["PATH"] = SAFE_PATH
    return child


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("child", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    child = list(args.child)
    if child and child[0] == "--":
        child = child[1:]
    try:
        generation = _validate_generation_path(args.generation)
        descriptor, interpreter = _verify_and_select(generation, args.artifact)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        fcntl.fcntl(descriptor, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
        proc_path = f"/proc/self/fd/{descriptor}"
        if interpreter == "bash":
            executable = "/usr/bin/bash"
            argv = [
                executable,
                "--noprofile",
                "--norc",
                "-p",
                proc_path,
                *child,
            ]
        else:
            executable = "/usr/bin/python3"
            argv = [executable, "-I", "-S", "-B", proc_path, *child]
        os.execve(executable, argv, _safe_environment(generation))
    except (HostExecError, OSError) as exc:
        print(f"host runtime: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
