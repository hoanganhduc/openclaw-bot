#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./deploy.sh [--prefix DIR] [--dry-run] [--confirm]

Deploys manifest-generated public artifacts to a live OpenClaw prefix by
delegating to install.sh. --confirm is required for non-dry-run deploys.
EOF
}

PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
DRY_RUN=0
CONFIRM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --confirm) CONFIRM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" -eq 0 && "$CONFIRM" -ne 1 ]]; then
  echo "refusing live deploy without --confirm" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
args=(--prefix "$PREFIX")
[[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
"$SCRIPT_DIR/install.sh" "${args[@]}"

echo "deploy complete"

