#!/bin/bash
# Tests for scripts/ingest_library.py

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

INGEST="$INGEST_PY"

run_ingest() {
    local ws="$1"; shift
    OPENCLAW_WORKSPACE="$ws" python3 "$INGEST" "$@"
}

setup_cache() {
    local ws="$1" fixture="$2"
    copy_fixture "$fixture" "$ws/data/calibre/cache/library.json"
}

# ── --check mode ──────────────────────────────────────────────────────────────

# 1. --check with no ingested.json → {"ingested": false}
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --check --source calibre --id 1)
assert_contains "--check no file: ingested false" '"ingested": false' "$out"

# 2. --check with item already present → {"ingested": true}
ws=$(new_ingest_ws)
echo '[{"source":"calibre","id":"1","processed_at":"2026-01-01T00:00:00Z"}]' \
    > "$ws/data/library/ingested.json"
out=$(run_ingest "$ws" --check --source calibre --id 1)
assert_contains "--check present: ingested true" '"ingested": true' "$out"

# 3. --check with different id → false
ws=$(new_ingest_ws)
echo '[{"source":"calibre","id":"1","processed_at":"2026-01-01T00:00:00Z"}]' \
    > "$ws/data/library/ingested.json"
out=$(run_ingest "$ws" --check --source calibre --id 99)
assert_contains "--check different id: ingested false" '"ingested": false' "$out"

# ── --batch mode ──────────────────────────────────────────────────────────────

# 4. Missing cache → error JSON
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --batch --limit 5)
assert_contains "--batch missing cache: error status" '"status": "error"' "$out"

# 5. Valid {"items":[...]} cache → processes items, processed >= 1
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_valid.json"
out=$(run_ingest "$ws" --batch --limit 5)
assert_contains "--batch valid cache: ok status" '"status": "ok"' "$out"
assert_contains "--batch valid cache: processed > 0" '"processed": 2' "$out"

# 6. Cache with non-dict entries → no crash, only dicts processed
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_mixed.json"
out=$(run_ingest "$ws" --batch --limit 5 2>&1)
assert_contains "--batch mixed cache: ok status" '"status": "ok"' "$out"
assert_contains "--batch mixed cache: 2 valid dicts processed" '"processed": 2' "$out"

# 7. Bare list cache (not {"items":[...]}) → also works
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_bare_list.json"
out=$(run_ingest "$ws" --batch --limit 5)
assert_contains "--batch bare list: ok status" '"status": "ok"' "$out"
assert_contains "--batch bare list: processed 1" '"processed": 1' "$out"

# 8. All items already ingested → processed: 0
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_valid.json"
echo '[{"source":"calibre","id":"1","processed_at":"2026-01-01T00:00:00Z"},{"source":"calibre","id":"2","processed_at":"2026-01-01T00:00:00Z"}]' \
    > "$ws/data/library/ingested.json"
out=$(run_ingest "$ws" --batch --limit 5)
assert_contains "--batch all ingested: processed 0" '"processed": 0' "$out"

# 9. --limit 1 with 2 candidates → only 1 processed
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_valid.json"
out=$(run_ingest "$ws" --batch --limit 1)
assert_contains "--batch limit 1: only 1 processed" '"processed": 1' "$out"

# 10. Priority: research-tagged item processed first when limit=1
#     Item 1 has tags ["algorithm","computation"] (score +6), item 2 has none (score 0)
#     Item 1 also has ISBN (+10), so item 1 should be picked first
ws=$(new_ingest_ws)
setup_cache "$ws" "calibre_cache_valid.json"
out=$(run_ingest "$ws" --batch --limit 1)
assert_contains "--batch priority: item with tags/isbn first" '"id": 1' "$out"

# ── single-item ingestion ─────────────────────────────────────────────────────

calibre_item='{"id":100,"title":"Test Single Book","authors":["Tester, T."],"year":"2023","tags":[],"series":null,"formats":["pdf"],"identifiers":{},"description":null,"comments":null}'

# 11. Single Calibre item → status ok, file created
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --source calibre --data "$calibre_item")
assert_contains "single calibre: ok status" '"status": "ok"' "$out"
assert_contains "single calibre: path returned" '"path"' "$out"

# 12. Same item ingested twice → second call skipped
ws=$(new_ingest_ws)
run_ingest "$ws" --source calibre --data "$calibre_item" >/dev/null
out=$(run_ingest "$ws" --source calibre --data "$calibre_item")
assert_contains "double ingest: skipped" '"status": "skipped"' "$out"

# 13. Single Zotero item → status ok, file in memory/papers/
zotero_item='{"key":"ABCD1234","title":"Test Zotero Paper","itemType":"journalArticle","date":"2022","DOI":"10.1234/test","url":"","abstractNote":"Test abstract.","creators":[{"creatorType":"author","lastName":"Smith","firstName":"John"}],"tags":[{"tag":"graph"}],"publicationTitle":"J. Tests","extra":"Citation Key: Smith2022"}'
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --source zotero --data "$zotero_item")
assert_contains "single zotero: ok status" '"status": "ok"' "$out"
# path should be under memory/papers/
path=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))")
assert_contains "single zotero: file in papers/" "papers/" "$path"

# 14. HTML stripped from description
html_item='{"id":101,"title":"HTML Book","authors":["A. Author"],"year":"2020","tags":[],"series":null,"formats":["pdf"],"identifiers":{},"description":"<b>Bold</b> and <p>paragraph</p> text.","comments":null}'
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --source calibre --data "$html_item")
path=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))")
content=$(cat "$path" 2>/dev/null)
assert_not_contains "html stripped: no <b> tags" "<b>" "$content"
assert_not_contains "html stripped: no <p> tags" "<p>" "$content"
assert_contains "html stripped: text preserved" "Bold" "$content"

# 15. Title with double-quotes → safely escaped in YAML frontmatter
quote_item='{"id":102,"title":"The \"Quoted\" Title: A Study","authors":["Q. Author"],"year":"2021","tags":[],"series":null,"formats":["pdf"],"identifiers":{},"description":null,"comments":null}'
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --source calibre --data "$quote_item")
assert_contains "quoted title: ok status" '"status": "ok"' "$out"
path=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))")
assert_file_exists "quoted title: file created" "$path"

# 16. identifiers: null in calibre item → no crash
null_ids_item='{"id":103,"title":"Null Ids Book","authors":["N. Author"],"year":"2019","tags":[],"series":null,"formats":["pdf"],"identifiers":null,"description":null,"comments":null}'
ws=$(new_ingest_ws)
out=$(run_ingest "$ws" --source calibre --data "$null_ids_item")
assert_contains "null identifiers: ok status" '"status": "ok"' "$out"

# 17. memory/books/ dir auto-created when it doesn't exist
ws=$(new_ingest_ws)
rm -rf "$ws/memory"  # remove memory dir entirely to test auto-creation
out=$(run_ingest "$ws" --source calibre --data "$calibre_item")
assert_contains "auto-create memory: ok status" '"status": "ok"' "$out"
path=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('path',''))")
assert_file_exists "auto-create memory: file exists" "$path"

summary
