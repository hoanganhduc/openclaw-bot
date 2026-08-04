#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-${HOME}/.openclaw/workspace}"
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
