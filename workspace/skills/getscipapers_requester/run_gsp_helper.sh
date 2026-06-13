#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
# Ensure workspace-local site-packages are visible (needed in sandbox containers)
for sp in "${HOME}/.local/lib"/python*/site-packages; do
  [[ -d "$sp" ]] && export PYTHONPATH="${sp}:${PYTHONPATH:-}" && break
done
export GETSCIPAPERS_SKILL_CONFIG="${GETSCIPAPERS_SKILL_CONFIG:-${OPENCLAW_WORKSPACE:-/workspace}/data/research/getscipapers_bot/state/config.json}"
exec python3 "$SCRIPT_DIR/gsp_openclaw_helper.py" "$@"
