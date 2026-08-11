#!/usr/bin/bash -p
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
# Submit one exact, pre-authorized email intent to the host queue.
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT
export PATH=/usr/bin:/bin

WS="${OPENCLAW_WORKSPACE:-/workspace}"
Q="$WS/data/email-queue"

APPROVAL_ID=""
MAIL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host-approval-id)
      [[ $# -ge 2 ]] || { echo '{"ok":false,"error_code":"approval_required","message":"--host-approval-id requires a value"}'; exit 2; }
      APPROVAL_ID="$2"
      shift 2
      ;;
    --host-approval-id=*)
      APPROVAL_ID="${1#*=}"
      shift
      ;;
    *)
      MAIL_ARGS+=("$1")
      shift
      ;;
  esac
done
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo '{"ok":false,"error_code":"approval_required","message":"a valid --host-approval-id is required"}'
  exit 2
}
mkdir -p "$Q"

JOB_ID="email-$(/usr/bin/date -u +%Y%m%dT%H%M%S)-$(/usr/bin/python3 -I -S -B - <<'PY'
import secrets
print(secrets.token_hex(8))
PY
)"
JOB="$Q/$JOB_ID.json"
RES="$Q/$JOB_ID.result"
cleanup() {
  /usr/bin/rm -f -- "$JOB" "$RES"
}
trap cleanup EXIT

/usr/bin/python3 -I -S -B - "$JOB" "$JOB_ID" "$APPROVAL_ID" "${MAIL_ARGS[@]}" <<'PY'
import json
import os
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "id": sys.argv[2],
    "type": "email",
    "argv": sys.argv[4:],
    "status": "pending",
    "approval_id": sys.argv[3],
}
temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("email queue job write was truncated")
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
PY

MAX=180; W=0
while [ "$W" -lt "$MAX" ]; do
  if [ -f "$RES" ]; then
    /usr/bin/python3 -I -S -B - "$RES" <<'PY'
import os
import stat
import sys

descriptor = os.open(
    sys.argv[1],
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
)
try:
    information = os.fstat(descriptor)
    if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1 or information.st_size > 1_048_576:
        raise SystemExit("unsafe email queue result")
    remaining = information.st_size
    while remaining:
        chunk = os.read(descriptor, min(remaining, 65_536))
        if not chunk:
            raise SystemExit("truncated email queue result")
        os.write(1, chunk)
        remaining -= len(chunk)
finally:
    os.close(descriptor)
PY
    exit 0
  fi
  /usr/bin/sleep 2; W=$((W + 2))
done
echo "{\"status\":\"error\",\"message\":\"email job timed out after ${MAX}s waiting for host worker\",\"job_id\":\"${JOB_ID}\"}"
exit 1
