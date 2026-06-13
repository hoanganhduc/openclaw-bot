#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
export GETSCIPAPERS_SKILL_CONFIG="${GETSCIPAPERS_SKILL_CONFIG:-{{ OPENCLAW_WORKSPACE }}/data/research/getscipapers_bot/state/config.json}"
exec python3 "$SCRIPT_DIR/gsp_openclaw_helper.py" "$@"
