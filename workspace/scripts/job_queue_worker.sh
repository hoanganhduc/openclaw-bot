#!/usr/bin/bash -p
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT
export PATH=/usr/bin:/bin
unset AAS_FILE_DELIVERY_SECRETS_FILE OPENCLAW_BIN

# Host-side queue consumer. Each systemd service selects exactly one queue so
# Docker, renderer, SMTP/PGP, and file-delivery authority never share a process.

WORKSPACE="${OPENCLAW_WORKSPACE:?OPENCLAW_WORKSPACE is required}"
STATE_PREFIX="${OPENCLAW_STATE_DIR:-}"
LIBEXEC="${OPENCLAW_LIBEXEC:?OPENCLAW_LIBEXEC is required}"
QUEUE_KIND="${OPENCLAW_QUEUE_KIND:?OPENCLAW_QUEUE_KIND is required}"
case "$QUEUE_KIND" in send|sage|manim|email) ;; *)
  echo "invalid OPENCLAW_QUEUE_KIND" >&2
  exit 2
esac
OWNER_LOCK="${OPENCLAW_EXPECTED_OWNER_STATE_LOCK:-}"
if [[ "$QUEUE_KIND" == send ]]; then
  [[ -n "$OWNER_LOCK" ]] || {
    echo "expected owner-state lock is unavailable" >&2
    exit 2
  }
else
  [[ -n "$STATE_PREFIX" ]] || {
    echo "OPENCLAW_STATE_DIR is required for this queue" >&2
    exit 2
  }
  STATE_PARENT="$(dirname -- "$STATE_PREFIX")"
  STATE_NAME="$(basename -- "$STATE_PREFIX")"
  if [[ "$STATE_NAME" == .* ]]; then
    EXPECTED_FROM_STATE="$STATE_PARENT/${STATE_NAME}.owner-state.lock"
  else
    EXPECTED_FROM_STATE="$STATE_PARENT/.${STATE_NAME}.owner-state.lock"
  fi
  [[ -z "$OWNER_LOCK" || "$OWNER_LOCK" == "$EXPECTED_FROM_STATE" ]] || {
    echo "owner-state lock does not match the state prefix" >&2
    exit 2
  }
  OWNER_LOCK="$EXPECTED_FROM_STATE"
fi
[[ "${OPENCLAW_OWNER_STATE_LOCK:-}" == "$OWNER_LOCK" ]] || {
  echo "owner-state lock is not inherited; refusing to run queue worker" >&2
  exit 2
}
DELIVERY_CHANNEL="${OPENCLAW_DELIVERY_CHANNEL:-}"
DELIVERY_POLICY="${OPENCLAW_DELIVERY_POLICY:-}"
DELIVERY_STATE="${OPENCLAW_DELIVERY_STATE:-}"
TELEGRAM_CREDENTIAL="${OPENCLAW_TELEGRAM_CREDENTIAL:-}"
if [[ "$QUEUE_KIND" == send ]]; then
  case "$DELIVERY_CHANNEL" in telegram|zulip|googlechat|whatsapp|zalo) ;; *)
    echo "invalid OPENCLAW_DELIVERY_CHANNEL" >&2
    exit 2
  esac
  [[ "$DELIVERY_POLICY" == /* ]] || {
    echo "delivery policy credential is unavailable" >&2
    exit 2
  }
  if [[ "$DELIVERY_CHANNEL" == telegram ]]; then
    [[ "$TELEGRAM_CREDENTIAL" == /* && -z "$DELIVERY_STATE" ]] || {
      echo "Telegram worker received an invalid authority set" >&2
      exit 2
    }
  else
    [[ "$DELIVERY_STATE" == /* && -z "$TELEGRAM_CREDENTIAL" ]] || {
      echo "channel worker received an invalid authority set" >&2
      exit 2
    }
  fi
fi
SEND_QUEUE="$WORKSPACE/data/send-queue/$DELIVERY_CHANNEL"
DELIVERY_HELPER="$LIBEXEC/file_delivery.py"
QUEUE_BOUNDARY="$LIBEXEC/queue_boundary.py"
JOB_QUEUE="$WORKSPACE/data/job-queue"
SAGE_OUTPUT="$WORKSPACE/data/research/sagemath"
SAGE_LOG="$SAGE_OUTPUT/run-log.jsonl"
# arm64 (this system) uses the prebuilt image; amd64 uses the official SageMath image.
case "$(/usr/bin/uname -m)" in aarch64|arm64) SAGE_IMAGE="ghcr.io/hoanganhduc/sagemath:10.8" ;; *) SAGE_IMAGE="sagemath/sagemath:10.8" ;; esac
SAGE_CONTAINER="sagemath-worker"
SAGE_SPOOL="$TMPDIR/sage-inputs"
OUTPUT_MAX_BYTES=1048576  # 1MB

# --- Manim (host-native render via the manim-math-animation venv; SEPARATE queue dir
#     so manim jobs never enter the sage glob on $JOB_QUEUE) ---
MANIM_QUEUE="$WORKSPACE/data/manim-queue"
MANIM_OUTPUT="$WORKSPACE/data/research/manim"
MANIM_LOG="$MANIM_OUTPUT/run-log.jsonl"
MANIM_RUNNER="manim-math-animation/run_manim_math_animation.sh"
MANIM_RENDER_TIMEOUT_DEFAULT=900

# --- Email send (host-native, exact one-time owner approval, separate queue). ---
EMAIL_QUEUE="$WORKSPACE/data/email-queue"
EMAIL_OUTPUT="$WORKSPACE/data/research/email"
EMAIL_LOG="$EMAIL_OUTPUT/run-log.jsonl"
EMAIL_DELIVERY_HELPER="$LIBEXEC/email_delivery.py"
EMAIL_TIMEOUT_DEFAULT=120

case "$QUEUE_KIND" in
  send) mkdir -p "$SEND_QUEUE" ;;
  sage) mkdir -p "$JOB_QUEUE" "$SAGE_OUTPUT" "$SAGE_SPOOL"; chmod 700 "$SAGE_SPOOL" ;;
  manim) mkdir -p "$MANIM_QUEUE" "$MANIM_OUTPUT" "$TMPDIR/manim-inputs" ;;
  email) mkdir -p "$EMAIL_QUEUE" "$EMAIL_OUTPUT" "$TMPDIR/email-inputs" ;;
esac

log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

job_field() {
  /usr/bin/python3 -I -S -B - "$1" "$2" "${3:-}" <<'PY'
import json
import os
import stat
import sys

path, key, fallback = sys.argv[1:]
descriptor = os.open(
    path,
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
)
try:
    information = os.fstat(descriptor)
    if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1 or information.st_size > 1_048_576:
        raise ValueError("unsafe queue job")
    chunks = []
    remaining = information.st_size
    while remaining:
        chunk = os.read(descriptor, min(remaining, 65_536))
        if not chunk:
            raise ValueError("truncated queue job")
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
finally:
    os.close(descriptor)
if len(payload) > 1_048_576:
    raise ValueError("oversized queue job")
data = json.loads(payload)
if not isinstance(data, dict):
    raise ValueError("queue job must be an object")
value = data.get(key, fallback)
if isinstance(value, bool):
    value = "true" if value else "false"
elif not isinstance(value, (str, int, float)):
    raise ValueError("queue job field must be scalar")
text = str(value)
if "\x00" in text or len(text.encode("utf-8")) > 262_144:
    raise ValueError("queue job field is invalid or oversized")
sys.stdout.write(text)
PY
}

claim_job_file() {
  local job_file="$1"
  local job_name work_file
  job_name=$(basename -- "$job_file")
  [[ "$job_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$ ]] || return 1
  work_file="${job_file%.json}.working"
  /usr/bin/python3 -I -S -B - "$job_file" "$work_file" <<'PY' 2>/dev/null || return 1
import ctypes
import errno
import os
import stat
import sys

source, destination = sys.argv[1:]
information = os.lstat(source)
if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
    raise SystemExit(1)
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = getattr(libc, "renameat2", None)
if renameat2 is None:
    raise SystemExit(1)
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOENT}:
        raise SystemExit(1)
    raise OSError(error, os.strerror(error))
PY
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
  /usr/bin/python3 -I -S -B - <<'PY'
import json
import os
from pathlib import Path
import secrets

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

path = Path(os.environ["RESULT_FILE"])
temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
        json.dump(payload, handle)
        handle.flush()
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
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
  /usr/bin/python3 -I -S -B - <<'PY'
import json
import os
from pathlib import Path
import secrets

payload = {"status": os.environ["STATUS"]}
if os.environ["JOB_ID"]:
    payload["job_id"] = os.environ["JOB_ID"]
if os.environ["MESSAGE"]:
    payload["message"] = os.environ["MESSAGE"]
if os.environ["DURATION"]:
    payload["duration_seconds"] = int(os.environ["DURATION"])
if os.environ["EXIT_CODE"]:
    payload["exit_code"] = int(os.environ["EXIT_CODE"])

path = Path(os.environ["RESULT_FILE"])
temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
        json.dump(payload, handle)
        handle.flush()
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
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

  local -a delivery_args=(
    --workspace "$WORKSPACE"
    --policy "$DELIVERY_POLICY"
    --expected-channel "$DELIVERY_CHANNEL"
    --job "$job_file"
    --result "$result_file"
  )
  if [[ "$DELIVERY_CHANNEL" == telegram ]]; then
    delivery_args+=(--telegram-credential "$TELEGRAM_CREDENTIAL")
  else
    delivery_args+=(--channel-state "$DELIVERY_STATE")
  fi
  log "SEND $job_id: channel=$DELIVERY_CHANNEL exact policy/credential boundary"
  if /usr/bin/python3 -I -S -B "$DELIVERY_HELPER" "${delivery_args[@]}"; then
    log "SEND OK $job_id"
  else
    log "SEND FAIL $job_id"
  fi
  /usr/bin/rm -f -- "$job_file"
}

# --- SageMath execution ---

sage_container_has_required_mounts() {
  local mounts
  mounts="$(docker inspect -f '{{range .Mounts}}{{println .Source "|" .Destination "|" .RW}}{{end}}' \
    "$SAGE_CONTAINER" 2>/dev/null)" || return 1
  grep -Fqx "$JOB_QUEUE | /workspace/data/job-queue | true" <<<"$mounts" \
    && grep -Fqx "$SAGE_SPOOL | /opt/openclaw-jobs | false" <<<"$mounts"
}

ensure_sage_container() {
  chmod 1777 "$JOB_QUEUE"
  if docker inspect "$SAGE_CONTAINER" >/dev/null 2>&1 \
      && ! sage_container_has_required_mounts; then
    log "SAGE recreating container with descriptor-snapshot spool"
    docker rm -f "$SAGE_CONTAINER" >/dev/null 2>&1
  fi
  if ! docker inspect "$SAGE_CONTAINER" >/dev/null 2>&1; then
    log "SAGE starting persistent container"
    docker run -d --name "$SAGE_CONTAINER" --restart=unless-stopped \
      --cpus=3 --memory=16g --memory-swap=16g --pids-limit=512 \
      --network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges \
      --tmpfs /tmp:rw,nosuid,nodev,noexec,size=2g -e SAGE_NUM_THREADS=3 \
      -v "$JOB_QUEUE:/workspace/data/job-queue" \
      -v "$SAGE_SPOOL:/opt/openclaw-jobs:ro" \
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
  local cancel_file="$JOB_QUEUE/${job_id}.cancel"
  local plot_file="$JOB_QUEUE/${job_id}.png"
  local start_time
  start_time=$(date +%s)

  local mode job_timeout save_label is_plot
  mode=$(job_field "$job_file" mode)
  job_timeout=$(job_field "$job_file" timeout)
  save_label=$(job_field "$job_file" save_label)
  is_plot=$(job_field "$job_file" plot false)
  [[ "$job_timeout" =~ ^[0-9]+$ && "$job_timeout" -ge 1 && "$job_timeout" -le 3600 ]] || job_timeout=300
  [[ -z "$save_label" || "$save_label" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || save_label=""

  log "SAGE $job_id: mode=$mode timeout=${job_timeout}s plot=$is_plot"

  ensure_sage_container

  # Run sage in background so we can check for cancellation
  local output exit_code
  local tmp_output="$JOB_QUEUE/${job_id}.stdout"
  local container_sage sage_snapshot source_path

  if [[ "$mode" == "file" ]]; then
    local file_path
    file_path=$(job_field "$job_file" file)
    source_path="$file_path"
  else
    source_path="data/job-queue/${job_id}.sage"
  fi
  if ! sage_snapshot=$(/usr/bin/python3 -I -S -B "$QUEUE_BOUNDARY" snapshot \
      --workspace "$WORKSPACE" --source "$source_path" --spool "$SAGE_SPOOL" \
      --label "$job_id" --max-bytes 16777216 \
      --allow-root data/job-queue --allow-root data/exports \
      --allow-root data/research); then
    if [[ "$mode" == "file" ]]; then
      write_sage_result "$result_file" "error" "$job_id" "Sage file not found: $file_path" "" ""
    else
      write_sage_result "$result_file" "error" "$job_id" "Sage code file missing" "" ""
    fi
    log "SAGE FAIL $job_id: input snapshot rejected"
    rm -f "$job_file"
    return
  fi
  container_sage="/opt/openclaw-jobs/$(basename -- "$sage_snapshot")"
  timeout "$job_timeout" docker exec "$SAGE_CONTAINER" sage "$container_sage" > "$tmp_output" 2>&1 &

  local bg_pid=$!

  # Poll for completion or cancellation
  while kill -0 "$bg_pid" 2>/dev/null; do
    if [[ -f "$cancel_file" ]]; then
      kill "$bg_pid" 2>/dev/null || true
      wait "$bg_pid" 2>/dev/null || true
      rm -f "$cancel_file" "$tmp_output"
      rm -f -- "$sage_snapshot"
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
    RESULT_FILE="$result_file" JOB_ID="$job_id" DURATION="$duration" \
      TRUNCATED="$truncated" HAS_PLOT="$has_plot" PLOT_PATH="$plot_path" \
      /usr/bin/python3 -I -S -B - 3<<<"$output" <<'PY'
import json, os, secrets, sys
from pathlib import Path
result = {
    'status': 'ok',
    'job_id': os.environ['JOB_ID'],
    'duration_seconds': int(os.environ['DURATION']),
    'truncated': os.environ['TRUNCATED'] == 'true',
    'plot': os.environ['PLOT_PATH'] if os.environ['HAS_PLOT'] == 'true' else None,
    'output': os.fdopen(3, encoding='utf-8').read()
}
path = Path(os.environ['RESULT_FILE'])
temporary = path.with_name(f'.{path.name}.{secrets.token_hex(8)}.tmp')
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8', closefd=False) as stream:
        json.dump(result, stream)
        stream.flush()
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
PY
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
    RESULT_FILE="$result_file" JOB_ID="$job_id" EXIT_CODE="$exit_code" \
      DURATION="$duration" SUGGESTION="$suggestion" \
      /usr/bin/python3 -I -S -B - 3<<<"$output" <<'PY'
import json, os, secrets, sys
from pathlib import Path
result = {
    'status': 'error',
    'job_id': os.environ['JOB_ID'],
    'exit_code': int(os.environ['EXIT_CODE']),
    'duration_seconds': int(os.environ['DURATION']),
    'message': f"SageMath error (exit code {os.environ['EXIT_CODE']}).{os.environ['SUGGESTION']}",
    'output': os.fdopen(3, encoding='utf-8').read()
}
path = Path(os.environ['RESULT_FILE'])
temporary = path.with_name(f'.{path.name}.{secrets.token_hex(8)}.tmp')
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8', closefd=False) as stream:
        json.dump(result, stream)
        stream.flush()
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
PY
    log "SAGE FAIL $job_id (exit $exit_code, ${duration}s)"
  fi

  # Clean up cancel file if it exists
  rm -f "$cancel_file" "$job_file" "$sage_snapshot"

  # Log to run log
  SAGE_LOG="$SAGE_LOG" JOB_ID="$job_id" MODE="$mode" DURATION="$duration" \
    EXIT_CODE="$exit_code" SAVE_LABEL="$save_label" \
    /usr/bin/python3 -I -S -B - <<'PY' 2>/dev/null || true
import datetime, json, os
entry = {
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
    'job_id': os.environ['JOB_ID'],
    'mode': os.environ['MODE'],
    'duration_seconds': int(os.environ['DURATION']),
    'exit_code': int(os.environ['EXIT_CODE']),
    'save_label': os.environ['SAVE_LABEL'] or None,
}
with open(os.environ['SAGE_LOG'], 'a', encoding='utf-8') as f:
    f.write(json.dumps(entry) + '\n')
PY

  # Save result if --save was used
  if [[ -n "$save_label" && "$exit_code" -eq 0 ]]; then
    cp "$result_file" "$SAGE_OUTPUT/${save_label}.json" 2>/dev/null || true
    log "SAGE SAVED $job_id -> ${save_label}.json"
  fi
}

# --- Manim execution (descriptor snapshots plus a networkless, resource-bounded service). ---

write_manim_result() {
  local result_file="$1"
  RESULT_FILE="$result_file" \
  STATUS="${2:-}" \
  JOB_ID="${3:-}" \
  MESSAGE="${4:-}" \
  DURATION="${5:-}" \
  EXIT_CODE="${6:-}" \
  CLIP="${7:-}" \
  /usr/bin/python3 -I -S -B - <<'PY'
import json
import os
from pathlib import Path
import secrets

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

path = Path(os.environ["RESULT_FILE"])
temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
        json.dump(payload, handle)
        handle.flush()
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
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
  spec=$(job_field "$job_file" spec 2>/dev/null || echo "")
  output=$(job_field "$job_file" output 2>/dev/null || echo "")
  quality=$(job_field "$job_file" quality -qh 2>/dev/null || echo "-qh")
  job_timeout=$(job_field "$job_file" timeout "$MANIM_RENDER_TIMEOUT_DEFAULT" 2>/dev/null || echo "$MANIM_RENDER_TIMEOUT_DEFAULT")
  save_label=$(job_field "$job_file" save_label 2>/dev/null || echo "")
  [[ "$job_timeout" =~ ^[0-9]+$ && "$job_timeout" -ge 1 && "$job_timeout" -le 3600 ]] || job_timeout="$MANIM_RENDER_TIMEOUT_DEFAULT"
  [[ "$quality" =~ ^-q[lmhkp]$ ]] || quality=-qh
  [[ -z "$save_label" || "$save_label" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || save_label=""

  if [[ -z "$spec" ]]; then
    write_manim_result "$result_file" "error" "$job_id" "Manim job missing 'spec' path" "" "" ""
    log "MANIM FAIL $job_id: no spec"
    rm -f "$job_file"
    return 0
  fi
  local host_spec
  if ! host_spec=$(/usr/bin/python3 -I -S -B "$QUEUE_BOUNDARY" snapshot \
      --workspace "$WORKSPACE" --source "$spec" --spool "$TMPDIR/manim-inputs" \
      --label "$job_id" --max-bytes 1048576 --allow-root data/manim-queue); then
    write_manim_result "$result_file" "error" "$job_id" "Spec not found on host: $spec" "" "" ""
    log "MANIM FAIL $job_id: spec snapshot rejected"
    rm -f "$job_file"
    return 0
  fi
  [[ -n "$output" ]] || output="data/research/manim/${job_id}.mp4"
  local private_out
  private_out="$(mktemp "$TMPDIR/manim-output.XXXXXX.mp4")"
  chmod 600 "$private_out"

  log "MANIM $job_id: quality=$quality timeout=${job_timeout}s"

  local tmp_output="$MANIM_QUEUE/${job_id}.stdout"
  /usr/bin/timeout "$job_timeout" /usr/bin/python3 -I -S -B "$LIBEXEC/host_exec.py" \
      --generation "$LIBEXEC" --artifact "$MANIM_RUNNER" -- \
      render --spec "$host_spec" --output "$private_out" --quality="$quality" \
    > "$tmp_output" 2>&1 &
  local bg_pid=$!

  while kill -0 "$bg_pid" 2>/dev/null; do
    if [[ -f "$cancel_file" ]]; then
      kill "$bg_pid" 2>/dev/null || true
      wait "$bg_pid" 2>/dev/null || true
      rm -f "$cancel_file" "$tmp_output"
      rm -f -- "$host_spec" "$private_out"
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

  if [[ "$exit_code" -eq 0 && -f "$private_out" ]]; then
    local host_out
    if /usr/bin/python3 -I -S -B "$QUEUE_BOUNDARY" publish \
        --workspace "$WORKSPACE" --source "$private_out" --destination "$output" \
        --allow-root data/research/manim --allow-root data/exports >/dev/null; then
      write_manim_result "$result_file" "ok" "$job_id" "" "$duration" "0" "$output"
      log "MANIM OK $job_id (${duration}s) -> $output"
      if [[ -n "$save_label" ]]; then
        local save_output="data/research/manim/${save_label}.mp4"
        if [[ "$save_output" != "$output" ]] && \
            /usr/bin/python3 -I -S -B "$QUEUE_BOUNDARY" publish \
              --workspace "$WORKSPACE" --source "$private_out" \
              --destination "$save_output" --allow-root data/research/manim \
              >/dev/null; then
          log "MANIM SAVED $job_id -> ${save_label}.mp4"
        else
          log "MANIM SAVE SKIPPED $job_id: alias exists or matches primary output"
        fi
      fi
    else
      exit_code=73
      write_manim_result "$result_file" "error" "$job_id" \
        "Manim output publication was rejected" "$duration" "$exit_code" ""
      log "MANIM FAIL $job_id: output publication rejected"
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

  MANIM_LOG="$MANIM_LOG" JOB_ID="$job_id" QUALITY="$quality" \
    DURATION="$duration" EXIT_CODE="$exit_code" SAVE_LABEL="$save_label" \
    /usr/bin/python3 -I -S -B - <<'PY' 2>/dev/null || true
import datetime, json, os
entry = {
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
    'job_id': os.environ['JOB_ID'],
    'quality': os.environ['QUALITY'],
    'duration_seconds': int(os.environ['DURATION']),
    'exit_code': int(os.environ['EXIT_CODE']),
    'save_label': os.environ['SAVE_LABEL'] or None,
}
with open(os.environ['MANIM_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(entry) + '\n')
PY

  rm -f "$cancel_file" "$job_file" "$host_spec" "$private_out"
  return 0
}

# --- Email send (host-native; exact intent is approved once before SMTP/PGP use). ---

process_email_job() {
  local job_file="$1"
  local job_name job_id
  job_name=$(basename "$job_file"); job_id="${job_name%.working}"; job_id="${job_id%.json}"
  local result_file="$EMAIL_QUEUE/${job_id}.result"
  local start_time; start_time=$(date +%s)
  log "EMAIL $job_id: checking exact one-time owner approval"
  if /usr/bin/timeout "$EMAIL_TIMEOUT_DEFAULT" \
      /usr/bin/python3 -I -S -B "$EMAIL_DELIVERY_HELPER" \
        --workspace "$WORKSPACE" --state-prefix "$STATE_PREFIX" \
        --job "$job_file" --result "$result_file" \
        --spool "$TMPDIR/email-inputs"; then
    log "EMAIL $job_id: processed"
  else
    log "EMAIL FAIL $job_id: host boundary failed"
  fi
  local duration=$(( $(date +%s) - start_time ))
  log "EMAIL $job_id: done (${duration}s)"
  EMAIL_LOG="$EMAIL_LOG" JOB_ID="$job_id" DURATION="$duration" \
    /usr/bin/python3 -I -S -B - <<'PY' 2>/dev/null || true
import datetime, json, os
with open(os.environ['EMAIL_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps({
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'job_id': os.environ['JOB_ID'],
        'duration_seconds': int(os.environ['DURATION']),
    }) + '\n')
PY
  rm -f "$job_file"
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

log "Job queue worker started: kind=$QUEUE_KIND${DELIVERY_CHANNEL:+ channel=$DELIVERY_CHANNEL}"

while true; do
  case "$QUEUE_KIND" in
    send)
      for job_file in "$SEND_QUEUE"/*.json; do
        [[ -f "$job_file" ]] || continue
        claimed_job=$(claim_job_file "$job_file") || continue
        process_send_job "$claimed_job"
      done
      ;;
    sage)
      for job_file in "$JOB_QUEUE"/*.json; do
        [[ -f "$job_file" ]] || continue
        claimed_job=$(claim_job_file "$job_file") || continue
        process_sage_job "$claimed_job"
      done
      health_counter=$((health_counter + 1))
      if [[ $health_counter -ge $HEALTH_CHECK_INTERVAL ]]; then
        check_sage_health
        health_counter=0
      fi
      ;;
    manim)
      for job_file in "$MANIM_QUEUE"/*.json; do
        [[ -f "$job_file" ]] || continue
        claimed_job=$(claim_job_file "$job_file") || continue
        process_manim_job "$claimed_job" || log "MANIM handler error (contained)"
      done
      ;;
    email)
      for job_file in "$EMAIL_QUEUE"/*.json; do
        [[ -f "$job_file" ]] || continue
        claimed_job=$(claim_job_file "$job_file") || continue
        process_email_job "$claimed_job" || log "EMAIL handler error (contained)"
      done
      ;;
  esac

  sleep 2
done
