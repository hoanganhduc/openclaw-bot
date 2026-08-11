#!/usr/bin/env python3
"""Offline closure gate for DB-first OpenClaw agent authentication.

This helper intentionally never starts OpenClaw. In particular it does not
run the OpenClaw repair CLI, load configuration/plugins, resolve SecretRefs,
contact a provider, or repair/install packages. Restore-time authority is limited to
local, read-only SQLite validation; legacy JSON/model authorities are moved to
an inert owner-private quarantine.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from typing import Any
import unicodedata


REPORT_SCHEMA = "openclaw.agent-auth-closure/v2"
MATERIALIZATION_SCHEMA = "openclaw.agent-auth-materialization/v2"
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
LEGACY_AUTHORITY_RE = re.compile(
    r"^\.?(?:auth-profiles|auth-state|auth|models)\.json(?:[._~-].*)?$",
    re.IGNORECASE,
)
REDACTION_SENTINELS = (
    "{{ redacted }}",
    "{{ secret_value }}",
    "{{ private_id }}",
    "{{ email }}",
    "{{ model_id }}",
    "<redacted>",
    "__redacted__",
)
MAX_AUTHORITY_BYTES = 16 * 1024 * 1024
MAX_STRUCTURED_DEPTH = 12
MAX_STRUCTURED_NODES = 100_000
SECRET_REF_SOURCES = frozenset({"env", "file", "exec"})
MATERIALIZATION_SCHEMA_BY_VERSION = {"2026.7.1-2": 1}
EXACT_LEGACY_AUTHORITY_NAMES = (
    "auth-profiles.json",
    "auth-state.json",
    "auth.json",
    "models.json",
)
NON_SECRET_MODEL_MARKERS = frozenset(
    {
        "custom-local",
        "codex-app-server",
        "gcp-vertex-credentials",
        "ollama-local",
        "secretref-managed",
        "minimax-oauth",
        "aws_bearer_token_bedrock",
        "aws_access_key_id",
        "aws_profile",
    }
)
ENV_MARKER_RE = re.compile(
    r"^[A-Z][A-Z0-9_]{0,127}(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS)$"
)
SENSITIVE_HEADER_RE = re.compile(
    r"^(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|"
    r"api-key|x-auth-token|x-access-token|token|secret|credential)$",
    re.IGNORECASE,
)
REQUIRED_TABLE_COLUMNS = {
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


class AuthClosureError(RuntimeError):
    pass


def _strict_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuthClosureError("legacy authority JSON contains a duplicate key")
        result[key] = value
    return result


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_directory_nofollow(path: Path) -> int:
    absolute = _absolute(path)
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


def _open_or_create_private_directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent,
    )
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) & 0o077:
        os.close(descriptor)
        raise AuthClosureError("legacy authority quarantine directory is unsafe")
    return descriptor


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise AuthClosureError("legacy authority metadata write was truncated")
        view = view[written:]


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


NORMALIZED_SENTINELS = tuple(_normalized_text(value) for value in REDACTION_SENTINELS)


def _string_has_sentinel(value: str, *, depth: int = 0) -> bool:
    normalized = _normalized_text(value)
    if any(sentinel in normalized for sentinel in NORMALIZED_SENTINELS):
        return True
    if depth >= MAX_STRUCTURED_DEPTH:
        return False
    # JSON parsing decodes escaped Unicode.  Recurse because a database cell
    # may itself contain a JSON-encoded string containing another JSON value.
    try:
        decoded: object = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if decoded is not None and decoded != value and _payload_has_redaction_sentinel(
        decoded, depth=depth + 1
    ):
        return True
    if "\\u" in value.casefold():
        try:
            unescaped = codecs.decode(value, "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            if unescaped != value and _string_has_sentinel(unescaped, depth=depth + 1):
                return True
    return False


def _payload_has_redaction_sentinel(value: object, *, depth: int = 0) -> bool:
    if depth > MAX_STRUCTURED_DEPTH:
        return False
    if isinstance(value, bytes):
        return _string_has_sentinel(value.decode("utf-8", errors="ignore"), depth=depth)
    if isinstance(value, str):
        return _string_has_sentinel(value, depth=depth)
    if isinstance(value, dict):
        return any(
            _payload_has_redaction_sentinel(key, depth=depth + 1)
            or _payload_has_redaction_sentinel(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(
            _payload_has_redaction_sentinel(item, depth=depth + 1) for item in value
        )
    return False


def _inspect_authority_json(value: object) -> tuple[bool, set[str]]:
    """Return whether nested JSON contains an executable SecretRef-like object.

    OpenClaw accepts ``source: exec`` SecretRefs in authentication profiles and
    materializes them later.  Restore closure must reject that executable
    authority without asking OpenClaw to parse or resolve it.
    """

    executable = False
    sources: set[str] = set()
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal executable, nodes
        nodes += 1
        if nodes > MAX_STRUCTURED_NODES:
            raise ValueError("authority JSON exceeds the structural node limit")
        if depth > MAX_STRUCTURED_DEPTH:
            raise ValueError("authority JSON exceeds the structural depth limit")
        if isinstance(item, dict):
            source = item.get("source")
            if isinstance(source, str):
                normalized = unicodedata.normalize("NFKC", source).casefold()
                if normalized in SECRET_REF_SOURCES:
                    sources.add(normalized)
                if normalized == "exec":
                    executable = True
            for key, nested in item.items():
                visit(key, depth + 1)
                visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)
        elif isinstance(item, str) and item[:1] in {"{", "[", '"'}:
            try:
                nested = json.loads(item)
            except json.JSONDecodeError:
                return
            if nested != item:
                visit(nested, depth + 1)

    visit(value, 0)
    return executable, sources


def _harden_owned_directory(path: Path) -> None:
    descriptor = _open_directory_nofollow(path)
    try:
        information = os.fstat(descriptor)
        if information.st_uid != os.geteuid():
            raise AuthClosureError("legacy agent directory has the wrong owner")
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def normalize_legacy_agent_layout(prefix: Path) -> dict[str, object]:
    """Converge the legacy agent tree without following or deleting its targets.

    Old installations sometimes left agent directories group-writable and an
    unconfigured ``sandbox -> main`` alias in the agent root.  Owner archives
    deliberately reject both shapes.  Every alias is preflighted and rechecked
    before any alias is unlinked; only a one-component relative link to another
    real agent directory in the same private root is accepted.  The link target
    is never renamed or removed.
    """

    prefix = _absolute(prefix)
    if not os.path.lexists(prefix):
        raise AuthClosureError("OpenClaw state prefix is missing")
    if prefix.is_symlink() or not prefix.is_dir():
        raise AuthClosureError("OpenClaw state prefix is unsafe")
    _harden_owned_directory(prefix)
    agents = prefix / "agents"
    if not os.path.lexists(agents):
        return {"status": "CLEAN", "aliasesRemoved": 0, "directoriesHardened": 1}
    if agents.is_symlink() or not agents.is_dir():
        raise AuthClosureError("OpenClaw agent root is unsafe")
    _harden_owned_directory(agents)
    aliases: list[tuple[str, str, tuple[int, int, int, int]]] = []
    real_agents: list[Path] = []
    with os.scandir(agents) as entries:
        for entry in entries:
            if AGENT_ID_RE.fullmatch(entry.name) is None:
                raise AuthClosureError("OpenClaw agent root contains an invalid agent id")
            information = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(information.st_mode):
                target = os.readlink(entry.path)
                if (
                    not target
                    or os.path.isabs(target)
                    or "/" in target
                    or target in {".", "..", entry.name}
                    or AGENT_ID_RE.fullmatch(target) is None
                ):
                    raise AuthClosureError("legacy agent alias leaves the agent root")
                aliases.append(
                    (
                        entry.name,
                        target,
                        (
                            information.st_dev,
                            information.st_ino,
                            information.st_size,
                            information.st_mtime_ns,
                        ),
                    )
                )
            elif stat.S_ISDIR(information.st_mode):
                if information.st_uid != os.geteuid():
                    raise AuthClosureError("legacy agent directory has the wrong owner")
                real_agents.append(Path(entry.path))
            else:
                raise AuthClosureError("OpenClaw agent root contains an unsafe entry")

    real_names = {path.name for path in real_agents}
    for _name, target, _identity in aliases:
        if target not in real_names:
            raise AuthClosureError("legacy agent alias target is missing or is another link")
        target_information = os.stat(agents / target, follow_symlinks=False)
        if (
            not stat.S_ISDIR(target_information.st_mode)
            or target_information.st_uid != os.geteuid()
        ):
            raise AuthClosureError("legacy agent alias target is unsafe")

    # Permission convergence is non-destructive and happens only after the full
    # alias set has passed preflight.
    hardened = 2
    for agent in real_agents:
        _harden_owned_directory(agent)
        hardened += 1
        state = agent / "agent"
        if os.path.lexists(state):
            if state.is_symlink() or not state.is_dir():
                raise AuthClosureError("OpenClaw agent state directory is unsafe")
            _harden_owned_directory(state)
            hardened += 1

    root_descriptor = _open_directory_nofollow(agents)
    try:
        # Recheck every link before the first unlink.  A conflicting change
        # therefore leaves the entire alias set intact.
        for name, target, expected_identity in aliases:
            current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            current_identity = (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            )
            if (
                not stat.S_ISLNK(current.st_mode)
                or current_identity != expected_identity
                or os.readlink(name, dir_fd=root_descriptor) != target
            ):
                raise AuthClosureError("legacy agent alias changed during normalization")
        displaced: list[tuple[str, str]] = []
        try:
            for index, (name, _target, _expected_identity) in enumerate(aliases):
                temporary = (
                    f".legacy-agent-alias-{os.getpid()}-{secrets.token_hex(12)}-{index}"
                )
                os.rename(
                    name,
                    temporary,
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                )
                displaced.append((name, temporary))
            os.fsync(root_descriptor)
        except Exception:
            for name, temporary in reversed(displaced):
                try:
                    os.rename(
                        temporary,
                        name,
                        src_dir_fd=root_descriptor,
                        dst_dir_fd=root_descriptor,
                    )
                except OSError as rollback_error:
                    raise AuthClosureError(
                        "legacy agent alias normalization rollback failed"
                    ) from rollback_error
            os.fsync(root_descriptor)
            raise
        for _name, temporary in displaced:
            os.unlink(temporary, dir_fd=root_descriptor)
        if displaced:
            os.fsync(root_descriptor)
    finally:
        os.close(root_descriptor)
    return {
        "status": "NORMALIZED" if aliases or hardened > 2 else "CLEAN",
        "aliasesRemoved": len(aliases),
        "directoriesHardened": hardened,
    }


def _read_legacy_json(path: Path) -> tuple[dict[str, object], str]:
    parent = _open_directory_nofollow(path.parent)
    descriptor: int | None = None
    try:
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > MAX_AUTHORITY_BYTES
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise AuthClosureError("legacy authority is unsafe or oversized")
        # A private state root makes this safe to repair in place, and closes a
        # legacy 0664 window before credential bytes are read.
        os.fchmod(descriptor, 0o600)
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUTHORITY_BYTES:
                raise AuthClosureError("legacy authority is oversized")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise AuthClosureError("legacy authority changed while it was read")
        try:
            parsed = json.loads(
                b"".join(chunks).decode("utf-8"),
                object_pairs_hook=_strict_object_pairs,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthClosureError("legacy authority is not strict UTF-8 JSON") from exc
        if not isinstance(parsed, dict):
            raise AuthClosureError("legacy authority JSON root is not an object")
        return parsed, digest.hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _provider_id(value: object) -> str:
    if not isinstance(value, str):
        raise AuthClosureError("legacy credential provider is missing")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if PROVIDER_ID_RE.fullmatch(normalized) is None:
        raise AuthClosureError("legacy credential provider is invalid")
    return normalized


def _profile_id(value: object) -> str:
    if not isinstance(value, str):
        raise AuthClosureError("legacy credential profile id is invalid")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 256
        or any(ord(character) < 0x20 for character in normalized)
    ):
        raise AuthClosureError("legacy credential profile id is invalid")
    return normalized


def _secret_ref(value: object) -> dict[str, str] | None:
    if isinstance(value, str):
        stripped = value.strip()
        match = re.fullmatch(
            r"(?:\$([A-Z][A-Z0-9_]{0,127})|\$\{([A-Z][A-Z0-9_]{0,127})\})",
            stripped,
        )
        if match:
            return {
                "source": "env",
                "provider": "default",
                "id": match.group(1) or match.group(2),
            }
        if stripped.startswith("secretref-env:"):
            identifier = stripped.removeprefix("secretref-env:")
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", identifier):
                return {"source": "env", "provider": "default", "id": identifier}
        return None
    if not isinstance(value, dict):
        return None
    if set(value) not in ({"source", "provider", "id"}, {"source", "id"}):
        return None
    source = value.get("source")
    identifier = value.get("id")
    provider = value.get("provider", "default")
    if source == "exec":
        raise AuthClosureError("legacy credential contains an executable SecretRef")
    if source not in {"env", "file"}:
        return None
    if (
        not isinstance(identifier, str)
        or not identifier.strip()
        or not isinstance(provider, str)
        or not provider.strip()
    ):
        return None
    if source == "env" and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", identifier) is None:
        return None
    return {
        "source": source,
        "provider": provider.strip(),
        "id": identifier.strip(),
    }


def _optional_string(raw: dict[str, object], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AuthClosureError("legacy credential contains an invalid string field")
    return value.strip()


def _normalize_credential(
    raw: object, *, fallback_provider: str | None = None
) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise AuthClosureError("legacy credential entry is not an object")
    if _payload_has_redaction_sentinel(raw):
        raise AuthClosureError("legacy credential contains a redaction sentinel")
    executable, _sources = _inspect_authority_json(raw)
    if executable:
        raise AuthClosureError("legacy credential contains an executable SecretRef")
    kind = raw.get("type", raw.get("mode"))
    if kind == "apiKey":
        kind = "api_key"
    provider = _provider_id(raw.get("provider", fallback_provider))
    common: dict[str, object] = {"type": kind, "provider": provider}
    for name in ("email", "displayName"):
        value = _optional_string(raw, name)
        if value is not None:
            common[name] = value
    copy_to_agents = raw.get("copyToAgents")
    if copy_to_agents is not None:
        if not isinstance(copy_to_agents, bool):
            raise AuthClosureError("legacy credential copy policy is invalid")
        common["copyToAgents"] = copy_to_agents

    if kind == "api_key":
        explicit_ref = _secret_ref(raw.get("keyRef"))
        key_value = raw.get("key", raw.get("apiKey"))
        inline_ref = _secret_ref(key_value)
        if explicit_ref is not None:
            common["keyRef"] = explicit_ref
        elif inline_ref is not None:
            common["keyRef"] = inline_ref
        elif isinstance(key_value, str) and key_value.strip():
            common["key"] = key_value.strip()
        else:
            raise AuthClosureError("legacy API-key credential has no usable secret")
        metadata = raw.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in metadata.items()
            ):
                raise AuthClosureError("legacy API-key metadata is invalid")
            common["metadata"] = dict(metadata)
        return common
    if kind == "token":
        explicit_ref = _secret_ref(raw.get("tokenRef"))
        token_value = raw.get("token")
        inline_ref = _secret_ref(token_value)
        if explicit_ref is not None:
            common["tokenRef"] = explicit_ref
        elif inline_ref is not None:
            common["tokenRef"] = inline_ref
        elif isinstance(token_value, str) and token_value.strip():
            common["token"] = token_value.strip()
        else:
            raise AuthClosureError("legacy token credential has no usable secret")
        expires = raw.get("expires")
        if expires is not None:
            if not isinstance(expires, (int, float)) or isinstance(expires, bool):
                raise AuthClosureError("legacy token expiry is invalid")
            common["expires"] = expires
        return common
    if kind == "oauth":
        for name in (
            "access",
            "refresh",
            "idToken",
            "clientId",
            "enterpriseUrl",
            "projectId",
            "accountId",
            "chatgptPlanType",
        ):
            value = _optional_string(raw, name)
            if value is not None:
                common[name] = value
        expires = raw.get("expires")
        if expires is not None:
            if not isinstance(expires, (int, float)) or isinstance(expires, bool):
                raise AuthClosureError("legacy OAuth expiry is invalid")
            common["expires"] = expires
        if not any(name in common for name in ("access", "refresh")):
            raise AuthClosureError("legacy OAuth credential has no usable token")
        return common
    raise AuthClosureError("legacy credential type is unsupported")


def _normalize_state(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise AuthClosureError("legacy auth state is not an object")
    state: dict[str, object] = {"version": 1}
    order = raw.get("order")
    if order is not None:
        if not isinstance(order, dict):
            raise AuthClosureError("legacy auth order is invalid")
        normalized_order: dict[str, list[str]] = {}
        for provider, profile_ids in order.items():
            normalized_provider = _provider_id(provider)
            if not isinstance(profile_ids, list) or not profile_ids:
                raise AuthClosureError("legacy auth order is invalid")
            normalized_order[normalized_provider] = [_profile_id(item) for item in profile_ids]
        state["order"] = normalized_order
    last_good = raw.get("lastGood")
    if last_good is not None:
        if not isinstance(last_good, dict):
            raise AuthClosureError("legacy last-good auth state is invalid")
        state["lastGood"] = {
            _provider_id(provider): _profile_id(profile_id)
            for provider, profile_id in last_good.items()
        }
    usage = raw.get("usageStats")
    if usage is not None:
        if not isinstance(usage, dict):
            raise AuthClosureError("legacy auth usage state is invalid")
        normalized_usage: dict[str, dict[str, object]] = {}
        for profile_id, values in usage.items():
            normalized_id = _profile_id(profile_id)
            if not isinstance(values, dict) or _payload_has_redaction_sentinel(values):
                raise AuthClosureError("legacy auth usage state is invalid")
            # Runtime state contains only bounded JSON scalars/records.  The
            # global structural guard rejects unexpectedly deep or huge input.
            _inspect_authority_json(values)
            normalized_usage[normalized_id] = dict(values)
        state["usageStats"] = normalized_usage
    return state


def _model_marker_or_ref(value: object) -> tuple[str, object | None]:
    reference = _secret_ref(value)
    if reference is not None:
        return "credential", reference
    if not isinstance(value, str) or not value.strip():
        return "missing", None
    stripped = value.strip()
    normalized = stripped.casefold()
    if (
        _string_has_sentinel(stripped)
        or normalized in NON_SECRET_MODEL_MARKERS
        or normalized.startswith("oauth:")
        or normalized.startswith("secretref-env:")
    ):
        return "marker", None
    if ENV_MARKER_RE.fullmatch(stripped):
        return (
            "credential",
            {"source": "env", "provider": "default", "id": stripped},
        )
    return "credential", stripped


def _merge_profile(
    profiles: dict[str, object],
    desired: str,
    credential: dict[str, object],
    *,
    suffix: str,
) -> tuple[str, bool]:
    existing = profiles.get(desired)
    if existing is None:
        profiles[desired] = credential
        return desired, True
    if existing == credential:
        return desired, False
    candidate = f"{credential['provider']}:{suffix}"
    counter = 2
    while candidate in profiles and profiles[candidate] != credential:
        candidate = f"{credential['provider']}:{suffix}-{counter}"
        counter += 1
    if candidate not in profiles:
        profiles[candidate] = credential
        return candidate, True
    return candidate, False


def _merge_state(
    destination: dict[str, object],
    source: dict[str, object],
    replacements: dict[str, str],
) -> None:
    for field in ("order", "lastGood", "usageStats"):
        source_record = source.get(field)
        if not isinstance(source_record, dict):
            continue
        target = destination.setdefault(field, {})
        assert isinstance(target, dict)
        for key, value in source_record.items():
            if field == "order":
                assert isinstance(value, list)
                rewritten = [replacements.get(item, item) for item in value]
                target[key] = list(dict.fromkeys(rewritten))
            elif field == "lastGood":
                assert isinstance(value, str)
                target[key] = replacements.get(value, value)
            else:
                target[replacements.get(key, key)] = value


def _load_primary_json(
    connection: sqlite3.Connection, table: str, key_column: str, value_column: str
) -> dict[str, object] | None:
    row = connection.execute(
        f"SELECT {value_column} FROM {table} WHERE {key_column}='primary'"
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0], object_pairs_hook=_strict_object_pairs)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthClosureError("canonical auth database contains malformed JSON") from exc
    if not isinstance(value, dict):
        raise AuthClosureError("canonical auth database JSON root is invalid")
    return value


def _materialize_agent(
    agent_id: str,
    directory: Path,
    *,
    expected_version: str,
) -> dict[str, object]:
    sources: dict[str, tuple[dict[str, object], str]] = {}
    for name in EXACT_LEGACY_AUTHORITY_NAMES:
        path = directory / name
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_file():
                raise AuthClosureError("legacy authority path is unsafe")
            sources[name] = _read_legacy_json(path)

    source_digest = hashlib.sha256()
    for name, (_value, digest) in sorted(sources.items()):
        source_digest.update(name.encode("utf-8"))
        source_digest.update(b"\0")
        source_digest.update(digest.encode("ascii"))
        source_digest.update(b"\n")

    database = directory / "openclaw-agent.sqlite"
    database_exists = os.path.lexists(database)
    if database_exists and (database.is_symlink() or not database.is_file()):
        raise AuthClosureError("canonical auth database path is unsafe")
    if not sources and not database_exists:
        return {
            "agentId": agent_id,
            "status": "UNCONFIGURED",
            "sourceFileCount": 0,
            "sourceSetSha256": source_digest.hexdigest(),
            "profilesImported": 0,
            "modelCredentialsImported": 0,
            "redactionMarkersSkipped": 0,
            "canonicalStore": {"exists": False, "sha256": None},
        }

    parent = _open_directory_nofollow(directory)
    temporary_name: str | None = None
    if database_exists:
        work_path = database
    else:
        temporary_name = f".openclaw-agent.sqlite.materialize-{secrets.token_hex(16)}"
        work_path = directory / temporary_name
    connection: sqlite3.Connection | None = None
    published = database_exists
    profiles_imported = 0
    model_imported = 0
    markers_skipped = 0
    try:
        connection = sqlite3.connect(work_path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("BEGIN IMMEDIATE")
        for statement in (
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
              meta_key TEXT NOT NULL PRIMARY KEY,
              role TEXT NOT NULL,
              schema_version INTEGER NOT NULL,
              agent_id TEXT,
              app_version TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_profile_store (
              store_key TEXT NOT NULL PRIMARY KEY,
              store_json TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_profile_state (
              state_key TEXT NOT NULL PRIMARY KEY,
              state_json TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """,
        ):
            connection.execute(statement)
        user_version = connection.execute("PRAGMA user_version").fetchone()
        if user_version not in {(0,), (1,)}:
            raise AuthClosureError("canonical auth database schema version is unsupported")
        existing_owner = connection.execute(
            "SELECT role, agent_id, schema_version, app_version "
            "FROM schema_meta WHERE meta_key='primary'"
        ).fetchone()
        if existing_owner is not None and existing_owner[:3] != ("agent", agent_id, 1):
            raise AuthClosureError("canonical auth database owner metadata conflicts")
        if existing_owner is not None and existing_owner[3] not in {
            None,
            expected_version,
        }:
            raise AuthClosureError("canonical auth database version metadata conflicts")
        now = int(time.time() * 1000)
        created_at = now
        if existing_owner is not None:
            created = connection.execute(
                "SELECT created_at FROM schema_meta WHERE meta_key='primary'"
            ).fetchone()
            if created is not None and isinstance(created[0], int):
                created_at = created[0]
        # The pinned OpenClaw build itself writes NULL for the primary row's
        # app_version.  Version compatibility is enforced by this helper's
        # exact implementation allowlist, not by inventing different DB data.
        connection.execute(
            "INSERT INTO schema_meta "
            "(meta_key, role, schema_version, agent_id, app_version, created_at, updated_at) "
            "VALUES ('primary', 'agent', 1, ?, NULL, ?, ?) "
            "ON CONFLICT(meta_key) DO UPDATE SET role='agent', schema_version=1, "
            "agent_id=excluded.agent_id, app_version=NULL, updated_at=excluded.updated_at",
            (agent_id, created_at, now),
        )
        connection.execute("PRAGMA user_version = 1")

        raw_store = _load_primary_json(
            connection, "auth_profile_store", "store_key", "store_json"
        )
        if raw_store is None:
            profiles: dict[str, object] = {}
        else:
            if raw_store.get("version", 1) != 1 or not isinstance(
                raw_store.get("profiles"), dict
            ):
                raise AuthClosureError("canonical auth profile store shape is unsupported")
            profiles = dict(raw_store["profiles"])
            for profile_id, credential in profiles.items():
                _profile_id(profile_id)
                _normalize_credential(credential)
        raw_state = _load_primary_json(
            connection, "auth_profile_state", "state_key", "state_json"
        )
        state = _normalize_state(raw_state or {})

        replacements: dict[str, str] = {}
        profile_source = sources.get("auth-profiles.json")
        if profile_source is not None:
            raw_profiles = profile_source[0]
            if raw_profiles.get("version", 1) != 1 or not isinstance(
                raw_profiles.get("profiles"), dict
            ):
                raise AuthClosureError("legacy auth profile store shape is unsupported")
            if _payload_has_redaction_sentinel(raw_profiles):
                raise AuthClosureError("legacy auth profile store contains a redaction sentinel")
            for source_id, raw_credential in raw_profiles["profiles"].items():
                desired = _profile_id(source_id)
                credential = _normalize_credential(raw_credential)
                actual, inserted = _merge_profile(
                    profiles,
                    desired,
                    credential,
                    suffix="legacy-auth-profiles",
                )
                replacements[desired] = actual
                profiles_imported += int(inserted)
            _merge_state(state, _normalize_state(raw_profiles), replacements)

        legacy_source = sources.get("auth.json")
        if legacy_source is not None:
            for provider_name, raw_credential in legacy_source[0].items():
                provider = _provider_id(provider_name)
                credential = _normalize_credential(
                    raw_credential, fallback_provider=provider
                )
                desired = f"{provider}:default"
                actual, inserted = _merge_profile(
                    profiles,
                    desired,
                    credential,
                    suffix="legacy-auth-json",
                )
                replacements[desired] = actual
                profiles_imported += int(inserted)

        state_source = sources.get("auth-state.json")
        if state_source is not None:
            _merge_state(state, _normalize_state(state_source[0]), replacements)

        models_source = sources.get("models.json")
        if models_source is not None:
            providers = models_source[0].get("providers")
            if providers is not None and not isinstance(providers, dict):
                raise AuthClosureError("legacy models provider set is invalid")
            for provider_name, raw_provider in (providers or {}).items():
                provider = _provider_id(provider_name)
                if not isinstance(raw_provider, dict):
                    raise AuthClosureError("legacy models provider is not an object")
                headers = raw_provider.get("headers")
                if headers is not None:
                    if not isinstance(headers, dict):
                        raise AuthClosureError("legacy models provider headers are invalid")
                    for header, value in headers.items():
                        if not isinstance(header, str):
                            raise AuthClosureError("legacy models provider header name is invalid")
                        if SENSITIVE_HEADER_RE.fullmatch(header.strip()) and isinstance(
                            value, str
                        ) and value.strip() and not _string_has_sentinel(value):
                            marker_kind, _marker_value = _model_marker_or_ref(value)
                            if marker_kind == "credential":
                                raise AuthClosureError(
                                    "sensitive models header cannot be represented in an auth profile"
                                )
                kind, secret_input = _model_marker_or_ref(raw_provider.get("apiKey"))
                if kind == "marker":
                    markers_skipped += 1
                    continue
                if kind == "missing":
                    continue
                credential: dict[str, object] = {
                    "type": "api_key",
                    "provider": provider,
                }
                if isinstance(secret_input, dict):
                    credential["keyRef"] = secret_input
                else:
                    assert isinstance(secret_input, str)
                    credential["key"] = secret_input
                desired = f"{provider}:default"
                actual, inserted = _merge_profile(
                    profiles,
                    desired,
                    credential,
                    suffix="models-json",
                )
                profiles_imported += int(inserted)
                model_imported += int(inserted)
                order = state.setdefault("order", {})
                last_good = state.setdefault("lastGood", {})
                assert isinstance(order, dict) and isinstance(last_good, dict)
                existing_order = order.get(provider, [])
                if not isinstance(existing_order, list):
                    raise AuthClosureError("canonical auth order is invalid")
                order[provider] = [actual, *[item for item in existing_order if item != actual]]
                last_good[provider] = actual

        store_payload = {"version": 1, "profiles": profiles}
        state_payload = {
            key: value
            for key, value in state.items()
            if key == "version" or (isinstance(value, dict) and value)
        }
        connection.execute(
            "INSERT INTO auth_profile_store (store_key, store_json, updated_at) "
            "VALUES ('primary', ?, ?) ON CONFLICT(store_key) DO UPDATE SET "
            "store_json=excluded.store_json, updated_at=excluded.updated_at",
            (json.dumps(store_payload, separators=(",", ":"), sort_keys=True), now),
        )
        if len(state_payload) > 1:
            connection.execute(
                "INSERT INTO auth_profile_state (state_key, state_json, updated_at) "
                "VALUES ('primary', ?, ?) ON CONFLICT(state_key) DO UPDATE SET "
                "state_json=excluded.state_json, updated_at=excluded.updated_at",
                (json.dumps(state_payload, separators=(",", ":"), sort_keys=True), now),
            )
        else:
            connection.execute(
                "DELETE FROM auth_profile_state WHERE state_key='primary'"
            )
        connection.execute("COMMIT")
        connection.close()
        connection = None
        os.chmod(work_path, 0o600)
        file_descriptor = os.open(
            work_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        if not database_exists:
            try:
                os.link(
                    work_path.name,
                    database.name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise AuthClosureError(
                    "canonical auth database appeared during materialization"
                ) from exc
            os.unlink(work_path.name, dir_fd=parent)
            os.fsync(parent)
            published = True
        digest = hashlib.sha256()
        with database.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        return {
            "agentId": agent_id,
            "status": "MATERIALIZED" if profiles_imported else "UNCHANGED",
            "sourceFileCount": len(sources),
            "sourceSetSha256": source_digest.hexdigest(),
            "profilesImported": profiles_imported,
            "modelCredentialsImported": model_imported,
            "redactionMarkersSkipped": markers_skipped,
            "canonicalStore": {"exists": True, "sha256": digest.hexdigest()},
        }
    except sqlite3.Error as exc:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise AuthClosureError("canonical auth database materialization failed") from exc
    finally:
        if connection is not None:
            connection.close()
        if temporary_name is not None and not published:
            try:
                os.unlink(temporary_name, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def materialize_legacy_authorities(
    prefix: Path, *, expected_version: str
) -> tuple[dict[str, object], int]:
    if expected_version not in MATERIALIZATION_SCHEMA_BY_VERSION:
        return (
            {
                "schema": MATERIALIZATION_SCHEMA,
                "status": "FAIL",
                "expectedVersion": expected_version,
                "schemaVersion": None,
                "openclawExecuted": False,
                "networkEnabled": False,
                "failureCount": 1,
                "failures": [{"reason": "unsupported-openclaw-version"}],
                "agents": [],
            },
            2,
        )
    report: dict[str, object] = {
        "schema": MATERIALIZATION_SCHEMA,
        "status": "FAIL",
        "expectedVersion": expected_version,
        "schemaVersion": MATERIALIZATION_SCHEMA_BY_VERSION[expected_version],
        "openclawExecuted": False,
        "networkEnabled": False,
        "layoutNormalization": None,
        "agents": [],
        "agentCount": 0,
        "sourceFileCount": 0,
        "profilesImported": 0,
        "modelCredentialsImported": 0,
        "redactionMarkersSkipped": 0,
        "failureCount": 0,
        "failures": [],
    }
    try:
        report["layoutNormalization"] = normalize_legacy_agent_layout(prefix)
        agents: list[dict[str, object]] = []
        for agent_id in _disk_agent_ids(prefix):
            directory = prefix / "agents" / agent_id / "agent"
            if not os.path.lexists(directory):
                continue
            agents.append(
                _materialize_agent(
                    agent_id,
                    directory,
                    expected_version=expected_version,
                )
            )
        report["agents"] = agents
        report["agentCount"] = len(agents)
        for field in (
            "sourceFileCount",
            "profilesImported",
            "modelCredentialsImported",
            "redactionMarkersSkipped",
        ):
            report[field] = sum(int(agent[field]) for agent in agents)
        report["status"] = "PASS"
        return report, 0
    except (AuthClosureError, OSError, ValueError) as exc:
        report["failureCount"] = 1
        report["failures"] = [{"reason": str(exc)}]
        return report, 2


def _copy_private_regular(source: Path, destination: Path) -> None:
    """Copy one verified-stage file without following either endpoint."""

    source_parent = _open_directory_nofollow(source.parent)
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        named = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
        source_descriptor = os.open(
            source.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_parent,
        )
        opened = os.fstat(source_descriptor)
        identity = (
            named.st_dev,
            named.st_ino,
            named.st_size,
            named.st_mtime_ns,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or identity
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise AuthClosureError("verified auth staging source is unsafe")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination_parent = _open_directory_nofollow(destination.parent)
        try:
            temporary = f".{destination.name}.auth-stage-{secrets.token_hex(16)}"
            destination_descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=destination_parent,
            )
            while chunk := os.read(source_descriptor, 64 * 1024):
                _write_all(destination_descriptor, chunk)
            after = os.fstat(source_descriptor)
            if identity != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise AuthClosureError("verified auth staging source changed during copy")
            os.fchmod(destination_descriptor, 0o600)
            os.fsync(destination_descriptor)
            os.close(destination_descriptor)
            destination_descriptor = None
            os.replace(
                temporary,
                destination.name,
                src_dir_fd=destination_parent,
                dst_dir_fd=destination_parent,
            )
            temporary = ""
            os.fsync(destination_parent)
        finally:
            if destination_descriptor is not None:
                os.close(destination_descriptor)
            if "temporary" in locals() and temporary:
                try:
                    os.unlink(temporary, dir_fd=destination_parent)
                except FileNotFoundError:
                    pass
            os.close(destination_parent)
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(source_parent)


def materialize_verified_archive_stage(
    stage: Path, *, expected_version: str
) -> tuple[dict[str, object], int]:
    """Merge quarantined legacy auth into canonical DBs inside verified staging.

    Live owner state is never touched. The merged database replaces only the
    canonical copy inside the private verified stage before restore overlay.
    """

    report: dict[str, object] = {
        "schema": MATERIALIZATION_SCHEMA,
        "status": "FAIL",
        "expectedVersion": expected_version,
        "openclawExecuted": False,
        "networkEnabled": False,
        "agents": [],
        "agentCount": 0,
        "failureCount": 0,
        "failures": [],
    }
    try:
        stage = _absolute(stage)
        _open = _open_directory_nofollow(stage)
        os.close(_open)
        payload_agents = (
            stage
            / "recovery-quarantine"
            / "archive-authority"
            / "payload"
            / "agents"
        )
        if not os.path.lexists(payload_agents):
            report["status"] = "PASS"
            return report, 0
        if payload_agents.is_symlink() or not payload_agents.is_dir():
            raise AuthClosureError("verified archive agent authority is unsafe")
        results: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory(prefix=".auth-merge-", dir=stage) as temporary:
            merge_root = Path(temporary)
            for entry in sorted(os.scandir(payload_agents), key=lambda value: value.name):
                if (
                    AGENT_ID_RE.fullmatch(entry.name) is None
                    or entry.is_symlink()
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    raise AuthClosureError("verified archive contains an unsafe agent")
                authority_dir = Path(entry.path) / "agent"
                if not authority_dir.is_dir() or authority_dir.is_symlink():
                    continue
                sources = [
                    authority_dir / name
                    for name in EXACT_LEGACY_AUTHORITY_NAMES
                    if os.path.lexists(authority_dir / name)
                ]
                if not sources:
                    continue
                merge_dir = merge_root / "agents" / entry.name / "agent"
                merge_dir.mkdir(parents=True, mode=0o700)
                canonical = (
                    stage / "agents" / entry.name / "agent" / "openclaw-agent.sqlite"
                )
                if os.path.lexists(canonical):
                    _copy_private_regular(canonical, merge_dir / canonical.name)
                for source in sources:
                    _copy_private_regular(source, merge_dir / source.name)
                result = _materialize_agent(
                    entry.name,
                    merge_dir,
                    expected_version=expected_version,
                )
                merged = merge_dir / "openclaw-agent.sqlite"
                if not merged.is_file() or merged.is_symlink():
                    raise AuthClosureError("verified archive auth merge produced no database")
                _copy_private_regular(merged, canonical)
                results.append(result)
        report["agents"] = results
        report["agentCount"] = len(results)
        report["status"] = "PASS"
        return report, 0
    except (AuthClosureError, OSError, ValueError) as exc:
        report["failureCount"] = 1
        report["failures"] = [{"reason": str(exc)}]
        return report, 2


def _disk_agent_ids(prefix: Path) -> list[str]:
    if os.path.lexists(prefix) and (prefix.is_symlink() or not prefix.is_dir()):
        raise AuthClosureError("OpenClaw state prefix is unsafe")
    root = prefix / "agents"
    if not os.path.lexists(root):
        return []
    if root.is_symlink() or not root.is_dir():
        raise AuthClosureError("OpenClaw agent root is unsafe")
    identifiers: list[str] = []
    for entry in os.scandir(root):
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise AuthClosureError("OpenClaw agent root contains an unsafe entry")
        if AGENT_ID_RE.fullmatch(entry.name) is None:
            raise AuthClosureError("OpenClaw agent root contains an invalid agent id")
        identifiers.append(entry.name)
    return sorted(identifiers)


def _legacy_authority_candidates(
    prefix: Path,
) -> list[tuple[str, Path, tuple[int, int, int, int]]]:
    matches: list[tuple[str, Path, tuple[int, int, int, int]]] = []
    for agent_id in _disk_agent_ids(prefix):
        directory = prefix / "agents" / agent_id / "agent"
        if not os.path.lexists(directory):
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise AuthClosureError("OpenClaw agent state directory is unsafe")
        for entry in os.scandir(directory):
            normalized_name = unicodedata.normalize("NFKC", entry.name)
            if not LEGACY_AUTHORITY_RE.fullmatch(normalized_name):
                continue
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise AuthClosureError("OpenClaw legacy authority is unsafe")
            information = entry.stat(follow_symlinks=False)
            if (
                information.st_nlink != 1
                or information.st_uid != os.geteuid()
                or information.st_size > MAX_AUTHORITY_BYTES
            ):
                raise AuthClosureError("OpenClaw legacy authority is unsafe or oversized")
            matches.append(
                (
                    agent_id,
                    Path(entry.path),
                    (
                        information.st_dev,
                        information.st_ino,
                        information.st_size,
                        information.st_mtime_ns,
                    ),
                )
            )
    return sorted(matches, key=lambda item: (item[0], item[1].name.casefold()))


def quarantine_legacy_authorities(
    prefix: Path, *, dry_run: bool, placeholders_only: bool = False
) -> dict[str, object]:
    matches = _legacy_authority_candidates(prefix)
    if placeholders_only:
        selected: list[tuple[str, Path, tuple[int, int, int, int]]] = []
        for candidate in matches:
            payload, _digest = _read_legacy_json(candidate[1])
            if _payload_has_redaction_sentinel(payload):
                selected.append(candidate)
        matches = selected
    if dry_run or not matches:
        return {
            "status": "WOULD_QUARANTINE" if matches else "CLEAN",
            "agentCount": len({agent_id for agent_id, _, _ in matches}),
            "fileCount": len(matches),
        }
    prefix_descriptor = _open_directory_nofollow(prefix)
    stamp = (
        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{os.getpid()}-{secrets.token_hex(12)}"
    )
    quarantine_descriptor: int | None = None
    journal_descriptor: int | None = None
    recovery_descriptor: int | None = None
    authority_descriptor: int | None = None
    try:
        prefix_information = os.fstat(prefix_descriptor)
        if (
            prefix_information.st_uid != os.geteuid()
            or stat.S_IMODE(prefix_information.st_mode) & 0o077
        ):
            raise AuthClosureError("OpenClaw state prefix is not owner-private")
        recovery_descriptor = _open_or_create_private_directory(
            prefix_descriptor, "recovery-quarantine"
        )
        authority_descriptor = _open_or_create_private_directory(
            recovery_descriptor, "legacy-agent-authority"
        )
        os.mkdir(stamp, 0o700, dir_fd=authority_descriptor)
        quarantine_descriptor = _open_or_create_private_directory(
            authority_descriptor, stamp
        )
        journal_descriptor = os.open(
            "MOVE-JOURNAL.jsonl",
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=quarantine_descriptor,
        )

        def append_journal(payload: dict[str, object]) -> None:
            assert journal_descriptor is not None
            _write_all(
                journal_descriptor,
                (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
            )
            os.fsync(journal_descriptor)

        append_journal(
            {
                "schema": "openclaw.legacy-authority-move-journal/v1",
                "phase": "prepared",
                "files": [
                    {
                        "agentId": agent_id,
                        "name": source.name,
                        "device": identity[0],
                        "inode": identity[1],
                    }
                    for agent_id, source, identity in matches
                ],
            }
        )
        os.fsync(quarantine_descriptor)
        moved_sources: list[tuple[str, Path, str, tuple[int, int, int, int]]] = []
        try:
            for agent_id, source, expected_identity in matches:
                destination_directory = _open_or_create_private_directory(
                    quarantine_descriptor, agent_id
                )
                source_parent = _open_directory_nofollow(source.parent)
                try:
                    current = os.stat(
                        source.name,
                        dir_fd=source_parent,
                        follow_symlinks=False,
                    )
                    identity = (
                        current.st_dev,
                        current.st_ino,
                        current.st_size,
                        current.st_mtime_ns,
                    )
                    if (
                        identity != expected_identity
                        or not stat.S_ISREG(current.st_mode)
                        or current.st_nlink != 1
                    ):
                        raise AuthClosureError(
                            "legacy authority changed before quarantine"
                        )
                    try:
                        os.stat(
                            source.name,
                            dir_fd=destination_directory,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        raise AuthClosureError("legacy authority quarantine collision")
                    os.rename(
                        source.name,
                        source.name,
                        src_dir_fd=source_parent,
                        dst_dir_fd=destination_directory,
                    )
                    moved_sources.append(
                        (agent_id, source.parent, source.name, expected_identity)
                    )
                    moved = os.open(
                        source.name,
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=destination_directory,
                    )
                    try:
                        os.fchmod(moved, 0o600)
                        os.fsync(moved)
                    finally:
                        os.close(moved)
                    os.fsync(source_parent)
                    os.fsync(destination_directory)
                    append_journal(
                        {
                            "phase": "moved",
                            "agentId": agent_id,
                            "name": source.name,
                            "device": expected_identity[0],
                            "inode": expected_identity[1],
                        }
                    )
                finally:
                    os.close(source_parent)
                    os.close(destination_directory)
        except Exception:
            for agent_id, source_parent_path, name, expected_identity in reversed(
                moved_sources
            ):
                destination_directory = _open_or_create_private_directory(
                    quarantine_descriptor, agent_id
                )
                source_parent = _open_directory_nofollow(source_parent_path)
                try:
                    try:
                        os.stat(name, dir_fd=source_parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise AuthClosureError(
                            "legacy authority rollback destination is occupied"
                        )
                    quarantined = os.stat(
                        name, dir_fd=destination_directory, follow_symlinks=False
                    )
                    if (quarantined.st_dev, quarantined.st_ino) != expected_identity[:2]:
                        raise AuthClosureError(
                            "legacy authority rollback identity changed"
                        )
                    os.rename(
                        name,
                        name,
                        src_dir_fd=destination_directory,
                        dst_dir_fd=source_parent,
                    )
                    os.fsync(destination_directory)
                    os.fsync(source_parent)
                finally:
                    os.close(source_parent)
                    os.close(destination_directory)
            append_journal({"phase": "rolled-back"})
            raise
        append_journal({"phase": "committed"})
        metadata_payload = {
            "schema": "openclaw.legacy-agent-authority-quarantine/v1",
            "createdAt": stamp,
            "fileCount": len(matches),
            "activation": "forbidden-during-restore",
        }
        metadata = (json.dumps(metadata_payload, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        metadata_descriptor = os.open(
            "QUARANTINE.json",
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=quarantine_descriptor,
        )
        try:
            _write_all(metadata_descriptor, metadata)
            os.fsync(metadata_descriptor)
        finally:
            os.close(metadata_descriptor)
        os.fsync(quarantine_descriptor)
        os.fsync(authority_descriptor)
    finally:
        if journal_descriptor is not None:
            os.close(journal_descriptor)
        if quarantine_descriptor is not None:
            os.close(quarantine_descriptor)
        if authority_descriptor is not None:
            os.close(authority_descriptor)
        if recovery_descriptor is not None:
            os.close(recovery_descriptor)
        os.close(prefix_descriptor)
    return {
        "status": "QUARANTINED",
        "agentCount": len({agent_id for agent_id, _, _ in matches}),
        "fileCount": len(matches),
    }


def quarantine_public_placeholders(
    prefix: Path, *, dry_run: bool
) -> dict[str, object]:
    """Remove only known public redaction material during component install."""

    return quarantine_legacy_authorities(
        prefix, dry_run=dry_run, placeholders_only=True
    )


def _database_metadata(
    path: Path, agent_id: str, expected_version: str
) -> tuple[dict[str, object], list[str]]:
    failures: list[str] = []
    metadata: dict[str, object] = {
        "exists": False,
        "integrity": None,
        "schemaVersion": None,
        "appVersion": None,
        "authStoreRows": None,
        "profileCount": None,
        "configured": False,
        "authorityJsonValid": None,
        "executableSecretRefFree": None,
        "credentialSourceKinds": [],
        "redactionSentinelFree": None,
        "device": None,
        "inode": None,
        "size": None,
        "mtimeNs": None,
        "ctimeNs": None,
    }
    if not os.path.lexists(path):
        return metadata, failures
    descriptor: int | None = None
    parent_descriptor: int | None = None
    try:
        parent_descriptor = _open_directory_nofollow(path.parent)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
    except OSError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        return metadata, ["canonical-store-symlink-or-unsafe-path"]
    information = os.fstat(descriptor)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_nlink != 1
        or information.st_size > MAX_AUTHORITY_BYTES * 32
    ):
        os.close(descriptor)
        os.close(parent_descriptor)
        return metadata, ["canonical-store-unsafe-file"]
    metadata.update(
        {
            "exists": True,
            "device": information.st_dev,
            "inode": information.st_ino,
            "size": information.st_size,
            "mtimeNs": information.st_mtime_ns,
            "ctimeNs": information.st_ctime_ns,
        }
    )
    if information.st_uid != os.geteuid():
        failures.append("canonical-store-wrong-owner")
    if stat.S_IMODE(information.st_mode) & 0o077:
        failures.append("canonical-store-unsafe-mode")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1",
            uri=True,
            timeout=5,
        )
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            failures.append("canonical-store-integrity")
            metadata["integrity"] = False
        else:
            metadata["integrity"] = True
        user_version = connection.execute("PRAGMA user_version").fetchone()
        metadata["schemaVersion"] = user_version[0] if user_version else None
        if user_version != (1,):
            failures.append("canonical-store-schema-version")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
            if table not in tables:
                failures.append("canonical-store-schema")
                continue
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not required_columns.issubset(columns):
                failures.append("canonical-store-schema")
        if "schema_meta" in tables:
            owner = connection.execute(
                "SELECT role, agent_id, schema_version, app_version "
                "FROM schema_meta WHERE meta_key='primary'"
            ).fetchone()
            if owner is not None:
                metadata["appVersion"] = owner[3]
            if owner is None or owner[:3] != ("agent", agent_id, 1):
                failures.append("canonical-store-owner-metadata")
            elif owner[3] not in {None, expected_version}:
                failures.append("canonical-store-app-version")
        if "auth_profile_store" in tables:
            row = connection.execute(
                "SELECT store_json FROM auth_profile_store WHERE store_key='primary'"
            ).fetchone()
            metadata["authStoreRows"] = 1 if row else 0
            if row:
                try:
                    primary_store = json.loads(
                        row[0], object_pairs_hook=_strict_object_pairs
                    )
                except (json.JSONDecodeError, TypeError, AuthClosureError):
                    failures.append("canonical-store-malformed-primary-auth-row")
                else:
                    if (
                        not isinstance(primary_store, dict)
                        or primary_store.get("version") != 1
                        or not isinstance(primary_store.get("profiles"), dict)
                    ):
                        failures.append("canonical-store-primary-auth-row-shape")
                    else:
                        metadata["profileCount"] = len(primary_store["profiles"])
                        metadata["configured"] = bool(primary_store["profiles"])
        sentinel_found = False
        malformed_json = False
        executable_secret_ref = False
        credential_source_kinds: set[str] = set()
        for table, column in (
            ("auth_profile_store", "store_json"),
            ("auth_profile_state", "state_json"),
        ):
            if table not in tables:
                continue
            for (value,) in connection.execute(f"SELECT {column} FROM {table}"):
                if _payload_has_redaction_sentinel(value):
                    sentinel_found = True
                try:
                    decoded = json.loads(value)
                    executable, sources = _inspect_authority_json(decoded)
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
                    malformed_json = True
                else:
                    executable_secret_ref = executable_secret_ref or executable
                    credential_source_kinds.update(sources)
        metadata["authorityJsonValid"] = not malformed_json
        metadata["executableSecretRefFree"] = not executable_secret_ref
        metadata["credentialSourceKinds"] = sorted(credential_source_kinds)
        metadata["redactionSentinelFree"] = not sentinel_found
        if malformed_json:
            failures.append("canonical-store-malformed-authority-json")
        if executable_secret_ref:
            failures.append("canonical-store-executable-secret-ref")
        if sentinel_found:
            failures.append("canonical-store-redaction-sentinel")
    except sqlite3.Error:
        failures.append("canonical-store-unreadable")
    finally:
        if connection is not None:
            connection.close()
    try:
        after = os.fstat(descriptor)
        named_after = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        failures.append("canonical-store-changed-during-verification")
    else:
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
        ) or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino):
            failures.append("canonical-store-changed-during-verification")
    os.close(descriptor)
    os.close(parent_descriptor)
    return metadata, sorted(set(failures))


def verify(
    *,
    prefix: Path,
    expected_version: str,
    allow_unconfigured: bool,
    **_unused: object,
) -> tuple[dict[str, object], int]:
    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "status": "FAIL",
        "runtimeVersion": expected_version,
        "verificationMode": "offline-structural-only",
        "openclawExecuted": False,
        "networkEnabled": False,
        "agents": [],
        "failureCount": 0,
        "failures": [],
    }
    failures: list[dict[str, object]] = []
    try:
        legacy = _legacy_authority_candidates(prefix)
        if legacy:
            failures.append(
                {
                    "reason": "legacy-agent-authority-remains",
                    "agentCount": len({agent_id for agent_id, _, _ in legacy}),
                    "fileCount": len(legacy),
                }
            )
        agents: list[dict[str, object]] = []
        for agent_id in _disk_agent_ids(prefix):
            store = prefix / "agents" / agent_id / "agent" / "openclaw-agent.sqlite"
            database, reasons = _database_metadata(
                store, agent_id, expected_version
            )
            unconfigured = not database["exists"] or not database["configured"]
            if unconfigured and not allow_unconfigured:
                reasons = [*reasons, "canonical-store-missing"]
            agent_status = (
                "FAIL"
                if reasons
                else "UNCONFIGURED"
                if unconfigured
                else "PASS"
            )
            agents.append(
                {
                    "agentId": agent_id,
                    "status": agent_status,
                    "canonicalStore": database,
                    "reasons": reasons,
                }
            )
            if reasons:
                failures.append({"agentId": agent_id, "reasons": reasons})
        report["agents"] = agents
    except (AuthClosureError, OSError) as exc:
        failures.append({"reason": str(exc)})
    report["failureCount"] = len(failures)
    report["failures"] = failures
    if failures:
        report["status"] = "FAIL"
    elif any(agent["status"] == "UNCONFIGURED" for agent in report["agents"]):
        report["status"] = "UNCONFIGURED"
    else:
        report["status"] = "PASS"
    return report, 0 if not failures else 2


def migrate(
    *,
    prefix: Path,
    expected_version: str,
    allow_unconfigured: bool,
    **kwargs: object,
) -> tuple[dict[str, object], int]:
    materialization, materialization_result = materialize_legacy_authorities(
        prefix, expected_version=expected_version
    )
    if materialization_result != 0:
        return (
            {
                "schema": REPORT_SCHEMA,
                "status": "FAIL",
                "runtimeVersion": expected_version,
                "verificationMode": "offline-structural-only",
                "openclawExecuted": False,
                "networkEnabled": False,
                "failureCount": 1,
                "failures": [{"reason": "legacy-authority-materialization-failed"}],
                "agents": [],
                "legacyAuthorityMaterialization": materialization,
            },
            2,
        )
    try:
        quarantine = quarantine_legacy_authorities(prefix, dry_run=False)
    except (AuthClosureError, OSError) as exc:
        return (
            {
                "schema": REPORT_SCHEMA,
                "status": "FAIL",
                "verificationMode": "offline-structural-only",
                "openclawExecuted": False,
                "networkEnabled": False,
                "failureCount": 1,
                "failures": [{"reason": str(exc)}],
                "agents": [],
            },
            2,
        )
    report, result = verify(
        prefix=prefix,
        expected_version=expected_version,
        allow_unconfigured=allow_unconfigured,
        **kwargs,
    )
    report["legacyAuthorityMaterialization"] = materialization
    report["legacyAuthorityQuarantine"] = quarantine
    return report, result


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "materialize-legacy",
            "materialize-archive-stage",
            "migrate",
            "verify",
            "quarantine-placeholders",
            "quarantine-legacy",
        ),
    )
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--openclaw", default="openclaw")
    parser.add_argument("--expected-version")
    parser.add_argument("--allow-unconfigured", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prefix = args.prefix.expanduser().absolute()
    if args.command in {"quarantine-placeholders", "quarantine-legacy"}:
        try:
            if args.command == "quarantine-placeholders":
                report = quarantine_public_placeholders(
                    prefix, dry_run=args.dry_run
                )
            else:
                report = quarantine_legacy_authorities(
                    prefix, dry_run=args.dry_run
                )
            result = 0
        except (AuthClosureError, OSError) as exc:
            report = {"status": "FAIL", "reason": str(exc)}
            result = 2
    elif not args.expected_version:
        parser.error(
            "--expected-version is required for materialize-legacy, migrate, and verify"
        )
    elif args.command == "materialize-legacy":
        report, result = materialize_legacy_authorities(
            prefix,
            expected_version=args.expected_version,
        )
    elif args.command == "materialize-archive-stage":
        report, result = materialize_verified_archive_stage(
            prefix,
            expected_version=args.expected_version,
        )
    elif args.command == "migrate":
        report, result = migrate(
            prefix=prefix,
            expected_version=args.expected_version,
            allow_unconfigured=args.allow_unconfigured,
        )
    else:
        report, result = verify(
            prefix=prefix,
            expected_version=args.expected_version,
            allow_unconfigured=args.allow_unconfigured,
        )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = args.output.with_name(f".{args.output.name}.tmp.{os.getpid()}")
        temporary.write_text(encoded, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, args.output)
    print(encoded, end="")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
