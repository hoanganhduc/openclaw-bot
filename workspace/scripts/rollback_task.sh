#!/bin/bash
# Task Rollback Script for OpenClaw workspace
# Usage:
#   rollback_task.sh start "task description"   -- snapshot before task
#   rollback_task.sh stop [--force]             -- rollback and clear task
#   rollback_task.sh done                        -- mark complete without rollback
#   rollback_task.sh status                      -- show active task info
#   rollback_task.sh checkpoint                  -- mid-task git commit

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
# Also support host-side path
if [ ! -d "$WORKSPACE" ]; then
  WORKSPACE="${OPENCLAW_HOME:-$HOME/.openclaw}/workspace"
fi
CONTROL_FILE="$WORKSPACE/_control/current_task.json"

case "$1" in
  start)
    TASK_NAME="${2:-unnamed task}"
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    cd "$WORKSPACE" || { echo "ERROR: Cannot cd to $WORKSPACE"; exit 1; }

    # Ensure git repo exists with at least one commit
    if ! git rev-parse HEAD &>/dev/null; then
      echo "WARNING: No git commits yet. Creating initial commit first..."
      git add -A
      git commit -m "Initial workspace snapshot (auto by rollback_task.sh)" || true
    fi

    COMMIT_SHA=$(git rev-parse HEAD)

    # Warn if there are uncommitted changes — they will be discarded by 'stop'
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "WARNING: Uncommitted changes detected. These will be DISCARDED if you run 'stop'."
      echo "         Run 'checkpoint' first to preserve them, or they will be lost."
    fi

    mkdir -p "$(dirname "$CONTROL_FILE")"
    python3 - "$CONTROL_FILE" "$TASK_NAME" "$COMMIT_SHA" "$TIMESTAMP" "$WORKSPACE" <<'PY'
import json
import sys

path, task, commit, started, workspace = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as f:
    json.dump(
        {"task": task, "commit": commit, "started": started, "workspace": workspace},
        f,
        indent=2,
    )
    f.write("\n")
PY
    echo "✓ Task snapshot created"
    echo "  Task   : $TASK_NAME"
    echo "  Commit : $COMMIT_SHA"
    echo "  Started: $TIMESTAMP"
    ;;

  stop)
    FORCE=false
    if [ "${2:-}" = "--force" ]; then
      FORCE=true
    fi

    if [ ! -f "$CONTROL_FILE" ]; then
      echo "No active task found (no control file at $CONTROL_FILE)"
      exit 1
    fi

    TASK=$(python3 - "$CONTROL_FILE" <<'PY' 2>/dev/null || echo "unknown"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("task", "unknown"))
PY
)
    COMMIT=$(python3 - "$CONTROL_FILE" <<'PY' 2>/dev/null || echo ""
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("commit", ""))
PY
)

    cd "$WORKSPACE" || { echo "ERROR: Cannot cd to $WORKSPACE"; exit 1; }

    if [ -z "$COMMIT" ]; then
      echo "ERROR: No commit SHA in control file. Cannot rollback."
      exit 1
    fi

    echo "Rolling back task: $TASK"
    echo "Restoring to commit: $COMMIT"
    echo ""
    echo "Changes that will be discarded:"
    git diff --stat HEAD
    git status --short
    git clean -ndx
    echo ""

    if ! $FORCE; then
      read -r -p "Discard all changes and roll back? [y/N] " CONFIRM
      if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "Aborted. Run with --force to skip this prompt."
        exit 0
      fi
    fi

    # Hard reset to pre-task commit
    git reset --hard "$COMMIT"
    git clean -fdx

    # Verify rollback succeeded
    ACTUAL_SHA=$(git rev-parse HEAD)
    if [ "$ACTUAL_SHA" != "$COMMIT" ]; then
      echo "ERROR: HEAD is $ACTUAL_SHA but expected $COMMIT — rollback failed"
      exit 1
    fi

    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "WARNING: Working tree is not clean after rollback — unexpected files may remain"
    fi

    rm -f "$CONTROL_FILE"
    echo ""
    echo "✓ Rollback complete. Workspace at $COMMIT"
    ;;

  status)
    if [ ! -f "$CONTROL_FILE" ]; then
      echo "No active task"
    else
      echo "Active task:"
      cat "$CONTROL_FILE"
      echo ""
      echo "Changes since task started:"
      cd "$WORKSPACE" && git status --short
    fi
    ;;

  done)
    # Mark task complete — clears control file without rollback
    if [ ! -f "$CONTROL_FILE" ]; then
      echo "No active task to mark done"
      exit 0
    fi
    TASK=$(python3 - "$CONTROL_FILE" <<'PY' 2>/dev/null || echo "unknown"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("task", "unknown"))
PY
)
    rm -f "$CONTROL_FILE"
    echo "✓ Task '$TASK' marked complete. Control file cleared."
    ;;

  checkpoint)
    # Create a mid-task git commit to preserve current state
    TASK="unknown"
    if [ -f "$CONTROL_FILE" ]; then
      TASK=$(python3 - "$CONTROL_FILE" <<'PY' 2>/dev/null || echo "unknown"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("task", "unknown"))
PY
)
    fi
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    cd "$WORKSPACE" || { echo "ERROR: Cannot cd to $WORKSPACE"; exit 1; }

    git add -A
    if git diff --cached --quiet; then
      echo "No changes to checkpoint"
    else
      git commit -m "Mid-task checkpoint [$TASK] at $TIMESTAMP"
      echo "✓ Checkpoint committed: $TASK at $TIMESTAMP"
    fi
    ;;

  *)
    echo "Usage: $0 {start 'task description'|stop [--force]|done|status|checkpoint}"
    exit 1
    ;;
esac
