#!/bin/bash
# Tests for scripts/error-detector.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

DETECTOR="$SCRIPTS_DIR/error-detector.sh"

run_detector() {
    CLAUDE_TOOL_OUTPUT="$1" bash "$DETECTOR"
}

# 1. Clean output → silent, exit 0
out=$(run_detector "All tests passed successfully.")
assert_eq "clean output: silent stdout" "" "$out"
assert_exit "clean output: exit 0" 0 bash -c "CLAUDE_TOOL_OUTPUT='All tests passed.' bash '$DETECTOR'"

# 2. "Error:" prefix → fires with <error-detected>
out=$(run_detector "Error: something went wrong")
assert_contains "Error: fires" "<error-detected>" "$out"

# 3. "Traceback" → fires (Python stack trace)
out=$(run_detector "Traceback (most recent call last):")
assert_contains "Traceback: fires" "<error-detected>" "$out"

# 4. "npm ERR!" → fires
out=$(run_detector "npm ERR! code ENOENT")
assert_contains "npm ERR!: fires" "<error-detected>" "$out"

# 5. "Permission denied" → fires
out=$(run_detector "bash: ./script.sh: Permission denied")
assert_contains "Permission denied: fires" "<error-detected>" "$out"

# 6. "No such file" → fires
out=$(run_detector "cat: /tmp/missing.txt: No such file or directory")
assert_contains "No such file: fires" "<error-detected>" "$out"

# 7. "fatal:" → fires (git errors)
out=$(run_detector "fatal: not a git repository")
assert_contains "fatal:: fires" "<error-detected>" "$out"

# 8. Empty CLAUDE_TOOL_OUTPUT → silent
out=$(run_detector "")
assert_eq "empty output: silent" "" "$out"

# 9. Word containing common letters but no pattern → no false positive
out=$(run_detector "Everything ran correctly and succeeded.")
assert_eq "false-positive guard: correctly/succeeded → silent" "" "$out"

# 10. Fired block contains the format hint
out=$(run_detector "TypeError: cannot read property")
assert_contains "fired block contains ERR format hint" "[ERR-YYYYMMDD-XXX]" "$out"

summary
