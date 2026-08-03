#!/usr/bin/env bash
# Wrapper that sets PYTHONPATH and runs cal.py
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Resolve workspace: /workspace inside sandbox → host path via OPENCLAW_WORKSPACE
if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE="$OPENCLAW_WORKSPACE"
elif [[ -d "{{ OPENCLAW_WORKSPACE }}" ]]; then
  WORKSPACE="{{ OPENCLAW_WORKSPACE }}"
else
  WORKSPACE="/workspace"
fi

# Dependencies are restored by the closure installer or baked into the sandbox.
# The skill must never mutate its environment or hide a failed install at runtime.
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_PACKAGES="$WORKSPACE/.local/lib/python${PY_VER}/site-packages"
export PYTHONPATH="$WORKSPACE/.local:$SITE_PACKAGES:$SKILL_DIR:${PYTHONPATH:-}"
export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WORKSPACE/.secrets.json}"

python3 -c 'import googleapiclient, google.auth, ebooklib, requests' >/dev/null \
  || { echo '{"status":"error","message":"Calibre Python dependency closure is unavailable"}' >&2; exit 2; }

exec python3 "$SKILL_DIR/cal.py" "$@"
