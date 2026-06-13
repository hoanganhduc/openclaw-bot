#!/bin/bash
# Smoke test runner for self-improving-agent
# Runs all test-*.sh files and reports aggregate results.

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$TESTS_DIR")"

pass_files=0
fail_files=0
fail_names=()

# ── Coverage check: every script must have a test file ────────────────────────
echo "Checking test coverage..."
coverage_ok=true
for script in "$SKILL_DIR"/scripts/*.sh; do
    name="$(basename "$script" .sh)"
    test_file="$TESTS_DIR/test-${name}.sh"
    if [ ! -f "$test_file" ]; then
        echo "  MISSING TEST: scripts/${name}.sh has no tests/test-${name}.sh"
        fail_names+=("test-${name}.sh [MISSING]")
        fail_files=$((fail_files + 1))
        coverage_ok=false
    fi
done
if $coverage_ok; then
    echo "  Coverage OK — all scripts have a test file."
fi
echo ""

for test_file in "$TESTS_DIR"/test-*.sh; do
    name="$(basename "$test_file")"
    printf "Running %-35s ... " "$name"

    output=$(bash "$test_file" 2>&1)
    exit_code=$?

    # Extract pass/fail counts from summary line
    summary_line=$(echo "$output" | grep "^# Tests:")
    passed=$(echo "$summary_line" | grep -oP '\d+(?= passed)')
    failed=$(echo "$summary_line" | grep -oP '\d+(?= failed)')

    if [ "$exit_code" -eq 0 ]; then
        echo "PASS (${passed}/${passed})"
        pass_files=$((pass_files + 1))
    else
        echo "FAIL (${passed}/$((passed + failed)))"
        fail_files=$((fail_files + 1))
        fail_names+=("$name")
        # Show failing test lines
        echo "$output" | grep "^not ok" | sed 's/^/    /'
    fi
done

echo ""
total=$((pass_files + fail_files))
if [ "$fail_files" -eq 0 ]; then
    echo "All $total test file(s) passed."
    exit 0
else
    echo "$fail_files of $total test file(s) had failures: ${fail_names[*]}"
    exit 1
fi
