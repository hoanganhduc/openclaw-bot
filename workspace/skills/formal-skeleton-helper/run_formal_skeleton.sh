#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
# Ensure workspace-local site-packages are visible (needed in sandbox containers)
for sp in "${HOME}/.local/lib"/python*/site-packages; do
  [[ -d "$sp" ]] && export PYTHONPATH="${sp}:${PYTHONPATH:-}" && break
done
if [[ -x "{{ USER_HOME }}/.venvs/bin/python" ]]; then
  exec "{{ USER_HOME }}/.venvs/bin/python" "$SCRIPT_DIR/formal_skeleton_helper.py" "$@"
elif [[ -x "{{ OPENCLAW_WORKSPACE }}/research/alerts/.research-skills-venv/bin/python" ]]; then
  exec "{{ OPENCLAW_WORKSPACE }}/research/alerts/.research-skills-venv/bin/python" "$SCRIPT_DIR/formal_skeleton_helper.py" "$@"
fi
exec python3 "$SCRIPT_DIR/formal_skeleton_helper.py" "$@"
