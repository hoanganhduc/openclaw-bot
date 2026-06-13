#!/usr/bin/env python3
"""OpenClaw wrapper for the vnthuquan CLI."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WRAPPER_VERSION = "0.1.0-openclaw"
DOWNLOADABLE_FORMATS = {"epub", "pdf", "text", "audio"}
QUEUE_SELECTOR_OPTIONS = {"--query", "--category", "--author-id", "--title", "--url", "--id"}
QUEUE_SCOPE_OPTIONS = {"--limit", "--pages"}
NATIVE_HELP_COMMANDS = {
    "archive",
    "categories",
    "completion",
    "config",
    "doctor",
    "download",
    "formats",
    "list",
    "mirrors",
    "search",
    "show",
    "validate",
}


WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/workspace")).expanduser()
TARGET = os.environ.get("VNTHUQUAN_TARGET", "openclaw")
STATE_DIR = Path(os.environ.get("VNTHUQUAN_STATE_DIR", str(WORKSPACE / "data/vnthuquan/state"))).expanduser()
RUN_DIR = Path(os.environ.get("VNTHUQUAN_RUN_DIR", str(WORKSPACE / "data/vnthuquan/runs"))).expanduser()
CACHE_DIR = Path(os.environ.get("VNTHUQUAN_CACHE_DIR", str(WORKSPACE / "data/vnthuquan/cache"))).expanduser()
DOWNLOAD_DIR = Path(os.environ.get("VNTHUQUAN_DOWNLOAD_DIR", str(WORKSPACE / "data/vnthuquan/downloads"))).expanduser()
CONFIG_PATH = STATE_DIR / "config.json"
ARCHIVE_PATH = STATE_DIR / "downloads.jsonl"
CACHE_PATH = CACHE_DIR / "http-cache.json"
CALIBRE_RUNNER = Path(
    os.environ.get("VNTHUQUAN_CALIBRE_RUNNER", str(WORKSPACE / "skills/calibre/run_cal.sh"))
).expanduser()
CALIBRE_TIMEOUT_SECONDS = int(os.environ.get("VNTHUQUAN_CALIBRE_TIMEOUT_SECONDS", "45"))
CALIBRE_WRITE_TIMEOUT_SECONDS = int(os.environ.get("VNTHUQUAN_CALIBRE_WRITE_TIMEOUT_SECONDS", "180"))
DEFAULT_QUEUE_JOBS = os.environ.get("VNTHUQUAN_QUEUE_JOBS", "3")


class WrapperError(Exception):
    def __init__(self, message: str, code: str = "wrapper_error", exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def default_config() -> dict[str, Any]:
    return {
        "default_mirror": "http://vietnamthuquan.eu",
        "download_dir": str(DOWNLOAD_DIR),
        "archive_path": str(ARCHIVE_PATH),
        "timeout": 30.0,
        "retries": 2,
        "retry_backoff_seconds": 0.5,
        "retry_jitter_seconds": 0.1,
        "cache_ttl_seconds": 300.0,
        "cache_path": str(CACHE_PATH),
        "request_interval_seconds": 0.4,
        "filename_template": "{title} - {author} - vnthuquan",
    }


def ensure_config() -> None:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(default_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_candidates() -> list[Path]:
    values = [
        os.environ.get("VNTHUQUAN_SOURCE_DIR"),
        str(WORKSPACE / "vendor/vnthuquan"),
        "{{ USER_HOME }}/vnthuquan",
    ]
    return [Path(value).expanduser() for value in values if value]


def resolve_vnthuquan() -> tuple[list[str], str, str | None, Path | None]:
    explicit = os.environ.get("VNTHUQUAN_BIN")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return [str(path)], str(path), shutil.which("python3"), None
        raise WrapperError(f"VNTHUQUAN_BIN is not executable: {path}", "missing_executable", 127)

    found = shutil.which("vnthuquan")
    if found:
        return [found], found, shutil.which("python3"), None

    workspace_exe = WORKSPACE / ".local/venv_vnthuquan/bin/vnthuquan"
    if workspace_exe.is_file() and os.access(workspace_exe, os.X_OK):
        return [str(workspace_exe)], str(workspace_exe), str(workspace_exe.parent / "python"), None

    host_exe = Path("{{ USER_HOME }}/.vnthuquan_venv/bin/vnthuquan")
    if host_exe.is_file() and os.access(host_exe, os.X_OK):
        return [str(host_exe)], str(host_exe), str(host_exe.parent / "python"), None

    python = shutil.which("python3") or shutil.which("python")
    if python:
        for candidate in source_candidates():
            if (candidate / "vnthuquan").is_dir():
                return [python, "-m", "vnthuquan"], f"{python} -m vnthuquan", python, candidate

    raise WrapperError("vnthuquan command not found", "missing_executable", 127)


def run_pkg(args: list[str], *, json_mode: bool = True) -> tuple[int, str, str]:
    ensure_config()
    cmd, _, _, cwd = resolve_vnthuquan()
    full = [*cmd, "--config", str(CONFIG_PATH), *args]
    if json_mode and "--json" not in full:
        full.append("--json")
    env = subprocess_env()
    if cwd is not None:
        env["PYTHONPATH"] = f"{cwd}:{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        full,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WrapperError(f"Could not parse vnthuquan JSON output: {exc}", "bad_package_json", 3)
    if not isinstance(data, dict):
        raise WrapperError("vnthuquan JSON output was not an object", "bad_package_json", 3)
    return data


def package_version() -> str | None:
    try:
        cmd, _, _, cwd = resolve_vnthuquan()
    except WrapperError:
        return None
    env = subprocess_env()
    if cwd is not None:
        env["PYTHONPATH"] = f"{cwd}:{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        [*cmd, "--version"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().replace("vnthuquan ", "", 1)


def base_payload(command: str) -> dict[str, Any]:
    return {
        "target": TARGET,
        "command": command,
        "wrapper_version": WRAPPER_VERSION,
        "vnthuquan_version": package_version(),
    }


def normalize_error(command: str, message: str, code: str, exit_code: int) -> dict[str, Any]:
    payload = base_payload(command)
    payload.update({"ok": False, "error_code": code, "message": message, "exit_code": exit_code})
    return payload


def finish(payload: dict[str, Any], json_out: bool) -> int:
    if json_out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("ok"):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"error: {payload.get('message')}", file=sys.stderr)
    return 0 if payload.get("ok", False) else int(payload.get("exit_code", 1))


def require_success(command: str, args: list[str]) -> dict[str, Any]:
    status, stdout, stderr = run_pkg(args, json_mode=True)
    if status != 0:
        payload = normalize_error(command, stderr.strip() or stdout.strip(), "package_error", status)
        payload["package_stdout"] = stdout
        payload["package_stderr"] = stderr
        return payload
    data = parse_json(stdout)
    return data


def has_option(args: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(arg == option or arg.startswith(prefix) for arg in args)


def consume_flag(args: list[str], flag: str) -> tuple[list[str], bool]:
    consumed = False
    kept: list[str] = []
    for arg in args:
        if arg == flag:
            consumed = True
        else:
            kept.append(arg)
    return kept, consumed


def consume_option_value(args: list[str], option: str) -> tuple[list[str], str | None]:
    kept: list[str] = []
    value: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == option:
            if i + 1 >= len(args):
                raise WrapperError(f"{option} requires a value", "usage", 2)
            value = args[i + 1]
            i += 2
            continue
        if arg.startswith(f"{option}="):
            value = arg.split("=", 1)[1]
            i += 1
            continue
        kept.append(arg)
        i += 1
    return kept, value


def append_option_if_missing(args: list[str], option: str, value: str | None = None) -> list[str]:
    if has_option(args, option):
        return args
    return [*args, option] if value is None else [*args, option, value]


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def write_run_json(prefix: str, payload: dict[str, Any]) -> str:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"{prefix}-{timestamp_slug()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def append_state_jsonl(name: str, payload: dict[str, Any]) -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / name
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return str(path)


def archive_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def diagnose() -> dict[str, Any]:
    ensure_dirs()
    try:
        cmd, executable, python, cwd = resolve_vnthuquan()
        version = package_version()
        ready = bool(version)
        message = None
    except WrapperError as exc:
        cmd, executable, python, cwd = [], None, shutil.which("python3"), None
        version = None
        ready = False
        message = str(exc)
    if ready:
        ensure_config()
    payload = base_payload("diagnose")
    payload.update(
        {
            "ok": ready,
            "ready": ready,
            "platform": sys.platform,
            "platform_detail": platform.platform(),
            "workspace": str(WORKSPACE),
            "executable": executable,
            "resolved_command": cmd,
            "python": python,
            "source_dir": str(cwd) if cwd else None,
            "state_dir": str(STATE_DIR),
            "run_dir": str(RUN_DIR),
            "download_dir": str(DOWNLOAD_DIR),
            "config_path": str(CONFIG_PATH),
            "config_scope": "openclaw-wrapper-managed",
            "config_exists": CONFIG_PATH.exists(),
            "archive_path": str(ARCHIVE_PATH),
            "cache_path": str(CACHE_PATH),
            "calibre_runner": str(CALIBRE_RUNNER),
        }
    )
    if message:
        payload.update({"message": message, "error_code": "missing_executable", "exit_code": 127})
    return payload


def doctor() -> dict[str, Any]:
    diag = diagnose()
    payload = base_payload("doctor")
    payload["diagnose"] = diag
    if not diag.get("ready"):
        payload.update({"ok": False, "message": "diagnose failed", "exit_code": 127})
        return payload
    data = require_success("doctor", ["doctor", "--resources"])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload.update({"ok": bool(data.get("ok", False)), "package_payload": data})
    return payload


def passthrough(command: str, args: list[str]) -> dict[str, Any]:
    if command == "formats" and not args:
        args = ["list"]
    data = require_success(command, [command, *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload(command)
    payload.update({"ok": bool(data.get("ok", True)), "package_payload": data})
    for key, value in data.items():
        if key != "ok":
            payload[key] = value
    return payload


def mirrors(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("mirrors", "missing mirrors subcommand", "usage", 2)
    args, yes = consume_flag(args, "--yes")
    subcommand = args[0]
    if subcommand in {"use", "reset"} and not yes:
        return normalize_error("mirrors", f"mirrors {subcommand} requires --yes", "confirmation_required", 2)
    if subcommand not in {"list", "check", "use", "reset"}:
        return normalize_error("mirrors", f"unsupported mirrors subcommand: {subcommand}", "usage", 2)
    payload = passthrough("mirrors", args)
    payload["subcommand"] = subcommand
    payload["wrapper_consumed_flags"] = ["--yes"] if yes else []
    return payload


def config_cmd(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("config", "missing config subcommand", "usage", 2)
    args, yes = consume_flag(args, "--yes")
    subcommand = args[0]
    if subcommand in {"set", "unset"} and not yes:
        return normalize_error("config", f"config {subcommand} requires --yes", "confirmation_required", 2)
    if subcommand == "path":
        payload = base_payload("config")
        payload.update({"ok": True, "subcommand": "path", "config_path": str(CONFIG_PATH)})
        return payload
    if subcommand not in {"show", "set", "unset"}:
        return normalize_error("config", f"unsupported config subcommand: {subcommand}", "usage", 2)
    payload = passthrough("config", args)
    payload["subcommand"] = subcommand
    payload["config_path"] = str(CONFIG_PATH)
    payload["config_scope"] = "openclaw-wrapper-managed"
    payload["wrapper_consumed_flags"] = ["--yes"] if yes else []
    return payload


def download(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    execute = has_option(args, "--execute")
    dry_run = has_option(args, "--dry-run")
    no_archive = has_option(args, "--no-archive")
    if execute and not yes:
        return normalize_error("download", "download --execute requires --yes", "confirmation_required", 2)
    if execute and no_archive:
        return normalize_error("download", "download execution must keep the OpenClaw wrapper archive", "archive_required", 2)
    if has_option(args, "--all") and execute:
        return normalize_error("download", "use queue plus execute-queue for --all downloads", "usage", 2)

    raw_args = ["download", *args]
    if not execute:
        raw_args = append_option_if_missing(raw_args, "--dry-run")
        dry_run = True
    else:
        raw_args = append_option_if_missing(raw_args, "--archive-path", str(ARCHIVE_PATH))

    data = require_success("download", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data
    path_value = data.get("path")
    path = Path(path_value).expanduser() if isinstance(path_value, str) else None
    payload = base_payload("download")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "dry_run": bool(dry_run and not execute),
            "executed": bool(execute),
            "path": path_value,
            "path_exists": path.is_file() if path else False,
            "sha256": archive_sha256(path),
            "archive_path": data.get("archive_path") or (str(ARCHIVE_PATH) if execute else None),
            "wrapper_consumed_flags": ["--yes"] if yes else [],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    for key in ("plan", "validation", "manifest_path", "warnings", "errors"):
        if key in data:
            payload[key] = data[key]
    return payload


def queue(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if has_option(args, "--execute"):
        return normalize_error("queue", "queue creates a dry-run manifest only; use execute-queue", "usage", 2)
    if not any(has_option(args, option) for option in QUEUE_SELECTOR_OPTIONS):
        return normalize_error("queue", "queue requires --query, --category, --author-id, --title, --url, or --id", "usage", 2)
    listing_queue = any(has_option(args, option) for option in {"--query", "--category", "--author-id"})
    if listing_queue and not any(has_option(args, option) for option in QUEUE_SCOPE_OPTIONS):
        return normalize_error("queue", "listing queues require --limit or --pages", "usage", 2)

    args, manifest_value = consume_option_value(args, "--manifest")
    manifest_path = Path(manifest_value).expanduser() if manifest_value else RUN_DIR / f"queue-{timestamp_slug()}.json"
    if manifest_path.exists() and not yes:
        return normalize_error("queue", f"manifest already exists and requires --yes: {manifest_path}", "confirmation_required", 2)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    raw_args = ["download", *args]
    raw_args = append_option_if_missing(raw_args, "--all")
    raw_args = append_option_if_missing(raw_args, "--dry-run")
    raw_args = append_option_if_missing(raw_args, "--manifest", str(manifest_path))

    data = require_success("queue", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data
    queue_data = data.get("queue") if isinstance(data.get("queue"), dict) else {}
    items = queue_data.get("items", []) if isinstance(queue_data, dict) else []
    payload = base_payload("queue")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "dry_run": True,
            "manifest_path": data.get("manifest_path") or str(manifest_path),
            "manifest_exists": manifest_path.is_file(),
            "manifest_sha256": archive_sha256(manifest_path),
            "count": len(items) if isinstance(items, list) else None,
            "items": items,
            "source": queue_data.get("source") if isinstance(queue_data, dict) else None,
            "wrapper_consumed_flags": ["--yes"] if yes else [],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    return payload


def execute_queue(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if not yes:
        return normalize_error("execute-queue", "execute-queue requires --yes", "confirmation_required", 2)
    if not args:
        return normalize_error("execute-queue", "missing queue manifest path", "usage", 2)
    if has_option(args, "--dry-run"):
        return normalize_error("execute-queue", "use queue for dry-run manifest creation", "usage", 2)
    if has_option(args, "--no-archive"):
        return normalize_error("execute-queue", "queue execution must keep the OpenClaw wrapper archive", "archive_required", 2)

    args, from_manifest_value = consume_option_value(args, "--from-manifest")
    manifest = from_manifest_value or args[0]
    if not from_manifest_value:
        args = args[1:]
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        return normalize_error("execute-queue", f"queue manifest not found: {manifest_path}", "missing_manifest", 2)
    raw_args = ["download", "--from-manifest", str(manifest_path), *args]
    raw_args = append_option_if_missing(raw_args, "--execute")
    raw_args = append_option_if_missing(raw_args, "--archive-path", str(ARCHIVE_PATH))
    raw_args = append_option_if_missing(raw_args, "--jobs", DEFAULT_QUEUE_JOBS)

    data = require_success("execute-queue", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data
    results = data.get("results", [])
    summary = {
        "total": len(results) if isinstance(results, list) else 0,
        "succeeded": sum(1 for item in results if isinstance(item, dict) and item.get("ok") is True),
        "failed": sum(1 for item in results if isinstance(item, dict) and item.get("ok") is False),
        "skipped": sum(1 for item in results if isinstance(item, dict) and item.get("skipped")),
    }
    payload = base_payload("execute-queue")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "executed": True,
            "manifest_path": str(manifest_path),
            "manifest_sha256": archive_sha256(manifest_path),
            "archive_path": str(ARCHIVE_PATH),
            "summary": summary,
            "results": results,
            "wrapper_consumed_flags": ["--yes"],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    payload["result_path"] = write_run_json("queue-result", payload)
    return payload


def requeue_failed(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if not args:
        return normalize_error("requeue-failed", "missing queue result JSON path", "usage", 2)
    result_path = Path(args[0]).expanduser()
    if not result_path.is_file():
        return normalize_error("requeue-failed", f"queue result JSON not found: {result_path}", "missing_result", 2)
    args = args[1:]
    args, manifest_value = consume_option_value(args, "--manifest")
    if args:
        return normalize_error("requeue-failed", f"unsupported arguments: {' '.join(args)}", "usage", 2)
    manifest_path = Path(manifest_value).expanduser() if manifest_value else RUN_DIR / f"retry-queue-{timestamp_slug()}.json"
    if manifest_path.exists() and not yes:
        return normalize_error("requeue-failed", f"manifest already exists and requires --yes: {manifest_path}", "confirmation_required", 2)
    try:
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return normalize_error("requeue-failed", f"could not parse queue result JSON: {exc}", "bad_result_json", 3)
    results = result_data.get("results")
    if not isinstance(results, list) and isinstance(result_data.get("package_payload"), dict):
        results = result_data["package_payload"].get("results")
    if not isinstance(results, list):
        return normalize_error("requeue-failed", "queue result JSON has no results list", "bad_result_json", 3)

    failed_items: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        failed = result.get("ok") is False or bool(result.get("errors"))
        if not failed or result.get("skipped"):
            continue
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        selector = plan.get("selector") if isinstance(plan.get("selector"), dict) else result.get("selector")
        if isinstance(selector, dict):
            failed_items.append(
                {
                    "selector": selector,
                    "format": plan.get("format") or result.get("format") or "epub",
                    "index": result.get("index"),
                }
            )
    manifest = {
        "items": failed_items,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "source": {"kind": "requeue-failed", "result_path": str(result_path)},
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = base_payload("requeue-failed")
    payload.update(
        {
            "ok": True,
            "manifest_path": str(manifest_path),
            "manifest_exists": manifest_path.is_file(),
            "manifest_sha256": archive_sha256(manifest_path),
            "count": len(failed_items),
            "items": failed_items,
            "wrapper_consumed_flags": ["--yes"] if yes else [],
        }
    )
    return payload


def validate_cmd(args: list[str]) -> dict[str, Any]:
    if not has_option(args, "--format"):
        args = ["--format", "auto", *args]
    return passthrough("validate", args)


def run_calibre(args: list[str], *, timeout: int = CALIBRE_TIMEOUT_SECONDS) -> dict[str, Any]:
    cmd = [str(CALIBRE_RUNNER), *args]
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout,
            env=subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "timeout": True, "command": cmd, "stdout": exc.stdout, "stderr": exc.stderr}
    payload: dict[str, Any] = {"ok": proc.returncode == 0, "returncode": proc.returncode, "command": cmd, "stdout": proc.stdout, "stderr": proc.stderr}
    try:
        payload["json"] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pass
    return payload


def duplicate_count(search_json: Any) -> int | None:
    if not isinstance(search_json, dict):
        return None
    value = search_json.get("count")
    if isinstance(value, int):
        return value
    results = search_json.get("results")
    if isinstance(results, list):
        return len(results)
    return None


def add_to_calibre(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    args, dry_flag = consume_flag(args, "--dry-run")
    args, execute = consume_flag(args, "--execute")
    args, duplicates_reviewed = consume_flag(args, "--duplicates-reviewed")
    args, allow_duplicate = consume_flag(args, "--allow-duplicate")
    args, title_override = consume_option_value(args, "--title")
    args, author_override = consume_option_value(args, "--author")
    args, tag_value = consume_option_value(args, "--tag")
    args, limit_value = consume_option_value(args, "--limit")
    if dry_flag and execute:
        return normalize_error("add-to-calibre", "choose either --dry-run or --execute, not both", "usage", 2)
    if not args:
        return normalize_error("add-to-calibre", "missing file path", "usage", 2)
    path = Path(args[0]).expanduser()
    if len(args) > 1:
        return normalize_error("add-to-calibre", f"unsupported arguments: {' '.join(args[1:])}", "usage", 2)
    if not path.is_file():
        return normalize_error("add-to-calibre", f"file not found: {path}", "missing_file", 2)
    if path.suffix.lower() not in {".epub", ".pdf"}:
        return normalize_error("add-to-calibre", "Calibre handoff accepts only EPUB/PDF", "unsupported_format", 2)
    try:
        duplicate_limit = int(limit_value) if limit_value else 5
    except ValueError:
        return normalize_error("add-to-calibre", "--limit must be an integer", "usage", 2)

    validation = validate_cmd([str(path)])
    validation_data = validation.get("validation") if isinstance(validation.get("validation"), dict) else {}
    if not validation.get("ok") or validation_data.get("ok") is False:
        payload = normalize_error("add-to-calibre", "file validation failed before Calibre handoff", "validation_failed", 2)
        payload["validation"] = validation
        return payload

    title = title_override or validation_data.get("metadata_title") or path.stem
    author = author_override or validation_data.get("metadata_creator") or "Unknown"
    tags = "vnthuquan"
    if tag_value:
        tags = f"{tags},{tag_value}"

    doctor = run_calibre(["doctor"], timeout=CALIBRE_TIMEOUT_SECONDS)
    doctor_json = doctor.get("json")
    doctor_ok = bool(doctor.get("ok") and isinstance(doctor_json, dict) and doctor_json.get("status") == "ok")
    if not doctor_ok:
        payload = normalize_error("add-to-calibre", "Calibre doctor failed or timed out", "calibre_unavailable", 2)
        payload["calibre_doctor"] = doctor_json or doctor
        payload["validation"] = validation
        return payload

    duplicate_search = run_calibre(["search", str(title), "--limit", str(duplicate_limit)], timeout=CALIBRE_TIMEOUT_SECONDS)
    duplicate_json = duplicate_search.get("json")
    dup_count = duplicate_count(duplicate_json)
    write_args = ["add", str(path), "--title", str(title), "--author", str(author), "--tag", tags]
    dry_run_args = [*write_args, "--dry-run"]
    dry_run = run_calibre(dry_run_args, timeout=CALIBRE_TIMEOUT_SECONDS)
    dry_json = dry_run.get("json")
    dry_ok = bool(dry_run.get("ok") and isinstance(dry_json, dict) and dry_json.get("status") in {"dry_run", "ok"})
    consumed_flags = [
        flag
        for flag, present in (
            ("--yes", yes),
            ("--dry-run", dry_flag),
            ("--execute", execute),
            ("--duplicates-reviewed", duplicates_reviewed),
            ("--allow-duplicate", allow_duplicate),
        )
        if present
    ]
    payload = base_payload("add-to-calibre")
    payload.update(
        {
            "ok": dry_ok,
            "dry_run": not execute,
            "dry_run_defaulted": not dry_flag and not execute,
            "executed": False,
            "write_attempted": False,
            "path": str(path),
            "format": path.suffix.lower().lstrip("."),
            "metadata": {"title": title, "author": author, "tags": tags},
            "validation": validation_data,
            "calibre_doctor": doctor_json or doctor,
            "duplicate_search_result": duplicate_json or duplicate_search,
            "duplicate_count": dup_count,
            "calibre_preflight_command": [str(CALIBRE_RUNNER), *dry_run_args],
            "calibre_preflight_result": dry_json or dry_run,
            "wrapper_consumed_flags": consumed_flags,
            "write_gate": {
                "execute_requested": execute,
                "confirmed": yes,
                "duplicates_reviewed": duplicates_reviewed,
                "allow_duplicate": allow_duplicate,
                "duplicate_count": dup_count,
            },
        }
    )
    if not dry_ok:
        payload.update({"message": "Calibre dry-run add failed", "error_code": "calibre_add_failed", "exit_code": 2})
        return payload
    if not execute:
        return payload
    payload.update({"ok": False, "dry_run": False, "calibre_write_command": [str(CALIBRE_RUNNER), *write_args]})
    if not yes:
        payload.update({"message": "Calibre write requires --execute --yes", "error_code": "confirmation_required", "exit_code": 2})
        return payload
    if dup_count is None:
        payload.update({"message": "Calibre duplicate search did not return a usable count", "error_code": "duplicate_review_unavailable", "exit_code": 2})
        return payload
    if not duplicates_reviewed:
        payload.update({"message": "Calibre write requires --duplicates-reviewed", "error_code": "duplicate_review_required", "exit_code": 2})
        return payload
    if dup_count and not allow_duplicate:
        payload.update({"message": "duplicate candidates found; use --allow-duplicate only after user approval", "error_code": "duplicate_candidates_found", "exit_code": 2})
        return payload

    write = run_calibre(write_args, timeout=CALIBRE_WRITE_TIMEOUT_SECONDS)
    write_json = write.get("json")
    write_ok = bool(write.get("ok") and isinstance(write_json, dict) and write_json.get("status") == "ok")
    result_record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "path": str(path),
        "metadata": {"title": title, "author": author, "tags": tags},
        "duplicate_count": dup_count,
        "calibre_write_result": write_json or write,
        "ok": write_ok,
    }
    result_path = write_run_json("calibre-add-result", result_record)
    log_path = append_state_jsonl("calibre-writes.jsonl", {**result_record, "result_path": result_path})
    payload.update(
        {
            "ok": write_ok,
            "executed": write_ok,
            "write_attempted": True,
            "calibre_write_result": write_json or write,
            "calibre_write_result_path": result_path,
            "calibre_write_log_path": log_path,
            "recovery_notes": [
                "Do not retry a failed Calibre write automatically.",
                "Run the Calibre skill doctor/sync workflow before retrying.",
                "Review duplicate candidates again before any second execute attempt.",
            ],
        }
    )
    if not write_ok:
        payload.update({"message": "Calibre write failed", "error_code": "calibre_write_failed", "exit_code": 2})
    return payload


def archive_cmd(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("archive", "missing archive subcommand", "usage", 2)
    if args[0] == "path":
        payload = base_payload("archive")
        payload.update({"ok": True, "subcommand": "path", "archive_path": str(ARCHIVE_PATH)})
        return payload
    if args[0] == "list":
        return passthrough("archive", ["list", "--archive-path", str(ARCHIVE_PATH), *args[1:]])
    return normalize_error("archive", f"unsupported archive subcommand: {args[0]}", "usage", 2)


def native_help(command: str, args: list[str], json_out: bool) -> int:
    status, stdout, stderr = run_pkg([command, *args], json_mode=False)
    if status != 0:
        payload = normalize_error(command, stderr.strip() or stdout.strip(), "package_error", status)
        return finish(payload, json_out)
    print(stdout, end="")
    return 0


def help_text() -> str:
    return f"""vnthuquan OpenClaw wrapper (target: {TARGET})

Usage:
  run_vnthuquan.sh <command> [args...] [--json]

Read/discovery:
  diagnose
  doctor
  mirrors list|check
  config path|show
  categories list|show
  formats
  list ...
  search ...
  show ...
  archive path|list

Write-capable:
  mirrors use|reset --yes
  config set|unset --yes
  download ... --execute --yes
  queue ... --limit N
  execute-queue MANIFEST --yes
  requeue-failed QUEUE_RESULT_JSON
  add-to-calibre PATH --execute --yes --duplicates-reviewed
"""


def main(argv: list[str]) -> int:
    ensure_dirs()
    json_out = False
    cleaned: list[str] = []
    for arg in argv:
        if arg == "--json":
            json_out = True
        else:
            cleaned.append(arg)
    if not cleaned or cleaned[0] in {"-h", "--help", "help"}:
        print(help_text())
        return 0
    if cleaned[0] == "--version":
        print(f"vnthuquan-openclaw-wrapper {WRAPPER_VERSION}")
        print(f"vnthuquan {package_version()}")
        return 0

    command, rest = cleaned[0], cleaned[1:]
    if any(arg in {"-h", "--help"} for arg in rest) and command in NATIVE_HELP_COMMANDS:
        return native_help(command, rest, json_out)
    try:
        if command == "diagnose":
            payload = diagnose()
        elif command == "doctor":
            payload = doctor()
        elif command == "mirrors":
            payload = mirrors(rest)
        elif command == "config":
            payload = config_cmd(rest)
        elif command in {"categories", "formats", "list", "search", "show"}:
            payload = passthrough(command, rest)
        elif command == "download":
            payload = download(rest)
        elif command == "queue":
            payload = queue(rest)
        elif command == "execute-queue":
            payload = execute_queue(rest)
        elif command == "requeue-failed":
            payload = requeue_failed(rest)
        elif command == "validate":
            payload = validate_cmd(rest)
        elif command == "archive":
            payload = archive_cmd(rest)
        elif command == "add-to-calibre":
            payload = add_to_calibre(rest)
        else:
            payload = normalize_error(command, f"unknown command: {command}", "usage", 2)
    except WrapperError as exc:
        payload = normalize_error(command, str(exc), exc.code, exc.exit_code)
    return finish(payload, json_out)


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
