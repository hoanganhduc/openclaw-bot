#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"
if [[ "${1:-}" == "--apply" ]]; then
  MODE="apply"
elif [[ "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  ./openclaw_googlechat_unthread.sh          # dry-run only
  ./openclaw_googlechat_unthread.sh --apply  # write changes

Optional env overrides:
  OPENCLAW_CONFIG=/full/path/to/openclaw.json
  OPENCLAW_PKG_ROOT=/path/to/openclaw/package/root
EOF
  exit 0
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 1
  }
}

require_cmd python3
require_cmd diff
require_cmd grep
require_cmd find

TS="$(date +%Y%m%d-%H%M%S)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

detect_config() {
  local c
  local candidates=(
    "${OPENCLAW_CONFIG:-}"
    "./openclaw.json"
    "$HOME/.openclaw/openclaw.json"
    "$HOME/.config/openclaw/openclaw.json"
    "$HOME/.openclaw-delegue/openclaw.json"
  )
  for c in "${candidates[@]}"; do
    [[ -n "$c" && -f "$c" ]] && { echo "$c"; return 0; }
  done
  return 1
}

detect_pkg_root_from_binary() {
  command -v openclaw >/dev/null 2>&1 || return 1

  local bin real cur
  bin="$(command -v openclaw 2>/dev/null || true)"
  [[ -n "$bin" ]] || return 1

  if command -v readlink >/dev/null 2>&1; then
    real="$(readlink -f "$bin" 2>/dev/null || true)"
  fi
  if [[ -z "${real:-}" ]]; then
    real="$bin"
  fi

  cur="$(dirname "$real")"
  while [[ -n "$cur" && "$cur" != "/" ]]; do
    if [[ -f "$cur/package.json" ]]; then
      if grep -q '"name"[[:space:]]*:[[:space:]]*"openclaw"' "$cur/package.json" 2>/dev/null; then
        echo "$cur"
        return 0
      fi
    fi
    cur="$(dirname "$cur")"
  done

  return 1
}

detect_pkg_root() {
  local d nr prefix
  if [[ -n "${OPENCLAW_PKG_ROOT:-}" && -d "${OPENCLAW_PKG_ROOT}" ]]; then
    echo "${OPENCLAW_PKG_ROOT}"
    return 0
  fi

  if command -v npm >/dev/null 2>&1; then
    nr="$(npm root -g 2>/dev/null || true)"
    if [[ -n "$nr" && -d "$nr/openclaw" ]]; then
      echo "$nr/openclaw"
      return 0
    fi

    prefix="$(npm prefix -g 2>/dev/null || true)"
    if [[ -n "$prefix" && -d "$prefix/lib/node_modules/openclaw" ]]; then
      echo "$prefix/lib/node_modules/openclaw"
      return 0
    fi
  fi

  if d="$(detect_pkg_root_from_binary)"; then
    echo "$d"
    return 0
  fi

  local candidates=(
    "/usr/lib/node_modules/openclaw"
    "/usr/local/lib/node_modules/openclaw"
    "$HOME/.local/lib/node_modules/openclaw"
    "$HOME/.npm-global/lib/node_modules/openclaw"
  )
  if command -v node >/dev/null 2>&1; then
    candidates+=("$HOME/.nvm/versions/node/$(node -v 2>/dev/null)/lib/node_modules/openclaw")
  fi

  for d in "${candidates[@]}"; do
    [[ -d "$d" ]] && { echo "$d"; return 0; }
  done
  return 1
}

CONFIG_FILE="$(detect_config || true)"
PKG_ROOT="$(detect_pkg_root || true)"

if [[ -z "${CONFIG_FILE}" ]]; then
  echo "Could not find openclaw.json. Set OPENCLAW_CONFIG=/full/path/to/openclaw.json" >&2
  exit 1
fi

if [[ -z "${PKG_ROOT}" ]]; then
  echo "Could not find installed OpenClaw package root. Set OPENCLAW_PKG_ROOT=/path/to/openclaw" >&2
  exit 1
fi

log "Config file: $CONFIG_FILE"
log "Package root: $PKG_ROOT"
log "Mode: $MODE"

PATCH_LIST="$WORKDIR/patch-list.txt"
: > "$PATCH_LIST"

PATCHED_CONFIG="$WORKDIR/$(basename "$CONFIG_FILE").patched"
python3 - "$CONFIG_FILE" "$PATCHED_CONFIG" <<'PY'
import json, pathlib, sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])

data = json.loads(src.read_text())

channels = data.setdefault("channels", {})
gc = channels.setdefault("googlechat", {})

# Keep only keys accepted by current OpenClaw Google Chat config.
gc.pop("replyToModeByChatType", None)

# replyToMode is the only threading control currently referenced for Google Chat bugs.
gc["replyToMode"] = "off"

dst.write_text(json.dumps(data, indent=2) + "\n")
PY

if ! cmp -s "$CONFIG_FILE" "$PATCHED_CONFIG"; then
  echo "$CONFIG_FILE|$PATCHED_CONFIG" >> "$PATCH_LIST"
fi

mapfile -t CANDIDATE_FILES < <(
  grep -RIl \
    -e 'threadId ?? replyToId' \
    -e 'replyToId ?? threadId' \
    -e 'thread: payload.replyToId' \
    -e 'thread: payload.threadId' \
    -e 'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD' \
    "$PKG_ROOT" 2>/dev/null | sort -u
)

if [[ "${#CANDIDATE_FILES[@]}" -eq 0 ]]; then
  log "No matching runtime files found under package root."
else
  log "Found ${#CANDIDATE_FILES[@]} candidate runtime file(s)."
fi

for SRC in "${CANDIDATE_FILES[@]}"; do
  REL="${SRC#$PKG_ROOT/}"
  DST="$WORKDIR/$(echo "$REL" | tr '/' '__').patched"

  python3 - "$SRC" "$DST" <<'PY'
import pathlib, re, sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
text = src.read_text()

repls = [
    (r'\bthreadId\s*\?\?\s*replyToId\b',
     'undefined /* patched: suppress googlechat threading */'),
    (r'\breplyToId\s*\?\?\s*threadId\b',
     'undefined /* patched: suppress googlechat threading */'),
    (r'\bthread\s*:\s*payload\.replyToId\b',
     'thread: undefined /* patched: suppress googlechat threading */'),
    (r'\bthread\s*:\s*payload\.threadId\b',
     'thread: undefined /* patched: suppress googlechat threading */'),
]

for pat, rep in repls:
    text = re.sub(pat, rep, text)

text = text.replace(
    'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD',
    'MESSAGE_REPLY_OPTION_UNSPECIFIED'
)

dst.write_text(text)
PY

  if ! cmp -s "$SRC" "$DST"; then
    echo "$SRC|$DST" >> "$PATCH_LIST"
  fi
done

if [[ ! -s "$PATCH_LIST" ]]; then
  log "No changes proposed. Your build may use different file paths or different code patterns."
  exit 0
fi

log "Proposed changes:"
while IFS='|' read -r SRC DST; do
  echo
  echo "===== $SRC ====="
  diff -u "$SRC" "$DST" || true
done < "$PATCH_LIST"

if [[ "$MODE" == "dry-run" ]]; then
  cat <<EOF

Dry-run only. Review the diff above.

If it looks correct, apply it with:
  $0 --apply

EOF
  exit 0
fi

BACKUP_DIR="$HOME/.openclaw-patch-backups/$TS"
mkdir -p "$BACKUP_DIR"

log "Applying changes. Backups -> $BACKUP_DIR"
while IFS='|' read -r SRC DST; do
  mkdir -p "$BACKUP_DIR/$(dirname "${SRC#/}")"
  cp -a "$SRC" "$BACKUP_DIR/${SRC#/}"
  cp -a "$DST" "$SRC"
  echo "Applied: $SRC"
done < "$PATCH_LIST"

if command -v openclaw >/dev/null 2>&1; then
  log "Attempting gateway restart"
  if openclaw gateway restart; then
    log "Gateway restarted successfully"
  else
    log "Gateway restart command failed; restart it manually"
  fi
else
  log "openclaw CLI not found in PATH; restart gateway manually"
fi

cat <<EOF

Done.

Backups saved in:
  $BACKUP_DIR

Suggested checks:
  openclaw doctor
  tailscale funnel status
  openclaw status
  openclaw logs --follow

EOF
