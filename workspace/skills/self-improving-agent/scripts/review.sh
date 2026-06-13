#!/bin/bash
# Self-Improvement Review Helper
# Shows a summary of pending learnings for quick triage
# Usage: ./review.sh [--high-only]

LEARNINGS_DIR="${OPENCLAW_WORKSPACE:-${HOME}/.openclaw/workspace}/.learnings"

HIGH_ONLY=False
if [[ "${1:-}" == "--high-only" ]]; then
  HIGH_ONLY=True
fi

if [ ! -d "$LEARNINGS_DIR" ]; then
  echo "No .learnings directory found at $LEARNINGS_DIR"
  exit 0
fi

total=0
high_count=0

echo "=== Pending Learnings ==="
echo ""

for file in ERRORS.md LEARNINGS.md FEATURE_REQUESTS.md; do
  filepath="$LEARNINGS_DIR/$file"
  [ -f "$filepath" ] || continue

  file_count=0
  while IFS= read -r line; do
    echo "$line"
  done < <(
    python3 - "$filepath" "$HIGH_ONLY" <<'PYEOF'
import sys, re

path = sys.argv[1]
high_only = sys.argv[2] == "True"

with open(path) as f:
    content = f.read()

# Split on entry headers
entries = re.split(r'(?=^## \[)', content, flags=re.MULTILINE)
results = []
for entry in entries:
    if not entry.strip() or not re.match(r'## \[', entry):
        continue
    if '**Status**: pending' not in entry:
        continue
    header_match = re.match(r'## (\[[A-Z]+-\d{8}-[A-Z0-9]+\] .+)', entry)
    if not header_match:
        continue
    header = header_match.group(1).strip()
    priority_match = re.search(r'\*\*Priority\*\*: (\w+)', entry)
    priority = priority_match.group(1) if priority_match else 'unknown'
    is_high = priority in ('high', 'critical')
    if high_only and not is_high:
        continue
    flag = '🔴' if priority == 'critical' else '🟠' if priority == 'high' else '🟡' if priority == 'medium' else '⚪'
    results.append(f"  {flag} [{priority}] {header}")

if results:
    import os
    print(f"--- {os.path.basename(path)} ---")
    for r in results:
        print(r)
    print()
PYEOF
  )
done

echo ""

# Count totals
total=$(python3 - "$LEARNINGS_DIR" <<'PYEOF'
import sys, re, os

d = sys.argv[1]
total = 0
for fname in ['ERRORS.md', 'LEARNINGS.md', 'FEATURE_REQUESTS.md']:
    path = os.path.join(d, fname)
    if not os.path.exists(path):
        continue
    with open(path) as f:
        content = f.read()
    entries = re.split(r'(?=^## \[)', content, flags=re.MULTILINE)
    for e in entries:
        if '**Status**: pending' in e and re.match(r'## \[', e):
            total += 1
print(total)
PYEOF
)

echo "Total pending: $total"
echo ""
echo "Actions:"
echo "  Resolve: update **Status**: pending → **Status**: resolved + add ### Resolution block"
echo "  Promote: copy distilled rule to SOUL.md / AGENTS.md / TOOLS.md / DECISIONS.md"
echo "  Mark in-progress: **Status**: in_progress"
