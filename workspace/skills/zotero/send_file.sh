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
unset AAS_FILE_DELIVERY_SECRETS_FILE OPENCLAW_BIN REMOTE_BRIDGE_SECRETS_FILE

# Untrusted queue producer.  Policy, channel credentials, and network delivery
# belong exclusively to the host consumer installed outside sandbox mounts.
CHANNEL="${1:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
TARGET="${2:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
FILE_PATH="${3:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
CAPTION="${4:-}"
case "$CHANNEL" in telegram|zulip|googlechat|whatsapp|zalo) ;;
  *) echo "unsupported delivery channel" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
QUEUE_DIR="$WORKSPACE/data/send-queue/$CHANNEL"
JOB_ID="$(/usr/bin/date -u +%Y%m%dT%H%M%S)-$(/usr/bin/python3 -I -S -B -c \
  'import secrets; print(secrets.token_hex(12))')"
JOB_FILE="$QUEUE_DIR/$JOB_ID.json"
RESULT_FILE="$QUEUE_DIR/$JOB_ID.result"

/usr/bin/python3 -I -S -B - "$QUEUE_DIR" "$JOB_ID" "$CHANNEL" "$TARGET" \
  "$FILE_PATH" "$CAPTION" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

queue, job_id, channel, target, media, caption = sys.argv[1:]
if channel not in {"telegram", "zulip", "googlechat", "whatsapp", "zalo"}:
    raise SystemExit("unsupported delivery channel")
limits = {"target": 4096, "media": 65536, "caption": 65536}
for name, value in (("target", target), ("media", media), ("caption", caption)):
    if "\x00" in value or len(value.encode("utf-8")) > limits[name]:
        raise SystemExit(f"{name} is invalid or oversized")
if not target or not media:
    raise SystemExit("delivery target and media are required")

queue_path = Path(os.path.abspath(queue))
parent = os.open(
    queue_path.parent,
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
)
try:
    try:
        os.mkdir(queue_path.name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    queue_fd = os.open(
        queue_path.name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
finally:
    os.close(parent)
try:
    information = os.fstat(queue_fd)
    if information.st_uid != os.geteuid() or stat.S_IMODE(information.st_mode) & 0o077:
        raise OSError("send queue is not owner-private")
    payload = (
        json.dumps(
            {
                "schema": "openclaw.send-queue-job/v1",
                "id": job_id,
                "channel": channel,
                "target": target,
                "media": media,
                "caption": caption,
                "status": "pending",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temporary = f".{job_id}.{os.getpid()}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=queue_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("send queue write was truncated")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rename(temporary, f"{job_id}.json", src_dir_fd=queue_fd, dst_dir_fd=queue_fd)
    os.fsync(queue_fd)
finally:
    os.close(queue_fd)
PY

for _attempt in $(/usr/bin/seq 1 60); do
  if [[ -f "$RESULT_FILE" && ! -L "$RESULT_FILE" ]]; then
    /usr/bin/cat -- "$RESULT_FILE"
    status=$(/usr/bin/python3 -I -S -B - "$RESULT_FILE" <<'PY' 2>/dev/null || printf error
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("status", "error"))
PY
)
    /usr/bin/rm -f -- "$RESULT_FILE"
    [[ "$status" == "ok" ]]
    exit
  fi
  /usr/bin/sleep 2
done

# A timeout never causes local delivery.  The host may still claim the queued
# job, so leave it for the trusted consumer and report its durable identifier.
printf '{"status":"queued","job_id":"%s","message":"host result pending"}\n' "$JOB_ID"
exit 3
