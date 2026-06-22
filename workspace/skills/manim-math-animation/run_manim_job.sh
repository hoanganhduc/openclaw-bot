#!/usr/bin/env bash
# Submit a manim render job to the host job-queue and wait for the result.
# The sandbox can't run manim (read-only root, no build toolchain); the host worker
# (job_queue_worker.sh, type=manim) renders via the manim venv and writes the clip
# back under /workspace. Use this instead of `run_manim_math_animation.sh render`
# inside OpenClaw. Prints the host worker's JSON result; leaves the .mp4 in place.
set -euo pipefail

WS="${OPENCLAW_WORKSPACE:-/workspace}"
QUEUE="$WS/data/manim-queue"
mkdir -p "$QUEUE"

SPEC="" OUTPUT="" QUALITY="-qh" TIMEOUT=900 SAVE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --spec) SPEC="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --quality) QUALITY="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --save) SAVE="$2"; shift 2 ;;
    -h|--help) echo "usage: run_manim_job.sh --spec <path> [--output <mp4>] [--quality -ql|-qm|-qh] [--timeout sec] [--save label]"; exit 0 ;;
    *) echo "{\"status\":\"error\",\"message\":\"unknown arg: $1\"}"; exit 2 ;;
  esac
done
[[ -n "$SPEC" ]] || { echo '{"status":"error","message":"--spec required"}'; exit 2; }

JOB_ID="manim-$(date +%Y%m%dT%H%M%S)-$$"
[[ -n "$OUTPUT" ]] || OUTPUT="{{ PRIVATE_DATA_DIR }}/manim-queue/${JOB_ID}.mp4"
JOB_FILE="$QUEUE/${JOB_ID}.json"
RESULT_FILE="$QUEUE/${JOB_ID}.result"

SPEC="$SPEC" OUTPUT="$OUTPUT" QUALITY="$QUALITY" TIMEOUT="$TIMEOUT" SAVE="$SAVE" JOB_ID="$JOB_ID" \
  python3 - "$JOB_FILE" <<'PY'
import json, os, sys
job = {
    "id": os.environ["JOB_ID"], "type": "manim", "spec": os.environ["SPEC"],
    "output": os.environ["OUTPUT"], "quality": os.environ["QUALITY"],
    "timeout": int(os.environ["TIMEOUT"]), "save_label": os.environ["SAVE"], "status": "pending",
}
json.dump(job, open(sys.argv[1], "w"))
PY

MAX_WAIT=$((TIMEOUT + 60)); WAITED=0
while [[ $WAITED -lt $MAX_WAIT ]]; do
  if [[ -f "$RESULT_FILE" ]]; then
    cat "$RESULT_FILE"
    rm -f "$JOB_FILE" "$RESULT_FILE" "$QUEUE/${JOB_ID}.cancel"
    exit 0
  fi
  sleep 2; WAITED=$((WAITED + 2))
done
echo "{\"status\":\"error\",\"message\":\"manim job timed out waiting for host worker after ${MAX_WAIT}s\",\"job_id\":\"${JOB_ID}\"}"
exit 1
