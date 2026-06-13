#!/usr/bin/env bash
set -euo pipefail

# Host-side worker that processes send-queue jobs.
# Watches {{ PRIVATE_DATA_DIR }}/send-queue/ for .json job files,
# sends via openclaw message send, writes .result files back.
#
# Run as: systemctl --user start send-queue-worker
# Or manually: ./send_queue_worker.sh

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
QUEUE_DIR="$WORKSPACE/data/send-queue"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"

mkdir -p "$QUEUE_DIR"

log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

claim_job_file() {
  local job_file="$1"
  local work_file="${job_file%.json}.working"
  mv "$job_file" "$work_file" 2>/dev/null || return 1
  printf '%s\n' "$work_file"
}

write_send_result() {
  local result_file="$1"
  RESULT_FILE="$result_file" \
  STATUS="${2:-}" \
  CHANNEL="${3:-}" \
  TARGET="${4:-}" \
  FILE_NAME="${5:-}" \
  MESSAGE="${6:-}" \
  OUTPUT="${7:-}" \
  python3 - <<'PY'
import json
import os

payload = {"status": os.environ["STATUS"]}
if os.environ["CHANNEL"]:
    payload["channel"] = os.environ["CHANNEL"]
if os.environ["TARGET"]:
    payload["target"] = os.environ["TARGET"]
if os.environ["FILE_NAME"]:
    payload["file"] = os.environ["FILE_NAME"]
if os.environ["MESSAGE"]:
    payload["message"] = os.environ["MESSAGE"]
if os.environ["OUTPUT"]:
    payload["output"] = os.environ["OUTPUT"]

with open(os.environ["RESULT_FILE"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

process_job() {
  local job_file="$1"
  local job_name job_id
  job_name=$(basename "$job_file")
  job_id="${job_name%.working}"
  job_id="${job_id%.json}"
  local result_file="$QUEUE_DIR/${job_id}.result"

  # Parse job
  local channel target media caption
  channel=$(python3 -c "import json; print(json.load(open('$job_file'))['channel'])")
  target=$(python3 -c "import json; print(json.load(open('$job_file'))['target'])")
  media=$(python3 -c "import json; print(json.load(open('$job_file'))['media'])")
  caption=$(python3 -c "import json; print(json.load(open('$job_file')).get('caption',''))")

  # Convert sandbox path to host path
  local host_media="${media/\/workspace/$WORKSPACE}"

  if [[ ! -f "$host_media" ]]; then
    write_send_result "$result_file" "error" "$channel" "$target" "" "File not found on host: $host_media" ""
    log "FAIL $job_id: file not found $host_media"
    rm -f "$job_file"
    return
  fi

  log "SEND $job_id: $channel -> $target ($(basename "$host_media"))"

  # Build command
  local cmd=("$OPENCLAW_BIN" message send --channel "$channel" --target "$target" --media "$host_media")
  if [[ -n "$caption" ]]; then
    cmd+=(-m "$caption")
  fi

  # Execute
  local output
  if output=$("${cmd[@]}" 2>&1); then
    write_send_result "$result_file" "ok" "$channel" "$target" "$(basename "$host_media")" "" "$(echo "$output" | head -1)"
    log "OK   $job_id: sent"
  else
    write_send_result "$result_file" "error" "$channel" "$target" "" "openclaw send failed" "$(echo "$output" | head -1)"
    log "FAIL $job_id: $output"
  fi

  rm -f "$job_file"
}

log "Send queue worker started. Watching $QUEUE_DIR"

while true; do
  for job_file in "$QUEUE_DIR"/*.json; do
    [[ -f "$job_file" ]] || continue
    claimed_job=$(claim_job_file "$job_file") || continue
    process_job "$claimed_job"
  done
  sleep 2
done
