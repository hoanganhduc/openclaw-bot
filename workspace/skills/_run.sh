#!/bin/bash
# Universal skill runner for Claude Code — sets up the OpenClaw workspace environment
# Usage: _run.sh <script> [args...]
#   e.g. _run.sh skills/zotero/run_zot.sh --json get "query"
#   e.g. _run.sh skills/sagemath/run_sage.sh "G = graphs.PetersenGraph(); print(G.chromatic_number())"

export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
export PYTHONPATH="$OPENCLAW_WORKSPACE/.local:${HOME}/.local/lib/python3.12/site-packages:$PYTHONPATH"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$HOME/.openclaw/secrets.json}"
export PATH="$HOME/.local/bin:$OPENCLAW_WORKSPACE/.local/bin:$OPENCLAW_WORKSPACE/.local/venv_getscipapers/bin:$HOME/.venvs/bin:$PATH"

cd "$OPENCLAW_WORKSPACE" || exit 1

# Resolve relative skill paths
script="$1"; shift
if [[ "$script" != /* ]]; then
    script="$OPENCLAW_WORKSPACE/$script"
fi

exec bash "$script" "$@"
