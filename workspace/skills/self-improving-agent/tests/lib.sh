#!/bin/bash
# Shared helpers for self-improving-agent smoke tests

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$TESTS_DIR/fixtures"
SKILL_DIR="$(dirname "$TESTS_DIR")"
WORKSPACE_DIR="$(dirname "$(dirname "$SKILL_DIR")")"
SCRIPTS_DIR="$SKILL_DIR/scripts"
HANDLER_JS="$SKILL_DIR/hooks/openclaw/handler.js"
INGEST_PY="$WORKSPACE_DIR/scripts/ingest_library.py"

_PASS=0
_FAIL=0

pass() { _PASS=$((_PASS + 1)); echo "ok $((_PASS + _FAIL)) - $1"; }
fail() { _FAIL=$((_FAIL + 1)); echo "not ok $((_PASS + _FAIL)) - $1${2:+ # $2}"; }

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        pass "$label"
    else
        fail "$label" "expected: $(printf '%q' "$expected") | got: $(printf '%q' "$actual")"
    fi
}

assert_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label" "needle not found: $needle"
    fi
}

assert_not_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label" "needle unexpectedly found: $needle"
    fi
}

assert_exit() {
    local label="$1" expected_code="$2"
    shift 2
    "$@" >/dev/null 2>&1
    local actual_code=$?
    if [ "$actual_code" = "$expected_code" ]; then
        pass "$label"
    else
        fail "$label" "expected exit $expected_code, got $actual_code"
    fi
}

assert_file_exists() {
    local label="$1" path="$2"
    if [ -f "$path" ]; then
        pass "$label"
    else
        fail "$label" "file not found: $path"
    fi
}

assert_file_not_exists() {
    local label="$1" path="$2"
    if [ ! -f "$path" ]; then
        pass "$label"
    else
        fail "$label" "file should not exist: $path"
    fi
}

# Create isolated temp workspace; register cleanup on EXIT.
# Sets TEST_WORKSPACE.
CLEANUP_DIRS=()
_cleanup_all() { for d in "${CLEANUP_DIRS[@]}"; do rm -rf "$d"; done; }
trap _cleanup_all EXIT

new_ws() {
    local ws
    ws=$(mktemp -d)
    CLEANUP_DIRS+=("$ws")
    echo "$ws"
}

# Create a workspace with a .learnings/ subdirectory.
new_learnings_ws() {
    local ws
    ws=$(new_ws)
    mkdir -p "$ws/.learnings"
    echo "$ws"
}

# Create a workspace suitable for ingest_library.py tests.
new_ingest_ws() {
    local ws
    ws=$(new_ws)
    mkdir -p "$ws/data/calibre/cache"
    mkdir -p "$ws/data/library"
    mkdir -p "$ws/memory"
    echo "$ws"
}

copy_fixture() {
    local src="$1" dest="$2"
    cp "$FIXTURES_DIR/$src" "$dest"
}

summary() {
    local total=$((_PASS + _FAIL))
    echo ""
    echo "# Tests: $_PASS passed, $_FAIL failed (total: $total)"
    [ "$_FAIL" -eq 0 ]
}
