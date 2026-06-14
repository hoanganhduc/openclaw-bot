#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./restore.sh --archive FILE [--prefix DIR] [--dry-run] [--skip-services]

Installs public baseline, then overlays an owner-private encrypted backup.
EOF
}

PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
ARCHIVE=""
DRY_RUN=0
SKIP_SERVICES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-services) SKIP_SERVICES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$ARCHIVE" ]] || { usage >&2; exit 2; }
[[ -f "$ARCHIVE" ]] || { echo "archive not found: $ARCHIVE" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_args=(--prefix "$PREFIX" --skip-config)
[[ "$DRY_RUN" -eq 1 ]] && install_args+=(--dry-run)
[[ "$SKIP_SERVICES" -eq 1 ]] && install_args+=(--skip-services)
"$SCRIPT_DIR/install.sh" "${install_args[@]}"

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
gpg --decrypt "$ARCHIVE" > "$TAR_PATH"
python3 - "$TAR_PATH" <<'PY'
import posixpath
import sys
import tarfile
from pathlib import PurePosixPath

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as tar:
    for member in tar.getmembers():
        name = member.name
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {name!r}")
        if member.islnk():
            raise SystemExit(f"hardlinks are not allowed in restore archive: {name!r}")
        if member.issym():
            target = PurePosixPath(member.linkname)
            if not member.linkname or target.is_absolute() or ".." in target.parts:
                raise SystemExit(f"unsafe symlink target in restore archive: {name!r} -> {member.linkname!r}")
            resolved = PurePosixPath(posixpath.normpath(str(path.parent / target)))
            if ".." in resolved.parts or resolved.is_absolute():
                raise SystemExit(f"symlink escapes restore root: {name!r} -> {member.linkname!r}")
            continue
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive member type: {name!r}")
PY
STAGE="$TMP_DIR/stage"
mkdir -p "$STAGE"
tar -C "$STAGE" -xzf "$TAR_PATH"
cp -a "$STAGE"/. "$PREFIX"/
echo "restore overlay complete"

if [[ "$SKIP_SERVICES" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

if [[ -x "$PREFIX/workspace/scripts/rollback_task.sh" ]]; then
  bash "$PREFIX/workspace/scripts/rollback_task.sh" status || true
fi
