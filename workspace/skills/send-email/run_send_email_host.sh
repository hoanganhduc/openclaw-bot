#!/usr/bin/env bash
# Submit a send-email job to the host job queue and wait for the result.
# Used for PGP-signed sends inside the OpenClaw sandbox: the sandbox has no gpg and
# no private key, so the host worker runs send_email.py with the host send-email
# config + ~/.gnupg (key never enters the sandbox) and returns the JSON result.
# Args are passed through verbatim to send_email.py (e.g. send --sign --account ...).
set -euo pipefail

WS="${OPENCLAW_WORKSPACE:-/workspace}"
Q="$WS/data/email-queue"
mkdir -p "$Q"

JOB_ID="email-$(date +%Y%m%dT%H%M%S)-$$"
JOB="$Q/$JOB_ID.json"
RES="$Q/$JOB_ID.result"

python3 - "$JOB" "$JOB_ID" "$@" <<'PY'
import json, sys
json.dump({"id": sys.argv[2], "type": "email", "argv": sys.argv[3:], "status": "pending"},
          open(sys.argv[1], "w"))
PY

MAX=180; W=0
while [ "$W" -lt "$MAX" ]; do
  if [ -f "$RES" ]; then
    cat "$RES"
    rm -f "$JOB" "$RES"
    exit 0
  fi
  sleep 2; W=$((W + 2))
done
echo "{\"status\":\"error\",\"message\":\"email job timed out after ${MAX}s waiting for host worker\",\"job_id\":\"${JOB_ID}\"}"
exit 1
