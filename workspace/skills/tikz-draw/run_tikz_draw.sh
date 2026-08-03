#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export PYTHONPATH="$WORKSPACE_ROOT/.local:$WORKSPACE_ROOT:${PYTHONPATH:-}"

exec python3 "$SCRIPT_DIR/tikz_draw.py" "$@"
