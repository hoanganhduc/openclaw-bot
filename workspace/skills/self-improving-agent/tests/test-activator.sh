#!/bin/bash
# Tests for scripts/activator.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

ACTIVATOR="$SCRIPTS_DIR/activator.sh"

# 1. Exit code is 0
assert_exit "exit code is 0" 0 bash "$ACTIVATOR"

# 2. Stdout contains opening tag
out=$(bash "$ACTIVATOR")
assert_contains "output contains <self-improvement-reminder>" "<self-improvement-reminder>" "$out"

# 3. Stdout contains closing tag
assert_contains "output contains </self-improvement-reminder>" "</self-improvement-reminder>" "$out"

# 4. Stdout contains the log instruction
assert_contains "output contains 'Log to .learnings/'" "Log to .learnings/" "$out"

# 5. No stderr output
stderr=$(bash "$ACTIVATOR" 2>&1 1>/dev/null)
assert_eq "no stderr output" "" "$stderr"

summary
