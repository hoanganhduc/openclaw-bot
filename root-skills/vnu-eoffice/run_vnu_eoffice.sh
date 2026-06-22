#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -d /workspace && -d /workspace/skills ]]; then
  export VNU_EOFFICE_REPO="${VNU_EOFFICE_REPO:-/workspace/vnueoffice_repo}"
  export VNU_OPENCLAW_DATA_DIR="${VNU_OPENCLAW_DATA_DIR:-{{ PRIVATE_DATA_DIR }}/vnu_eoffice}"
  if [[ -f /workspace/secrets/vnu-eoffice/secrets.json ]]; then
    export VNU_SECRETS_FILE="${VNU_SECRETS_FILE:-/workspace/secrets/vnu-eoffice/secrets.json}"
  fi
  if [[ -d /workspace/.local ]]; then
    export PYTHONPATH="/workspace/.local${PYTHONPATH:+:${PYTHONPATH}}"
  fi
else
  export VNU_EOFFICE_REPO="${VNU_EOFFICE_REPO:-{{ USER_HOME }}/vnueoffice}"
  export VNU_OPENCLAW_DATA_DIR="${VNU_OPENCLAW_DATA_DIR:-{{ OPENCLAW_WORKSPACE }}/data/vnu_eoffice}"
  if [[ -f {{ OPENCLAW_WORKSPACE }}/secrets/vnu-eoffice/secrets.json ]]; then
    export VNU_SECRETS_FILE="${VNU_SECRETS_FILE:-{{ OPENCLAW_WORKSPACE }}/secrets/vnu-eoffice/secrets.json}"
  fi
fi

export VNU_ITEMS_FILE="${VNU_ITEMS_FILE:-${VNU_OPENCLAW_DATA_DIR}/state/last_items.json}"
export PYTHONPATH="${VNU_EOFFICE_REPO}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 "${SCRIPT_DIR}/vnu_eoffice_openclaw.py" "$@"
