#!/usr/bin/bash -p
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT
export PATH=/usr/bin:/bin

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || { echo "trusted system Python is unavailable" >&2; exit 127; }

exec "$PYTHON" -I -S -B - "$SCRIPT_DIR" "$@" <<'PY'
import argparse
import fnmatch
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(os.path.abspath(sys.argv[1]))
ARGV = sys.argv[2:]


def absolute(path):
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def open_directory_nofollow(path):
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


def read_regular_nofollow(path, maximum=64 * 1024 * 1024):
    path = absolute(path)
    parent = open_directory_nofollow(path.parent)
    descriptor = None
    try:
        path_info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
            or (path_info.st_dev, path_info.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OSError("sync source is not a bounded single-link regular file")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
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
        ):
            raise OSError("sync source changed while reading")
        return payload, stat.S_IMODE(before.st_mode)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def parse_args():
    parser = argparse.ArgumentParser(description="Sync live OpenClaw core surfaces into a sanitized rebuild repo/staging tree.")
    parser.add_argument("--prefix", default=os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
    parser.add_argument("--repo", default=str(SCRIPT_DIR))
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--staging", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Generate a temporary staging tree only. Default unless --apply is set.")
    parser.add_argument("--apply", action="store_true", help="Write generated public artifacts into --repo.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args(ARGV)
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if not args.apply:
        args.dry_run = True
    return args


ARGS = parse_args()
PREFIX = absolute(ARGS.prefix)
HOME = absolute(Path.home())
REPO = absolute(ARGS.repo)
MANIFEST = absolute(ARGS.manifest) if ARGS.manifest else REPO / "REBUILD-MANIFEST.json"
for required_root in (PREFIX, HOME, REPO):
    descriptor = open_directory_nofollow(required_root)
    os.close(descriptor)
if not os.path.lexists(MANIFEST):
    raise SystemExit(f"manifest not found: {MANIFEST}")

manifest_bytes, _manifest_mode = read_regular_nofollow(MANIFEST, maximum=4 * 1024 * 1024)
manifest = json.loads(manifest_bytes.decode("utf-8"))

STAGING_MARKER = ".openclaw-bot-staging"

if ARGS.apply:
    TARGET = REPO
else:
    TARGET = absolute(ARGS.staging) if ARGS.staging else Path(tempfile.mkdtemp(prefix="openclaw-bot-staging."))
    if TARGET.exists():
        marker = TARGET / STAGING_MARKER
        if ARGS.staging and not marker.exists():
            raise SystemExit(f"refusing to remove unmarked staging path: {TARGET}")
        shutil.rmtree(TARGET)
TARGET.mkdir(parents=True, exist_ok=True)
if not ARGS.apply:
    (TARGET / STAGING_MARKER).write_text("generated by openclaw-bot sync dry-run\n", encoding="utf-8")

BASES = {
    "openclaw": PREFIX,
    "home": HOME,
}

TEXT_EXTS = {
    ".bash", ".cjs", ".conf", ".css", ".env", ".fish", ".html", ".ini",
    ".js", ".json", ".md", ".mjs", ".ps1", ".py", ".sage", ".sh",
    ".service", ".timer", ".toml", ".ts", ".txt", ".yaml", ".yml", ".zsh"
}

SENSITIVE_KEY_RE = re.compile(r"(secret|token|password|credential|private|api[_-]?key|auth|cookie|access|refresh|jwt|allow[_-]?from|pairing|chat[_-]?id|audience)", re.I)
REQUIRED_HOST_SECRET_TEMPLATE_KEYS = {"TELEGRAM_CHAT_ID"}
# Exact JSON field names whose VALUE is always a credential/identifier, even
# though the field name does not contain a "sensitive" word (these are the
# fields that leaked the Google + Z.AI keys: {"key": "...","type":"api_key"}).
SECRET_FIELD_NAMES = {"key", "apikey", "token", "accountid", "ownerid",
                      "clientid", "clientsecret", "bearer", "sessionid",
                      "serviceaccount", "serviceaccountfile", "appprincipal"}
TAILNET_URL_RE = re.compile(r"https://[a-z0-9-]+\.tail[0-9a-f]+\.ts\.net")
# Value-shaped secret patterns (redacted regardless of field name):
GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")
OPAQUE_KEY_RE = re.compile(r"\b[0-9a-f]{16,}\.[0-9A-Za-z]{8,}\b")  # e.g. zai <hex>.<suffix>
MODEL_ID_RE = re.compile(r"\b[a-z0-9][a-z0-9_.-]*/(?:claude|gpt|glm|kimi|deepseek|qwen|llama|mistral|gemini|opus|sonnet)[A-Za-z0-9_.:+/-]*", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
LONG_ID_RE = re.compile(r"\b(?:spaces/[A-Za-z0-9_-]+|[0-9]{8,}|[A-Za-z0-9_-]{24,})\b")
SECRET_PREFIXES = ["s" + "k-", "g" + "sk_", "p" + "plx-"]
SECRET_VALUE_RE = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in SECRET_PREFIXES) + r")[A-Za-z0-9_-]{8,}\b")

# Private owner denylist (literal personal IDs: chat ids, app ids, agent names...)
# — replaced with {{ PRIVATE_ID }} in every public artifact (plan §4).
_DENYLIST_PATH = os.environ.get(
    "OPENCLAW_PRIVATE_DENYLIST",
    os.path.join(str(Path.home()), ".config/coding-system/leak-denylist.txt"))
PRIVATE_LITERALS = []
if os.path.lexists(_DENYLIST_PATH):
    denylist_payload, _denylist_mode = read_regular_nofollow(
        _DENYLIST_PATH, maximum=1024 * 1024
    )
    PRIVATE_LITERALS = sorted(
        (
            line.strip()
            for line in denylist_payload.decode("utf-8").splitlines()
            if len(line.strip()) >= 4
        ),
        key=len,
        reverse=True,
    )
elif ARGS.apply:
    raise SystemExit(f"private denylist missing; refusing --apply: {_DENYLIST_PATH}")
else:
    print(f"WARN: private denylist missing; personal literal masking disabled: {_DENYLIST_PATH}", file=sys.stderr)


def rel_match(rel, patterns):
    rel = rel.replace(os.sep, "/")
    return any(pat == "**/*" or fnmatch.fnmatch(rel, pat) for pat in patterns)


def is_probably_text(path, payload=None):
    if path.suffix in TEXT_EXTS:
        return True
    try:
        chunk = (payload if payload is not None else read_regular_nofollow(path)[0])[:4096]
        chunk.decode("utf-8")
        return True
    except Exception:
        return False


def redact_text(text):
    replacements = [
        (str(PREFIX / "workspace"), "{{ OPENCLAW_WORKSPACE }}"),
        (str(PREFIX), "{{ OPENCLAW_HOME }}"),
        (str(HOME), "{{ USER_HOME }}"),
        ("/workspace/data/" + "writing-style.md", "{{ WRITING_STYLE_FILE }}"),
        ("data/" + "writing-style.md", "{{ WRITING_STYLE_FILE }}"),
        ("/workspace/data", "{{ PRIVATE_DATA_DIR }}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    for literal in PRIVATE_LITERALS:
        text = text.replace(literal, "{{ PRIVATE_ID }}")
    text = EMAIL_RE.sub("{{ EMAIL }}", text)
    text = TAILNET_URL_RE.sub("{{ FUNNEL_BASE_URL }}", text)
    text = GOOGLE_KEY_RE.sub("{{ SECRET_VALUE }}", text)
    text = OPAQUE_KEY_RE.sub("{{ SECRET_VALUE }}", text)
    text = MODEL_ID_RE.sub("{{ MODEL_ID }}", text)
    text = SECRET_VALUE_RE.sub("{{ SECRET_VALUE }}", text)
    text = re.sub(
        r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----.*?-----END \1-----",
        "{{ PRIVATE_KEY_BLOCK }}",
        text,
        flags=re.S,
    )
    return text


def redact_json_obj(obj, key_name=""):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if SENSITIVE_KEY_RE.search(str(key)) or str(key).lower() in SECRET_FIELD_NAMES:
                if isinstance(value, dict):
                    out[key] = {k: "{{ REDACTED }}" for k in value.keys()}
                elif isinstance(value, list):
                    out[key] = []
                elif value is None:
                    out[key] = None
                else:
                    out[key] = "{{ REDACTED }}"
            elif key in {"state", "lastRun", "lastError", "lastEventId", "offset"}:
                out[key] = None
            else:
                out[key] = redact_json_obj(value, str(key))
        return out
    if isinstance(obj, list):
        return [redact_json_obj(item, key_name) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def sanitize_openclaw_config(data):
    """Strip obsolete local checkout references while preserving bundled provider
    and plugin wiring. The old custom plugin lived in excluded openclaw-src
    paths; bundled DeepSeek config is no longer removed."""
    if not isinstance(data, dict):
        return data
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        load = plugins.get("load")
        if isinstance(load, dict) and isinstance(load.get("paths"), list):
            load["paths"] = [p for p in load["paths"] if "openclaw-src" not in str(p)]

    def replace_models(obj):
        if isinstance(obj, dict):
            # dict KEYS may be model ids too (agents.defaults.models map)
            return {k: ("{{ DEFAULT_PRIMARY_MODEL }}"
                        if isinstance(v, str) and v.startswith("deepseek/")
                        else replace_models(v))
                    for k, v in obj.items()
                    if not (isinstance(k, str) and k.startswith("deepseek/"))}
        if isinstance(obj, list):
            return [("{{ DEFAULT_PRIMARY_MODEL }}"
                     if isinstance(i, str) and i.startswith("deepseek/")
                     else replace_models(i)) for i in obj]
        return obj
    agents = data.get("agents")
    if isinstance(agents, dict):
        data["agents"] = replace_models(agents)
    return data


def write_target_nofollow(dest, payload, mode):
    dest = absolute(dest)
    target = absolute(TARGET)
    try:
        relative = dest.relative_to(target)
    except ValueError as exc:
        raise SystemExit(f"sync destination escapes target: {dest}") from exc
    root = open_directory_nofollow(target)
    descriptor = root
    try:
        for component in relative.parts[:-1]:
            try:
                os.mkdir(component, 0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            if descriptor != root:
                os.close(descriptor)
            descriptor = next_descriptor
        try:
            existing = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise SystemExit(f"refusing unsafe sync destination: {dest}")
        temporary = f".{relative.name}.sync.{os.getpid()}.{os.urandom(8).hex()}"
        output = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode & ~0o022,
            dir_fd=descriptor,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(output, view)
                view = view[written:]
            os.fchmod(output, mode & ~0o022)
            os.fsync(output)
        finally:
            os.close(output)
        os.replace(temporary, relative.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        if descriptor != root:
            os.close(descriptor)
        os.close(root)


def render_file(src, dest, template):
    raw, mode = read_regular_nofollow(src)
    text_payload = None
    if template == "secrets-keys":
        try:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                keys = set(data)
            else:
                keys = set()
        except Exception:
            keys = set()
        keys.update(REQUIRED_HOST_SECRET_TEMPLATE_KEYS)
        rendered = {key: "" for key in sorted(keys)}
        text_payload = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
    elif template == "openclaw-json-sanitize" and src.suffix == ".json":
        data = json.loads(raw.decode("utf-8"))
        data = sanitize_openclaw_config(data)
        text_payload = json.dumps(redact_json_obj(data), indent=2, sort_keys=True) + "\n"
    elif template in {"json-redact", "json-or-text-redact"} and src.suffix == ".json":
        try:
            data = json.loads(raw.decode("utf-8"))
            text_payload = json.dumps(redact_json_obj(data), indent=2, sort_keys=True) + "\n"
        except Exception:
            text_payload = redact_text(raw.decode("utf-8", errors="replace"))
    elif template in {"text-redact", "json-or-text-redact"} and is_probably_text(src, raw):
        text_payload = redact_text(raw.decode("utf-8", errors="replace"))
    elif is_probably_text(src, raw):
        text_payload = redact_text(raw.decode("utf-8", errors="replace"))
    else:
        write_target_nofollow(dest, raw, mode)
        return
    write_target_nofollow(dest, text_payload.encode("utf-8"), mode)


def iter_tree_files(root, include, exclude):
    if root.is_symlink() or not root.is_dir():
        raise SystemExit(f"refusing unsafe allowlisted tree root: {root}")
    root_descriptor = open_directory_nofollow(root)
    os.close(root_descriptor)
    include = include or ["**/*"]
    exclude = exclude or []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            if rel_match(rel, exclude) or not rel_match(rel, include):
                continue
            raise SystemExit(f"refusing symlink under allowlisted tree: {root / rel}")
        if not path.is_file():
            continue
        if rel_match(rel, exclude):
            continue
        if not rel_match(rel, include):
            continue
        yield path, rel


def copy_control_files():
    for name in [
        "REBUILD-MANIFEST.json",
        "sync.sh",
        "install.sh",
        "backup.sh",
        "restore.sh",
        "scripts/file_delivery.py",
        "scripts/queue_boundary.py",
        "scripts/email_delivery.py",
        "scripts/harden_runtime_ancestors.py",
        "scripts/host_exec.py",
        "scripts/openclaw_host_cli.py",
        "scripts/owner_archive.py",
        "scripts/openclaw_auth_closure.py",
        "scripts/owner_state_lock.py",
        "scripts/private_tmp.py",
        "scripts/restore_transaction.py",
        "scripts/run_host_command.py",
        "scripts/service_transaction.py",
        "config/file-delivery-policy.json.template",
        "config/email-policy.json.template",
        "deploy.sh",
        "test-roundtrip.sh",
        ".gitignore",
    ]:
        src = REPO / name
        if os.path.lexists(src):
            information = src.lstat()
            if not stat.S_ISREG(information.st_mode) or stat.S_ISLNK(information.st_mode):
                raise SystemExit(f"refusing unsafe control artifact: {src}")
            dest = TARGET / name
            if absolute(src) == absolute(dest):
                continue  # --apply writes into the repo itself
            payload, mode = read_regular_nofollow(src)
            write_target_nofollow(dest, payload, mode)


def scan_release_artifact():
    forbidden = manifest.get("release_checks", {}).get("forbidden_text", [])
    forbidden_path_parts = set(manifest.get("release_checks", {}).get("forbidden_path_parts", []))
    findings = []
    skip_names = {"REBUILD-MANIFEST.json"}
    for path in TARGET.rglob("*"):
        if path.is_symlink():
            findings.append(f"forbidden symlink in release artifact: {path.relative_to(TARGET)}")
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(TARGET).as_posix()
        if path.name in skip_names:
            continue
        rel_parts = path.relative_to(TARGET).parts
        if ".git" in rel_parts:
            continue  # repo metadata when --apply targets a git working tree
        parts = set(rel_parts)
        generated_path_parts = {"__pycache__", ".pytest_cache", ".venv"}
        if parts & (generated_path_parts | forbidden_path_parts):
            findings.append(f"forbidden generated path: {rel}")
            continue
        try:
            payload, _mode = read_regular_nofollow(path)
        except OSError as exc:
            findings.append(f"unsafe release artifact file: {rel}")
            continue
        if not is_probably_text(path, payload):
            continue
        text = payload.decode("utf-8", errors="replace")
        for needle in forbidden:
            if not needle:
                continue
            if needle in SECRET_PREFIXES:
                found = re.search(r"\b" + re.escape(needle) + r"[A-Za-z0-9_-]{8,}\b", text) is not None
            else:
                found = needle in text
            if found:
                findings.append(f"forbidden text {needle!r} in {rel}")
    return findings


def main():
    copied = []
    skipped = []
    copy_control_files()
    for entry in manifest.get("classifications", []):
        cls = entry.get("class")
        if cls not in {"public-copy", "public-template"}:
            continue
        base = BASES.get(entry.get("base", "openclaw"))
        if base is None:
            raise SystemExit(f"unknown base in manifest entry: {entry}")
        source_value = entry.get("source")
        if not isinstance(source_value, str):
            raise SystemExit(f"manifest source is invalid: {entry}")
        source_relative = Path(source_value)
        if source_relative.is_absolute() or ".." in source_relative.parts:
            raise SystemExit(f"manifest source escapes its declared base: {entry}")
        src = absolute(base / source_relative)
        if os.path.commonpath((os.fspath(base), os.fspath(src))) != os.fspath(base):
            raise SystemExit(f"manifest source escapes its declared base: {entry}")
        destination_value = entry.get("dest")
        if not isinstance(destination_value, str):
            raise SystemExit(f"manifest destination is invalid: {entry}")
        destination_relative = Path(destination_value)
        if destination_relative.is_absolute() or ".." in destination_relative.parts:
            raise SystemExit(f"manifest destination escapes the target: {entry}")
        dest = TARGET / destination_relative
        optional = bool(entry.get("optional"))
        template = entry.get("template", "text-redact" if cls == "public-template" else "copy")
        if not os.path.lexists(src):
            if optional:
                skipped.append(str(src))
                continue
            raise SystemExit(f"required source missing: {src}")
        if src.is_symlink():
            raise SystemExit(f"refusing symlinked manifest source: {src}")
        if entry.get("mode") == "file":
            information = src.lstat()
            if not stat.S_ISREG(information.st_mode):
                raise SystemExit(f"manifest file source is not regular: {src}")
            render_file(src, dest, template)
            copied.append(dest.relative_to(TARGET).as_posix())
        elif entry.get("mode") == "tree":
            for item, rel in iter_tree_files(src, entry.get("include"), entry.get("exclude")):
                out = dest / rel
                render_file(item, out, template)
                copied.append(out.relative_to(TARGET).as_posix())
        else:
            raise SystemExit(f"unsupported mode in manifest entry: {entry}")

    findings = scan_release_artifact()
    summary = {
        "mode": "apply" if ARGS.apply else "dry-run",
        "target": str(TARGET),
        "copied_count": len(copied),
        "skipped_optional_count": len(skipped),
        "findings": findings,
    }
    if ARGS.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"mode: {summary['mode']}")
        print(f"target: {summary['target']}")
        print(f"copied: {summary['copied_count']}")
        if skipped:
            print(f"skipped optional: {len(skipped)}")
        if findings:
            print("release check findings:")
            for finding in findings:
                print(f"  - {finding}")
    if findings:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
PY
