#!/usr/bin/bash -p
# Non-destructive task marker for the OpenClaw workspace.
# Usage:
#   rollback_task.sh start "task description"   -- snapshot before task
#   rollback_task.sh stop                       -- refuse automatic rollback
#   rollback_task.sh done                        -- mark complete without rollback
#   rollback_task.sh status                      -- show active task info
#   rollback_task.sh checkpoint                  -- update task metadata only

set -euo pipefail
umask 077

WORKSPACE="${OPENCLAW_WORKSPACE:-/workspace}"
# Also support host-side path
if [ ! -d "$WORKSPACE" ]; then
  WORKSPACE="${OPENCLAW_HOME:-$HOME/.openclaw}/workspace"
fi
CONTROL_FILE="$WORKSPACE/_control/current_task.json"

case "${1:-}" in
  start)
    TASK_NAME="${2:-unnamed task}"
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    cd "$WORKSPACE" || { echo "ERROR: Cannot cd to $WORKSPACE"; exit 1; }

    if ! git rev-parse --verify HEAD &>/dev/null; then
      echo "ERROR: Workspace has no existing commit; refusing to create an automatic snapshot." >&2
      exit 2
    fi

    COMMIT_SHA=$(git rev-parse HEAD)

    # Report uncommitted changes without staging, committing, or discarding them.
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "NOTICE: Uncommitted changes detected; this helper will not modify them."
    fi

    mkdir -p "$(dirname "$CONTROL_FILE")"
    python3 -I -S -B - "$CONTROL_FILE" "$TASK_NAME" "$COMMIT_SHA" "$TIMESTAMP" "$WORKSPACE" <<'PY'
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
payload = {
    "task": sys.argv[2],
    "commit": sys.argv[3],
    "started": sys.argv[4],
    "workspace": sys.argv[5],
}
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
    echo "✓ Task snapshot created"
    echo "  Task   : $TASK_NAME"
    echo "  Commit : $COMMIT_SHA"
    echo "  Started: $TIMESTAMP"
    ;;

  stop)
    echo "Automatic rollback is disabled; no files were changed." >&2
    echo "Review the diff and revert only explicitly selected paths with owner approval." >&2
    exit 2
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
    TASK=$(python3 -c "import json; d=json.load(open('$CONTROL_FILE')); print(d.get('task','unknown'))" 2>/dev/null || echo "unknown")
    rm -f "$CONTROL_FILE"
    echo "✓ Task '$TASK' marked complete. Control file cleared."
    ;;

  checkpoint)
    if [ ! -f "$CONTROL_FILE" ]; then
      echo "No active task to checkpoint"
      exit 0
    fi
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    python3 -I -S -B - "$CONTROL_FILE" "$TIMESTAMP" <<'PY'
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["lastCheckpoint"] = sys.argv[2]
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
    echo "✓ Checkpoint metadata updated at $TIMESTAMP; git state unchanged"
    ;;

  *)
    echo "Usage: $0 {start 'task description'|stop|done|status|checkpoint}"
    exit 1
    ;;
esac
