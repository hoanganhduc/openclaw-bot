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
Usage: ./backup.sh [--prefix DIR] [--dry-run] [--verify] [--output DIR]
       [--allow-persistent-plaintext-staging ACKNOWLEDGE_PERSISTENT_OWNER_PLAINTEXT]
Env:   OPENCLAW_BACKUP_PASSPHRASE_FILE=/path (non-interactive gpg batch/loopback)

Creates an owner-private encrypted archive. This script may include private
data; it must not be used as public sync input.
EOF
}

ORIGINAL_ARGS=("$@")
PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
OUTPUT="$PWD/backups"
DRY_RUN=0
VERIFY=0
PERSISTENT_PLAINTEXT_ACK=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER_ARCHIVE_HELPER="$SCRIPT_DIR/scripts/owner_archive.py"
PRIVATE_TMP_HELPER="$SCRIPT_DIR/scripts/private_tmp.py"
ACCOUNT_HOME="$(/usr/bin/python3 -I -S -B -c 'import os,pwd; print(pwd.getpwuid(os.geteuid()).pw_dir)')"
OPENCLAW_CLI="$ACCOUNT_HOME/.npm-global/lib/node_modules/openclaw/openclaw.mjs"
OPENCLAW_NODE=/usr/bin/node
# A coding-system restore links the package into a sealed npm closure and has
# no system Node; the closure runs on the sealed Node that ~/.npm-global links.
if [[ -L "$ACCOUNT_HOME/.npm-global/lib/node_modules/openclaw" ]]; then
  OPENCLAW_NODE="$ACCOUNT_HOME/.npm-global/bin/node"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --verify) VERIFY=1; shift ;;
    --allow-persistent-plaintext-staging)
      PERSISTENT_PLAINTEXT_ACK="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

PREFIX="$(/usr/bin/python3 -I -S -B -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$PREFIX")"
OUTPUT="$(/usr/bin/python3 -I -S -B -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$OUTPUT")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$OUTPUT/openclaw-private-$STAMP.tar.gz.gpg"

INCLUDE=(
  "openclaw.json"
  "secrets.json"
  ".env"
  ".stignore"
  "credentials"
  "devices"
  "identity"
  "state"
  "cron"
  "workspace/data"
  "workspace/memory"
  "workspace/reports"
  "media"
  "agents"
  "memory"
  "logs"
  "tasks"
  "flows"
  "workspace-host/data"
  "workspace-host/memory"
  "workspace-host/reports"
  "workspace-moltbook/data"
  "workspace-moltbook/memory"
  "workspace-moltbook/reports"
  "workspace-review/data"
  "workspace-review/memory"
  "workspace-review/reports"
  "workspace-sanitizer/data"
  "workspace-sanitizer/memory"
  "workspace-sanitizer/reports"
  "workspace-moltbook-reviewer/data"
  "workspace-moltbook-reviewer/memory"
  "workspace-moltbook-reviewer/reports"
)

LOCK_HELPER="$SCRIPT_DIR/scripts/owner_state_lock.py"
[[ -f "$LOCK_HELPER" && ! -L "$LOCK_HELPER" ]] || {
  echo "owner-state lock helper is missing or unsafe" >&2
  exit 2
}
PREFIX_PARENT="$(dirname -- "$PREFIX")"
PREFIX_NAME="$(basename -- "$PREFIX")"
LOCK_PATH="$PREFIX_PARENT/.${PREFIX_NAME}.owner-state.lock"
if [[ "${OPENCLAW_OWNER_STATE_LOCK:-}" != "$LOCK_PATH" ]]; then
  exec /usr/bin/python3 -I -S -B "$LOCK_HELPER" --lock-path "$LOCK_PATH" -- \
    "$SCRIPT_DIR/backup.sh" "${ORIGINAL_ARGS[@]}"
fi
/usr/bin/python3 -I -S -B "$LOCK_HELPER" --lock-path "$LOCK_PATH" --validate-inherited

existing=()
for item in "${INCLUDE[@]}"; do
  [[ -e "$PREFIX/$item" ]] && existing+=("$item")
done

echo "prefix: $PREFIX"
echo "items: ${#existing[@]}"
if [[ "$DRY_RUN" -eq 1 ]]; then
  [[ -x "$OPENCLAW_NODE" && -f "$OPENCLAW_CLI" && ! -L "$OPENCLAW_CLI" ]] \
    || { echo "pinned OpenClaw CLI is unavailable; canonical SQLite snapshot is unavailable" >&2; exit 2; }
  OPENCLAW_STATE_DIR="$PREFIX" OPENCLAW_CONFIG_PATH="$PREFIX/openclaw.json" \
    "$OPENCLAW_NODE" "$OPENCLAW_CLI" backup create \
      --no-include-workspace --dry-run --json >/dev/null
  printf '%s\n' "${existing[@]}"
  exit 0
fi

/usr/bin/python3 -I -S -B - "$OUTPUT" <<'PY'
import os, stat, sys
path = os.path.abspath(os.path.expanduser(sys.argv[1]))
descriptor = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
try:
    for component in path.split(os.sep)[1:]:
        if not component:
            continue
        try:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
        except FileNotFoundError:
            os.mkdir(component, 0o700, dir_fd=descriptor)
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
        os.close(descriptor)
        descriptor = next_descriptor
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid():
        raise SystemExit("backup output directory has the wrong owner")
    os.fchmod(descriptor, 0o700)
finally:
    os.close(descriptor)
PY
if [[ ! -x /usr/bin/gpg || -L /usr/bin/gpg ]]; then
  echo "trusted /usr/bin/gpg is unavailable; refusing private backup" >&2
  exit 1
fi
if [[ ! -x "$OPENCLAW_NODE" || ! -f "$OPENCLAW_CLI" || -L "$OPENCLAW_CLI" ]]; then
  echo "pinned OpenClaw CLI is unavailable; refusing a direct live SQLite copy" >&2
  exit 2
fi
[[ -f "$OWNER_ARCHIVE_HELPER" && ! -L "$OWNER_ARCHIVE_HELPER" ]] \
  || { echo "owner archive helper is missing or unsafe" >&2; exit 2; }
[[ -f "$PRIVATE_TMP_HELPER" && ! -L "$PRIVATE_TMP_HELPER" ]] \
  || { echo "private plaintext staging helper is missing or unsafe" >&2; exit 2; }

EXPECTED_VERSION="$(/usr/bin/python3 -I -S -B - "$SCRIPT_DIR/REBUILD-MANIFEST.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["openclaw"]["observed_version"])
PY
)"

# Backup is intentionally non-mutating. Existing canonical databases are
# snapshotted as-is; legacy JSON/model authority remains authenticated but inert
# and is materialized only by an explicit migration or inside verified restore
# staging after authority activation has been authorized.
# OpenClaw owns locking for canonical *.sqlite state. The helper combines those
# snapshots with its inert data allowlist, snapshots persistent *.db/*.sqlite3
# through SQLite's backup API, and rejects sidecars or unsupported SQLite names.
tmp_args=(create)
[[ -z "$PERSISTENT_PLAINTEXT_ACK" ]] \
  || tmp_args+=(--allow-persistent "$PERSISTENT_PLAINTEXT_ACK")
TMP_DIR="$(/usr/bin/python3 -I -S -B "$PRIVATE_TMP_HELPER" "${tmp_args[@]}")"
PUBLISH_DIR="$(mktemp -d "$OUTPUT/.openclaw-owner-publish.XXXXXX")"
export TMPDIR="$TMP_DIR"
export OPENCLAW_OWNER_PLAINTEXT_TMPDIR="$TMP_DIR"
export OPENCLAW_OWNER_PERSISTENT_PLAINTEXT_ACK="$PERSISTENT_PLAINTEXT_ACK"
cleanup() {
  local original_status=$?
  local cleanup_failed=0
  trap - EXIT
  rm -rf -- "$PUBLISH_DIR" || cleanup_failed=1
  remove_args=(remove --path "$TMP_DIR")
  [[ -z "$PERSISTENT_PLAINTEXT_ACK" ]] \
    || remove_args+=(--allow-persistent "$PERSISTENT_PLAINTEXT_ACK")
  if ! /usr/bin/python3 -I -S -B "$PRIVATE_TMP_HELPER" "${remove_args[@]}" \
      >/dev/null; then
    echo "owner backup plaintext staging cleanup failed" >&2
    cleanup_failed=1
  fi
  if [[ "$original_status" -ne 0 ]]; then
    exit "$original_status"
  fi
  [[ "$cleanup_failed" -eq 0 ]] || exit 70
}
trap cleanup EXIT
NATIVE_ARCHIVE="$TMP_DIR/openclaw-native.tar.gz"
OWNER_TAR="$TMP_DIR/owner.tar.gz"
ENCRYPTED_TMP="$PUBLISH_DIR/archive.gpg"
OPENCLAW_STATE_DIR="$PREFIX" OPENCLAW_CONFIG_PATH="$PREFIX/openclaw.json" \
  "$OPENCLAW_NODE" "$OPENCLAW_CLI" backup create --no-include-workspace --verify \
    --output "$NATIVE_ARCHIVE" >/dev/null
/usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" build \
  --native-archive "$NATIVE_ARCHIVE" --state-dir "$PREFIX" --output "$OWNER_TAR"
/usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" verify \
  --archive "$OWNER_TAR" --expected-runtime-version "$EXPECTED_VERSION" >/dev/null
crypt_args=(encrypt --source "$OWNER_TAR" --output "$ENCRYPTED_TMP")
if [[ -n "${OPENCLAW_BACKUP_PASSPHRASE_FILE:-}" ]]; then
  PASS_FILE="$(/usr/bin/python3 -I -S -B -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$OPENCLAW_BACKUP_PASSPHRASE_FILE")"
  crypt_args+=(--passphrase-file "$PASS_FILE")
fi
/usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" "${crypt_args[@]}"
chmod 600 "$ENCRYPTED_TMP"

if [[ "$VERIFY" -eq 1 ]]; then
  VERIFY_TMP="$TMP_DIR/verify.tar.gz"
  decrypt_args=(decrypt --source "$ENCRYPTED_TMP" --output "$VERIFY_TMP")
  if [[ -n "${OPENCLAW_BACKUP_PASSPHRASE_FILE:-}" ]]; then
    decrypt_args+=(--passphrase-file "$PASS_FILE")
  fi
  /usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" "${decrypt_args[@]}"
  /usr/bin/python3 -I -S -B "$OWNER_ARCHIVE_HELPER" verify \
    --archive "$VERIFY_TMP" --expected-runtime-version "$EXPECTED_VERSION" >/dev/null
  echo "verify: ok"
fi

# Publish only a complete encrypted artifact, without overwriting a backup that
# another same-second invocation may already have committed.
/usr/bin/python3 -I -S -B - "$ENCRYPTED_TMP" "$ARCHIVE" <<'PY'
import os, stat, sys
source, destination = sys.argv[1:]

def open_directory(path):
    absolute = os.path.abspath(path)
    parts = absolute.split(os.sep)
    descriptor = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in parts[1:]:
            if not component:
                continue
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise

source_parent = open_directory(os.path.dirname(source))
destination_parent = open_directory(os.path.dirname(destination))
descriptor = os.open(
    os.path.basename(source),
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    dir_fd=source_parent,
)
try:
    information = os.fstat(descriptor)
    if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
        raise SystemExit("refusing to publish an unsafe owner backup")
    os.fsync(descriptor)
finally:
    os.close(descriptor)
try:
    os.link(
        os.path.basename(source),
        os.path.basename(destination),
        src_dir_fd=source_parent,
        dst_dir_fd=destination_parent,
        follow_symlinks=False,
    )
except FileExistsError as exc:
    raise SystemExit("refusing to overwrite an existing owner backup") from exc
try:
    os.fsync(destination_parent)
finally:
    os.close(source_parent)
    os.close(destination_parent)
PY
echo "wrote encrypted archive: $ARCHIVE"
