#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./backup.sh [--prefix DIR] [--dry-run] [--verify] [--output DIR]

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

tar -C "$PREFIX" -czf - "${existing[@]}" | gpg --symmetric --cipher-algo AES256 -o "$ARCHIVE"
chmod 600 "$ARCHIVE"
echo "wrote encrypted archive: $ARCHIVE"

if [[ "$VERIFY" -eq 1 ]]; then
  gpg --decrypt "$ARCHIVE" 2>/dev/null | tar -tzf - >/dev/null
  echo "verify: ok"
fi
