#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./test-roundtrip.sh [--keep] [--quick]

Runs a conservative local roundtrip in /tmp:
  sync dry-run -> temp install -> backup temp prefix dry-run -> deploy dry-run.
EOF
}

KEEP=0
QUICK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --quick) QUICK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openclaw-roundtrip.XXXXXX")"
STAGING="$RUN_DIR/staging"
PREFIX="$RUN_DIR/prefix"

cleanup() {
  if [[ "$KEEP" -eq 0 ]]; then
    rm -rf "$RUN_DIR"
  else
    echo "kept roundtrip dir: $RUN_DIR"
  fi
}
trap cleanup EXIT

echo "roundtrip dir: $RUN_DIR"
"$SCRIPT_DIR/sync.sh" --dry-run --staging "$STAGING"
"$STAGING/install.sh" --prefix "$PREFIX" --skip-openclaw-install --skip-docker --skip-services
"$SCRIPT_DIR/backup.sh" --prefix "$PREFIX" --dry-run >/dev/null
"$STAGING/deploy.sh" --prefix "$PREFIX" --dry-run

if [[ "$QUICK" -eq 0 ]]; then
  "$STAGING/sync.sh" --prefix "$PREFIX" --dry-run --staging "$RUN_DIR/resync"
fi

echo "roundtrip: ok"
