#!/bin/bash
# Tests for scripts/review.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

REVIEW="$SCRIPTS_DIR/review.sh"

run_review() {
    local ws="$1"; shift
    OPENCLAW_WORKSPACE="$ws" bash "$REVIEW" "$@"
}

# 1. No .learnings dir → informational message, exit 0
ws=$(new_ws)  # no .learnings subdir
out=$(run_review "$ws")
assert_contains "no .learnings: message shown" "No .learnings directory found" "$out"
assert_exit "no .learnings: exit 0" 0 bash -c "OPENCLAW_WORKSPACE='$ws' bash '$REVIEW'"

# 2. Empty dir (no files at all) → Total pending: 0
ws=$(new_learnings_ws)
out=$(run_review "$ws")
assert_contains "empty dir: Total pending: 0" "Total pending: 0" "$out"

# 3. One high-priority pending → 🟠, total 1, ID shown
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_high.md" "$ws/.learnings/ERRORS.md"
out=$(run_review "$ws")
assert_contains "high: 🟠 flag shown" "🟠" "$out"
assert_contains "high: Total pending: 1" "Total pending: 1" "$out"
assert_contains "high: entry ID shown" "[ERR-20260101-001]" "$out"

# 4. Critical entry → 🔴 flag
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_critical.md" "$ws/.learnings/ERRORS.md"
out=$(run_review "$ws")
assert_contains "critical: 🔴 flag shown" "🔴" "$out"

# 5. Medium entry → 🟡 flag
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_medium.md" "$ws/.learnings/LEARNINGS.md"
out=$(run_review "$ws")
assert_contains "medium: 🟡 flag shown" "🟡" "$out"

# 6. Low entry → ⚪ flag
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_low.md" "$ws/.learnings/LEARNINGS.md"
out=$(run_review "$ws")
assert_contains "low: ⚪ flag shown" "⚪" "$out"

# 7. Resolved entry → Total pending: 0
ws=$(new_learnings_ws)
copy_fixture "learnings_one_resolved.md" "$ws/.learnings/ERRORS.md"
out=$(run_review "$ws")
assert_contains "resolved: not counted" "Total pending: 0" "$out"

# 8. Promoted entry → Total pending: 0
ws=$(new_learnings_ws)
copy_fixture "learnings_one_promoted.md" "$ws/.learnings/LEARNINGS.md"
out=$(run_review "$ws")
assert_contains "promoted: not counted" "Total pending: 0" "$out"

# 9. Multi: 1 high + 1 low pending, 1 resolved → Total pending: 2
ws=$(new_learnings_ws)
copy_fixture "learnings_multi.md" "$ws/.learnings/ERRORS.md"
out=$(run_review "$ws")
assert_contains "multi: Total pending: 2" "Total pending: 2" "$out"

# 10. --high-only: only high entry displayed; low entry hidden; total still 2
ws=$(new_learnings_ws)
copy_fixture "learnings_multi.md" "$ws/.learnings/ERRORS.md"
out=$(run_review "$ws" --high-only)
assert_contains     "--high-only: high entry shown" "🟠" "$out"
assert_not_contains "--high-only: low entry hidden" "⚪" "$out"
assert_contains     "--high-only: total still counts all" "Total pending: 2" "$out"

# 11. Entries across ERRORS.md and LEARNINGS.md → summed correctly
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_high.md" "$ws/.learnings/ERRORS.md"
copy_fixture "learnings_one_pending_low.md"  "$ws/.learnings/LEARNINGS.md"
out=$(run_review "$ws")
assert_contains "cross-file count: Total pending: 2" "Total pending: 2" "$out"

# 12. Malformed file (no ## [ headers) → no crash, exit 0
ws=$(new_learnings_ws)
copy_fixture "learnings_empty.md" "$ws/.learnings/ERRORS.md"
assert_exit "malformed file: exit 0" 0 bash -c "OPENCLAW_WORKSPACE='$ws' bash '$REVIEW'"

summary
