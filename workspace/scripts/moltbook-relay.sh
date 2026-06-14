#!/usr/bin/env bash
# Relay: move moltbook staging files → sanitizer input queue
set -euo pipefail

OPENCLAW_HOME="${OPENCLAW_HOME:-{{ OPENCLAW_HOME }}}"
STAGING_DIR="$OPENCLAW_HOME/workspace-moltbook/staging"
INPUT_DIR="$OPENCLAW_HOME/workspace-sanitizer/input"

mkdir -p "$INPUT_DIR"

shopt -s nullglob
files=("$STAGING_DIR"/*.md)
if [[ ${#files[@]} -eq 0 ]]; then
    exit 0
fi

for f in "${files[@]}"; do
    fname="$(basename "$f")"
    mv "$f" "$INPUT_DIR/$fname"
    echo "relayed: $fname"
done
