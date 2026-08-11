#!/usr/bin/env python3
"""One-time, exact-intent approval gate for host SMTP/PGP queue jobs."""

from __future__ import annotations

import argparse
from email.utils import getaddresses
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import secrets
import stat
import subprocess
import sys
from typing import Any

import queue_boundary


POLICY_SCHEMA = "openclaw.email-policy/v1"
INTENT_SCHEMA = "openclaw.email-intent/v1"
APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PGP_FINGERPRINT_RE = re.compile(r"^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$")
MAX_JOB_BYTES = 1024 * 1024
MAX_ARG_BYTES = 262_144
MAX_ARGS = 256
MAX_APPROVALS = 1000
PATH_FLAGS = frozenset(
    {
        "--body-file",
        "--html-file",
        "--attach",
        "--signature-file",
        "--signature-html-file",
    }
)
VALUE_FLAGS = frozenset(
    {
        "--to",
        "--cc",
        "--bcc",
        "--subject",
        "--body",
        "--html",
        "--reply-to",
        "--signature",
        "--pgp-key",
    }
)
BOOLEAN_FLAGS = frozenset(
    {
        "--no-signature",
        "--no-reply-to-self",
        "--no-bcc-self",
        "--sign",
        "--no-sign",
        "--dry-run",
    }
)
FORBIDDEN_FLAGS = frozenset(
    {
        "--account",
        "--host",
        "--port",
        "--user",
        "--password",
        "--security",
        "--timeout",
        "--from",
        "--from-name",
        "--allow-insecure-auth",
        "--gnupg-home",
        "--save-recipients",
    }
)
APPROVED_INPUT_ROOTS = (
    PurePosixPath("data", "email-queue"),
    PurePosixPath("data", "exports"),
    PurePosixPath("data", "research"),
    PurePosixPath("data", "calibre", "staging"),
)


class EmailDeliveryError(RuntimeError):
    def __init__(self, message: str, *, intent: dict[str, object] | None = None):
        super().__init__(message)
        self.intent = intent


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EmailDeliveryError("email authority contains a duplicate key")
        value[key] = item
    return value


def _read_regular_json(
    path: Path, *, max_bytes: int, owner_private: bool = False
) -> tuple[dict[str, Any], os.stat_result]:
    parent = queue_boundary._open_directory(path.parent)
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
            or before.st_uid != os.geteuid()
            or (owner_private and stat.S_IMODE(before.st_mode) & 0o077)
            or before.st_size > max_bytes
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise EmailDeliveryError("email authority is unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise EmailDeliveryError("email authority was truncated")
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
            raise EmailDeliveryError("email authority changed while reading")
        try:
            payload = json.loads(
                b"".join(chunks), object_pairs_hook=_strict_pairs
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmailDeliveryError("email authority is malformed") from exc
        if not isinstance(payload, dict):
            raise EmailDeliveryError("email authority must contain an object")
        return payload, before
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _read_job(path: Path) -> dict[str, Any]:
    payload, _information = _read_regular_json(path, max_bytes=MAX_JOB_BYTES)
    if set(payload) != {"id", "type", "argv", "status", "approval_id"}:
        raise EmailDeliveryError("email queue job schema is invalid")
    if payload.get("type") != "email" or payload.get("status") != "pending":
        raise EmailDeliveryError("email queue job state is invalid")
    job_id = payload.get("id")
    if not isinstance(job_id, str) or APPROVAL_ID_RE.fullmatch(job_id) is None:
        raise EmailDeliveryError("email queue job id is invalid")
    if path.name not in {f"{job_id}.json", f"{job_id}.working"}:
        raise EmailDeliveryError("email queue job id does not match its claimed file")
    approval_id = payload.get("approval_id")
    if not isinstance(approval_id, str) or APPROVAL_ID_RE.fullmatch(approval_id) is None:
        raise EmailDeliveryError("email queue job lacks an exact approval id")
    argv = payload.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > MAX_ARGS
        or not all(
            isinstance(value, str)
            and "\x00" not in value
            and len(value.encode("utf-8")) <= MAX_ARG_BYTES
            for value in argv
        )
    ):
        raise EmailDeliveryError("email queue argv is invalid")
    if argv[0] != "send":
        raise EmailDeliveryError("host email queue accepts only send")
    return payload


def _snapshot_private_authority(
    source: Path, spool: Path, label: str, *, max_bytes: int
) -> Path:
    if APPROVAL_ID_RE.fullmatch(label) is None:
        raise EmailDeliveryError("email authority snapshot label is invalid")
    spool_path, spool_descriptor = queue_boundary._private_spool(spool)
    source_parent = queue_boundary._open_directory(source.parent)
    source_descriptor: int | None = None
    output_descriptor: int | None = None
    output_name = f"smtp-{label}-{secrets.token_hex(16)}.json"
    try:
        source_descriptor = queue_boundary._open_relative_regular(
            source_parent, PurePosixPath(source.name)
        )
        before = os.fstat(source_descriptor)
        if stat.S_IMODE(before.st_mode) & 0o077 or before.st_size > max_bytes:
            raise EmailDeliveryError("SMTP authority is not owner-private and bounded")
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
                raise EmailDeliveryError("SMTP authority was truncated")
            view = memoryview(chunk)
            while view:
                written = os.write(output_descriptor, view)
                if written <= 0:
                    raise EmailDeliveryError("SMTP authority snapshot was truncated")
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
            raise EmailDeliveryError("SMTP authority changed during snapshot")
        os.fchmod(output_descriptor, 0o600)
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        os.fsync(spool_descriptor)
        return spool_path / output_name
    except Exception:
        try:
            os.unlink(output_name, dir_fd=spool_descriptor)
        except FileNotFoundError:
            pass
        raise
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(source_parent)
        os.close(spool_descriptor)


def _authority_identity(path: Path) -> dict[str, object]:
    payload, _information = _read_regular_json(
        path, max_bytes=MAX_JOB_BYTES, owner_private=True
    )
    accounts = payload.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    selected = payload.get("default_account")
    if isinstance(selected, str) and isinstance(accounts.get(selected), dict):
        raw = accounts[selected]
        account: str | None = selected
    else:
        smtp = payload.get("smtp")
        raw = smtp if isinstance(smtp, dict) else payload
        account = None
    normalized = {
        (str(key).lower()[5:] if str(key).lower().startswith("smtp_") else str(key).lower()): value
        for key, value in raw.items()
    }
    sender = normalized.get("from", normalized.get("sender"))
    if not isinstance(sender, str) or not sender.strip():
        raise EmailDeliveryError("SMTP authority has no fixed sender identity")
    parsed = getaddresses([sender])
    sender_address = parsed[0][1].strip().lower() if len(parsed) == 1 else ""
    if not sender_address or "\r" in sender or "\n" in sender:
        raise EmailDeliveryError("SMTP authority sender identity is invalid")
    return {"account": account, "sender": sender_address}


def _file_digest(path: Path) -> tuple[str, int]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
            raise EmailDeliveryError("host email snapshot is unsafe")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
        return digest.hexdigest(), information.st_size
    finally:
        os.close(descriptor)


def _split_flag(argv: list[str], index: int) -> tuple[str, str | None, int]:
    argument = argv[index]
    flag, separator, inline = argument.partition("=")
    if flag in BOOLEAN_FLAGS:
        if separator:
            raise EmailDeliveryError("boolean email option cannot take a value")
        return flag, None, index + 1
    if flag in FORBIDDEN_FLAGS:
        raise EmailDeliveryError("email job overrides host authority")
    if flag not in VALUE_FLAGS and flag not in PATH_FLAGS:
        raise EmailDeliveryError("email job contains an unsupported option")
    if separator:
        if not inline:
            raise EmailDeliveryError("email option value is empty")
        return flag, inline, index + 1
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        raise EmailDeliveryError("email option requires a value")
    return flag, argv[index + 1], index + 2


def prepare_intent(
    job: dict[str, Any],
    workspace: Path,
    spool: Path,
    authority_identity: dict[str, object],
) -> tuple[list[str], dict[str, object], list[Path]]:
    argv = job["argv"]
    assert isinstance(argv, list)
    executable = ["send"]
    canonical: list[object] = ["send"]
    recipients_raw: list[str] = []
    signing = False
    no_sign = False
    signing_key: str | None = None
    snapshots: list[Path] = []
    index = 1
    try:
        while index < len(argv):
            flag, value, next_index = _split_flag(argv, index)
            if value is None:
                executable.append(flag)
                canonical.append(flag)
                signing = signing or flag == "--sign"
                no_sign = no_sign or flag == "--no-sign"
            elif flag in PATH_FLAGS:
                limit = 1024 * 1024 if flag != "--attach" else 32 * 1024 * 1024
                snapshot_path = queue_boundary.snapshot(
                    workspace,
                    value,
                    spool,
                    f"email-{job['id']}",
                    APPROVED_INPUT_ROOTS,
                    limit,
                )
                snapshots.append(snapshot_path)
                digest, size = _file_digest(snapshot_path)
                executable.extend((flag, os.fspath(snapshot_path)))
                canonical.append(
                    {
                        "flag": flag,
                        "name": Path(value).name,
                        "sha256": digest,
                        "size": size,
                    }
                )
            else:
                executable.extend((flag, value))
                canonical.extend((flag, value))
                if flag in {"--to", "--cc", "--bcc"}:
                    recipients_raw.append(value)
                if flag == "--pgp-key":
                    signing_key = value
            index = next_index
        if signing == no_sign:
            raise EmailDeliveryError(
                "email job must select exactly one of --sign or --no-sign"
            )
        if signing_key is not None and not signing:
            raise EmailDeliveryError("email signing key requires --sign")
        if signing and (
            signing_key is None or PGP_FINGERPRINT_RE.fullmatch(signing_key) is None
        ):
            raise EmailDeliveryError(
                "signed email jobs require an explicit full OpenPGP fingerprint"
            )
        recipients = sorted(
            {
                address.strip().lower()
                for _name, address in getaddresses(recipients_raw)
                if address.strip()
            }
        )
        if not recipients:
            raise EmailDeliveryError("email job has no recipients")
        intent_payload = {
            "schema": INTENT_SCHEMA,
            "argv": canonical,
            "recipients": recipients,
            "signing": signing,
            "signingKey": signing_key,
            "account": authority_identity["account"],
            "sender": authority_identity["sender"],
        }
        encoded = json.dumps(
            intent_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        intent = {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "recipients": recipients,
            "signing": signing,
            "signingKey": signing_key,
            "account": authority_identity["account"],
            "sender": authority_identity["sender"],
        }
        return executable, intent, snapshots
    except Exception:
        for path in snapshots:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def _policy_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if set(payload) != {"schema", "email_policy"} or payload.get("schema") != POLICY_SCHEMA:
        raise EmailDeliveryError("email policy schema is invalid")
    policy = payload.get("email_policy")
    if not isinstance(policy, dict) or set(policy) != {"approved_messages"}:
        raise EmailDeliveryError("email policy body is invalid")
    entries = policy.get("approved_messages")
    if not isinstance(entries, list) or len(entries) > MAX_APPROVALS:
        raise EmailDeliveryError("email policy approvals are invalid")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "intent_sha256",
            "recipients",
            "signing",
            "signing_key",
            "account",
            "sender",
        }:
            raise EmailDeliveryError("email policy approval is invalid")
        approval_id = entry.get("id")
        recipients = entry.get("recipients")
        if (
            not isinstance(approval_id, str)
            or APPROVAL_ID_RE.fullmatch(approval_id) is None
            or approval_id in seen
            or not isinstance(entry.get("intent_sha256"), str)
            or SHA256_RE.fullmatch(entry["intent_sha256"]) is None
            or not isinstance(recipients, list)
            or not all(isinstance(value, str) and value for value in recipients)
            or recipients != sorted(set(recipients))
            or not isinstance(entry.get("signing"), bool)
            or not (
                entry.get("signing_key") is None
                or isinstance(entry.get("signing_key"), str)
            )
            or not (entry.get("account") is None or isinstance(entry.get("account"), str))
            or not isinstance(entry.get("sender"), str)
            or not entry.get("sender")
            or (
                entry.get("signing")
                and (
                    not isinstance(entry.get("signing_key"), str)
                    or PGP_FINGERPRINT_RE.fullmatch(entry["signing_key"]) is None
                )
            )
            or (not entry.get("signing") and entry.get("signing_key") is not None)
        ):
            raise EmailDeliveryError("email policy approval fields are invalid")
        seen.add(approval_id)
    return entries


def consume_approval(
    policy_path: Path,
    approval_id: str,
    intent: dict[str, object],
) -> None:
    parent = queue_boundary._open_directory(policy_path.parent)
    parent_information = os.fstat(parent)
    if (
        parent_information.st_uid != os.geteuid()
        or stat.S_IMODE(parent_information.st_mode) & 0o077
    ):
        os.close(parent)
        raise EmailDeliveryError("email approval directory is not owner-private")
    lock_descriptor: int | None = None
    descriptor: int | None = None
    temporary = f".{policy_path.name}.claim-{secrets.token_hex(16)}"
    try:
        lock_descriptor = os.open(
            ".policy.lock",
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent,
        )
        lock_information = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_information.st_mode)
            or lock_information.st_nlink != 1
            or lock_information.st_uid != os.geteuid()
            or stat.S_IMODE(lock_information.st_mode) & 0o077
        ):
            raise EmailDeliveryError("email approval lock is unsafe")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        try:
            payload, identity = _read_regular_json(
                policy_path, max_bytes=MAX_JOB_BYTES, owner_private=True
            )
        except FileNotFoundError as exc:
            raise EmailDeliveryError(
                "email intent has no one-time owner approval", intent=intent
            ) from exc
        entries = _policy_entries(payload)
        matching = [entry for entry in entries if entry["id"] == approval_id]
        if len(matching) != 1:
            raise EmailDeliveryError(
                "email intent has no one-time owner approval", intent=intent
            )
        approval = matching[0]
        if (
            approval["intent_sha256"] != intent["sha256"]
            or approval["recipients"] != intent["recipients"]
            or approval["signing"] is not intent["signing"]
            or approval["signing_key"] != intent["signingKey"]
            or approval["account"] != intent["account"]
            or approval["sender"] != intent["sender"]
        ):
            raise EmailDeliveryError(
                "email intent does not match owner approval", intent=intent
            )
        current = os.stat(policy_path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns) != (
            identity.st_dev,
            identity.st_ino,
            identity.st_mtime_ns,
            identity.st_ctime_ns,
        ):
            raise EmailDeliveryError("email policy changed before approval claim")
        payload["email_policy"]["approved_messages"] = [
            entry for entry in entries if entry["id"] != approval_id
        ]
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EmailDeliveryError("email approval claim was truncated")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(
            temporary,
            policy_path.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
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
        if lock_descriptor is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        os.close(parent)


def _write_result(path: Path, payload: dict[str, object]) -> None:
    parent = queue_boundary._open_directory(path.parent)
    temporary = f".{path.name}.result-{secrets.token_hex(16)}"
    descriptor: int | None = None
    try:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EmailDeliveryError("email result write was truncated")
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


def process(
    workspace: Path,
    state_prefix: Path,
    job_path: Path,
    result_path: Path,
    spool: Path,
) -> dict[str, object]:
    snapshots: list[Path] = []
    try:
        job = _read_job(job_path)
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        smtp_snapshot = _snapshot_private_authority(
            home / ".config" / "send-email" / "secrets.json",
            spool,
            str(job["id"]),
            max_bytes=MAX_JOB_BYTES,
        )
        snapshots.append(smtp_snapshot)
        authority_identity = _authority_identity(smtp_snapshot)
        executable, intent, input_snapshots = prepare_intent(
            job, workspace, spool, authority_identity
        )
        snapshots.extend(input_snapshots)
        policy = state_prefix / "email-approvals" / "policy.json"
        consume_approval(policy, str(job["approval_id"]), intent)
        send_email = Path(__file__).resolve().parent / "send-email" / "send_email.py"
        child_env = {
            "HOME": os.fspath(home),
            "GNUPGHOME": os.fspath(home / ".gnupg"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "SEND_EMAIL_SECRETS_FILE": os.fspath(smtp_snapshot),
            "SEND_EMAIL_EXACT_QUEUE": "1",
        }
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", os.fspath(send_email), *executable],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=110,
            env=child_env,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "command": "send",
                "error_code": "host_handler_failed",
                "message": "host email handler returned invalid output",
            }
        if not isinstance(payload, dict):
            raise EmailDeliveryError("host email handler returned invalid output")
        payload["approval_id"] = job["approval_id"]
        payload["approval_consumed"] = True
        return payload
    except EmailDeliveryError as exc:
        payload: dict[str, object] = {
            "ok": False,
            "command": "send",
            "error_code": "approval_required",
            "message": str(exc),
        }
        if exc.intent is not None:
            payload["intent"] = exc.intent
        return payload
    except (OSError, queue_boundary.QueueBoundaryError, subprocess.SubprocessError):
        return {
            "ok": False,
            "command": "send",
            "error_code": "host_boundary_failed",
            "message": "host email boundary rejected the job",
        }
    finally:
        for path in snapshots:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state-prefix", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--spool", type=Path, required=True)
    args = parser.parse_args()
    payload = process(
        args.workspace,
        args.state_prefix,
        args.job,
        args.result,
        args.spool,
    )
    _write_result(args.result, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
