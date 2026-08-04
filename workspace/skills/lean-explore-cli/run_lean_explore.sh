#!/usr/bin/env bash
# Direct-CLI Lean declaration search for OpenClaw (non-MCP).  The sandbox uses
# its image-owned closure; host execution uses the restored host closure.  This
# launcher never creates an environment or contacts a package index.
set -euo pipefail

WS="${OPENCLAW_WORKSPACE:-${HOME}/.openclaw/workspace}"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WS/.secrets.json}"
if [[ "$HOME" == "/workspace" && "$WS" == "/workspace" ]]; then
  VENV="/opt/coding-system/python-closure/lean-explore"
  RUNTIME_LABEL="locked OpenClaw sandbox image"
else
  VENV="${LEAN_EXPLORE_VENV:-${HOME}/.local/share/coding-system/python-closure/lean-explore}"
  RUNTIME_LABEL="host Python closure"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "lean-explore runtime missing from the $RUNTIME_LABEL: $VENV" >&2
  exit 1
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/lean_explore_cli.py" "$@"
