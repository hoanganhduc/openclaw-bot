#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# Ensure workspace-local site-packages are visible (needed in sandbox containers)
for sp in "${HOME}/.local/lib"/python*/site-packages; do
  [[ -d "$sp" ]] && export PYTHONPATH="${sp}:${PYTHONPATH:-}" && break
done
exec python3 "$BASE_DIR/model_router.py" --base-dir "$BASE_DIR" "$@"
