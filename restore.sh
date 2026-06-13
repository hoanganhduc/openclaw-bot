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
gpg --decrypt "$ARCHIVE" | tar -C "$PREFIX" -xzf -
echo "restore overlay complete"

if [[ "$SKIP_SERVICES" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

if [[ -x "$PREFIX/workspace/scripts/rollback_task.sh" ]]; then
  bash "$PREFIX/workspace/scripts/rollback_task.sh" status || true
fi
