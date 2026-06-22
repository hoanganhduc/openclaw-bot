#!/usr/bin/env bash
set -euo pipefail

# Unified host-side job queue worker.
# Handles: file sending (type=send) and SageMath execution (type=sage).
# Watches two directories for .json job files.
#
# Run as: systemctl --user start send-queue-worker
# Or manually: ./job_queue_worker.sh

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
SEND_QUEUE="$WORKSPACE/data/send-queue"
JOB_QUEUE="$WORKSPACE/data/job-queue"
SAGE_OUTPUT="$WORKSPACE/data/research/sagemath"
SAGE_LOG="$SAGE_OUTPUT/run-log.jsonl"
# arm64 (this system) uses the prebuilt image; amd64 uses the official SageMath image.
case "$(uname -m)" in aarch64|arm64) SAGE_IMAGE="${SAGE_DOCKER_IMAGE:-ghcr.io/hoanganhduc/sagemath:10.8}" ;; *) SAGE_IMAGE="${SAGE_DOCKER_IMAGE:-sagemath/sagemath:10.8}" ;; esac
SAGE_CONTAINER="sagemath-worker"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OUTPUT_MAX_BYTES=1048576  # 1MB

# --- Manim (host-native render via the manim-math-animation venv; SEPARATE queue dir
#     so manim jobs never enter the sage glob on $JOB_QUEUE) ---
MANIM_QUEUE="$WORKSPACE/data/manim-queue"
MANIM_OUTPUT="$WORKSPACE/data/research/manim"
MANIM_LOG="$MANIM_OUTPUT/run-log.jsonl"
MANIM_RUNNER="$WORKSPACE/skills/manim-math-animation/run_manim_math_animation.sh"
MANIM_PYTHON="${MANIM_PYTHON:-{{ USER_HOME }}/.local/share/manim-math-animation-venv/bin/python}"
MANIM_RENDER_TIMEOUT_DEFAULT=900

mkdir -p "$SEND_QUEUE" "$JOB_QUEUE" "$SAGE_OUTPUT" "$MANIM_QUEUE" "$MANIM_OUTPUT"

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

write_sage_result() {
  local result_file="$1"
  RESULT_FILE="$result_file" \
  STATUS="${2:-}" \
  JOB_ID="${3:-}" \
  MESSAGE="${4:-}" \
  DURATION="${5:-}" \
  EXIT_CODE="${6:-}" \
  python3 - <<'PY'
import json
import os

payload = {"status": os.environ["STATUS"]}
if os.environ["JOB_ID"]:
    payload["job_id"] = os.environ["JOB_ID"]
if os.environ["MESSAGE"]:
    payload["message"] = os.environ["MESSAGE"]
if os.environ["DURATION"]:
    payload["duration_seconds"] = int(os.environ["DURATION"])
if os.environ["EXIT_CODE"]:
    payload["exit_code"] = int(os.environ["EXIT_CODE"])

with open(os.environ["RESULT_FILE"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

# --- File sending (existing logic) ---

process_send_job() {
  local job_file="$1"
  local job_name job_id
  job_name=$(basename "$job_file")
  job_id="${job_name%.working}"
  job_id="${job_id%.json}"
  local result_file="$SEND_QUEUE/${job_id}.result"

  local channel target media caption
  channel=$(python3 -c "import json; print(json.load(open('$job_file'))['channel'])")
  target=$(python3 -c "import json; print(json.load(open('$job_file'))['target'])")
  media=$(python3 -c "import json; print(json.load(open('$job_file'))['media'])")
  caption=$(python3 -c "import json; print(json.load(open('$job_file')).get('caption',''))")

  local host_media="${media/\/workspace/$WORKSPACE}"

  if [[ ! -f "$host_media" ]]; then
    write_send_result "$result_file" "error" "$channel" "$target" "" "File not found on host: $host_media" ""
    log "SEND FAIL $job_id: file not found $host_media"
    rm -f "$job_file"
    return
  fi

  log "SEND $job_id: $channel -> $target ($(basename "$host_media"))"

  local cmd=("$OPENCLAW_BIN" message send --channel "$channel" --target "$target" --media "$host_media")
  if [[ -n "$caption" ]]; then
    cmd+=(-m "$caption")
  fi

  local output
  if output=$("${cmd[@]}" 2>&1); then
    write_send_result "$result_file" "ok" "$channel" "$target" "$(basename "$host_media")" "" ""
    log "SEND OK $job_id"
  else
    write_send_result "$result_file" "error" "$channel" "$target" "" "send failed" "$(echo "$output" | head -1)"
    log "SEND FAIL $job_id: $output"
  fi

  rm -f "$job_file"
}

# --- SageMath execution ---

ensure_sage_container() {
  if ! docker inspect "$SAGE_CONTAINER" >/dev/null 2>&1; then
    log "SAGE starting persistent container"
    docker run -d --name "$SAGE_CONTAINER" --restart=unless-stopped \
      --cpus=3 --memory=16g --network=none -e SAGE_NUM_THREADS=3 \
      -v "$WORKSPACE:/workspace" \
      "$SAGE_IMAGE" tail -f /dev/null >/dev/null 2>&1
    sleep 2
  elif [[ "$(docker inspect -f '{{.State.Running}}' "$SAGE_CONTAINER" 2>/dev/null)" != "true" ]]; then
    log "SAGE restarting stopped container"
    docker start "$SAGE_CONTAINER" >/dev/null 2>&1
    sleep 2
  fi
}

process_sage_job() {
  local job_file="$1"
  local job_name job_id
  job_name=$(basename "$job_file")
  job_id="${job_name%.working}"
  job_id="${job_id%.json}"
  local result_file="$JOB_QUEUE/${job_id}.result"
  local sage_file="$JOB_QUEUE/${job_id}.sage"
  local cancel_file="$JOB_QUEUE/${job_id}.cancel"
  local plot_file="$JOB_QUEUE/${job_id}.png"
  local start_time
  start_time=$(date +%s)

  local mode job_timeout save_label is_plot
  mode=$(python3 -c "import json; print(json.load(open('$job_file'))['mode'])")
  job_timeout=$(python3 -c "import json; print(json.load(open('$job_file'))['timeout'])")
  save_label=$(python3 -c "import json; print(json.load(open('$job_file')).get('save_label',''))")
  is_plot=$(python3 -c "import json; print(json.load(open('$job_file')).get('plot', False))")

  log "SAGE $job_id: mode=$mode timeout=${job_timeout}s plot=$is_plot"

  ensure_sage_container

  # Run sage in background so we can check for cancellation
  local output exit_code
  local tmp_output="$JOB_QUEUE/${job_id}.stdout"

  if [[ "$mode" == "file" ]]; then
    local file_path
    file_path=$(python3 -c "import json; print(json.load(open('$job_file'))['file'])")
    local host_file="${file_path/\/workspace/$WORKSPACE}"
    if [[ ! -f "$host_file" ]]; then
      write_sage_result "$result_file" "error" "$job_id" "Sage file not found: $file_path" "" ""
      log "SAGE FAIL $job_id: file not found"
      rm -f "$job_file"
      return
    fi
    timeout "$job_timeout" docker exec "$SAGE_CONTAINER" sage "$file_path" > "$tmp_output" 2>&1 &
  else
    if [[ ! -f "$sage_file" ]]; then
      write_sage_result "$result_file" "error" "$job_id" "Sage code file missing" "" ""
      log "SAGE FAIL $job_id: no .sage file"
      rm -f "$job_file"
      return
    fi
    local container_sage="{{ PRIVATE_DATA_DIR }}/job-queue/${job_id}.sage"
    timeout "$job_timeout" docker exec "$SAGE_CONTAINER" sage "$container_sage" > "$tmp_output" 2>&1 &
  fi

  local bg_pid=$!

  # Poll for completion or cancellation
  while kill -0 "$bg_pid" 2>/dev/null; do
    if [[ -f "$cancel_file" ]]; then
      kill "$bg_pid" 2>/dev/null || true
      wait "$bg_pid" 2>/dev/null || true
      rm -f "$cancel_file" "$tmp_output"
      write_sage_result "$result_file" "cancelled" "$job_id" "Job cancelled by user" "" ""
      log "SAGE CANCEL $job_id"
      rm -f "$job_file"
      return
    fi
    sleep 1
  done

  if wait "$bg_pid" 2>/dev/null; then
    exit_code=0
  else
    exit_code=$?
  fi
  output=$(cat "$tmp_output" 2>/dev/null || echo "")
  rm -f "$tmp_output"

  exit_code="${exit_code:-0}"
  local end_time duration
  end_time=$(date +%s)
  duration=$((end_time - start_time))

  # Truncate output if too large
  local output_bytes
  output_bytes=$(echo -n "$output" | wc -c)
  local truncated=false
  if [[ "$output_bytes" -gt "$OUTPUT_MAX_BYTES" ]]; then
    output=$(echo "$output" | head -c "$OUTPUT_MAX_BYTES")
    output="${output}
... [truncated: output exceeded 1MB limit]"
    truncated=true
  fi

  # Detect plot file
  local has_plot=false
  local plot_path=""
  if [[ -f "$plot_file" ]]; then
    has_plot=true
    plot_path="$plot_file"
  fi

  # Build result
  if [[ "$exit_code" -eq 0 ]]; then
    python3 -c "
import json, sys
result = {
    'status': 'ok',
    'job_id': '$job_id',
    'duration_seconds': $duration,
    'truncated': $( [[ "$truncated" == "true" ]] && echo "True" || echo "False" ),
    'plot': '$plot_path' if '$has_plot' == 'true' else None,
    'output': sys.stdin.read()
}
with open('$result_file', 'w') as f:
    json.dump(result, f)
" <<< "$output"
    log "SAGE OK $job_id (${duration}s)$( [[ "$has_plot" == "true" ]] && echo " [plot]" )"
  elif [[ "$exit_code" -eq 124 ]]; then
    write_sage_result "$result_file" "error" "$job_id" "Computation timed out after ${job_timeout}s. Try --timeout with a larger value or reduce input size." "$duration" ""
    log "SAGE TIMEOUT $job_id (${duration}s)"
  elif [[ "$exit_code" -eq 137 ]]; then
    write_sage_result "$result_file" "error" "$job_id" "Computation killed (likely out of memory). Try a smaller input or a less memory-intensive algorithm." "$duration" ""
    log "SAGE OOM $job_id (${duration}s)"
  else
    # Other error — add actionable suggestions
    local suggestion=""
    if echo "$output" | grep -qi "SyntaxError\|NameError\|TypeError"; then
      suggestion=" Check Sage syntax — see /workspace/skills/sagemath/sage_reference.md"
    elif echo "$output" | grep -qi "MemoryError\|Killed"; then
      suggestion=" Try a smaller graph or less memory-intensive method."
    fi
    python3 -c "
import json, sys
result = {
    'status': 'error',
    'job_id': '$job_id',
    'exit_code': $exit_code,
    'duration_seconds': $duration,
    'message': 'SageMath error (exit code $exit_code).$suggestion',
    'output': sys.stdin.read()
}
with open('$result_file', 'w') as f:
    json.dump(result, f)
" <<< "$output"
    log "SAGE FAIL $job_id (exit $exit_code, ${duration}s)"
  fi

  # Clean up cancel file if it exists
  rm -f "$cancel_file" "$job_file"

  # Log to run log
  python3 -c "
import json, datetime
entry = {
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
    'job_id': '$job_id',
    'mode': '$mode',
    'duration_seconds': $duration,
    'exit_code': $exit_code,
    'save_label': '$save_label' or None
}
with open('$SAGE_LOG', 'a') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null || true

  # Save result if --save was used
  if [[ -n "$save_label" && "$exit_code" -eq 0 ]]; then
    cp "$result_file" "$SAGE_OUTPUT/${save_label}.json" 2>/dev/null || true
    log "SAGE SAVED $job_id -> ${save_label}.json"
  fi
}

# --- Manim execution (host-native render via the manim venv; mirrors the sage handler,
#     but runs on the host, not in a container; fully guarded so it can never abort the loop) ---

write_manim_result() {
  local result_file="$1"
  RESULT_FILE="$result_file" \
  STATUS="${2:-}" \
  JOB_ID="${3:-}" \
  MESSAGE="${4:-}" \
  DURATION="${5:-}" \
  EXIT_CODE="${6:-}" \
  CLIP="${7:-}" \
  python3 - <<'PY'
import json
import os

payload = {"status": os.environ["STATUS"]}
if os.environ["JOB_ID"]:
    payload["job_id"] = os.environ["JOB_ID"]
if os.environ["MESSAGE"]:
    payload["message"] = os.environ["MESSAGE"]
if os.environ["DURATION"]:
    payload["duration_seconds"] = int(os.environ["DURATION"])
if os.environ["EXIT_CODE"]:
    payload["exit_code"] = int(os.environ["EXIT_CODE"])
if os.environ["CLIP"]:
    payload["clip"] = os.environ["CLIP"]

with open(os.environ["RESULT_FILE"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

process_manim_job() {
  local job_file="$1"
  local job_name job_id
  job_name=$(basename "$job_file")
  job_id="${job_name%.working}"
  job_id="${job_id%.json}"
  local result_file="$MANIM_QUEUE/${job_id}.result"
  local cancel_file="$MANIM_QUEUE/${job_id}.cancel"
  local start_time
  start_time=$(date +%s)

  local spec output quality job_timeout save_label
  spec=$(python3 -c "import json; print(json.load(open('$job_file'))['spec'])" 2>/dev/null || echo "")
  output=$(python3 -c "import json; print(json.load(open('$job_file')).get('output',''))" 2>/dev/null || echo "")
  quality=$(python3 -c "import json; print(json.load(open('$job_file')).get('quality','-qh'))" 2>/dev/null || echo "-qh")
  job_timeout=$(python3 -c "import json; print(json.load(open('$job_file')).get('timeout', $MANIM_RENDER_TIMEOUT_DEFAULT))" 2>/dev/null || echo "$MANIM_RENDER_TIMEOUT_DEFAULT")
  save_label=$(python3 -c "import json; print(json.load(open('$job_file')).get('save_label',''))" 2>/dev/null || echo "")

  if [[ -z "$spec" ]]; then
    write_manim_result "$result_file" "error" "$job_id" "Manim job missing 'spec' path" "" "" ""
    log "MANIM FAIL $job_id: no spec"
    rm -f "$job_file"
    return 0
  fi
  local host_spec="${spec/\/workspace/$WORKSPACE}"
  if [[ ! -f "$host_spec" ]]; then
    write_manim_result "$result_file" "error" "$job_id" "Spec not found on host: $spec" "" "" ""
    log "MANIM FAIL $job_id: spec not found $host_spec"
    rm -f "$job_file"
    return 0
  fi
  [[ -n "$output" ]] || output="{{ PRIVATE_DATA_DIR }}/manim-queue/${job_id}.mp4"
  local host_out="${output/\/workspace/$WORKSPACE}"
  mkdir -p "$(dirname "$host_out")" 2>/dev/null || true

  log "MANIM $job_id: quality=$quality timeout=${job_timeout}s"

  local tmp_output="$MANIM_QUEUE/${job_id}.stdout"
  MMA_PYTHON="$MANIM_PYTHON" HOME="${HOME:-{{ USER_HOME }}}" \
    timeout "$job_timeout" bash "$MANIM_RUNNER" render --spec "$host_spec" --output "$host_out" --quality="$quality" \
    > "$tmp_output" 2>&1 &
  local bg_pid=$!

  while kill -0 "$bg_pid" 2>/dev/null; do
    if [[ -f "$cancel_file" ]]; then
      kill "$bg_pid" 2>/dev/null || true
      wait "$bg_pid" 2>/dev/null || true
      rm -f "$cancel_file" "$tmp_output"
      write_manim_result "$result_file" "cancelled" "$job_id" "Job cancelled by user" "" "" ""
      log "MANIM CANCEL $job_id"
      rm -f "$job_file"
      return 0
    fi
    sleep 1
  done

  local exit_code
  if wait "$bg_pid" 2>/dev/null; then exit_code=0; else exit_code=$?; fi
  exit_code="${exit_code:-0}"
  local output_text
  output_text=$(cat "$tmp_output" 2>/dev/null || echo "")
  rm -f "$tmp_output"
  local end_time duration
  end_time=$(date +%s)
  duration=$((end_time - start_time))

  if [[ "$exit_code" -eq 0 && -f "$host_out" ]]; then
    write_manim_result "$result_file" "ok" "$job_id" "" "$duration" "0" "$output"
    log "MANIM OK $job_id (${duration}s) -> $output"
    if [[ -n "$save_label" ]]; then
      cp "$host_out" "$MANIM_OUTPUT/${save_label}.mp4" 2>/dev/null || true
      log "MANIM SAVED $job_id -> ${save_label}.mp4"
    fi
  elif [[ "$exit_code" -eq 124 ]]; then
    write_manim_result "$result_file" "error" "$job_id" "Render timed out after ${job_timeout}s. Increase --timeout or lower --quality." "$duration" "124" ""
    log "MANIM TIMEOUT $job_id (${duration}s)"
  elif [[ "$exit_code" -eq 137 ]]; then
    write_manim_result "$result_file" "error" "$job_id" "Render killed (likely out of memory). Lower --quality or simplify the scene." "$duration" "137" ""
    log "MANIM OOM $job_id (${duration}s)"
  else
    local tail_msg
    tail_msg=$(echo "$output_text" | tail -c 600)
    write_manim_result "$result_file" "error" "$job_id" "Manim render failed (exit $exit_code): $tail_msg" "$duration" "$exit_code" ""
    log "MANIM FAIL $job_id (exit $exit_code, ${duration}s)"
  fi

  python3 -c "
import json, datetime
entry = {'timestamp': datetime.datetime.utcnow().isoformat()+'Z', 'job_id': '$job_id', 'quality': '$quality', 'duration_seconds': $duration, 'exit_code': $exit_code, 'save_label': '$save_label' or None}
with open('$MANIM_LOG','a') as f: f.write(json.dumps(entry)+'\n')
" 2>/dev/null || true

  rm -f "$cancel_file" "$job_file"
  return 0
}

# --- Health check for persistent container ---
HEALTH_CHECK_INTERVAL=150  # every ~5 minutes (150 * 2s sleep)
health_counter=0

check_sage_health() {
  if docker inspect "$SAGE_CONTAINER" >/dev/null 2>&1; then
    if ! timeout 10 docker exec "$SAGE_CONTAINER" sage -c "print(1)" >/dev/null 2>&1; then
      log "SAGE HEALTH: container unresponsive, restarting"
      docker restart "$SAGE_CONTAINER" >/dev/null 2>&1 || true
    fi
  fi
}

# --- Main loop ---

log "Job queue worker started"
log "  Send queue: $SEND_QUEUE"
log "  Job queue:  $JOB_QUEUE"
log "  Sage image: $SAGE_IMAGE"

while true; do
  # Process send queue
  for job_file in "$SEND_QUEUE"/*.json; do
    [[ -f "$job_file" ]] || continue
    claimed_job=$(claim_job_file "$job_file") || continue
    process_send_job "$claimed_job"
  done

  # Process job queue (SageMath)
  for job_file in "$JOB_QUEUE"/*.json; do
    [[ -f "$job_file" ]] || continue
    claimed_job=$(claim_job_file "$job_file") || continue
    process_sage_job "$claimed_job"
  done

  # Process manim queue (host-native render). Contained with || so a manim job
  # can never abort the worker loop (protects type=send and type=sage delivery).
  for job_file in "$MANIM_QUEUE"/*.json; do
    [[ -f "$job_file" ]] || continue
    claimed_job=$(claim_job_file "$job_file") || continue
    process_manim_job "$claimed_job" || log "MANIM handler error (contained; worker continues)"
  done

  # Periodic health check
  health_counter=$((health_counter + 1))
  if [[ $health_counter -ge $HEALTH_CHECK_INTERVAL ]]; then
    check_sage_health
    health_counter=0
  fi

  sleep 2
done
