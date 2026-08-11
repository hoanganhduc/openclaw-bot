#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
TRUSTED_PYTHON=/usr/bin/python3
[[ -x "$TRUSTED_PYTHON" ]] || {
  printf 'trusted Python runtime is unavailable\n' >&2
  exit 127
}

safe_path="$WORKSPACE/.local/venv_vnthuquan/bin:{{ USER_HOME }}/.vnthuquan_venv/bin:$WORKSPACE/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
environment=(
  "HOME=${HOME:-/workspace}"
  "PATH=$safe_path"
  "OPENCLAW_WORKSPACE=$WORKSPACE"
  "AAS_RUNTIME_WORKSPACE=$WORKSPACE"
  "VNTHUQUAN_TARGET=${VNTHUQUAN_TARGET:-openclaw}"
  "VNTHUQUAN_STATE_DIR=${VNTHUQUAN_STATE_DIR:-$WORKSPACE/data/vnthuquan/state}"
  "VNTHUQUAN_RUN_DIR=${VNTHUQUAN_RUN_DIR:-$WORKSPACE/data/vnthuquan/runs}"
  "VNTHUQUAN_CACHE_DIR=${VNTHUQUAN_CACHE_DIR:-$WORKSPACE/data/vnthuquan/cache}"
  "VNTHUQUAN_DOWNLOAD_DIR=${VNTHUQUAN_DOWNLOAD_DIR:-$WORKSPACE/data/vnthuquan/downloads}"
  "VNTHUQUAN_CALIBRE_RUNNER=${VNTHUQUAN_CALIBRE_RUNNER:-$WORKSPACE/skills/calibre/run_cal.sh}"
  "VNTHUQUAN_CALIBRE_TIMEOUT_SECONDS=${VNTHUQUAN_CALIBRE_TIMEOUT_SECONDS:-45}"
  "VNTHUQUAN_CALIBRE_WRITE_TIMEOUT_SECONDS=${VNTHUQUAN_CALIBRE_WRITE_TIMEOUT_SECONDS:-180}"
  "VNTHUQUAN_QUEUE_JOBS=${VNTHUQUAN_QUEUE_JOBS:-3}"
)
for name in LANG LC_ALL LC_CTYPE TZ SSL_CERT_DIR SSL_CERT_FILE \
  VNTHUQUAN_BIN VNTHUQUAN_SOURCE_DIR; do
  [[ -z "${!name:-}" ]] || environment+=("$name=${!name}")
done

exec /usr/bin/env -i "${environment[@]}" \
  "$TRUSTED_PYTHON" -I -S -B "$SCRIPT_DIR/vnthuquan_openclaw_helper.py" "$@"
