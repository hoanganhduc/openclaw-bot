#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${OPENCLAW_WORKSPACE:-}"
if [[ -z "$WORKSPACE" ]]; then
  case "$SCRIPT_DIR" in
    */workspace/skills/*) WORKSPACE="${SCRIPT_DIR%%/skills/*}" ;;
    *) WORKSPACE="${HOME}/.openclaw/workspace" ;;
  esac
fi
if [[ "$WORKSPACE" == "/workspace" && "${HOME:-}" == "/workspace" ]]; then
  ENTRYPOINT="/usr/local/bin/getscipapers"
  [[ -x "$ENTRYPOINT" ]] || {
    echo "getscipapers runtime missing from the locked OpenClaw sandbox image" >&2
    exit 127
  }
  exec "$ENTRYPOINT" "$@"
fi

VENV="{{ USER_HOME }}/.local/share/coding-system/python-closure/getscipapers"
PYTHON="$VENV/bin/python"
ENTRYPOINT="$VENV/bin/getscipapers"

[[ -x "$PYTHON" && -f "$ENTRYPOINT" ]] || {
  echo "getscipapers runtime missing: $VENV" >&2
  exit 127
}
exec "$PYTHON" "$ENTRYPOINT" "$@"
