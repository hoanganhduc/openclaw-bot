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
VENV="$WORKSPACE/.local/venv_getscipapers"
PYTHON="$VENV/bin/python"
ENTRYPOINT="$VENV/bin/getscipapers"

[[ -x "$PYTHON" && -f "$ENTRYPOINT" ]] || {
  echo "getscipapers runtime missing: $VENV" >&2
  exit 127
}
exec "$PYTHON" "$ENTRYPOINT" "$@"
