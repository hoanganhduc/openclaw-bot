#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./backup.sh [--prefix DIR] [--dry-run] [--verify] [--output DIR]\nEnv:   OPENCLAW_BACKUP_PASSPHRASE_FILE=/path (non-interactive gpg batch/loopback)

Creates an owner-private encrypted archive. This script may include private
data; it must not be used as public sync input.
EOF
}

PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
OUTPUT="$PWD/backups"
DRY_RUN=0
VERIFY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

PREFIX="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$PREFIX")"
OUTPUT="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$OUTPUT")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$OUTPUT/openclaw-private-$STAMP.tar.gz.gpg"

INCLUDE=(
  "openclaw.json"
  "secrets.json"
  ".env"
  ".stignore"
  "credentials"
  "identity"
  "cron"
  "plugins"
  "extensions"
  "hooks"
  "skills"
  "workspace/.git"
  "workspace/data"
  "workspace/memory"
  "workspace/reports"
  "workspace/scripts"
  "workspace/openclaw-scripts"
  "workspace/_control"
  "media"
  "agents"
  "memory"
  "logs"
  "browser"
  "tasks"
  "flows"
  "workspace-host"
  "workspace-moltbook"
  "workspace-review"
  "workspace-sanitizer"
  "workspace-moltbook-reviewer"
)

existing=()
for item in "${INCLUDE[@]}"; do
  [[ -e "$PREFIX/$item" ]] && existing+=("$item")
done

echo "prefix: $PREFIX"
echo "items: ${#existing[@]}"
if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '%s\n' "${existing[@]}"
  exit 0
fi

mkdir -p "$OUTPUT"
if ! command -v gpg >/dev/null 2>&1; then
  echo "gpg not found; refusing to write unencrypted private backup" >&2
  exit 1
fi

# Non-interactive mode: OPENCLAW_BACKUP_PASSPHRASE_FILE points at a passphrase
# file (chmod 600); gpg then runs batch/loopback so cron can drive this script.
# Without it, gpg prompts interactively as before.
GPG_PASS_OPTS=()
if [[ -n "${OPENCLAW_BACKUP_PASSPHRASE_FILE:-}" ]]; then
  [[ -r "$OPENCLAW_BACKUP_PASSPHRASE_FILE" ]] || { echo "passphrase file not readable: $OPENCLAW_BACKUP_PASSPHRASE_FILE" >&2; exit 2; }
  GPG_PASS_OPTS=(--batch --pinentry-mode loopback --passphrase-file "$OPENCLAW_BACKUP_PASSPHRASE_FILE")
fi

# Owner archives are deliberately link-free. Runtime-generated absolute links
# and dependency-tree links are reconstructed by the installer, while regular
# files are stored independently even when they share an inode.
(
  cd "$PREFIX"
  for item in "${existing[@]}"; do
    find -P "$item" \( -type d -o -type f \) -print0
  done
) | tar -C "$PREFIX" --null --verbatim-files-from --no-recursion \
      --hard-dereference -czf - -T - \
    | gpg "${GPG_PASS_OPTS[@]}" --symmetric --cipher-algo AES256 -o "$ARCHIVE"
chmod 600 "$ARCHIVE"
echo "wrote encrypted archive: $ARCHIVE"

if [[ "$VERIFY" -eq 1 ]]; then
  VERIFY_TMP="$(mktemp "${TMPDIR:-/tmp}/openclaw-backup-verify.XXXXXX.tar.gz")"
  trap 'rm -f -- "$VERIFY_TMP"' EXIT
  chmod 600 "$VERIFY_TMP"
  gpg "${GPG_PASS_OPTS[@]}" --decrypt "$ARCHIVE" > "$VERIFY_TMP" 2>/dev/null
  python3 - "$VERIFY_TMP" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

allowed = {
    "openclaw.json", "secrets.json", ".env", ".stignore", "credentials",
    "identity", "cron", "plugins", "extensions", "hooks", "skills",
    "workspace", "media", "agents", "memory", "logs", "browser", "tasks",
    "flows", "workspace-host", "workspace-moltbook", "workspace-review",
    "workspace-sanitizer", "workspace-moltbook-reviewer",
}
count = 0
size = 0
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive:
        count += 1
        path = PurePosixPath(member.name)
        if (
            count > 1_000_000
            or not member.name
            or path.is_absolute()
            or ".." in path.parts
            or path.parts[0] not in allowed
            or member.islnk()
            or member.issym()
            or not (member.isfile() or member.isdir())
        ):
            raise SystemExit(f"unsafe owner archive member: {member.name!r}")
        if member.isfile():
            size += member.size
            if size > 20 * 1024 * 1024 * 1024:
                raise SystemExit("owner archive expands beyond 20 GiB")
PY
  rm -f -- "$VERIFY_TMP"
  trap - EXIT
  echo "verify: ok"
fi
