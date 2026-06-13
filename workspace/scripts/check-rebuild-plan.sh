#!/bin/bash
# Stop hook: reminds Claude to update the rebuild plan's DECISIONS.md if
# system files were modified DURING THIS SESSION (not by prior sessions).
#
# Session scoping: compares current uncommitted file list against a baseline
# captured by capture-session-git.sh at SessionStart. If baseline is missing,
# falls back to the full diff (old behavior).

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
PLAN="data/research/openclaw-rebuild-plan.md"

cd "$WORKSPACE" || exit 0

INPUT=$(cat 2>/dev/null || true)
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
session_id=""
if [[ -n "$PY" && -n "$INPUT" ]]; then
  session_id=$(printf '%s' "$INPUT" | "$PY" -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
fi

CURRENT=$(git diff --name-only HEAD 2>/dev/null | sort -u)
[ -z "$CURRENT" ] && exit 0

baseline="/{{ MODEL_ID }}${session_id}-git-baseline.txt"
if [ -n "$session_id" ] && [ -f "$baseline" ]; then
  CHANGED=$(comm -23 <(printf '%s\n' "$CURRENT") "$baseline")
else
  CHANGED="$CURRENT"
fi
[ -z "$CHANGED" ] && exit 0

# If the rebuild plan itself was modified this turn, nothing to do
echo "$CHANGED" | grep -qF "$PLAN" && exit 0

SYSTEM_FILES=(
    "AGENTS.md"
    "SOUL.md"
    "instruction.md"
    "openclaw.json"
)
SYSTEM_PREFIXES=(
    "skills/"
    "scripts/"
    "cron/"
    "{{ WRITING_STYLE_FILE }}"
)

triggered=""
for f in "${SYSTEM_FILES[@]}"; do
    if echo "$CHANGED" | grep -qxF "$f"; then
        triggered="$f"
        break
    fi
done

if [ -z "$triggered" ]; then
    for p in "${SYSTEM_PREFIXES[@]}"; do
        if echo "$CHANGED" | grep -qF "$p"; then
            triggered="$p"
            break
        fi
    done
fi

if [ -n "$triggered" ]; then
    msg="REBUILD PLAN NOT UPDATED: '${triggered}' was modified but the DECISIONS.md section of ${PLAN} was not updated. You MUST append a dated entry and bump the version header now."
    printf '{"stopReason":"%s"}\n' \
        "$(echo "$msg" | sed 's/"/\\"/g')"
fi
