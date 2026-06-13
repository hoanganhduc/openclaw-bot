#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE_ROOT="$(cd -- "$ROOT/../.." && pwd)"

export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$WORKSPACE_ROOT}"
export OPENCLAW_CALLER_CWD="${OPENCLAW_CALLER_CWD:-${CODEX_CALLER_CWD:-${OLDPWD:-$PWD}}}"
export CODEX_CALLER_CWD="${CODEX_CALLER_CWD:-$OPENCLAW_CALLER_CWD}"
export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

exec python3 "$ROOT/modal_research_compute.py" "$@"
