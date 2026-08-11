#!/usr/bin/bash -p
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT \
  GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
export PATH=/usr/bin:/bin

usage() {
  cat <<'EOF'
Usage: ./restore.sh --archive FILE [--prefix DIR] [--dry-run] [--overlay-only] [--allow-legacy]
       [--activate-reviewed-authority ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY]
       [--signed-recovery-set-v2-owner-sha256 SHA256]
       [--allow-persistent-plaintext-staging ACKNOWLEDGE_PERSISTENT_OWNER_PLAINTEXT]
Env:   OPENCLAW_BACKUP_PASSPHRASE_FILE=/path (non-interactive gpg batch/loopback)
       OPENCLAW_ACTIVE_RUNTIME_HOME=/path (runtime whose writers must be stopped)

Builds and validates a private candidate tree, then atomically publishes it.
Archive config, secrets, credentials, identity, schedules, tasks, and flows are
quarantined by default. Restore never installs or reloads user services.
Markerless legacy archives require both --allow-legacy and --overlay-only.
EOF
}

ORIGINAL_ARGS=("$@")
PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
ACTIVE_RUNTIME_PREFIX="${OPENCLAW_ACTIVE_RUNTIME_HOME:-${OPENCLAW_HOME:-$HOME/.openclaw}}"
ARCHIVE=""
DRY_RUN=0
OVERLAY_ONLY=0
ALLOW_LEGACY=0
ACTIVATE_REVIEWED_AUTHORITY=""
SIGNED_RECOVERY_SET_V2_OWNER_SHA256=""
PERSISTENT_PLAINTEXT_ACK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-services) shift ;; # retained as a harmless compatibility option
    --overlay-only) OVERLAY_ONLY=1; shift ;;
    --allow-legacy) ALLOW_LEGACY=1; shift ;;
    --activate-reviewed-authority) ACTIVATE_REVIEWED_AUTHORITY="$2"; shift 2 ;;
    --signed-recovery-set-v2-owner-sha256)
      SIGNED_RECOVERY_SET_V2_OWNER_SHA256="$2"; shift 2 ;;
    --allow-persistent-plaintext-staging)
      PERSISTENT_PLAINTEXT_ACK="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$ARCHIVE" ]] || { usage >&2; exit 2; }
if [[ "$ALLOW_LEGACY" -eq 1 && "$OVERLAY_ONLY" -eq 0 ]]; then
  echo "legacy owner archives are accepted only in isolated --overlay-only mode" >&2
  exit 2
fi
if [[ -n "$ACTIVATE_REVIEWED_AUTHORITY" \
      && "$ACTIVATE_REVIEWED_AUTHORITY" != "ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY" ]]; then
  echo "archive authority activation requires the exact reviewed-activation token" >&2
  exit 2
fi
if [[ "$ALLOW_LEGACY" -eq 1 && -n "$ACTIVATE_REVIEWED_AUTHORITY" ]]; then
  echo "markerless legacy archives can never activate restored authorities" >&2
  exit 2
fi
if [[ -n "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256" ]]; then
  [[ "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "signed recovery-set v2 owner digest is invalid" >&2
    exit 2
  }
  [[ "$OVERLAY_ONLY" -eq 1 && "$ALLOW_LEGACY" -eq 0 \
      && -z "$ACTIVATE_REVIEWED_AUTHORITY" ]] || {
    echo "signed recovery-set v2 activation requires modern overlay-only restore and cannot combine with a human token" >&2
    exit 2
  }
fi

abspath() {
  /usr/bin/python3 -I -S -B -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$1"
}

PREFIX="$(abspath "$PREFIX")"
ACTIVE_RUNTIME_PREFIX="$(abspath "$ACTIVE_RUNTIME_PREFIX")"
ARCHIVE="$(abspath "$ARCHIVE")"
PREFIX_PARENT="$(dirname -- "$PREFIX")"
PREFIX_NAME="$(basename -- "$PREFIX")"
[[ "$PREFIX" != "/" && -n "$PREFIX_NAME" && "$PREFIX_NAME" != "." ]] || {
  echo "refusing unsafe restore prefix" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER_ARCHIVE_HELPER="$SCRIPT_DIR/scripts/owner_archive.py"
AUTH_CLOSURE_HELPER="$SCRIPT_DIR/scripts/openclaw_auth_closure.py"
TRANSACTION_HELPER="$SCRIPT_DIR/scripts/restore_transaction.py"
LOCK_HELPER="$SCRIPT_DIR/scripts/owner_state_lock.py"
PRIVATE_TMP_HELPER="$SCRIPT_DIR/scripts/private_tmp.py"
for helper in "$OWNER_ARCHIVE_HELPER" "$AUTH_CLOSURE_HELPER" "$TRANSACTION_HELPER" "$LOCK_HELPER" "$PRIVATE_TMP_HELPER"; do
  [[ -f "$helper" && ! -L "$helper" ]] || {
    echo "required restore helper is missing or unsafe" >&2
    exit 2
  }
done
[[ -d "$PREFIX_PARENT" && ! -L "$PREFIX_PARENT" ]] || {
  echo "restore prefix parent must already be a real directory" >&2
  exit 2
}
PARENT_IDENTITY="$(/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" inspect-directory --path "$PREFIX_PARENT")"

LOCK_PATH="$PREFIX_PARENT/.${PREFIX_NAME}.owner-state.lock"
if [[ "${OPENCLAW_OWNER_STATE_LOCK:-}" != "$LOCK_PATH" ]]; then
  exec /usr/bin/python3 -I -S -B "$LOCK_HELPER" --lock-path "$LOCK_PATH" -- \
    "$SCRIPT_DIR/restore.sh" "${ORIGINAL_ARGS[@]}"
fi
/usr/bin/python3 -I -S -B "$LOCK_HELPER" --lock-path "$LOCK_PATH" --validate-inherited

TARGET_IDENTITY=""
TARGET_MISSING=0
if [[ -e "$PREFIX" || -L "$PREFIX" ]]; then
  TARGET_IDENTITY="$(/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" inspect-directory \
    --path "$PREFIX" --owner-private)"
else
  TARGET_MISSING=1
fi

EXPECTED_VERSION="$(/usr/bin/python3 -I -S -B - "$SCRIPT_DIR/REBUILD-MANIFEST.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["openclaw"]["observed_version"])
PY
)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  if [[ "$OVERLAY_ONLY" -eq 0 ]]; then
    "$SCRIPT_DIR/install.sh" --prefix "$PREFIX" --destination-prefix "$PREFIX" \
      --skip-config --skip-openclaw-install --skip-services --convergent --dry-run
  fi
  echo "[dry-run] would verify, stage, close auth, and atomically restore $ARCHIVE into $PREFIX"
  exit 0
fi

[[ -x /usr/bin/gpg && ! -L /usr/bin/gpg ]] || {
  echo "trusted /usr/bin/gpg is unavailable; cannot restore encrypted archive" >&2
  exit 1
}

assert_quiescent() {
  [[ "$PREFIX" == "$ACTIVE_RUNTIME_PREFIX" ]] || return 0
  local runtime_active=0
  if command -v systemctl >/dev/null 2>&1 \
      && systemctl --user is-active --quiet openclaw-gateway.service 2>/dev/null; then
    runtime_active=1
  fi
  if command -v pgrep >/dev/null 2>&1 \
      && pgrep -f '/openclaw/dist/index\.js.*[g]ateway' >/dev/null 2>&1; then
    runtime_active=1
  fi
  local worker_unit
  if command -v systemctl >/dev/null 2>&1; then
    for worker_unit in send-queue-worker.service \
        openclaw-zulip-delivery-worker.service \
        openclaw-googlechat-delivery-worker.service \
        openclaw-whatsapp-delivery-worker.service \
        openclaw-zalo-delivery-worker.service \
        openclaw-sage-worker.service openclaw-manim-worker.service \
        openclaw-email-worker.service; do
      if systemctl --user is-active --quiet "$worker_unit" 2>/dev/null; then
        runtime_active=1
      fi
    done
  fi
  if command -v pgrep >/dev/null 2>&1 \
      && pgrep -f '/workspace/(scripts|skills/zotero)/job_queue_worker\.[s]h' \
        >/dev/null 2>&1; then
    runtime_active=1
  fi
  [[ "$runtime_active" -eq 0 ]] || {
    echo "OpenClaw writer is active; stop the gateway and all queue workers before restoring owner state" >&2
    exit 2
  }
}

assert_quiescent

tmp_args=(create)
[[ -z "$PERSISTENT_PLAINTEXT_ACK" ]] \
  || tmp_args+=(--allow-persistent "$PERSISTENT_PLAINTEXT_ACK")
TMP_DIR="$(/usr/bin/python3 -I -S -B "$PRIVATE_TMP_HELPER" "${tmp_args[@]}")"
export TMPDIR="$TMP_DIR"
export OPENCLAW_OWNER_PLAINTEXT_TMPDIR="$TMP_DIR"
export OPENCLAW_OWNER_PERSISTENT_PLAINTEXT_ACK="$PERSISTENT_PLAINTEXT_ACK"
CANDIDATE="$(mktemp -d "$PREFIX_PARENT/.${PREFIX_NAME}.restore-stage.XXXXXX")"
cleanup() {
  local original_status=$?
  local cleanup_failed=0
  trap - EXIT
  remove_args=(remove --path "$TMP_DIR")
  [[ -z "$PERSISTENT_PLAINTEXT_ACK" ]] \
    || remove_args+=(--allow-persistent "$PERSISTENT_PLAINTEXT_ACK")
  if ! /usr/bin/python3 -I -S -B "$PRIVATE_TMP_HELPER" "${remove_args[@]}" \
      >/dev/null; then
    echo "owner restore plaintext staging cleanup failed" >&2
    cleanup_failed=1
  fi
  if [[ -n "${CANDIDATE:-}" && -d "$CANDIDATE" \
      && "$(dirname -- "$CANDIDATE")" == "$PREFIX_PARENT" \
      && "$(basename -- "$CANDIDATE")" == ".${PREFIX_NAME}.restore-stage."* ]]; then
    rm -rf -- "$CANDIDATE" || cleanup_failed=1
  fi
  if [[ "$original_status" -ne 0 ]]; then
    exit "$original_status"
  fi
  [[ "$cleanup_failed" -eq 0 ]] || exit 70
}
trap cleanup EXIT

TAR_PATH="$TMP_DIR/private.tar.gz"
STAGE="$TMP_DIR/archive-stage"
mkdir -m 700 "$STAGE"

crypt_args=(decrypt --source "$ARCHIVE" --output "$TAR_PATH")
if [[ -n "${OPENCLAW_BACKUP_PASSPHRASE_FILE:-}" ]]; then
  crypt_args+=(--passphrase-file "$(abspath "$OPENCLAW_BACKUP_PASSPHRASE_FILE")")
fi
[[ -z "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256" ]] \
  || crypt_args+=(--expected-source-sha256 "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256")
/usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" "${crypt_args[@]}"

extract_args=(verify-extract --archive "$TAR_PATH" --destination "$STAGE" \
  --expected-runtime-version "$EXPECTED_VERSION")
[[ "$ALLOW_LEGACY" -eq 1 ]] && extract_args+=(--allow-legacy)
/usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" "${extract_args[@]}" >/dev/null

# Historical owner archives may predate the canonical agent-auth database.
# Convert their reviewed legacy authority while it is still inside the inert
# verified stage.  The JSON files themselves remain legacy-never-active and
# are quarantined by the overlay transaction below.
if [[ -n "$ACTIVATE_REVIEWED_AUTHORITY" || -n "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256" ]]; then
  /usr/bin/python3 -I -S -B "$AUTH_CLOSURE_HELPER" materialize-archive-stage \
    --prefix "$STAGE" --expected-version "$EXPECTED_VERSION" >/dev/null
fi

prepare_args=(prepare --source "$PREFIX" --candidate "$CANDIDATE")
[[ "$OVERLAY_ONLY" -eq 1 ]] && prepare_args+=(--clone-existing)
PREPARE_RESULT="$(/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" "${prepare_args[@]}")"
CANDIDATE_IDENTITY="$(/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" inspect-directory \
  --path "$CANDIDATE" --owner-private)"

if [[ "$OVERLAY_ONLY" -eq 0 ]]; then
  install_args=(--prefix "$PREFIX" --destination-prefix "$CANDIDATE" \
    --skip-config --skip-openclaw-install --skip-services --skip-docker --convergent)
  "$SCRIPT_DIR/install.sh" "${install_args[@]}"
fi

overlay_args=(overlay --stage "$STAGE" --candidate "$CANDIDATE")
[[ "$OVERLAY_ONLY" -eq 1 ]] && overlay_args+=(--preserve-authorities)
[[ "$ALLOW_LEGACY" -eq 1 ]] && overlay_args+=(--isolate-authorities)
[[ -n "$ACTIVATE_REVIEWED_AUTHORITY" ]] \
  && overlay_args+=(--activate-reviewed-authorities "$ACTIVATE_REVIEWED_AUTHORITY")
[[ -z "$SIGNED_RECOVERY_SET_V2_OWNER_SHA256" ]] \
  || overlay_args+=(--activate-reviewed-authorities ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY)
/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" "${overlay_args[@]}"
/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" empty-queues --candidate "$CANDIDATE"

# Full component restore proves the candidate's DB-first auth state before it
# can become active. Umbrella overlay mode preserves its already-restored
# authority/config set and leaves final convergence to the umbrella owner.
if [[ "$OVERLAY_ONLY" -eq 0 ]]; then
  /usr/bin/python3 -I -S -B "$AUTH_CLOSURE_HELPER" migrate \
    --prefix "$CANDIDATE" --expected-version "$EXPECTED_VERSION" \
    --allow-unconfigured >/dev/null
  echo "OpenClaw agent auth offline structural closure: ok"
else
  echo "OpenClaw agent auth closure deferred to the full restore convergence phase"
fi

# A writer starting during a long staging operation must still block publish.
assert_quiescent

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLLBACK_PATH="$PREFIX_PARENT/${PREFIX_NAME}.restore-rollback-$STAMP-$$"
read -r PARENT_DEVICE PARENT_INODE < <(/usr/bin/python3 -I -S -B - "$PARENT_IDENTITY" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
print(value["device"], value["inode"])
PY
)
read -r CANDIDATE_DEVICE CANDIDATE_INODE < <(/usr/bin/python3 -I -S -B - "$CANDIDATE_IDENTITY" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
print(value["device"], value["inode"])
PY
)
commit_args=(commit --candidate "$CANDIDATE" --target "$PREFIX" --rollback "$ROLLBACK_PATH" \
  --expected-parent-device "$PARENT_DEVICE" --expected-parent-inode "$PARENT_INODE" \
  --expected-candidate-device "$CANDIDATE_DEVICE" --expected-candidate-inode "$CANDIDATE_INODE")
if [[ "$TARGET_MISSING" -eq 1 ]]; then
  commit_args+=(--expected-target-missing)
else
  read -r TARGET_DEVICE TARGET_INODE < <(/usr/bin/python3 -I -S -B - "$TARGET_IDENTITY" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
print(value["device"], value["inode"])
PY
  )
  commit_args+=(--expected-target-device "$TARGET_DEVICE" --expected-target-inode "$TARGET_INODE")
fi
COMMIT_RESULT="$(/usr/bin/python3 -I -S -B "$TRANSACTION_HELPER" "${commit_args[@]}")"
CANDIDATE=""

/usr/bin/python3 -I -S -B - "$PREPARE_RESULT" "$COMMIT_RESULT" <<'PY'
import json, sys
prepare = json.loads(sys.argv[1])
commit = json.loads(sys.argv[2])
print("restore transaction published atomically")
if prepare.get("quarantinedQueues"):
    print("pre-existing action queues: quarantined; replay requires an explicit confirmation")
if commit.get("rollback"):
    print(f"previous state retained for rollback: {commit['rollback']}")
print("user services: unchanged; activation requires a separate reviewed install")
PY
