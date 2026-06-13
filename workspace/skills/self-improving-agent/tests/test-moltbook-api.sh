#!/bin/bash
# Smoke tests for Moltbook agent: API connectivity, staging pipeline, relay script

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

MOLTBOOK_WS="$HOME/.openclaw/workspace-moltbook"
MEMORY_FILE="$MOLTBOOK_WS/MEMORY.md"
STAGING_DIR="$MOLTBOOK_WS/staging"
RELAY_SCRIPT="$HOME/.openclaw/workspace/scripts/moltbook-relay.sh"
SANITIZER_INPUT="$HOME/.openclaw/workspace-sanitizer/input"

# ── 1. MEMORY.md exists ───────────────────────────────────────────────────────
assert_file_exists "MEMORY.md exists" "$MEMORY_FILE"

# ── 2. API key present in MEMORY.md ──────────────────────────────────────────
api_key=""
if [ -f "$MEMORY_FILE" ]; then
    api_key=$(grep -oP 'MOLTBOOK_API_KEY:\s*\K\S+' "$MEMORY_FILE" || true)
fi
if [ -n "$api_key" ]; then
    pass "API key found in MEMORY.md"
else
    fail "API key found in MEMORY.md" "MOLTBOOK_API_KEY line missing or empty"
fi

# ── 3. API base URL present in MEMORY.md ─────────────────────────────────────
api_base=""
if [ -f "$MEMORY_FILE" ]; then
    api_base=$(grep -oP 'API base:\s*\K\S+' "$MEMORY_FILE" || true)
fi
if [ -n "$api_base" ]; then
    pass "API base URL found in MEMORY.md"
else
    fail "API base URL found in MEMORY.md" "API base line missing or empty"
fi

# ── 4. Staging dir exists (or can be created) ────────────────────────────────
mkdir -p "$STAGING_DIR" 2>/dev/null
if [ -d "$STAGING_DIR" ]; then
    pass "staging dir exists"
else
    fail "staging dir exists" "$STAGING_DIR not found and could not be created"
fi

# ── 5. Staging dir is writable ───────────────────────────────────────────────
tmpfile="$STAGING_DIR/.write-test-$$"
if touch "$tmpfile" 2>/dev/null; then
    rm -f "$tmpfile"
    pass "staging dir is writable"
else
    fail "staging dir is writable" "$STAGING_DIR not writable"
fi

# ── 6. Relay script exists and is executable ─────────────────────────────────
if [ -x "$RELAY_SCRIPT" ]; then
    pass "relay script exists and is executable"
elif [ -f "$RELAY_SCRIPT" ]; then
    fail "relay script exists and is executable" "$RELAY_SCRIPT exists but is not executable"
else
    fail "relay script exists and is executable" "$RELAY_SCRIPT not found"
fi

# ── 7. Relay moves a test file from staging to sanitizer input ───────────────
relay_test_file="$STAGING_DIR/test-relay-smoke-$$.md"
echo "# relay smoke test" > "$relay_test_file"
bash "$RELAY_SCRIPT" >/dev/null 2>&1
relay_exit=$?
if [ "$relay_exit" -eq 0 ] && [ ! -f "$relay_test_file" ] && [ -f "$SANITIZER_INPUT/$(basename "$relay_test_file")" ]; then
    pass "relay moves staging file to sanitizer input"
    rm -f "$SANITIZER_INPUT/$(basename "$relay_test_file")"
elif [ "$relay_exit" -ne 0 ]; then
    fail "relay moves staging file to sanitizer input" "relay script exited $relay_exit"
    rm -f "$relay_test_file"
else
    fail "relay moves staging file to sanitizer input" "file not moved (still in staging or not in sanitizer)"
    rm -f "$relay_test_file"
fi

# ── 8. API: /api/v1 health/reachability (unauthenticated) ────────────────────
if [ -n "$api_base" ]; then
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$api_base" 2>/dev/null || echo "000")
    if [[ "$http_code" =~ ^[2345][0-9][0-9]$ ]]; then
        pass "API base URL is reachable (HTTP $http_code)"
    else
        fail "API base URL is reachable" "curl returned HTTP $http_code (network down or DNS failure?)"
    fi
else
    fail "API base URL is reachable" "skipped — no api_base"
fi

# ── 9. API: key is not rejected (not 401/403) ────────────────────────────────
if [ -n "$api_key" ] && [ -n "$api_base" ]; then
    auth_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "Authorization: Bearer $api_key" \
        "$api_base/submolts/m/general" 2>/dev/null || echo "000")
    if [ "$auth_code" = "401" ] || [ "$auth_code" = "403" ]; then
        fail "API key not rejected (HTTP $auth_code)" "HTTP $auth_code — key may be invalid or expired"
    elif [ "$auth_code" = "000" ]; then
        fail "API key not rejected" "curl failed — network down?"
    elif [[ "$auth_code" =~ ^5[0-9][0-9]$ ]]; then
        fail "API key not rejected" "HTTP $auth_code — server error"
    else
        pass "API key not rejected (HTTP $auth_code)"
    fi
else
    fail "API key not rejected" "skipped — missing api_key or api_base"
fi

summary
