#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./restore.sh --archive FILE [--prefix DIR] [--dry-run] [--skip-services] [--overlay-only]
Env:   OPENCLAW_BACKUP_PASSPHRASE_FILE=/path (non-interactive gpg batch/loopback)

Installs public baseline, then overlays an owner-private encrypted backup.
EOF
}

PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
ARCHIVE=""
DRY_RUN=0
SKIP_SERVICES=0
OVERLAY_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-services) SKIP_SERVICES=1; shift ;;
    --overlay-only) OVERLAY_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$ARCHIVE" ]] || { usage >&2; exit 2; }
[[ -f "$ARCHIVE" ]] || { echo "archive not found: $ARCHIVE" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$OVERLAY_ONLY" -eq 0 ]]; then
  install_args=(--prefix "$PREFIX" --skip-config --skip-openclaw-install --convergent)
  [[ "$DRY_RUN" -eq 1 ]] && install_args+=(--dry-run)
  [[ "$SKIP_SERVICES" -eq 1 ]] && install_args+=(--skip-services)
  "$SCRIPT_DIR/install.sh" "${install_args[@]}"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would decrypt and restore $ARCHIVE into $PREFIX"
  exit 0
fi

if ! command -v gpg >/dev/null 2>&1; then
  echo "gpg not found; cannot restore encrypted archive" >&2
  exit 1
fi

mkdir -p "$PREFIX"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openclaw-restore.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

TAR_PATH="$TMP_DIR/private.tar.gz"
GPG_PASS_OPTS=()
if [[ -n "${OPENCLAW_BACKUP_PASSPHRASE_FILE:-}" ]]; then
  [[ -r "$OPENCLAW_BACKUP_PASSPHRASE_FILE" ]] \
    || { echo "passphrase file not readable: $OPENCLAW_BACKUP_PASSPHRASE_FILE" >&2; exit 2; }
  GPG_PASS_OPTS=(--batch --pinentry-mode loopback --passphrase-file "$OPENCLAW_BACKUP_PASSPHRASE_FILE")
fi
gpg "${GPG_PASS_OPTS[@]}" --decrypt "$ARCHIVE" > "$TAR_PATH"
python3 - "$TAR_PATH" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

archive = sys.argv[1]
allowed = {
    "openclaw.json", "secrets.json", ".env", ".stignore", "credentials",
    "identity", "cron", "plugins", "extensions", "hooks", "skills",
    "workspace", "media", "agents", "memory", "logs", "browser", "tasks",
    "flows", "workspace-host", "workspace-moltbook", "workspace-review",
    "workspace-sanitizer", "workspace-moltbook-reviewer",
}
total_size = 0
member_count = 0
with tarfile.open(archive, "r:gz") as tar:
    for member in tar.getmembers():
        member_count += 1
        if member_count > 1_000_000:
            raise SystemExit("restore archive has too many members")
        name = member.name
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {name!r}")
        if path.parts[0] not in allowed:
            raise SystemExit(f"archive path is outside the owner-data allowlist: {name!r}")
        if member.islnk() or member.issym():
            raise SystemExit(f"links are not allowed in restore archive: {name!r}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive member type: {name!r}")
        if member.isfile():
            total_size += member.size
            if total_size > 20 * 1024 * 1024 * 1024:
                raise SystemExit("restore archive expands beyond the 20 GiB limit")
PY
STAGE="$TMP_DIR/stage"
mkdir -p "$STAGE"
tar -C "$STAGE" -xzf "$TAR_PATH" --no-same-owner --no-same-permissions
python3 - "$STAGE" "$PREFIX" <<'PY'
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

stage = Path(sys.argv[1])
prefix = Path(sys.argv[2])

# Refuse to traverse a pre-existing symlink at the restore root or any of its
# ancestors. Archive members were already proved link-free above; this closes
# the corresponding destination-side escape route.
cursor = prefix
while True:
    if cursor.is_symlink():
        raise SystemExit(f"restore destination traverses a symlink: {cursor}")
    if cursor == cursor.parent:
        break
    cursor = cursor.parent
if prefix.exists() and not prefix.is_dir():
    raise SystemExit(f"restore destination is not a directory: {prefix}")
prefix.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_real_directory(path: Path, source_mode: int = 0o700) -> None:
    relative = path.relative_to(prefix)
    current = prefix
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f"restore destination contains a symlink: {current}")
        if current.exists():
            if not current.is_dir():
                raise SystemExit(f"restore directory collides with a non-directory: {current}")
        else:
            current.mkdir(mode=source_mode)


for root, directories, files in os.walk(stage, topdown=True, followlinks=False):
    source_root = Path(root)
    relative_root = source_root.relative_to(stage)
    destination_root = prefix / relative_root
    source_mode = stat.S_IMODE(source_root.stat(follow_symlinks=False).st_mode)
    ensure_real_directory(destination_root, source_mode)

    for directory in directories:
        source = source_root / directory
        mode = stat.S_IMODE(source.stat(follow_symlinks=False).st_mode)
        ensure_real_directory(destination_root / directory, mode)

    for filename in files:
        source = source_root / filename
        destination = destination_root / filename
        if destination.is_symlink():
            raise SystemExit(f"restore file collides with a symlink: {destination}")
        if destination.exists() and not destination.is_file():
            raise SystemExit(f"restore file collides with a non-file: {destination}")
        mode = stat.S_IMODE(source.stat(follow_symlinks=False).st_mode)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.restore.", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), mode)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
PY
echo "restore overlay complete"

if [[ "$SKIP_SERVICES" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

if [[ -x "$PREFIX/workspace/scripts/rollback_task.sh" ]]; then
  bash "$PREFIX/workspace/scripts/rollback_task.sh" status || true
fi
