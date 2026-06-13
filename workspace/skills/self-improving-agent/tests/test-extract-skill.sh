#!/bin/bash
# Tests for scripts/extract-skill.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

EXTRACT="$SCRIPTS_DIR/extract-skill.sh"

run_extract() {
    local ws="$1"; shift
    (cd "$ws" && bash "$EXTRACT" "$@")
}

run_extract_out() {
    local ws="$1"; shift
    (cd "$ws" && bash "$EXTRACT" "$@" 2>&1)
}

# 1. No args → exit 1, "Skill name is required"
ws=$(new_ws)
out=$(run_extract_out "$ws" 2>&1)
assert_exit "no args: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT'"
assert_contains "no args: skill name required message" "Skill name is required" "$out"

# 2. --help → exit 0, usage shown
ws=$(new_ws)
out=$(run_extract_out "$ws" --help)
assert_exit "--help: exit 0" 0 bash -c "cd '$ws' && bash '$EXTRACT' --help"
assert_contains "--help: usage shown" "Usage:" "$out"

# 3. Name with spaces → exit 1 (treated as two positional args → "Unexpected argument")
ws=$(new_ws)
out=$(run_extract_out "$ws" "foo" "bar")
assert_exit "two args: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' foo bar"
assert_contains "two args: unexpected argument error" "Unexpected argument" "$out"

# 4. Name with uppercase → exit 1
ws=$(new_ws)
out=$(run_extract_out "$ws" "FooBar")
assert_exit "uppercase name: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' FooBar"
assert_contains "uppercase name: invalid format error" "Invalid skill name format" "$out"

# 5. Name starting with hyphen → exit 1 (parsed as unknown flag)
ws=$(new_ws)
out=$(run_extract_out "$ws" -- "-foo")
assert_exit "hyphen-start name: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' -- -foo"

# 6. --output-dir with absolute path → exit 1
ws=$(new_ws)
out=$(run_extract_out "$ws" my-skill --output-dir /tmp/absolute)
assert_exit "absolute output-dir: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' my-skill --output-dir /tmp/absolute"
assert_contains "absolute output-dir: error message" "relative path" "$out"

# 7. --output-dir with .. traversal → exit 1
ws=$(new_ws)
out=$(run_extract_out "$ws" my-skill --output-dir "../escape")
assert_exit "path traversal: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' my-skill --output-dir ../escape"
assert_contains "path traversal: error message" ".." "$out"

# 8. --output-dir with no argument → exit 1
ws=$(new_ws)
assert_exit "--output-dir no arg: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' my-skill --output-dir"

# 9. Unknown flag → exit 1
ws=$(new_ws)
assert_exit "unknown flag: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' --foobar"

# 10. --dry-run → exit 0, no directory created, SKILL.md template shown
ws=$(new_ws)
out=$(run_extract_out "$ws" dry-run-skill --dry-run)
assert_exit "--dry-run: exit 0" 0 bash -c "cd '$ws' && bash '$EXTRACT' dry-run-skill --dry-run"
assert_contains "--dry-run: SKILL.md mentioned" "SKILL.md" "$out"
assert_contains "--dry-run: skill name in template" "dry-run-skill" "$out"
assert_file_not_exists "--dry-run: no dir created" "$ws/skills/dry-run-skill/SKILL.md"

# 11. Actual creation → exit 0, SKILL.md exists
ws=$(new_ws)
assert_exit "actual creation: exit 0" 0 bash -c "cd '$ws' && bash '$EXTRACT' my-real-skill"
assert_file_exists "actual creation: SKILL.md created" "$ws/skills/my-real-skill/SKILL.md"

# 12. SKILL.md name field matches skill name exactly
ws=$(new_ws)
run_extract "$ws" name-check-skill >/dev/null 2>&1
content=$(cat "$ws/skills/name-check-skill/SKILL.md" 2>/dev/null)
assert_contains "SKILL.md name field correct" 'name: name-check-skill' "$content"

# 13. Duplicate creation → exit 1, "Skill already exists"
ws=$(new_ws)
run_extract "$ws" dupe-skill >/dev/null 2>&1
out=$(run_extract_out "$ws" dupe-skill 2>&1)
assert_exit "duplicate: exit 1" 1 bash -c "cd '$ws' && bash '$EXTRACT' dupe-skill"
assert_contains "duplicate: error message" "Skill already exists" "$out"

# 14. --output-dir custom-dir → creates under ./custom-dir/
ws=$(new_ws)
run_extract "$ws" custom-skill --output-dir custom-dir >/dev/null 2>&1
assert_file_exists "custom output-dir: skill created there" "$ws/custom-dir/custom-skill/SKILL.md"
assert_file_not_exists "custom output-dir: not in default skills/" "$ws/skills/custom-skill/SKILL.md"

# 15. Single-char name → valid
ws=$(new_ws)
assert_exit "single-char name: exit 0 (dry-run)" 0 bash -c "cd '$ws' && bash '$EXTRACT' a --dry-run"

# 16. Numbers in name → valid
ws=$(new_ws)
assert_exit "numbers in name: exit 0 (dry-run)" 0 bash -c "cd '$ws' && bash '$EXTRACT' tool2fix --dry-run"

summary
