#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

exec "$PYTHON" - "$SCRIPT_DIR" "$@" <<'PY'
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


SCRIPT_DIR = Path(sys.argv[1]).resolve()
ARGV = sys.argv[2:]


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
PREFIX = Path(ARGS.prefix).expanduser().resolve()
HOME = Path.home().resolve()
REPO = Path(ARGS.repo).expanduser().resolve()
MANIFEST = Path(ARGS.manifest).expanduser().resolve() if ARGS.manifest else REPO / "REBUILD-MANIFEST.json"
if not MANIFEST.exists():
    raise SystemExit(f"manifest not found: {MANIFEST}")

with MANIFEST.open("r", encoding="utf-8") as f:
    manifest = json.load(f)

if ARGS.apply:
    TARGET = REPO
else:
    TARGET = Path(ARGS.staging).expanduser().resolve() if ARGS.staging else Path(tempfile.gettempdir()) / "openclaw-bot-staging"
    if TARGET.exists():
        shutil.rmtree(TARGET)
TARGET.mkdir(parents=True, exist_ok=True)

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
# Exact JSON field names whose VALUE is always a credential/identifier, even
# though the field name does not contain a "sensitive" word (these are the
# fields that leaked the Google + Z.AI keys: {"key": "...","type":"api_key"}).
SECRET_FIELD_NAMES = {"key", "apikey", "token", "accountid", "ownerid",
                      "clientid", "clientsecret", "bearer", "sessionid",
                      "serviceaccount", "serviceaccountfile"}
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
if os.path.exists(_DENYLIST_PATH):
    with open(_DENYLIST_PATH) as _fh:
        PRIVATE_LITERALS = sorted(
            (l.strip() for l in _fh if len(l.strip()) >= 4), key=len, reverse=True)


def rel_match(rel, patterns):
    rel = rel.replace(os.sep, "/")
    return any(pat == "**/*" or fnmatch.fnmatch(rel, pat) for pat in patterns)


def is_probably_text(path):
    if path.suffix in TEXT_EXTS:
        return True
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
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
    text = re.sub(r"BEGIN [A-Z ]*PRIVATE KEY", "BEGIN {{ PRIVATE_KEY_TYPE }}", text)
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
    """Decision 10 (coding-system-rebuild, 2026-06-12): strip the personal
    deepseek plugin/provider wiring from the public openclaw.json template.
    The custom plugin lived in the excluded ~/openclaw-src checkout; the
    rebuilt config must not reference it. Model primaries pointing at
    deepseek/* become {{ DEFAULT_PRIMARY_MODEL }} placeholders."""
    if not isinstance(data, dict):
        return data
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        load = plugins.get("load")
        if isinstance(load, dict) and isinstance(load.get("paths"), list):
            load["paths"] = [p for p in load["paths"] if "openclaw-src" not in str(p)]
        if isinstance(plugins.get("allow"), list):
            plugins["allow"] = [p for p in plugins["allow"] if p != "deepseek"]
        if isinstance(plugins.get("entries"), dict):
            plugins["entries"].pop("deepseek", None)
    models = data.get("models")
    if isinstance(models, dict) and isinstance(models.get("providers"), dict):
        models["providers"].pop("deepseek", None)
    auth = data.get("auth")
    if isinstance(auth, dict) and isinstance(auth.get("profiles"), dict):
        auth["profiles"].pop("deepseek:default", None)

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


def render_file(src, dest, template):
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(src.stat().st_mode)
    if template == "secrets-keys":
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rendered = {key: "" for key in sorted(data.keys())}
            else:
                rendered = {}
        except Exception:
            rendered = {}
        dest.write_text(json.dumps(rendered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif template == "openclaw-json-sanitize" and src.suffix == ".json":
        data = json.loads(src.read_text(encoding="utf-8"))
        data = sanitize_openclaw_config(data)
        dest.write_text(json.dumps(redact_json_obj(data), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif template in {"json-redact", "json-or-text-redact"} and src.suffix == ".json":
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            dest.write_text(json.dumps(redact_json_obj(data), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            dest.write_text(redact_text(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    elif template in {"text-redact", "json-or-text-redact"} and is_probably_text(src):
        dest.write_text(redact_text(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    elif is_probably_text(src):
        dest.write_text(redact_text(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    else:
        shutil.copy2(src, dest)
    os.chmod(dest, mode)


def iter_tree_files(root, include, exclude):
    include = include or ["**/*"]
    exclude = exclude or []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
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
        "deploy.sh",
        "test-roundtrip.sh",
        ".gitignore",
    ]:
        src = REPO / name
        if src.exists():
            dest = TARGET / name
            if src.resolve() == dest.resolve():
                continue  # --apply writes into the repo itself
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def scan_release_artifact():
    forbidden = manifest.get("release_checks", {}).get("forbidden_text", [])
    findings = []
    skip_names = {"REBUILD-MANIFEST.json"}
    for path in TARGET.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(TARGET).as_posix()
        if path.name in skip_names:
            continue
        if any(part in {"__pycache__", ".pytest_cache", ".venv"} for part in path.parts):
            findings.append(f"forbidden generated path: {rel}")
            continue
        if ".git" in path.parts:
            continue  # repo metadata when --apply targets a git working tree
        if not is_probably_text(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
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
        src = (base / entry["source"]).resolve()
        dest = TARGET / entry["dest"]
        optional = bool(entry.get("optional"))
        template = entry.get("template", "text-redact" if cls == "public-template" else "copy")
        if not src.exists():
            if optional:
                skipped.append(str(src))
                continue
            raise SystemExit(f"required source missing: {src}")
        if entry.get("mode") == "file":
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
