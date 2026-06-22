#!/usr/bin/env bash
# Direct-CLI Lean declaration search for the OpenClaw sandboxed agent (non-MCP).
# Bootstraps a workspace-local venv (the sandbox has pip + outbound net) and runs
# the lean_explore API client. The venv lives under /workspace/.venvs (.stignore'd,
# not synced); the key comes from OPENCLAW_SECRETS_FILE (.secrets.json, not synced).
set -euo pipefail

WS="${OPENCLAW_WORKSPACE:-/workspace}"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WS/.secrets.json}"
VENV="$WS/.venvs/lean-explore"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

if [ ! -x "$VENV/bin/python" ]; then
  echo "lean-explore-cli: bootstrapping venv (one-time, ~30s)..." >&2
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >&2
  "$VENV/bin/python" -m pip install --quiet "lean-explore==1.2.1" >&2
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/lean_explore_cli.py" "$@"
