#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE="$OPENCLAW_WORKSPACE"
elif [[ -d "{{ OPENCLAW_WORKSPACE }}" ]]; then
  WORKSPACE="{{ OPENCLAW_WORKSPACE }}"
else
  WORKSPACE="/workspace"
fi

export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WORKSPACE/.secrets.json}"
export VNTHUQUAN_TARGET="${VNTHUQUAN_TARGET:-openclaw}"
export VNTHUQUAN_STATE_DIR="${VNTHUQUAN_STATE_DIR:-$WORKSPACE/data/vnthuquan/state}"
export VNTHUQUAN_RUN_DIR="${VNTHUQUAN_RUN_DIR:-$WORKSPACE/data/vnthuquan/runs}"
export VNTHUQUAN_CACHE_DIR="${VNTHUQUAN_CACHE_DIR:-$WORKSPACE/data/vnthuquan/cache}"
export VNTHUQUAN_DOWNLOAD_DIR="${VNTHUQUAN_DOWNLOAD_DIR:-$WORKSPACE/data/vnthuquan/downloads}"
export VNTHUQUAN_CALIBRE_RUNNER="${VNTHUQUAN_CALIBRE_RUNNER:-$WORKSPACE/skills/calibre/run_cal.sh}"

if [[ -d "$WORKSPACE/.local/venv_vnthuquan/bin" ]]; then
  export PATH="$WORKSPACE/.local/venv_vnthuquan/bin:$PATH"
fi
if [[ -d "{{ USER_HOME }}/.vnthuquan_venv/bin" ]]; then
  export PATH="{{ USER_HOME }}/.vnthuquan_venv/bin:$PATH"
fi

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
SITE_PACKAGES="$WORKSPACE/.local/lib/$PY_VER/site-packages"
if [[ -d "$SITE_PACKAGES" ]]; then
  export PYTHONPATH="$SITE_PACKAGES:$WORKSPACE/.local:${PYTHONPATH:-}"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/vnthuquan_openclaw_helper.py" "$@"
