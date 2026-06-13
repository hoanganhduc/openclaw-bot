#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
WORKSPACE_ROOT="${OPENCLAW_WORKSPACE:-$(cd -- "$SCRIPT_DIR/../.." && pwd -P)}"
export OPENCLAW_WORKSPACE="$WORKSPACE_ROOT"
# Ensure workspace-local site-packages are visible (needed in sandbox containers)
for sp in "${HOME}/.local/lib"/python*/site-packages; do
  [[ -d "$sp" ]] && export PYTHONPATH="${sp}:${PYTHONPATH:-}" && break
done
if [[ -x "${HOME}/.venvs/bin/python" ]]; then
  exec "${HOME}/.venvs/bin/python" "$SCRIPT_DIR/research_digest.py" "$@"
elif [[ -x "$WORKSPACE_ROOT/research/alerts/.research-skills-venv/bin/python" ]]; then
  exec "$WORKSPACE_ROOT/research/alerts/.research-skills-venv/bin/python" "$SCRIPT_DIR/research_digest.py" "$@"
fi
exec python3 "$SCRIPT_DIR/research_digest.py" "$@"
