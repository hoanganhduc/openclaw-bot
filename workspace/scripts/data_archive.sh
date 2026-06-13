#!/usr/bin/env bash
# data_archive.sh — Archive session transcripts older than 5 years.
# Memory files are NEVER archived — they stay active permanently.
# Archived items are compressed yearly and moved out of the active QMD index.
# Import back with: data_archive.sh import <year>
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-{{ OPENCLAW_WORKSPACE }}}"
AGENTS_DIR="${OPENCLAW_AGENTS_DIR:-{{ OPENCLAW_HOME }}/agents}"
ARCHIVE_DIR="$WORKSPACE/archive"
MANIFEST="$ARCHIVE_DIR/manifest.json"
CUTOFF_YEARS=5
CUTOFF_DATE=$(date -u -d "$CUTOFF_YEARS years ago" +%Y-%m-%d 2>/dev/null || date -u -v-${CUTOFF_YEARS}y +%Y-%m-%d)

mkdir -p "$ARCHIVE_DIR/sessions"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

init_manifest() {
  if [ ! -f "$MANIFEST" ]; then
    echo '{"archives":[],"last_run":"","cutoff_years":5}' > "$MANIFEST"
  fi
}

archive_sessions() {
  log "Archiving session transcripts older than $CUTOFF_DATE..."
  local count=0
  for agent_dir in "$AGENTS_DIR"/*/sessions/; do
    [ -d "$agent_dir" ] || continue
    local agent_name
    agent_name=$(basename "$(dirname "$agent_dir")")
    local staging="$ARCHIVE_DIR/sessions/$agent_name"
    mkdir -p "$staging"

    while IFS= read -r -d '' jsonl; do
      local mtime
      mtime=$(stat -c %Y "$jsonl" 2>/dev/null || stat -f %m "$jsonl")
      local file_date
      file_date=$(date -u -d "@$mtime" +%Y-%m-%d 2>/dev/null || date -u -r "$mtime" +%Y-%m-%d)
      local file_year
      file_year=$(echo "$file_date" | cut -d- -f1)

      if [[ "$file_date" < "$CUTOFF_DATE" ]]; then
        local year_dir="$staging/$file_year"
        mkdir -p "$year_dir"
        mv "$jsonl" "$year_dir/"
        count=$((count + 1))
      fi
    done < <(find "$agent_dir" -maxdepth 1 -name "*.jsonl" -print0 2>/dev/null)

    # Compress each year directory
    for year_dir in "$staging"/*/; do
      [ -d "$year_dir" ] || continue
      local year
      year=$(basename "$year_dir")
      local tarball="$ARCHIVE_DIR/sessions/${agent_name}_${year}.tar.gz"
      if [ ! -f "$tarball" ]; then
        tar -czf "$tarball" -C "$staging" "$year"
        rm -rf "$year_dir"
        log "  Compressed: $tarball"
      else
        local tmp_merge
        tmp_merge=$(mktemp -d)
        tar -xzf "$tarball" -C "$tmp_merge"
        cp "$year_dir"/* "$tmp_merge/$year/" 2>/dev/null || true
        tar -czf "$tarball" -C "$tmp_merge" "$year"
        rm -rf "$tmp_merge" "$year_dir"
        log "  Updated: $tarball"
      fi
    done
  done
  log "Archived $count session transcript(s)."
}

update_manifest() {
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local entries=""
  for tarball in "$ARCHIVE_DIR"/sessions/*.tar.gz; do
    [ -f "$tarball" ] || continue
    local name
    name=$(basename "$tarball")
    local size
    size=$(stat -c %s "$tarball" 2>/dev/null || stat -f %z "$tarball")
    entries="$entries{\"file\":\"$name\",\"category\":\"sessions\",\"size\":$size},"
  done
  entries="${entries%,}"

  cat > "$MANIFEST" <<EOF
{
  "cutoff_years": $CUTOFF_YEARS,
  "last_run": "$now",
  "cutoff_date": "$CUTOFF_DATE",
  "note": "Only session transcripts are archived. Memory files are never archived.",
  "archives": [$entries]
}
EOF
  log "Updated manifest: $MANIFEST"
}

import_year() {
  local year="$1"
  log "Importing archived sessions from year $year..."

  for tarball in "$ARCHIVE_DIR"/sessions/*_"${year}".tar.gz; do
    [ -f "$tarball" ] || continue
    local agent_name
    agent_name=$(basename "$tarball" | sed "s/_${year}\\.tar\\.gz//")
    local target="$AGENTS_DIR/$agent_name/sessions"
    mkdir -p "$target"
    local tmp_extract
    tmp_extract=$(mktemp -d)
    tar -xzf "$tarball" -C "$tmp_extract"
    cp "$tmp_extract/$year"/*.jsonl "$target/" 2>/dev/null || true
    rm -rf "$tmp_extract"
    log "  Restored sessions for agent: $agent_name"
  done

  log "Import complete. Run 'openclaw memory reindex' to update QMD search index."
}

list_archives() {
  if [ ! -f "$MANIFEST" ]; then
    echo "No archives found."
    return
  fi
  echo "=== Data Archives ==="
  echo "Cutoff: $CUTOFF_YEARS years (before $CUTOFF_DATE)"
  echo "Note: Only session transcripts are archived. Memory files are never archived."
  echo ""
  python3 -c "
import json, sys
m = json.load(open('$MANIFEST'))
print(f'Last run: {m.get(\"last_run\", \"never\")}')
print(f'Archives: {len(m.get(\"archives\", []))}')
for a in m.get('archives', []):
    size_mb = a['size'] / 1024 / 1024
    print(f'  {a[\"category\"]:10s} {a[\"file\"]:40s} {size_mb:.1f} MB')
" 2>/dev/null || cat "$MANIFEST"
}

case "${1:-run}" in
  run)
    init_manifest
    archive_sessions
    update_manifest
    log "Archival complete."
    ;;
  import)
    if [ -z "${2:-}" ]; then
      echo "Usage: $0 import <year>" >&2
      exit 1
    fi
    import_year "$2"
    ;;
  list)
    list_archives
    ;;
  *)
    echo "Usage: $0 {run|import <year>|list}" >&2
    exit 1
    ;;
esac
