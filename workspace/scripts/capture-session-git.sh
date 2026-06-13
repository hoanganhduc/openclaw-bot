#!/bin/bash
# SessionStart hook: record the workspace's uncommitted file set so that the
# Stop hook (check-rebuild-plan.sh) can compute session-scoped changes.
set -u

INPUT=$(cat 2>/dev/null || true)
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
session_id=""
if [[ -n "$PY" && -n "$INPUT" ]]; then
  session_id=$(printf '%s' "$INPUT" | "$PY" -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
fi
[[ -z "$session_id" ]] && exit 0

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
cd "$WORKSPACE" 2>/dev/null || exit 0

out="/{{ MODEL_ID }}${session_id}-git-baseline.txt"
git diff --name-only HEAD 2>/dev/null | sort -u > "$out" || true
exit 0
