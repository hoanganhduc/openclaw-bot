#!/usr/bin/env python3
"""Transactionally render reviewed OpenClaw user-service definitions."""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys


MAX_FILES = 100
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_BYTES = 256 * 1024 * 1024
MAX_DELIVERY_CONFIG_BYTES = 4 * 1024 * 1024
MAX_WHATSAPP_FILES = 10_000
MAX_WHATSAPP_FILE_BYTES = 2 * 1024 * 1024
MAX_WHATSAPP_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PROJECTION_CLEANUP_ENTRIES = 20_000
HOST_RUNTIME_SCHEMA = "openclaw.host-runtime/v1"
DELIVERY_PROJECTION_SCHEMA = "openclaw.delivery-projection/v1"
DELIVERY_CHANNELS = ("telegram", "zulip", "googlechat", "whatsapp", "zalo")
DELIVERY_STATE_CHANNELS = ("zulip", "googlechat", "whatsapp", "zalo")
OBSOLETE_REVIEWED_FILES = (
    PurePosixPath("openclaw-gateway.service.d/10-moltbook-env.conf"),
)
DELIVERY_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "zulip": ("ZULIP_API_KEY",),
    "googlechat": (),
    "whatsapp": (),
    "zalo": ("ZALO_BOT_TOKEN",),
}
CHANNEL_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "telegram": frozenset(
        {
            "allowFrom",
            "botToken",
            "dmPolicy",
            "enabled",
            "groupAllowFrom",
            "groupPolicy",
            "streaming",
        }
    ),
    "zulip": frozenset(
        {
            "allowFrom",
            "apiKey",
            "chatmode",
            "dmPolicy",
            "email",
            "enabled",
            "groupAllowFrom",
            "groupPolicy",
            "requireMention",
            "streams",
            "url",
        }
    ),
    "googlechat": frozenset(
        {
            "actions",
            "appPrincipal",
            "audience",
            "audienceType",
            "dm",
            "enabled",
            "groupAllowFrom",
            "groupPolicy",
            "replyToMode",
            "requireMention",
            "serviceAccountFile",
            "typingIndicator",
            "webhookPath",
        }
    ),
    "whatsapp": frozenset(
        {
            "allowFrom",
            "blockStreaming",
            "debounceMs",
            "dmPolicy",
            "enabled",
            "groupAllowFrom",
            "groupPolicy",
            "mediaMaxMb",
            "selfChatMode",
        }
    ),
    "zalo": frozenset(
        {
            "allowFrom",
            "botToken",
            "dmPolicy",
            "enabled",
            "groupAllowFrom",
            "groupPolicy",
        }
    ),
}
SAFE_UNIT_PATH_RE = re.compile(r"^/[A-Za-z0-9._+@:/-]+$")
SAFE_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
WHATSAPP_MEMBER_RE = re.compile(r"^[A-Za-z0-9@._-]{1,255}\.json$")
DELIVERY_STAGE_RE = re.compile(
    r"^\.(telegram|zulip|googlechat|whatsapp|zalo)\.delivery-stage-[0-9a-f]{32}$"
)
SECRET_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
GOOGLE_SERVICE_ACCOUNT_REQUIRED_KEYS = frozenset(
    {
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "auth_uri",
        "token_uri",
        "auth_provider_x509_cert_url",
        "client_x509_cert_url",
    }
)
GOOGLE_SERVICE_ACCOUNT_ALLOWED_KEYS = GOOGLE_SERVICE_ACCOUNT_REQUIRED_KEYS | {
    "universe_domain"
}
GOOGLE_PRIVATE_KEY_LABEL = "PRIVATE" + " KEY"
GOOGLE_PRIVATE_KEY_BEGIN = f"-----BEGIN {GOOGLE_PRIVATE_KEY_LABEL}-----\n"
GOOGLE_PRIVATE_KEY_END = f"-----END {GOOGLE_PRIVATE_KEY_LABEL}-----"
HOST_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("host_exec.py", "scripts/host_exec.py", "python-isolated"),
    ("openclaw_host_cli.py", "scripts/openclaw_host_cli.py", "python-isolated"),
    ("owner_state_lock.py", "scripts/owner_state_lock.py", "python-isolated"),
    ("file_delivery.py", "scripts/file_delivery.py", "python-isolated"),
    ("queue_boundary.py", "scripts/queue_boundary.py", "python-isolated"),
    ("email_delivery.py", "scripts/email_delivery.py", "python-isolated"),
    ("job_queue_worker.sh", "workspace/scripts/job_queue_worker.sh", "bash"),
    ("moltbook-relay.sh", "workspace/scripts/moltbook-relay.sh", "bash"),
    (
        "rss-news-digest/run_and_summarize.sh",
        "workspace/skills/rss-news-digest/run_and_summarize.sh",
        "bash",
    ),
    (
        "rss-news-digest/rss_news_digest.py",
        "workspace/skills/rss-news-digest/rss_news_digest.py",
        "data",
    ),
    (
        "rss-news-digest/rss_summary_publish.py",
        "scripts/rss_summary_publish.py",
        "python-isolated",
    ),
    (
        "manim-math-animation/run_manim_math_animation.sh",
        "workspace/skills/manim-math-animation/run_manim_math_animation.sh",
        "bash",
    ),
    (
        "manim-math-animation/manim_math_animation_runtime.py",
        "workspace/skills/manim-math-animation/manim_math_animation_runtime.py",
        "data",
    ),
    ("manim-math-animation/mma/__init__.py", "workspace/skills/manim-math-animation/mma/__init__.py", "data"),
    ("manim-math-animation/mma/doctor.py", "workspace/skills/manim-math-animation/mma/doctor.py", "data"),
    ("manim-math-animation/mma/model.py", "workspace/skills/manim-math-animation/mma/model.py", "data"),
    ("manim-math-animation/mma/render.py", "workspace/skills/manim-math-animation/mma/render.py", "data"),
    ("manim-math-animation/mma/scenegen.py", "workspace/skills/manim-math-animation/mma/scenegen.py", "data"),
    ("manim-math-animation/mma/selftest.py", "workspace/skills/manim-math-animation/mma/selftest.py", "data"),
    ("send-email/send_email.py", "workspace/skills/send-email/send_email.py", "data"),
)


class ServiceTransactionError(RuntimeError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def reviewed_install_scope(
    installer_descriptor: int,
) -> tuple[Path, Path, Path, Path]:
    """Bind the internal helper to this repo and the caller's real home.

    The installer descriptor is a narrow inherited capability.  Path-based
    source/destination/home arguments are deliberately not part of the CLI.
    """

    helper = absolute(Path(__file__))
    helper_information = helper.lstat()
    if (
        stat.S_ISLNK(helper_information.st_mode)
        or not stat.S_ISREG(helper_information.st_mode)
        or helper_information.st_nlink != 1
        or helper_information.st_uid != os.geteuid()
        or stat.S_IMODE(helper_information.st_mode) & 0o022
    ):
        raise ServiceTransactionError("service transaction helper is unsafe")
    repository = helper.parent.parent
    installer = repository / "install.sh"
    expected = installer.lstat()
    opened = os.fstat(installer_descriptor)
    flags = fcntl.fcntl(installer_descriptor, fcntl.F_GETFL)
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
        or expected.st_uid != os.geteuid()
        or stat.S_IMODE(expected.st_mode) & 0o022
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        or flags & os.O_ACCMODE != os.O_RDONLY
    ):
        raise ServiceTransactionError("invalid reviewed-installer capability")
    account = pwd.getpwuid(os.geteuid())
    home = absolute(Path(account.pw_dir))
    source = repository / "systemd" / "user"
    destination = home / ".config" / "systemd" / "user"
    return source, destination, home, repository


def open_directory_nofollow(path: Path) -> int:
    path = absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.anchor or os.sep, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in path.parts[1:]:
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


def ensure_directory(path: Path, *, mode: int = 0o700) -> int:
    path = absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.anchor or os.sep, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in path.parts[1:]:
            try:
                os.mkdir(component, mode, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component,
                flags | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) & 0o022:
            raise ServiceTransactionError("service destination is not owner-controlled")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_regular_at(
    parent: int,
    name: str,
    *,
    maximum: int = MAX_FILE_BYTES,
    allowed_uids: frozenset[int] | None = None,
    require_nonwritable: bool = True,
) -> tuple[bytes, int, tuple[int, int]]:
    if allowed_uids is None:
        allowed_uids = frozenset({os.geteuid()})
    path_info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0),
        dir_fd=parent,
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in allowed_uids
            or (require_nonwritable and stat.S_IMODE(before.st_mode) & 0o022)
            or before.st_size > maximum
            or (path_info.st_dev, path_info.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ServiceTransactionError("service file is unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if len(payload) > maximum or (
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
            raise ServiceTransactionError("service file changed while reading")
        return payload, stat.S_IMODE(before.st_mode), (before.st_dev, before.st_ino)
    finally:
        os.close(descriptor)


def write_atomic_at(parent: int, name: str, payload: bytes, mode: int) -> None:
    temporary = f".{name}.service-stage.{secrets.token_hex(12)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ServiceTransactionError("service file write was truncated")
                view = view[written:]
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
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


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ServiceTransactionError("delivery projection contains invalid JSON") from exc
    return (encoded + "\n").encode("utf-8")


def _load_private_json(
    path: Path,
    *,
    label: str,
    required: bool,
    maximum: int = MAX_DELIVERY_CONFIG_BYTES,
) -> dict[str, object] | None:
    parent = open_directory_nofollow(path.parent)
    try:
        parent_information = os.fstat(parent)
        if (
            parent_information.st_uid != os.geteuid()
            or stat.S_IMODE(parent_information.st_mode) & 0o022
        ):
            raise ServiceTransactionError(f"{label} parent is not owner-controlled")
        try:
            payload, mode, _identity = read_regular_at(
                parent,
                path.name,
                maximum=maximum,
            )
        except FileNotFoundError:
            if required:
                raise ServiceTransactionError(f"{label} is missing")
            return None
    finally:
        os.close(parent)
    if mode & 0o077:
        raise ServiceTransactionError(f"{label} is not owner-private")
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ServiceTransactionError(f"{label} is malformed") from exc
    if not isinstance(value, dict):
        raise ServiceTransactionError(f"{label} must be a JSON object")
    return value


def _validate_owner_directory_chain(
    base: Path,
    target: Path,
    *,
    private_final: bool,
) -> None:
    base = absolute(base)
    target = absolute(target)
    try:
        relative = target.relative_to(base)
    except ValueError as exc:
        raise ServiceTransactionError("canonical authority directory is outside its root") from exc
    descriptor = open_directory_nofollow(base)
    try:
        for index, component in enumerate((None, *relative.parts)):
            if component is not None:
                child = os.open(
                    component,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            information = os.fstat(descriptor)
            final = index == len(relative.parts)
            if (
                information.st_uid != os.geteuid()
                or stat.S_IMODE(information.st_mode) & 0o022
                or (
                    private_final
                    and final
                    and stat.S_IMODE(information.st_mode) & 0o077
                )
            ):
                raise ServiceTransactionError(
                    "canonical authority directory is not owner-controlled"
                )
    finally:
        os.close(descriptor)


def _channel_config(
    document: dict[str, object] | None, channel: str
) -> dict[str, object] | None:
    if document is None:
        return None
    channels = document.get("channels")
    if channels is None:
        return None
    if not isinstance(channels, dict):
        raise ServiceTransactionError("OpenClaw channel configuration is malformed")
    value = channels.get(channel)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ServiceTransactionError("configured delivery channel is malformed")
    enabled = value.get("enabled")
    if enabled is not True:
        if enabled is None or enabled is False:
            return None
        raise ServiceTransactionError("delivery channel enabled flag is malformed")
    if not set(value).issubset(CHANNEL_ALLOWED_KEYS[channel]):
        raise ServiceTransactionError("configured delivery channel has unsupported authority")
    if len(_json_bytes(value)) > MAX_DELIVERY_CONFIG_BYTES:
        raise ServiceTransactionError("configured delivery channel is oversized")
    return value


def _validate_secret_ref(
    channel_config: dict[str, object],
    *,
    field: str,
    secret_key: str,
) -> None:
    reference = channel_config.get(field)
    if (
        not isinstance(reference, dict)
        or set(reference) != {"source", "provider", "id"}
        or reference.get("source") != "file"
        or not isinstance(reference.get("provider"), str)
        or SECRET_PROVIDER_RE.fullmatch(str(reference["provider"])) is None
        or reference.get("id") != f"/{secret_key}"
    ):
        raise ServiceTransactionError("configured delivery SecretRef is not canonical")


def _secret_value(
    secrets_document: dict[str, object] | None,
    *,
    secret_key: str,
) -> str:
    if secrets_document is None:
        raise ServiceTransactionError("configured delivery secret authority is missing")
    value = secrets_document.get(secret_key)
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 16 * 1024
        or any(character.isspace() or ord(character) == 127 for character in value)
    ):
        raise ServiceTransactionError("configured delivery secret authority is invalid")
    return value


def _expanded_owner_path(value: str, home: Path) -> Path:
    if value.startswith("~/"):
        candidate = home / value[2:]
    elif value.startswith("~"):
        raise ServiceTransactionError("Google Chat service-account path is invalid")
    else:
        candidate = Path(value)
    if not candidate.is_absolute():
        raise ServiceTransactionError("Google Chat service-account path is not absolute")
    return absolute(candidate)


def _google_service_account(
    channel_config: dict[str, object], *, home: Path
) -> dict[str, object]:
    configured = channel_config.get("serviceAccountFile")
    if not isinstance(configured, str) or not configured:
        raise ServiceTransactionError("configured Google Chat authority is missing")
    canonical_root = absolute(home / ".config" / "openclaw" / "google-chat")
    try:
        _validate_owner_directory_chain(
            home,
            canonical_root,
            private_final=True,
        )
    except FileNotFoundError as exc:
        raise ServiceTransactionError("configured Google Chat authority is missing") from exc
    source = _expanded_owner_path(configured, home)
    if (
        source.parent != canonical_root
        or source.suffix != ".json"
        or source.name in {".", ".."}
        or "/" in source.name
        or len(source.name.encode("utf-8", errors="surrogatepass")) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in source.name)
    ):
        raise ServiceTransactionError("Google Chat authority is outside its canonical root")
    value = _load_private_json(
        source,
        label="Google Chat service-account authority",
        required=True,
        maximum=1024 * 1024,
    )
    assert value is not None
    if (
        not GOOGLE_SERVICE_ACCOUNT_REQUIRED_KEYS.issubset(value)
        or not set(value).issubset(GOOGLE_SERVICE_ACCOUNT_ALLOWED_KEYS)
        or value.get("type") != "service_account"
    ):
        raise ServiceTransactionError("Google Chat service-account authority is invalid")
    for key in GOOGLE_SERVICE_ACCOUNT_REQUIRED_KEYS - {"type", "private_key"}:
        item = value.get(key)
        if (
            not isinstance(item, str)
            or not item
            or len(item.encode("utf-8")) > 64 * 1024
            or any(ord(character) < 32 and character not in "\t\r\n" for character in item)
        ):
            raise ServiceTransactionError("Google Chat service-account authority is invalid")
    private_key = value.get("private_key")
    if (
        not isinstance(private_key, str)
        or len(private_key.encode("utf-8")) > 128 * 1024
        or not private_key.startswith(GOOGLE_PRIVATE_KEY_BEGIN)
        or not private_key.rstrip("\n").endswith(GOOGLE_PRIVATE_KEY_END)
    ):
        raise ServiceTransactionError("Google Chat service-account private key is invalid")
    if "@" not in str(value["client_email"]) or not str(value["token_uri"]).startswith(
        "https://"
    ):
        raise ServiceTransactionError("Google Chat service-account identity is invalid")
    return {key: value[key] for key in sorted(value)}


def _whatsapp_authority(prefix: Path) -> list[tuple[PurePosixPath, bytes, int]]:
    root = prefix / "credentials" / "whatsapp" / "default"
    try:
        _validate_owner_directory_chain(prefix, root, private_final=True)
    except FileNotFoundError as exc:
        raise ServiceTransactionError("configured WhatsApp session authority is missing") from exc
    try:
        descriptor = open_directory_nofollow(root)
    except FileNotFoundError as exc:
        raise ServiceTransactionError("configured WhatsApp session authority is missing") from exc
    try:
        before = os.fstat(descriptor)
        if (
            before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise ServiceTransactionError("WhatsApp session authority is not owner-private")
        names = sorted(os.listdir(descriptor))
        if not names or len(names) > MAX_WHATSAPP_FILES:
            raise ServiceTransactionError("WhatsApp session authority has an invalid member count")
        projected: list[tuple[PurePosixPath, bytes, int]] = []
        total = 0
        for name in names:
            if WHATSAPP_MEMBER_RE.fullmatch(name) is None:
                raise ServiceTransactionError("WhatsApp session authority has an unsafe member")
            payload, mode, _identity = read_regular_at(
                descriptor,
                name,
                maximum=MAX_WHATSAPP_FILE_BYTES,
            )
            if mode & 0o077:
                raise ServiceTransactionError("WhatsApp session member is not owner-private")
            total += len(payload)
            if total > MAX_WHATSAPP_TOTAL_BYTES:
                raise ServiceTransactionError("WhatsApp session authority is oversized")
            try:
                json.loads(
                    payload,
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ServiceTransactionError("WhatsApp session authority is malformed") from exc
            projected.append(
                (
                    PurePosixPath("credentials", "whatsapp", "default", name),
                    payload,
                    0o400,
                )
            )
        after = os.fstat(descriptor)
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
            raise ServiceTransactionError("WhatsApp session authority changed while reading")
        return projected
    finally:
        os.close(descriptor)


def _status_projection(channel: str, status: str) -> tuple[PurePosixPath, bytes, int]:
    return (
        PurePosixPath("STATUS.json"),
        _json_bytes(
            {
                "channel": channel,
                "schema": DELIVERY_PROJECTION_SCHEMA,
                "status": status,
            }
        ),
        0o400,
    )


def _openclaw_projection_config(
    channel: str,
    channel_config: dict[str, object],
    *,
    authority_root: Path,
    secret_key: str | None,
) -> bytes:
    projected_config = dict(channel_config)
    if secret_key is not None:
        field = "apiKey" if channel == "zulip" else "botToken"
        projected_config[field] = {
            "id": f"/{secret_key}",
            "provider": "delivery",
            "source": "file",
        }
    document: dict[str, object] = {
        "channels": {channel: projected_config},
        "plugins": {
            "allow": [channel],
            "bundledDiscovery": "compat",
            "entries": {channel: {"config": {}, "enabled": True}},
            "load": {"paths": []},
        },
    }
    if secret_key is not None:
        document["secrets"] = {
            "defaults": {"file": "delivery"},
            "providers": {
                "delivery": {
                    "mode": "json",
                    "path": os.fspath(authority_root / channel / "secrets.json"),
                    "source": "file",
                }
            },
        }
    return _json_bytes(document)


def _build_delivery_projections(
    prefix: Path, home: Path, authority_root: Path
) -> dict[str, list[tuple[PurePosixPath, bytes, int]]]:
    config = _load_private_json(
        prefix / "openclaw.json",
        label="OpenClaw channel configuration",
        required=False,
    )
    channel_configs = {
        channel: _channel_config(config, channel) for channel in DELIVERY_CHANNELS
    }
    needs_secrets = any(
        channel_configs[channel] is not None and DELIVERY_SECRET_KEYS[channel]
        for channel in DELIVERY_CHANNELS
    )
    secrets_document = (
        _load_private_json(
            prefix / "secrets.json",
            label="OpenClaw secrets authority",
            required=True,
            maximum=1024 * 1024,
        )
        if needs_secrets
        else None
    )
    projections: dict[str, list[tuple[PurePosixPath, bytes, int]]] = {}
    for channel in DELIVERY_CHANNELS:
        channel_config = channel_configs[channel]
        if channel_config is None:
            files = [_status_projection(channel, "NOT_CONFIGURED")]
            if channel == "telegram":
                files.append((PurePosixPath("token"), b"", 0o400))
            projections[channel] = files
            continue

        files = [_status_projection(channel, "CONFIGURED")]
        secret_key = DELIVERY_SECRET_KEYS[channel][0] if DELIVERY_SECRET_KEYS[channel] else None
        secret_value: str | None = None
        if secret_key is not None:
            field = "apiKey" if channel == "zulip" else "botToken"
            _validate_secret_ref(
                channel_config,
                field=field,
                secret_key=secret_key,
            )
            secret_value = _secret_value(
                secrets_document,
                secret_key=secret_key,
            )
        if channel == "telegram":
            assert secret_value is not None
            files.append((PurePosixPath("token"), secret_value.encode("utf-8"), 0o400))
            projections[channel] = files
            continue

        if channel == "googlechat":
            service_account = _google_service_account(channel_config, home=home)
            channel_config = dict(channel_config)
            channel_config["serviceAccountFile"] = os.fspath(
                authority_root / channel / "service-account.json"
            )
            files.append(
                (
                    PurePosixPath("service-account.json"),
                    _json_bytes(service_account),
                    0o400,
                )
            )
        if channel == "whatsapp":
            files.extend(_whatsapp_authority(prefix))
        if secret_key is not None:
            assert secret_value is not None
            files.append(
                (
                    PurePosixPath("secrets.json"),
                    _json_bytes({secret_key: secret_value}),
                    0o400,
                )
            )
        files.append(
            (
                PurePosixPath("openclaw.json"),
                _openclaw_projection_config(
                    channel,
                    channel_config,
                    authority_root=authority_root,
                    secret_key=secret_key,
                ),
                0o400,
            )
        )
        projections[channel] = files
    return projections


def _existing_projection(parent: int, channel: str) -> tuple[int, os.stat_result] | None:
    try:
        descriptor = os.open(
            channel,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
    except FileNotFoundError:
        return None
    try:
        information = os.fstat(descriptor)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ServiceTransactionError("existing delivery projection is unsafe")
        names = os.listdir(descriptor)
        if names:
            payload, mode, _identity = read_regular_at(
                descriptor,
                "STATUS.json",
                maximum=4096,
            )
            status = json.loads(
                payload,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            if (
                mode != 0o400
                or not isinstance(status, dict)
                or set(status) != {"schema", "channel", "status"}
                or status.get("schema") != DELIVERY_PROJECTION_SCHEMA
                or status.get("channel") != channel
                or status.get("status") not in {"CONFIGURED", "NOT_CONFIGURED"}
            ):
                raise ServiceTransactionError("existing delivery projection is unrecognized")
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        os.close(descriptor)
        raise ServiceTransactionError("existing delivery projection is unrecognized") from exc
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, information


def _remove_projection_tree_at(
    parent: int,
    name: str,
    *,
    expected_identity: tuple[int, int],
) -> None:
    if DELIVERY_STAGE_RE.fullmatch(name) is None:
        raise ServiceTransactionError("refusing unsafe delivery-projection cleanup")
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    budget = [MAX_PROJECTION_CLEANUP_ENTRIES]

    def remove_contents(directory: int) -> None:
        for member in os.listdir(directory):
            budget[0] -= 1
            if budget[0] < 0:
                raise ServiceTransactionError("delivery-projection cleanup exceeded its bound")
            information = os.stat(member, dir_fd=directory, follow_symlinks=False)
            if information.st_uid != os.geteuid():
                raise ServiceTransactionError("delivery-projection cleanup found foreign ownership")
            if stat.S_ISDIR(information.st_mode):
                child = os.open(
                    member,
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
                        raise ServiceTransactionError(
                            "delivery-projection cleanup encountered a race"
                        )
                    remove_contents(child)
                finally:
                    os.close(child)
                os.rmdir(member, dir_fd=directory)
            elif stat.S_ISREG(information.st_mode) or stat.S_ISLNK(information.st_mode):
                os.unlink(member, dir_fd=directory)
            else:
                raise ServiceTransactionError(
                    "delivery-projection cleanup found a special file"
                )
        os.fsync(directory)

    try:
        information = os.fstat(descriptor)
        if (
            (information.st_dev, information.st_ino) != expected_identity
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ServiceTransactionError("delivery-projection cleanup target changed")
        remove_contents(descriptor)
    finally:
        os.close(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != expected_identity:
        raise ServiceTransactionError("delivery-projection cleanup target changed")
    os.rmdir(name, dir_fd=parent)
    os.fsync(parent)


def _write_projection_stage(
    parent: int,
    channel: str,
    files: list[tuple[PurePosixPath, bytes, int]],
) -> tuple[str, tuple[int, int]]:
    stage_name = f".{channel}.delivery-stage-{secrets.token_hex(16)}"
    os.mkdir(stage_name, 0o700, dir_fd=parent)
    stage = os.open(
        stage_name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
    identity = os.fstat(stage)
    try:
        for relative, payload, mode in files:
            current = stage
            opened: list[int] = []
            try:
                for component in relative.parts[:-1]:
                    child = _open_child_private(current, component)
                    opened.append(child)
                    current = child
                _write_exclusive_at(current, relative.name, payload, mode)
                os.fsync(current)
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
        os.fsync(stage)
    except Exception:
        os.close(stage)
        _remove_projection_tree_at(
            parent,
            stage_name,
            expected_identity=(identity.st_dev, identity.st_ino),
        )
        raise
    os.close(stage)
    return stage_name, (identity.st_dev, identity.st_ino)


def _publish_projection(
    parent: int,
    channel: str,
    stage_name: str,
    stage_identity: tuple[int, int],
) -> None:
    try:
        _rename_noreplace(parent, stage_name, channel)
    except FileExistsError:
        existing = _existing_projection(parent, channel)
        if existing is None:
            raise ServiceTransactionError("delivery projection changed during publication")
        old_descriptor, old_information = existing
        exchanged = False
        try:
            _rename_exchange(parent, stage_name, channel)
            exchanged = True
            parked = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
            if (parked.st_dev, parked.st_ino) != (
                old_information.st_dev,
                old_information.st_ino,
            ):
                raise ServiceTransactionError("delivery projection changed during publication")
        except Exception:
            if not exchanged:
                named = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino) == stage_identity:
                    _remove_projection_tree_at(
                        parent,
                        stage_name,
                        expected_identity=stage_identity,
                    )
            raise
        finally:
            os.close(old_descriptor)
        _remove_projection_tree_at(
            parent,
            stage_name,
            expected_identity=(old_information.st_dev, old_information.st_ino),
        )
    except Exception:
        try:
            named = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (named.st_dev, named.st_ino) == stage_identity:
                _remove_projection_tree_at(
                    parent,
                    stage_name,
                    expected_identity=stage_identity,
                )
        raise
    os.fsync(parent)


def _remove_legacy_telegram_token(parent: int) -> None:
    name = "telegram-token"
    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0),
        dir_fd=parent,
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ServiceTransactionError("legacy Telegram projection is unsafe")
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ServiceTransactionError("legacy Telegram projection changed")
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


def _materialize_delivery_authorities(prefix: Path, home: Path) -> None:
    authority_root = absolute(
        home / ".local" / "state" / "openclaw-bot" / "delivery-authorities"
    )
    root = open_directory_nofollow(authority_root)
    try:
        information = os.fstat(root)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise ServiceTransactionError("delivery authority root is unsafe")
        projections = _build_delivery_projections(prefix, home, authority_root)
        # Preflight every existing target before publishing the first projection.
        for channel in DELIVERY_CHANNELS:
            existing = _existing_projection(root, channel)
            if existing is not None:
                os.close(existing[0])
        for channel in DELIVERY_CHANNELS:
            stage_name, stage_identity = _write_projection_stage(
                root,
                channel,
                projections[channel],
            )
            _publish_projection(
                root,
                channel,
                stage_name,
                stage_identity,
            )
        _remove_legacy_telegram_token(root)
    finally:
        os.close(root)


def _safe_unit_path(path: Path, *, label: str) -> Path:
    value = os.fspath(absolute(path))
    if SAFE_UNIT_PATH_RE.fullmatch(value) is None or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ServiceTransactionError(f"{label} cannot be represented safely in a unit")
    return Path(value)


def _owner_state_lock_path(prefix: Path) -> Path:
    name = prefix.name
    if not name.startswith("."):
        name = f".{name}"
    return prefix.parent / f"{name}.owner-state.lock"


def _render(
    text: str, *, prefix: Path, home: Path, libexec: Path
) -> str:
    return (
        text.replace("{{ OPENCLAW_HOME }}", os.fspath(prefix))
        .replace("{{ OPENCLAW_WORKSPACE }}", os.fspath(prefix / "workspace"))
        .replace(
            "{{ OWNER_STATE_LOCK }}",
            os.fspath(_owner_state_lock_path(prefix)),
        )
        .replace("{{ USER_HOME }}", os.fspath(home))
        .replace("{{ OPENCLAW_LIBEXEC_ROOT }}", os.fspath(libexec.parent))
        .replace("{{ OPENCLAW_LIBEXEC }}", os.fspath(libexec))
    )


def _validate_rendered_unit(relative: PurePosixPath, text: str, *, libexec: Path) -> None:
    if any(
        character != "\n" and (ord(character) < 32 or ord(character) == 127)
        for character in text
    ):
        raise ServiceTransactionError("rendered service contains a control character")
    section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            if section not in {"Unit", "Service", "Install", "Timer"}:
                raise ServiceTransactionError("rendered service contains an unknown section")
            continue
        if section is None or "=" not in line or line[:1].isspace():
            raise ServiceTransactionError("rendered service contains an injected directive")
        key, value = line.split("=", 1)
        if not key or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key):
            raise ServiceTransactionError("rendered service contains an invalid directive")
        if key in {"ExecStart", "ExecStartPre", "ExecStartPost"}:
            try:
                argv = shlex.split(value, posix=True)
            except ValueError as exc:
                raise ServiceTransactionError("rendered service command is malformed") from exc
            if not argv or not argv[0].startswith("/"):
                raise ServiceTransactionError("rendered service command is not absolute")
            if any("/workspace/" in argument for argument in argv):
                raise ServiceTransactionError("rendered service executes writable workspace code")
            if argv[0] == "/usr/bin/python3":
                expected = os.fspath(libexec / "host_exec.py")
                if expected not in argv:
                    raise ServiceTransactionError("custom service command bypasses host attestation")
            elif argv[0] != "/usr/bin/Xvfb":
                raise ServiceTransactionError("service command is outside the reviewed executable set")
        if key == "EnvironmentFile" and "/workspace/" in value:
            raise ServiceTransactionError("service environment authority is inside the workspace")
    if relative.suffix == ".service" and "[Service]" not in text:
        raise ServiceTransactionError("service definition lacks a Service section")


def collect(
    source: Path, *, prefix: Path, home: Path, libexec: Path
) -> list[tuple[PurePosixPath, bytes, int]]:
    source = absolute(source)
    source_descriptor = open_directory_nofollow(source)
    source_information = os.fstat(source_descriptor)
    if (
        source_information.st_uid != os.geteuid()
        or stat.S_IMODE(source_information.st_mode) & 0o022
    ):
        os.close(source_descriptor)
        raise ServiceTransactionError("service source directory is not owner-controlled")
    os.close(source_descriptor)
    output: list[tuple[PurePosixPath, bytes, int]] = []
    for root, directories, files in os.walk(source, topdown=True, followlinks=False):
        root_path = Path(root)
        safe_directories: list[str] = []
        for name in sorted(directories):
            child = root_path / name
            information = child.lstat()
            if (
                stat.S_ISLNK(information.st_mode)
                or not stat.S_ISDIR(information.st_mode)
                or information.st_uid != os.geteuid()
                or stat.S_IMODE(information.st_mode) & 0o022
            ):
                raise ServiceTransactionError("service source tree contains a link/special entry")
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files):
            child = root_path / name
            if child.is_symlink():
                raise ServiceTransactionError("service source tree contains a symlink")
            parent = open_directory_nofollow(child.parent)
            try:
                payload, mode, _identity = read_regular_at(parent, child.name)
            finally:
                os.close(parent)
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ServiceTransactionError("service definition is not UTF-8") from exc
            rendered = _render(text, prefix=prefix, home=home, libexec=libexec)
            if "{{" in rendered or "}}" in rendered:
                raise ServiceTransactionError("service definition retains an unresolved placeholder")
            relative = PurePosixPath(child.relative_to(source).as_posix())
            _validate_rendered_unit(relative, rendered, libexec=libexec)
            output.append((relative, rendered.encode("utf-8"), mode & ~0o022))
            if len(output) > MAX_FILES:
                raise ServiceTransactionError("service source exceeds the file limit")
    return output


def _collect_host_artifacts(
    repository: Path, external_runtime: dict[str, object]
) -> tuple[str, list[tuple[PurePosixPath, bytes, int, str]], bytes]:
    artifacts: list[tuple[PurePosixPath, bytes, int, str]] = []
    records: dict[str, dict[str, object]] = {}
    for destination, source_name, interpreter in HOST_ARTIFACTS:
        source = repository / source_name
        _validate_source_chain(repository, source.parent)
        parent = open_directory_nofollow(source.parent)
        try:
            payload, source_mode, _identity = read_regular_at(parent, source.name)
        finally:
            os.close(parent)
        relative = PurePosixPath(destination)
        mode = 0o500 if interpreter in {"bash", "python-isolated"} else 0o400
        if source_mode & 0o111 and interpreter == "data":
            mode = 0o400
        digest = hashlib.sha256(payload).hexdigest()
        records[relative.as_posix()] = {
            "sha256": digest,
            "mode": mode,
            "interpreter": interpreter,
        }
        artifacts.append((relative, payload, mode, interpreter))
    generation_input = json.dumps(
        {"artifacts": records, "externalRuntime": external_runtime},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generation = hashlib.sha256(generation_input).hexdigest()
    manifest = (
        json.dumps(
            {
                "schema": HOST_RUNTIME_SCHEMA,
                "generation": generation,
                "artifacts": records,
                "externalRuntime": external_runtime,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return generation, artifacts, manifest


def _prepare_service_state(prefix: Path, home: Path, repository: Path) -> None:
    """Create only the private bind sources required by reviewed workers."""

    workspace = prefix / "workspace"
    directories = (
        workspace / "data" / "send-queue",
        workspace / "data" / "send-queue" / "telegram",
        workspace / "data" / "send-queue" / "zulip",
        workspace / "data" / "send-queue" / "googlechat",
        workspace / "data" / "send-queue" / "whatsapp",
        workspace / "data" / "send-queue" / "zalo",
        workspace / "data" / "job-queue",
        workspace / "data" / "manim-queue",
        workspace / "data" / "email-queue",
        workspace / "data" / "exports",
        workspace / "data" / "research" / "sagemath",
        workspace / "data" / "research" / "manim",
        workspace / "data" / "research" / "email",
        workspace / "data" / "research" / "rss",
        workspace / "data" / "research" / "rss" / "digests",
        workspace / "data" / "research" / "rss" / "backups",
        workspace / "data" / "sessions",
        prefix / "email-approvals",
        home / ".local" / "state" / "openclaw-bot" / "delivery-spool",
        home / ".local" / "state" / "openclaw-bot" / "delivery-authorities",
    )
    for path in directories:
        descriptor = ensure_directory(path)
        try:
            information = os.fstat(descriptor)
            if information.st_uid != os.geteuid():
                raise ServiceTransactionError(
                    "worker bind source has the wrong owner"
                )
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    lock_parent = open_directory_nofollow(prefix.parent)
    lock_descriptor: int | None = None
    try:
        parent_information = os.fstat(lock_parent)
        if (
            parent_information.st_uid != os.geteuid()
            or stat.S_IMODE(parent_information.st_mode) & 0o022
        ):
            raise ServiceTransactionError("owner-state lock parent is unsafe")
        lock_descriptor = os.open(
            _owner_state_lock_path(prefix).name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=lock_parent,
        )
        lock_information = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_information.st_mode)
            or lock_information.st_nlink != 1
            or lock_information.st_uid != os.geteuid()
            or stat.S_IMODE(lock_information.st_mode) & 0o077
        ):
            raise ServiceTransactionError("owner-state lock is unsafe")
        os.fchmod(lock_descriptor, 0o600)
        os.fsync(lock_descriptor)
        os.fsync(lock_parent)
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        os.close(lock_parent)

    template_parent = open_directory_nofollow(repository / "config")
    try:
        email_template, _mode, _identity = read_regular_at(
            template_parent, "email-policy.json.template"
        )
        file_delivery_template, _mode, _identity = read_regular_at(
            template_parent, "file-delivery-policy.json.template"
        )
    finally:
        os.close(template_parent)
    try:
        email_template_payload = json.loads(
            email_template, object_pairs_hook=_unique_json_object
        )
        file_delivery_template_payload = json.loads(
            file_delivery_template, object_pairs_hook=_unique_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ServiceTransactionError("reviewed email policy template is malformed") from exc
    if email_template_payload != {
        "schema": "openclaw.email-policy/v1",
        "email_policy": {"approved_messages": []},
    }:
        raise ServiceTransactionError("reviewed email policy template is not deny-all")
    deny_targets = {
        "telegram": [],
        "zulip": [],
        "googlechat": [],
        "whatsapp": [],
        "zalo": [],
    }
    if file_delivery_template_payload != {
        "schema": "openclaw.file-delivery-policy/v1",
        "delivery_policy": {"allowed_targets": deny_targets},
    }:
        raise ServiceTransactionError(
            "reviewed file-delivery policy template is not deny-all"
        )

    state_parent = open_directory_nofollow(prefix)
    try:
        try:
            current_policy, policy_mode, _identity = read_regular_at(
                state_parent, "file-delivery-policy.json"
            )
        except FileNotFoundError:
            _write_exclusive_at(
                state_parent,
                "file-delivery-policy.json",
                file_delivery_template,
                0o600,
            )
            os.fsync(state_parent)
        else:
            if policy_mode & 0o077:
                raise ServiceTransactionError("file-delivery policy is not private")
            try:
                policy_payload = json.loads(
                    current_policy, object_pairs_hook=_unique_json_object
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ServiceTransactionError(
                    "file-delivery policy is malformed"
                ) from exc
            body = (
                policy_payload.get("delivery_policy")
                if isinstance(policy_payload, dict)
                else None
            )
            allowed = body.get("allowed_targets") if isinstance(body, dict) else None
            if (
                not isinstance(policy_payload, dict)
                or set(policy_payload) != {"schema", "delivery_policy"}
                or policy_payload.get("schema")
                != "openclaw.file-delivery-policy/v1"
                or not isinstance(body, dict)
                or set(body) != {"allowed_targets"}
                or not isinstance(allowed, dict)
                or set(allowed) != set(deny_targets)
                or any(
                    not isinstance(values, list)
                    or len(values) > 1000
                    or any(not isinstance(value, str) or not value for value in values)
                    for values in allowed.values()
                )
            ):
                raise ServiceTransactionError(
                    "file-delivery policy shape is invalid"
                )
    finally:
        os.close(state_parent)

    _materialize_delivery_authorities(prefix, home)

    policy_parent = open_directory_nofollow(prefix / "email-approvals")
    try:
        try:
            current, mode, _identity = read_regular_at(
                policy_parent, "policy.json"
            )
        except FileNotFoundError:
            _write_exclusive_at(policy_parent, "policy.json", email_template, 0o600)
            os.fsync(policy_parent)
        else:
            if mode & 0o077:
                raise ServiceTransactionError("email approval policy is not private")
            try:
                payload = json.loads(
                    current, object_pairs_hook=_unique_json_object
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ServiceTransactionError("email approval policy is malformed") from exc
            if (
                not isinstance(payload, dict)
                or set(payload) != {"schema", "email_policy"}
                or payload.get("schema") != "openclaw.email-policy/v1"
                or not isinstance(payload.get("email_policy"), dict)
                or set(payload["email_policy"]) != {"approved_messages"}
                or not isinstance(
                    payload["email_policy"].get("approved_messages"), list
                )
            ):
                raise ServiceTransactionError("email approval policy shape is invalid")
    finally:
        os.close(policy_parent)


def _validate_source_chain(repository: Path, directory: Path) -> None:
    relative = directory.relative_to(repository)
    descriptor = open_directory_nofollow(repository)
    try:
        for component in (None, *relative.parts):
            if component is not None:
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            information = os.fstat(descriptor)
            if (
                information.st_uid != os.geteuid()
                or stat.S_IMODE(information.st_mode) & 0o022
            ):
                raise ServiceTransactionError(
                    "host runtime source directory is not owner-controlled"
                )
    finally:
        os.close(descriptor)


def _harden_runtime_path(path: Path) -> None:
    information = path.lstat()
    if (
        stat.S_ISLNK(information.st_mode)
        or information.st_uid != os.geteuid()
        or not (stat.S_ISDIR(information.st_mode) or stat.S_ISREG(information.st_mode))
    ):
        raise ServiceTransactionError("OpenClaw runtime path is not owner-controlled")
    mode = stat.S_IMODE(information.st_mode)
    if mode & 0o022:
        os.chmod(path, mode & ~0o022, follow_symlinks=False)


def _harden_runtime_tree(root: Path) -> None:
    root = absolute(root)
    count = 0
    for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        _harden_runtime_path(base)
        safe_directories: list[str] = []
        for name in sorted(directories):
            child = base / name
            information = child.lstat()
            count += 1
            if stat.S_ISLNK(information.st_mode):
                try:
                    child.resolve(strict=True).relative_to(root)
                except (OSError, ValueError) as exc:
                    raise ServiceTransactionError(
                        "OpenClaw runtime contains an external directory link"
                    ) from exc
                continue
            if not stat.S_ISDIR(information.st_mode):
                raise ServiceTransactionError(
                    "OpenClaw runtime contains a special directory entry"
                )
            _harden_runtime_path(child)
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files):
            child = base / name
            information = child.lstat()
            count += 1
            if stat.S_ISLNK(information.st_mode):
                try:
                    child.resolve(strict=True).relative_to(root)
                except (OSError, ValueError) as exc:
                    raise ServiceTransactionError(
                        "OpenClaw runtime contains an external file link"
                    ) from exc
                continue
            if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
                raise ServiceTransactionError(
                    "OpenClaw runtime contains an unsafe code file"
                )
            _harden_runtime_path(child)
            if count > 100_000:
                raise ServiceTransactionError("OpenClaw runtime tree is unexpectedly large")


def _runtime_record(
    path: Path, *, require_locked: bool
) -> tuple[dict[str, object], bytes]:
    parent = open_directory_nofollow(path.parent)
    try:
        payload, mode, _identity = read_regular_at(
            parent,
            path.name,
            maximum=MAX_RUNTIME_BYTES,
            allowed_uids=frozenset({0, os.geteuid()}),
            require_nonwritable=require_locked,
        )
        information = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)
    if len(payload) > MAX_RUNTIME_BYTES:
        raise ServiceTransactionError("OpenClaw runtime file exceeds the attestation limit")
    return (
        {
            "path": os.fspath(path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "device": information.st_dev,
            "inode": information.st_ino,
            "size": information.st_size,
            "mode": mode,
            "uid": information.st_uid,
        },
        payload,
    )


def _collect_external_runtime(
    repository: Path, home: Path, *, harden: bool
) -> dict[str, object]:
    manifest_parent = open_directory_nofollow(repository)
    try:
        manifest_payload, _mode, _identity = read_regular_at(
            manifest_parent, "REBUILD-MANIFEST.json"
        )
    finally:
        os.close(manifest_parent)
    try:
        rebuild = json.loads(manifest_payload)
        expected_version = rebuild["openclaw"]["observed_version"]
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceTransactionError("reviewed OpenClaw version metadata is invalid") from exc
    if not isinstance(expected_version, str) or not expected_version:
        raise ServiceTransactionError("reviewed OpenClaw version is missing")

    package_root = home / ".npm-global/lib/node_modules/openclaw"
    entry = package_root / "dist/index.js"
    package = package_root / "package.json"
    if harden:
        _harden_runtime_tree(package_root)
        for path in (
            home / ".npm-global",
            home / ".npm-global/lib",
            home / ".npm-global/lib/node_modules",
            package_root,
            package_root / "dist",
            entry,
            package,
        ):
            _harden_runtime_path(path)
    node_record, _node_payload = _runtime_record(
        Path("/usr/bin/node"), require_locked=True
    )
    entry_record, _entry_payload = _runtime_record(entry, require_locked=harden)
    package_record, package_payload = _runtime_record(
        package, require_locked=harden
    )
    try:
        package_metadata = json.loads(package_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceTransactionError("OpenClaw package metadata is malformed") from exc
    if not isinstance(package_metadata, dict) or package_metadata.get("version") != expected_version:
        raise ServiceTransactionError("installed OpenClaw version does not match the reviewed version")
    return {
        "version": expected_version,
        "node": node_record,
        "entry": entry_record,
        "package": package_record,
    }


def _verify_existing_generation(
    parent: int,
    generation: str,
    artifacts: list[tuple[PurePosixPath, bytes, int, str]],
    manifest: bytes,
) -> None:
    descriptor = os.open(
        generation,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    try:
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) != 0o700:
            raise ServiceTransactionError("existing host runtime generation is unsafe")
        actual_manifest, mode, _identity = read_regular_at(descriptor, "MANIFEST.json")
        if actual_manifest != manifest or mode != 0o400:
            raise ServiceTransactionError("existing host runtime manifest does not match")
        for relative, payload, expected_mode, _interpreter in artifacts:
            current = descriptor
            opened: list[int] = []
            try:
                for component in relative.parts[:-1]:
                    child = os.open(
                        component,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=current,
                    )
                    child_information = os.fstat(child)
                    if (
                        child_information.st_uid != os.geteuid()
                        or stat.S_IMODE(child_information.st_mode) != 0o700
                    ):
                        os.close(child)
                        raise ServiceTransactionError(
                            "existing host runtime directory is unsafe"
                        )
                    opened.append(child)
                    current = child
                actual, actual_mode, _identity = read_regular_at(current, relative.name)
                if actual != payload or actual_mode != expected_mode:
                    raise ServiceTransactionError(
                        "existing host runtime artifact does not match"
                    )
            finally:
                for child in reversed(opened):
                    os.close(child)
    finally:
        os.close(descriptor)


def _rename_with_flags(
    parent: int, source: str, destination: str, flags: int
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ServiceTransactionError("renameat2 is required for atomic publication")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        parent,
        os.fsencode(source),
        parent,
        os.fsencode(destination),
        flags,
    ) != 0:
        error = ctypes.get_errno()
        if flags == 1 and error == errno.EEXIST:
            raise FileExistsError(destination)
        raise OSError(error, os.strerror(error))


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    _rename_with_flags(parent, source, destination, 1)


def _rename_exchange(parent: int, source: str, destination: str) -> None:
    _rename_with_flags(parent, source, destination, 2)


def _install_host_generation(
    *, home: Path, generation: str, artifacts: list[tuple[PurePosixPath, bytes, int, str]], manifest: bytes
) -> Path:
    for parent_path in (home / ".local", home / ".local/libexec"):
        parent_descriptor = ensure_directory(parent_path)
        try:
            parent_information = os.fstat(parent_descriptor)
            os.fchmod(
                parent_descriptor,
                stat.S_IMODE(parent_information.st_mode) & ~0o022,
            )
        finally:
            os.close(parent_descriptor)
    root = home / ".local" / "libexec" / "openclaw-bot"
    root_descriptor = ensure_directory(root)
    try:
        os.fchmod(root_descriptor, 0o700)
        generations = _open_child_private(root_descriptor, "generations")
        try:
            try:
                existing = os.open(
                    generation,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=generations,
                )
            except FileNotFoundError:
                existing = None
            if existing is not None:
                os.close(existing)
                _verify_existing_generation(
                    generations, generation, artifacts, manifest
                )
                return root / "generations" / generation
            stage_name = f".stage-{secrets.token_hex(16)}"
            os.mkdir(stage_name, 0o700, dir_fd=generations)
            stage = os.open(
                stage_name,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=generations,
            )
            try:
                for relative, payload, mode, _interpreter in artifacts:
                    parent = stage
                    opened: list[int] = []
                    try:
                        for component in relative.parts[:-1]:
                            next_parent = _open_child_private(parent, component)
                            opened.append(next_parent)
                            parent = next_parent
                        _write_exclusive_at(parent, relative.name, payload, mode)
                    finally:
                        for descriptor in reversed(opened):
                            os.close(descriptor)
                _write_exclusive_at(stage, "MANIFEST.json", manifest, 0o400)
                os.fsync(stage)
            finally:
                os.close(stage)
            try:
                _rename_noreplace(generations, stage_name, generation)
            except FileExistsError:
                _verify_existing_generation(
                    generations, generation, artifacts, manifest
                )
                _remove_tree_at(generations, stage_name)
            except Exception:
                _remove_tree_at(generations, stage_name)
                raise
            os.fsync(generations)
        finally:
            os.close(generations)
    finally:
        os.close(root_descriptor)
    return root / "generations" / generation


def _open_child_private(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) & 0o077:
        os.close(descriptor)
        raise ServiceTransactionError("host runtime directory is unsafe")
    return descriptor


def _write_exclusive_at(parent: int, name: str, payload: bytes, mode: int) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=parent,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ServiceTransactionError("host runtime write was truncated")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree_at(parent: int, name: str) -> None:
    # Only transaction-created random stage names are accepted here.
    if not name.startswith(".stage-"):
        raise ServiceTransactionError("refusing unsafe host-runtime cleanup")
    path = Path(f"/proc/self/fd/{parent}") / name
    import shutil

    shutil.rmtree(path)


def reload_services(home: Path) -> None:
    executable = Path("/usr/bin/systemctl")
    if not executable.is_file() or executable.is_symlink():
        raise ServiceTransactionError("systemctl is unavailable for required daemon reload")
    result = subprocess.run(
        [os.fspath(executable), "--user", "daemon-reload"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env={
            "HOME": os.fspath(home),
            "PATH": "/usr/bin:/bin",
            **(
                {"XDG_RUNTIME_DIR": os.environ["XDG_RUNTIME_DIR"]}
                if os.environ.get("XDG_RUNTIME_DIR")
                else {}
            ),
        },
    )
    if result.returncode != 0:
        raise ServiceTransactionError("systemd user daemon reload failed")


def restart_active_services(home: Path, services: tuple[str, ...]) -> None:
    if not services:
        return
    if any(SAFE_SERVICE_NAME_RE.fullmatch(service) is None for service in services):
        raise ServiceTransactionError("reviewed service name is unsafe")
    executable = Path("/usr/bin/systemctl")
    if not executable.is_file() or executable.is_symlink():
        raise ServiceTransactionError("systemctl is unavailable for required service restart")
    result = subprocess.run(
        [
            os.fspath(executable),
            "--user",
            "try-restart",
            "--",
            *services,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
        env={
            "HOME": os.fspath(home),
            "PATH": "/usr/bin:/bin",
            **(
                {"XDG_RUNTIME_DIR": os.environ["XDG_RUNTIME_DIR"]}
                if os.environ.get("XDG_RUNTIME_DIR")
                else {}
            ),
        },
    )
    if result.returncode != 0:
        raise ServiceTransactionError("systemd active user-service restart failed")


def _install_reviewed_files(
    source: Path,
    destination: Path,
    *,
    prefix: Path,
    home: Path,
    libexec: Path,
    reload: bool,
    dry_run: bool,
) -> None:
    files = collect(source, prefix=prefix, home=home, libexec=libexec)
    services = tuple(
        sorted(
            relative.name
            for relative, _payload, _mode in files
            if len(relative.parts) == 1 and relative.suffix == ".service"
        )
    )
    managed_relatives = {relative for relative, _payload, _mode in files}
    obsolete = tuple(
        relative
        for relative in OBSOLETE_REVIEWED_FILES
        if relative not in managed_relatives
    )
    if dry_run:
        print(f"would install {len(files)} reviewed user-service files")
        if obsolete:
            print(
                "would remove obsolete reviewed user-service paths if present: "
                + ", ".join(map(str, obsolete))
            )
        if reload:
            print(
                "would reload the user service manager and restart active reviewed "
                f"services ({len(services)} candidates)"
            )
        return
    destination = absolute(destination)
    destination_descriptor = ensure_directory(destination)
    lock_descriptor = os.open(
        ".openclaw-service-install.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=destination_descriptor,
    )
    lock_information = os.fstat(lock_descriptor)
    if (
        not stat.S_ISREG(lock_information.st_mode)
        or lock_information.st_nlink != 1
        or lock_information.st_uid != os.geteuid()
        or stat.S_IMODE(lock_information.st_mode) & 0o077
    ):
        os.close(lock_descriptor)
        os.close(destination_descriptor)
        raise ServiceTransactionError("service transaction lock is unsafe")
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    originals: dict[PurePosixPath, tuple[bytes, int] | None] = {}
    installed: dict[PurePosixPath, str] = {}
    removed: dict[PurePosixPath, tuple[bytes, int]] = {}
    parent_descriptors: dict[PurePosixPath, int] = {}
    try:
        # Preflight every destination before changing the first file.
        for relative in [
            *(item[0] for item in files),
            *obsolete,
        ]:
            parent = destination_descriptor
            for component in relative.parts[:-1]:
                try:
                    os.mkdir(component, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                next_parent = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent,
                )
                if parent != destination_descriptor:
                    os.close(parent)
                parent = next_parent
            parent_descriptors[relative] = parent
            try:
                os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                originals[relative] = None
            else:
                old, mode, _identity = read_regular_at(parent, relative.name)
                originals[relative] = (old, mode)
        for relative, payload, mode in files:
            parent = parent_descriptors[relative]
            installed[relative] = hashlib.sha256(payload).hexdigest()
            write_atomic_at(parent, relative.name, payload, mode)
        for relative in obsolete:
            original = originals[relative]
            if original is None:
                continue
            parent = parent_descriptors[relative]
            os.unlink(relative.name, dir_fd=parent)
            os.fsync(parent)
            removed[relative] = original
        if reload:
            reload_services(home)
            restart_active_services(home, services)
    except Exception:
        for relative in reversed(list(installed)):
            parent = parent_descriptors[relative]
            try:
                current, _mode, _identity = read_regular_at(parent, relative.name)
            except FileNotFoundError:
                if originals[relative] is None:
                    continue
                write_atomic_at(
                    parent,
                    relative.name,
                    originals[relative][0],
                    originals[relative][1],
                )
                continue
            if hashlib.sha256(current).hexdigest() != installed[relative]:
                continue
            original = originals[relative]
            if original is None:
                os.unlink(relative.name, dir_fd=parent)
                os.fsync(parent)
            else:
                write_atomic_at(parent, relative.name, original[0], original[1])
        for relative, original in removed.items():
            parent = parent_descriptors[relative]
            try:
                os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                write_atomic_at(parent, relative.name, original[0], original[1])
        if reload:
            try:
                reload_services(home)
                restart_active_services(home, services)
            except ServiceTransactionError:
                pass
        raise
    finally:
        for descriptor in parent_descriptors.values():
            if descriptor != destination_descriptor:
                os.close(descriptor)
        os.close(lock_descriptor)
        os.close(destination_descriptor)


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(
        description="Internal reviewed-service transaction helper"
    )
    parser.add_argument("--installer-fd", type=int, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--no-reload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        source, destination, home, repository = reviewed_install_scope(args.installer_fd)
        prefix = _safe_unit_path(args.prefix, label="OpenClaw prefix")
        home = _safe_unit_path(home, label="account home")
        external_runtime = _collect_external_runtime(
            repository, home, harden=not args.dry_run
        )
        generation, artifacts, manifest = _collect_host_artifacts(
            repository, external_runtime
        )
        if args.dry_run:
            libexec = home / ".local" / "libexec" / "openclaw-bot" / "generations" / generation
        else:
            _prepare_service_state(prefix, home, repository)
            libexec = _install_host_generation(
                home=home,
                generation=generation,
                artifacts=artifacts,
                manifest=manifest,
            )
        _install_reviewed_files(
            source,
            destination,
            prefix=prefix,
            home=home,
            libexec=libexec,
            reload=not args.no_reload,
            dry_run=args.dry_run,
        )
    except (BlockingIOError, OSError, ServiceTransactionError, subprocess.TimeoutExpired) as exc:
        print(f"service transaction: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
