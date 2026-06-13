#!/bin/bash
# Tests for hooks/openclaw/handler.js

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"

# Helper: run a node snippet with OPENCLAW_WORKSPACE set
node_handler() {
    local ws="$1"
    OPENCLAW_WORKSPACE="$ws" node - <<'NODEOF'
const handler = require(process.env.HANDLER_JS);
const ws = process.env.OPENCLAW_WORKSPACE;
NODEOF
}

run_bootstrap() {
    local ws="$1"
    OPENCLAW_WORKSPACE="$ws" HANDLER_JS="$HANDLER_JS" node << EOF
const handler = require(process.env.HANDLER_JS);
const event = {type:'agent',action:'bootstrap',sessionKey:'test',context:{bootstrapFiles:[]}};
handler(event).then(()=>{
  const f = event.context.bootstrapFiles[0];
  process.stdout.write(f ? f.content : '__NO_FILE__');
});
EOF
}

run_reset() {
    local ws="$1"
    OPENCLAW_WORKSPACE="$ws" HANDLER_JS="$HANDLER_JS" node << EOF
const handler = require(process.env.HANDLER_JS);
const event = {type:'command',action:'reset',sessionKey:'test',context:{bootstrapFiles:[]}};
handler(event).then(()=>{
  const f = event.context.bootstrapFiles[0];
  process.stdout.write(f ? f.content : '__NO_FILE__');
});
EOF
}

# 1. Null event → no throw, exits 0
assert_exit "null event: no throw" 0 bash -c "HANDLER_JS='$HANDLER_JS' node -e \"
const h = require(process.env.HANDLER_JS);
h(null).then(()=>process.exit(0)).catch(()=>process.exit(1));
\""

# 2. String event → no throw
assert_exit "string event: no throw" 0 bash -c "HANDLER_JS='$HANDLER_JS' node -e \"
const h = require(process.env.HANDLER_JS);
h('hello').then(()=>process.exit(0)).catch(()=>process.exit(1));
\""

# 3. Event with no type → returns silently, no file injected
ws=$(new_learnings_ws)
out=$(OPENCLAW_WORKSPACE="$ws" HANDLER_JS="$HANDLER_JS" node << 'EOF'
const handler = require(process.env.HANDLER_JS);
const event = {action:'bootstrap',sessionKey:'test',context:{bootstrapFiles:[]}};
handler(event).then(()=>{
  process.stdout.write(event.context.bootstrapFiles.length === 0 ? 'empty' : 'injected');
});
EOF
)
assert_eq "no-type event: nothing injected" "empty" "$out"

# 4 & 5. Subagent session key → bootstrap skipped, bootstrapFiles stays empty
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_high.md" "$ws/.learnings/ERRORS.md"
out=$(OPENCLAW_WORKSPACE="$ws" HANDLER_JS="$HANDLER_JS" node << 'EOF'
const handler = require(process.env.HANDLER_JS);
const event = {type:'agent',action:'bootstrap',sessionKey:'main:subagent:child',context:{bootstrapFiles:[]}};
handler(event).then(()=>{
  process.stdout.write(event.context.bootstrapFiles.length === 0 ? 'empty' : 'injected');
});
EOF
)
assert_eq "subagent: bootstrap skipped" "empty" "$out"

# 6. Bootstrap with nonexistent workspace → "No pending learnings"
out=$(run_bootstrap "/nonexistent_path_xyz")
assert_contains "nonexistent ws: No pending learnings" "No pending learnings" "$out"
assert_contains "nonexistent ws: DECISIONS.md reminder" "DECISIONS.md" "$out"

# 7. Bootstrap with empty .learnings/ (dir exists, no files) → "No pending learnings"
ws=$(new_learnings_ws)
out=$(run_bootstrap "$ws")
assert_contains "empty .learnings: No pending learnings" "No pending learnings" "$out"

# 8. Bootstrap with one high-priority pending → count and high-priority shown
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_high.md" "$ws/.learnings/ERRORS.md"
out=$(run_bootstrap "$ws")
assert_contains "one high pending: count shown" "1 pending item" "$out"
assert_contains "one high pending: High-priority label" "High-priority" "$out"
assert_contains "one high pending: entry ID shown" "[ERR-20260101-001]" "$out"

# 9. Bootstrap with 3 pending (multi fixture: 2 pending + LEARNINGS file with 1 pending)
ws=$(new_learnings_ws)
copy_fixture "learnings_multi.md"            "$ws/.learnings/ERRORS.md"
copy_fixture "learnings_one_pending_low.md"  "$ws/.learnings/LEARNINGS.md"
out=$(run_bootstrap "$ws")
assert_contains "3 pending: count shown" "3 pending items" "$out"

# 10. Bootstrap with all resolved → "No pending learnings"
ws=$(new_learnings_ws)
copy_fixture "learnings_one_resolved.md" "$ws/.learnings/ERRORS.md"
out=$(run_bootstrap "$ws")
assert_contains "all resolved: No pending learnings" "No pending learnings" "$out"

# 11. Bootstrap with context missing → no throw
assert_exit "missing context: no throw" 0 bash -c "HANDLER_JS='$HANDLER_JS' node -e \"
const h = require(process.env.HANDLER_JS);
h({type:'agent',action:'bootstrap',sessionKey:'test'}).then(()=>process.exit(0)).catch(()=>process.exit(1));
\""

# 12. Bootstrap with bootstrapFiles not an array → no throw
assert_exit "non-array bootstrapFiles: no throw" 0 bash -c "HANDLER_JS='$HANDLER_JS' node -e \"
const h = require(process.env.HANDLER_JS);
h({type:'agent',action:'bootstrap',sessionKey:'test',context:{bootstrapFiles:'not-array'}}).then(()=>process.exit(0)).catch(()=>process.exit(1));
\""

# 13. command:reset with no pending items → bootstrapFiles stays empty
ws=$(new_learnings_ws)
copy_fixture "learnings_one_resolved.md" "$ws/.learnings/ERRORS.md"
out=$(run_reset "$ws")
assert_eq "reset: no pending → nothing injected" "__NO_FILE__" "$out"

# 14. command:reset with pending items → SELF_IMPROVEMENT_RESET_REMINDER.md injected
ws=$(new_learnings_ws)
copy_fixture "learnings_one_pending_high.md" "$ws/.learnings/ERRORS.md"
out=$(run_reset "$ws")
assert_contains "reset: reminder injected" "Before You Reset" "$out"
assert_contains "reset: pending count shown" "1 pending item" "$out"
assert_contains "reset: log-now message" "Log now before context is lost" "$out"

# 15. Regression: EOF entry (no trailing ---) must still be counted
#     This tests the \Z → (?![\s\S]) fix in handler.js
ws=$(new_learnings_ws)
copy_fixture "learnings_eof_pending.md" "$ws/.learnings/ERRORS.md"
out=$(run_bootstrap "$ws")
assert_contains "EOF regression: entry counted" "1 pending item" "$out"

# 16. Five pending entries → recent shows only last 3 (slice(-3))
ws=$(new_learnings_ws)
copy_fixture "learnings_five_pending.md" "$ws/.learnings/LEARNINGS.md"
out=$(run_bootstrap "$ws")
assert_contains     "recent slice: 5th entry shown"     "[LRN-20260101-014]" "$out"
assert_contains     "recent slice: 4th entry shown"     "[LRN-20260101-013]" "$out"
assert_contains     "recent slice: 3rd entry shown"     "[LRN-20260101-012]" "$out"
assert_not_contains "recent slice: 1st entry NOT shown" "[LRN-20260101-010]" "$out"
assert_not_contains "recent slice: 2nd entry NOT shown" "[LRN-20260101-011]" "$out"

summary
