#!/usr/bin/env python3
"""Execute the fixed, install-attested OpenClaw CLI through a narrow API."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys


MANIFEST_SCHEMA = "openclaw.host-runtime/v1"
SAFE_PATH = "/usr/bin:/bin"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEALED_NODE_GENERATION = r"sha256-(amd64|arm64)-[0-9a-f]{64}"
SEALED_NPM_CLOSURE = r"sha256-(amd64|arm64)-[0-9a-f]{64}-[0-9a-f]{64}"
MAX_ATTESTED_BYTES = 256 * 1024 * 1024
CHANNELS = frozenset({"zulip", "googlechat", "whatsapp", "zalo"})


class RuntimeError_(RuntimeError):
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


def _read_regular(path: Path, *, maximum: int) -> tuple[int, bytes, os.stat_result]:
    parent = _open_directory(path.parent)
    descriptor: int | None = None
    try:
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > maximum
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RuntimeError_("attested OpenClaw runtime file is unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError_("attested OpenClaw runtime file was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
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
            raise RuntimeError_("attested OpenClaw runtime changed while reading")
        return descriptor, b"".join(chunks), before
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def _ancestor_is_controlled(
    path: Path, information: os.stat_result, *, home: Path
) -> bool:
    mode = stat.S_IMODE(information.st_mode)
    if path == Path(path.anchor):
        # An unprivileged systemd mount namespace maps the host root owner to
        # the overflow uid.  A non-writable root is still a safe path anchor.
        return mode & 0o022 == 0
    if information.st_uid in {0, os.geteuid()} and mode & 0o022 == 0:
        return True
    # ProtectHome=tmpfs creates a private, sticky synthetic home parent owned
    # by the service uid.  Nothing outside the unit can mutate this namespace.
    return (
        path == home.parent
        and information.st_uid in {0, os.geteuid()}
        and mode == 0o1777
    )


def _validate_ancestors(path: Path, *, home: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in (None, *absolute.parts[1:]):
        if component is not None:
            current /= component
        descriptor = _open_directory(current)
        try:
            information = os.fstat(descriptor)
            if not _ancestor_is_controlled(current, information, home=home):
                raise RuntimeError_(
                    "OpenClaw runtime ancestor is not owner-controlled"
                )
        finally:
            os.close(descriptor)


def _expected_runtime_paths(
    runtime: dict[str, object], home: Path
) -> dict[str, Path]:
    """The legacy npm-global layout, or one sealed coding-system Node and closure."""
    node_record = runtime.get("node")
    package_record = runtime.get("package")
    node_value = node_record.get("path") if isinstance(node_record, dict) else None
    package_value = (
        package_record.get("path") if isinstance(package_record, dict) else None
    )
    if node_value == "/usr/bin/node":
        node = Path("/usr/bin/node")
        package_root = home / ".npm-global/lib/node_modules/openclaw"
    else:
        coding = re.escape(os.fspath(home / ".local/share/coding-system"))
        node_match = re.fullmatch(
            coding + "/node-generations/" + SEALED_NODE_GENERATION + "/bin/node",
            node_value if isinstance(node_value, str) else "",
        )
        package_match = re.fullmatch(
            coding
            + "/npm-closures/"
            + SEALED_NPM_CLOSURE
            + "/node_modules/openclaw/package\\.json",
            package_value if isinstance(package_value, str) else "",
        )
        if (
            node_match is None
            or package_match is None
            or node_match.group(1) != package_match.group(1)
        ):
            raise RuntimeError_("OpenClaw runtime attestation path is invalid")
        node = Path(node_match.group(0))
        package_root = Path(package_match.group(0)).parent
    return {
        "node": node,
        "entry": package_root / "dist/index.js",
        "package": package_root / "package.json",
    }


def _manifest(generation: Path) -> dict[str, object]:
    descriptor, payload, _ = _read_regular(
        generation / "MANIFEST.json", maximum=1024 * 1024
    )
    os.close(descriptor)
    try:
        manifest = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError_("host runtime manifest is malformed") from exc
    if not isinstance(manifest, dict) or frozenset(manifest) != {
        "schema",
        "generation",
        "artifacts",
        "externalRuntime",
    }:
        raise RuntimeError_("host runtime manifest shape is invalid")
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("generation") != generation.name
    ):
        raise RuntimeError_("host runtime manifest identity is invalid")
    return manifest


def _attested_runtime(generation: Path) -> tuple[int, int, int, str]:
    manifest = _manifest(generation)
    runtime = manifest.get("externalRuntime")
    if not isinstance(runtime, dict) or frozenset(runtime) != {
        "version",
        "node",
        "entry",
        "package",
    }:
        raise RuntimeError_("OpenClaw runtime attestation is missing")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    expected_paths = _expected_runtime_paths(runtime, home)
    opened: dict[str, tuple[int, bytes]] = {}
    try:
        for label, expected_path in expected_paths.items():
            _validate_ancestors(expected_path.parent, home=home)
            record = runtime.get(label)
            if not isinstance(record, dict) or frozenset(record) != {
                "path",
                "sha256",
                "device",
                "inode",
                "size",
                "mode",
                "uid",
            }:
                raise RuntimeError_("OpenClaw runtime attestation record is invalid")
            digest = record.get("sha256")
            if (
                record.get("path") != os.fspath(expected_path)
                or not isinstance(digest, str)
                or HEX64.fullmatch(digest) is None
            ):
                raise RuntimeError_("OpenClaw runtime attestation path is invalid")
            descriptor, payload, information = _read_regular(
                expected_path, maximum=MAX_ATTESTED_BYTES
            )
            identity = {
                "device": information.st_dev,
                "inode": information.st_ino,
                "size": information.st_size,
                "mode": stat.S_IMODE(information.st_mode),
                "uid": information.st_uid,
            }
            if any(record.get(key) != value for key, value in identity.items()) or (
                hashlib.sha256(payload).hexdigest() != digest
            ):
                os.close(descriptor)
                raise RuntimeError_("OpenClaw runtime attestation failed")
            opened[label] = (descriptor, payload)
        try:
            package = json.loads(opened["package"][1])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError_("OpenClaw package metadata is malformed") from exc
        if not isinstance(package, dict) or package.get("version") != runtime.get("version"):
            raise RuntimeError_("OpenClaw package version changed")
        dist_descriptor = _open_directory(expected_paths["entry"].parent)
        dist_information = os.fstat(dist_descriptor)
        if (
            dist_information.st_uid != os.geteuid()
            or stat.S_IMODE(dist_information.st_mode) & 0o022
        ):
            os.close(dist_descriptor)
            raise RuntimeError_("OpenClaw distribution directory is unsafe")
        return (
            opened["node"][0],
            opened["entry"][0],
            dist_descriptor,
            str(runtime["version"]),
        )
    finally:
        for label, (descriptor, _payload) in opened.items():
            if label not in {"node", "entry"}:
                os.close(descriptor)


def _safe_environment(state_prefix: Path) -> dict[str, str]:
    home = pwd.getpwuid(os.geteuid()).pw_dir
    environment = {
        "HOME": home,
        "PATH": SAFE_PATH,
        "OPENCLAW_STATE_DIR": os.fspath(state_prefix),
        "OPENCLAW_CONFIG_PATH": os.fspath(state_prefix / "openclaw.json"),
    }
    for name in ("DISPLAY", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return environment


def _media_descriptor(value: int) -> None:
    information = os.fstat(value)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_nlink != 1
        or information.st_uid != os.geteuid()
        or stat.S_IMODE(information.st_mode) & 0o077
    ):
        raise RuntimeError_("delivery media descriptor is unsafe")
    flags = fcntl.fcntl(value, fcntl.F_GETFD)
    fcntl.fcntl(value, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("gateway")
    health = subparsers.add_parser("health")
    health.add_argument("--timeout", type=int, default=10_000)
    subparsers.add_parser("cron-export")
    send = subparsers.add_parser("message-send")
    send.add_argument("--channel", choices=sorted(CHANNELS), required=True)
    send.add_argument("--target", required=True)
    send.add_argument("--media-fd", type=int, required=True)
    send.add_argument("--caption", default="")
    args = parser.parse_args()
    try:
        generation_value = os.environ.get("OPENCLAW_LIBEXEC", "")
        if not generation_value or not Path(generation_value).is_absolute():
            raise RuntimeError_("host runtime generation is unavailable")
        generation = Path(os.path.abspath(generation_value))
        (
            node_descriptor,
            entry_descriptor,
            dist_descriptor,
            _version,
        ) = _attested_runtime(generation)
        state_value = os.environ.get("OPENCLAW_STATE_DIR", "")
        if not state_value or not Path(state_value).is_absolute():
            raise RuntimeError_("OpenClaw state path is unavailable")
        state_prefix = Path(os.path.abspath(state_value))
        if args.command == "gateway":
            child = ["gateway", "--port", "18789"]
        elif args.command == "health":
            if not 1_000 <= args.timeout <= 60_000:
                raise RuntimeError_("health timeout is outside the reviewed range")
            child = ["health", "--json", "--timeout", str(args.timeout)]
        elif args.command == "cron-export":
            child = ["cron", "list", "--all", "--json"]
        else:
            if (
                not args.target
                or len(args.target.encode("utf-8")) > 4096
                or len(args.caption.encode("utf-8")) > 65_536
                or any(character in args.target for character in "\r\n\t\x00")
            ):
                raise RuntimeError_("delivery arguments are invalid")
            _media_descriptor(args.media_fd)
            child = [
                "message",
                "send",
                "--channel",
                args.channel,
                "--target",
                args.target,
                "--media",
                f"/proc/self/fd/{args.media_fd}",
            ]
            if args.caption:
                child.extend(("-m", args.caption))
        for descriptor in (node_descriptor, entry_descriptor, dist_descriptor):
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
            fcntl.fcntl(descriptor, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
        argv = [
            "/proc/self/fd/" + str(node_descriptor),
            "/proc/self/fd/" + str(dist_descriptor) + "/index.js",
            *child,
        ]
        os.execve(argv[0], argv, _safe_environment(state_prefix))
    except (OSError, RuntimeError_) as exc:
        print(f"OpenClaw host CLI: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
