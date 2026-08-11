#!/usr/bin/env python3
"""Host-only OpenClaw file-delivery queue consumer.

The sandbox is an untrusted producer.  This helper alone reads the canonical
host policy and channel credentials.  It opens an approved workspace export
without following links, copies the opened bytes into a private host spool,
and sends the still-open spool descriptor.  A workspace path is never delivery
provenance by itself.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
from typing import Callable


POLICY_SCHEMA = "openclaw.file-delivery-policy/v1"
JOB_SCHEMA = "openclaw.send-queue-job/v1"
PROJECTION_SCHEMA = "openclaw.delivery-projection/v1"
SUPPORTED_CHANNELS = frozenset(
    {"telegram", "zulip", "googlechat", "whatsapp", "zalo"}
)
MAX_JSON_BYTES = 1_000_000
MAX_MEDIA_BYTES = 1024 * 1024 * 1024
MAX_TARGET_BYTES = 4096
MAX_CAPTION_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 16 * 1024
SAFE_PATH = "/usr/bin:/bin"
JOB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DeliveryError(RuntimeError):
    pass


def _duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryError("JSON authority contains duplicate keys")
        result[key] = value
    return result


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_directory(path: Path) -> int:
    absolute = _absolute(path)
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        root_information = os.fstat(descriptor)
        if (
            root_information.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(root_information.st_mode) & 0o022
        ):
            raise DeliveryError("host delivery directory is not owner-private")
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_private_directory(path: Path) -> int:
    absolute = _absolute(path)
    account_home = _absolute(Path(pwd.getpwuid(os.geteuid()).pw_dir))
    try:
        absolute.relative_to(account_home)
    except ValueError as exc:
        raise DeliveryError("host delivery directory is outside the account home") from exc
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    current = Path(absolute.anchor)
    try:
        root_information = os.fstat(descriptor)
        if (
            root_information.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(root_information.st_mode) & 0o022
        ):
            raise DeliveryError("host delivery directory is not owner-private")
        for component in absolute.parts[1:]:
            try:
                os.mkdir(component, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            information = os.fstat(descriptor)
            current /= component
            # Root-owned system ancestors are trusted only while they remain
            # non-writable by group/other.  From the account home downward the
            # complete spool chain is owner-only.
            within_home = current == account_home or account_home in current.parents
            if within_home:
                unsafe = (
                    information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) & 0o077
                )
            else:
                unsafe = (
                    information.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(information.st_mode) & 0o022
                )
            if unsafe:
                raise DeliveryError("host delivery directory is not owner-private")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_json(
    path: Path,
    *,
    private_parent: bool,
    private_file: bool,
) -> dict[str, object]:
    absolute = _absolute(path)
    parent = _open_directory(absolute.parent)
    descriptor: int | None = None
    try:
        parent_information = os.fstat(parent)
        if parent_information.st_uid != os.geteuid() or (
            private_parent and stat.S_IMODE(parent_information.st_mode) & 0o077
        ):
            raise DeliveryError("JSON authority parent is unsafe")
        named = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or (private_file and stat.S_IMODE(before.st_mode) & 0o077)
            or before.st_size > MAX_JSON_BYTES
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise DeliveryError("JSON authority is unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise DeliveryError("JSON authority was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) and (after.st_dev, after.st_ino) == (
            named_after.st_dev,
            named_after.st_ino,
        )
        if not stable:
            raise DeliveryError("JSON authority changed while reading")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
    try:
        value = json.loads(
            b"".join(chunks).decode("utf-8"), object_pairs_hook=_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("JSON authority is malformed") from exc
    if not isinstance(value, dict):
        raise DeliveryError("JSON authority must be an object")
    return value


def load_policy(path: Path, *, channel: str, target: str) -> None:
    policy = _read_json(path, private_parent=True, private_file=True)
    if frozenset(policy) != {"schema", "delivery_policy"}:
        raise DeliveryError("file-delivery policy has an invalid top-level shape")
    if policy.get("schema") != POLICY_SCHEMA:
        raise DeliveryError("file-delivery policy schema is unsupported")
    body = policy.get("delivery_policy")
    if not isinstance(body, dict) or frozenset(body) != {"allowed_targets"}:
        raise DeliveryError("file-delivery policy body is invalid")
    allowed = body.get("allowed_targets")
    if not isinstance(allowed, dict) or frozenset(allowed) != SUPPORTED_CHANNELS:
        raise DeliveryError("file-delivery policy channel set is invalid")
    if channel not in SUPPORTED_CHANNELS:
        raise DeliveryError("file-delivery channel is unsupported")
    for name, values in allowed.items():
        if not isinstance(values, list) or len(values) > 1000:
            raise DeliveryError("file-delivery target list is invalid")
        if len(values) != len(set(values)):
            raise DeliveryError("file-delivery target list contains duplicates")
        for value in values:
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > MAX_TARGET_BYTES
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise DeliveryError("file-delivery target value is invalid")
        if name == channel and target not in values:
            raise DeliveryError("file-delivery target is not authorized")


def _validate_channel_projection(path: Path, *, channel: str) -> None:
    path = _absolute(path)
    if path.name != channel or channel not in SUPPORTED_CHANNELS - {"telegram"}:
        raise DeliveryError("channel projection identity is invalid")
    status = _read_json(
        path / "STATUS.json",
        private_parent=True,
        private_file=True,
    )
    if (
        frozenset(status) != {"schema", "channel", "status"}
        or status.get("schema") != PROJECTION_SCHEMA
        or status.get("channel") != channel
        or status.get("status") != "CONFIGURED"
    ):
        raise DeliveryError("channel projection is not configured")
    config = _read_json(
        path / "openclaw.json",
        private_parent=True,
        private_file=True,
    )
    expected_top = {"channels", "plugins"}
    if channel in {"zulip", "zalo"}:
        expected_top.add("secrets")
    channels = config.get("channels")
    plugins = config.get("plugins")
    channel_config = channels.get(channel) if isinstance(channels, dict) else None
    if (
        frozenset(config) != expected_top
        or not isinstance(channels, dict)
        or frozenset(channels) != {channel}
        or not isinstance(channel_config, dict)
        or channel_config.get("enabled") is not True
        or not isinstance(plugins, dict)
        or plugins.get("allow") != [channel]
    ):
        raise DeliveryError("channel projection configuration is invalid")
    if channel in {"zulip", "zalo"}:
        secret_key = "ZULIP_API_KEY" if channel == "zulip" else "ZALO_BOT_TOKEN"
        secrets_document = _read_json(
            path / "secrets.json",
            private_parent=True,
            private_file=True,
        )
        secret_value = secrets_document.get(secret_key)
        if (
            frozenset(secrets_document) != {secret_key}
            or not isinstance(secret_value, str)
            or not secret_value
        ):
            raise DeliveryError("channel projection secret is invalid")
        reference_field = "apiKey" if channel == "zulip" else "botToken"
        if channel_config.get(reference_field) != {
            "id": f"/{secret_key}",
            "provider": "delivery",
            "source": "file",
        }:
            raise DeliveryError("channel projection SecretRef is invalid")
    if channel == "googlechat" and channel_config.get("serviceAccountFile") != os.fspath(
        path / "service-account.json"
    ):
        raise DeliveryError("Google Chat projection authority is invalid")


def _validated_job(path: Path) -> dict[str, str]:
    job = _read_json(path, private_parent=False, private_file=False)
    expected = {"schema", "id", "channel", "target", "media", "caption", "status"}
    if frozenset(job) != expected or job.get("schema") != JOB_SCHEMA:
        raise DeliveryError("queue job shape is invalid")
    values: dict[str, str] = {}
    for key in expected - {"schema"}:
        value = job.get(key)
        if not isinstance(value, str) or "\x00" in value:
            raise DeliveryError("queue job field is invalid")
        values[key] = value
    if values["status"] != "pending" or JOB_NAME_RE.fullmatch(values["id"]) is None:
        raise DeliveryError("queue job identity is invalid")
    stem = path.name
    for suffix in (".working", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem != values["id"]:
        raise DeliveryError("queue job filename does not bind its id")
    if (
        not values["target"]
        or len(values["target"].encode("utf-8")) > MAX_TARGET_BYTES
        or len(values["caption"].encode("utf-8")) > MAX_CAPTION_BYTES
        or any(character in values["target"] for character in "\r\n\t")
    ):
        raise DeliveryError("queue delivery fields are invalid")
    return values


def _authorized_roots(workspace: Path) -> tuple[Path, ...]:
    return (
        workspace / "data" / "research" / "zotero" / "staging",
        workspace / "data" / "calibre" / "staging",
        workspace / "data" / "vnu_eoffice" / "documents",
        workspace / "data" / "exports",
    )


def _map_media_path(workspace: Path, value: str) -> Path:
    if value == "/workspace":
        return workspace
    if value.startswith("/workspace/"):
        return workspace / value[len("/workspace/") :]
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _open_export(workspace: Path, source: Path) -> tuple[int, int, str]:
    source = _absolute(source)
    selected: tuple[Path, Path] | None = None
    for root in _authorized_roots(workspace):
        root = _absolute(root)
        try:
            relative = source.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            selected = (root, relative)
            break
    if selected is None:
        raise DeliveryError("media is outside approved export roots")
    root, relative = selected
    root_descriptor = _open_directory(root)
    parent = root_descriptor
    try:
        for component in relative.parts[:-1]:
            next_parent = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent,
            )
            if parent != root_descriptor:
                os.close(parent)
            parent = next_parent
        name = relative.parts[-1]
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > MAX_MEDIA_BYTES
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            os.close(descriptor)
            raise DeliveryError("media is unsafe or oversized")
        if parent != root_descriptor:
            os.close(root_descriptor)
        return descriptor, parent, name
    except Exception:
        if parent != root_descriptor:
            os.close(parent)
        os.close(root_descriptor)
        raise


@dataclass
class HostSnapshot:
    descriptor: int
    parent_descriptor: int
    directory_descriptor: int
    directory_name: str
    file_name: str
    display_name: str
    size: int

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        finally:
            try:
                os.unlink(self.file_name, dir_fd=self.directory_descriptor)
            except FileNotFoundError:
                pass
            os.close(self.directory_descriptor)
            try:
                os.rmdir(self.directory_name, dir_fd=self.parent_descriptor)
            except FileNotFoundError:
                pass
            os.close(self.parent_descriptor)


def snapshot_export(workspace: Path, source: Path) -> HostSnapshot:
    source_descriptor, source_parent, source_name = _open_export(workspace, source)
    account_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    spool_parent = _ensure_private_directory(
        account_home / ".local" / "state" / "openclaw-bot" / "delivery-spool"
    )
    directory_name = f"send-{secrets.token_hex(16)}"
    directory_descriptor: int | None = None
    output_descriptor: int | None = None
    file_name = "payload"
    try:
        os.mkdir(directory_name, 0o700, dir_fd=spool_parent)
        directory_descriptor = os.open(
            directory_name,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=spool_parent,
        )
        before = os.fstat(source_descriptor)
        output_descriptor = os.open(
            file_name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_MEDIA_BYTES:
                raise DeliveryError("media exceeds the delivery size limit")
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                if written <= 0:
                    raise DeliveryError("host spool write was truncated")
                view = view[written:]
        os.fchmod(output_descriptor, 0o600)
        os.fsync(output_descriptor)
        after = os.fstat(source_descriptor)
        named_after = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or (after.st_dev, after.st_ino)
            != (named_after.st_dev, named_after.st_ino)
            or copied != before.st_size
        ):
            raise DeliveryError("media changed while creating the host snapshot")
        os.lseek(output_descriptor, 0, os.SEEK_SET)
        os.fsync(directory_descriptor)
        os.fsync(spool_parent)
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", source_name)[:200] or "upload.bin"
        snapshot = HostSnapshot(
            descriptor=output_descriptor,
            parent_descriptor=spool_parent,
            directory_descriptor=directory_descriptor,
            directory_name=directory_name,
            file_name=file_name,
            display_name=safe_name,
            size=copied,
        )
        output_descriptor = None
        directory_descriptor = None
        spool_parent = -1
        return snapshot
    finally:
        os.close(source_descriptor)
        os.close(source_parent)
        if output_descriptor is not None:
            os.close(output_descriptor)
        if directory_descriptor is not None:
            try:
                os.unlink(file_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            os.close(directory_descriptor)
        if spool_parent >= 0:
            try:
                os.rmdir(directory_name, dir_fd=spool_parent)
            except FileNotFoundError:
                pass
            os.close(spool_parent)


def _telegram_token(credential_path: Path) -> str:
    """Read only the systemd-projected Telegram credential.

    The networked worker never receives the broad OpenClaw ``secrets.json`` or
    any other owner-state authority.  ``LoadCredential=`` snapshots this exact
    file into the service-private credential directory before execution.
    """

    absolute = _absolute(credential_path)
    parent = _open_directory(absolute.parent)
    descriptor: int | None = None
    try:
        parent_information = os.fstat(parent)
        if (
            parent_information.st_uid != os.geteuid()
            or stat.S_IMODE(parent_information.st_mode) & 0o077
        ):
            raise DeliveryError("Telegram credential parent is unsafe")
        named = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_size > MAX_TOKEN_BYTES
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise DeliveryError("Telegram credential is unsafe")
        payload = b""
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                raise DeliveryError("Telegram credential was truncated")
            payload += chunk
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        if (
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
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise DeliveryError("Telegram credential changed while reading")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
    try:
        token = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryError("Telegram credential is unavailable") from exc
    if (
        not token
        or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in token)
    ):
        raise DeliveryError("Telegram credential is unavailable")
    return token


def _curl_config_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _send_telegram(
    *, credential_path: Path, target: str, caption: str, snapshot: HostSnapshot
) -> None:
    token = _telegram_token(credential_path)
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    arguments = [
        "/usr/bin/curl",
        "-q",
        "--config",
        "-",
        "--silent",
        "--show-error",
        "--request",
        "POST",
        "--form-string",
        f"chat_id={target}",
        "--form",
        f"document=@/proc/self/fd/{snapshot.descriptor};filename={snapshot.display_name}",
        "--max-time",
        "120",
    ]
    if caption:
        arguments.extend(("--form-string", f"caption={caption}"))
    result = subprocess.run(
        arguments,
        input=f'url = "{_curl_config_value(url)}"\n',
        text=True,
        capture_output=True,
        check=False,
        timeout=130,
        pass_fds=(snapshot.descriptor,),
        env={"HOME": pwd.getpwuid(os.geteuid()).pw_dir, "PATH": SAFE_PATH},
    )
    if result.returncode != 0:
        raise DeliveryError("Telegram delivery failed")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryError("Telegram delivery returned an invalid response") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise DeliveryError("Telegram delivery was rejected")


def _send_openclaw(
    *,
    state_prefix: Path,
    channel: str,
    target: str,
    caption: str,
    snapshot: HostSnapshot,
) -> None:
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    generation_value = os.environ.get("OPENCLAW_LIBEXEC", "")
    if not generation_value or not Path(generation_value).is_absolute():
        raise DeliveryError("attested OpenClaw delivery runtime is unavailable")
    generation = _absolute(Path(generation_value))
    host_exec = generation / "host_exec.py"
    arguments = [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        os.fspath(host_exec),
        "--generation",
        os.fspath(generation),
        "--artifact",
        "openclaw_host_cli.py",
        "--",
        "message-send",
        "--channel",
        channel,
        "--target",
        target,
        "--media-fd",
        str(snapshot.descriptor),
    ]
    if caption:
        arguments.extend(("--caption", caption))
    result = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=130,
        pass_fds=(snapshot.descriptor,),
        env={
            "HOME": os.fspath(home),
            "PATH": SAFE_PATH,
            "OPENCLAW_STATE_DIR": os.fspath(state_prefix),
            "OPENCLAW_CONFIG_PATH": os.fspath(state_prefix / "openclaw.json"),
            "OPENCLAW_LIBEXEC": os.fspath(generation),
        },
    )
    if result.returncode != 0:
        raise DeliveryError("OpenClaw channel delivery failed")


def process_job(
    *,
    workspace: Path,
    policy_path: Path,
    expected_channel: str,
    job_path: Path,
    telegram_credential: Path | None = None,
    channel_state: Path | None = None,
    sender: Callable[..., None] | None = None,
) -> dict[str, object]:
    workspace = _absolute(workspace)
    policy_path = _absolute(policy_path)
    if expected_channel not in SUPPORTED_CHANNELS:
        raise DeliveryError("delivery worker channel is unsupported")
    if expected_channel == "telegram":
        if telegram_credential is None or channel_state is not None:
            raise DeliveryError("Telegram worker authority is incomplete")
        telegram_credential = _absolute(telegram_credential)
    else:
        if channel_state is None or telegram_credential is not None:
            raise DeliveryError("channel worker authority is incomplete")
        channel_state = _absolute(channel_state)
    job = _validated_job(job_path)
    if job["channel"] != expected_channel:
        raise DeliveryError("queue job channel does not match this worker")
    if channel_state is not None:
        _validate_channel_projection(channel_state, channel=expected_channel)
    load_policy(
        policy_path,
        channel=job["channel"],
        target=job["target"],
    )
    snapshot = snapshot_export(workspace, _map_media_path(workspace, job["media"]))
    try:
        if sender is not None:
            sender(
                channel_state=channel_state,
                telegram_credential=telegram_credential,
                channel=job["channel"],
                target=job["target"],
                caption=job["caption"],
                snapshot=snapshot,
            )
        elif job["channel"] == "telegram":
            _send_telegram(
                credential_path=telegram_credential,
                target=job["target"],
                caption=job["caption"],
                snapshot=snapshot,
            )
        else:
            _send_openclaw(
                state_prefix=channel_state,
                channel=job["channel"],
                target=job["target"],
                caption=job["caption"],
                snapshot=snapshot,
            )
        return {
            "status": "ok",
            "channel": job["channel"],
            "target": job["target"],
            "file": snapshot.display_name,
            "size": snapshot.size,
        }
    finally:
        snapshot.close()


def _write_result(path: Path, payload: dict[str, object]) -> None:
    parent = _open_directory(path.parent)
    temporary = f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DeliveryError("delivery result write was truncated")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
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
        os.close(parent)


def main() -> int:
    os.umask(0o077)
    os.environ["PATH"] = SAFE_PATH
    for name in (
        "AAS_FILE_DELIVERY_SECRETS_FILE",
        "OPENCLAW_BIN",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
    ):
        os.environ.pop(name, None)
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--expected-channel", choices=sorted(SUPPORTED_CHANNELS), required=True
    )
    parser.add_argument("--telegram-credential", type=Path)
    parser.add_argument("--channel-state", type=Path)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = process_job(
            workspace=args.workspace,
            policy_path=args.policy,
            expected_channel=args.expected_channel,
            job_path=args.job,
            telegram_credential=args.telegram_credential,
            channel_state=args.channel_state,
        )
        result = 0
    except (DeliveryError, OSError, subprocess.SubprocessError) as exc:
        payload = {"status": "error", "message": str(exc)}
        result = 1
    try:
        _write_result(args.result, payload)
    except (DeliveryError, OSError) as exc:
        print(f"file delivery result: {exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
