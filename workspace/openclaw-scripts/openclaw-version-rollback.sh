#!/usr/bin/env bash
# openclaw-version-rollback.sh
# Atomically swap between OpenClaw binary versions (bidirectional — run again to undo):
#   1. Swap openclaw-gateway.service ↔ openclaw-gateway.service.bak
#   2. Clean openclaw.json: remove stale plugin entries, fix meta.lastTouchedVersion
#   3. Clear jiti transpilation cache (/tmp/jiti/)
#   4. Re-apply Google Chat unthread patch
#   5. Fix Zulip plugin openclaw/plugin-sdk symlink
#   6. Reload systemd + restart gateway
#   7. Verify health
#
# Usage:
#   openclaw-version-rollback.sh swap              # swap to other version
#   openclaw-version-rollback.sh --dry-run swap    # preview without changes
#   openclaw-version-rollback.sh status            # show current/backup versions
#   openclaw-version-rollback.sh verify            # check gateway health

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
OPENCLAW_DIR="${OPENCLAW_HOME:-$HOME/.openclaw}"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_ACTIVE="$SERVICE_DIR/openclaw-gateway.service"
SERVICE_BAK="$SERVICE_DIR/openclaw-gateway.service.bak"
CONFIG_ACTIVE="$OPENCLAW_DIR/openclaw.json"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
UNTHREAD_SCRIPT="$SCRIPTS_DIR/openclaw_googlechat_unthread.sh"
JITI_CACHE="/tmp/jiti"
ZULIP_EXT_DIR="$OPENCLAW_DIR/extensions/zulip/node_modules"
OPENCLAW_GLOBAL_PKG="$HOME/.npm-global/lib/node_modules/openclaw"

DRY_RUN=false

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "  $*"; }
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*"; }
err()  { echo "✗ $*" >&2; }

run() {
  if $DRY_RUN; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 1; }
}

service_version() {
  grep -oP 'OPENCLAW_SERVICE_VERSION=\K[^\s]+' "$1" 2>/dev/null || echo "(unknown)"
}

# ── Status ────────────────────────────────────────────────────────────────────
cmd_status() {
  echo "OpenClaw Version Rollback — Status"
  echo ""

  if [ -f "$SERVICE_ACTIVE" ]; then
    echo "  Active : $(service_version "$SERVICE_ACTIVE")  ($SERVICE_ACTIVE)"
  else
    warn "No active service file at $SERVICE_ACTIVE"
  fi

  if [ -f "$SERVICE_BAK" ]; then
    echo "  Backup : $(service_version "$SERVICE_BAK")  ($SERVICE_BAK)"
  else
    warn "No backup service file at $SERVICE_BAK"
  fi

  echo ""
  CONFIG_VER=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_ACTIVE')); print(d.get('meta',{}).get('lastTouchedVersion','(unknown)'))" 2>/dev/null || echo "(unreadable)")
  echo "  Config meta.lastTouchedVersion : $CONFIG_VER"

  RUNNING=$(systemctl --user show openclaw-gateway.service --property=ActiveState --value 2>/dev/null || echo "unknown")
  echo "  Service state                  : $RUNNING"
}

# ── Verify ────────────────────────────────────────────────────────────────────
cmd_verify() {
  echo "OpenClaw Version Rollback — Verify"
  echo ""
  local FAIL=0

  # 1. Service running
  STATE=$(systemctl --user show openclaw-gateway.service --property=ActiveState --value 2>/dev/null || echo "unknown")
  if [ "$STATE" = "active" ]; then
    ok "Gateway service is active"
  else
    err "Gateway service state: $STATE"; FAIL=1
  fi

  # 2. Google Chat route check
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:18789/googlechat 2>/dev/null || echo "000")
  if [ "$HTTP" = "401" ]; then
    ok "Google Chat endpoint → 401 (route registered)"
  elif [ "$HTTP" = "404" ]; then
    err "Google Chat endpoint → 404 (route not registered — known broken on 2026.3.12+)"; FAIL=1
  else
    warn "Google Chat endpoint → $HTTP (expected 401)"
  fi

  # 3. Zulip openclaw symlink
  if [ -L "$ZULIP_EXT_DIR/openclaw" ] || [ -d "$ZULIP_EXT_DIR/openclaw" ]; then
    ok "Zulip openclaw/plugin-sdk symlink present"
  else
    warn "Zulip symlink missing — plugin will fail with: Cannot find module 'openclaw/plugin-sdk'"
  fi

  # 4. Config version matches service version
  CONFIG_VER=$(python3 -c "import json; d=json.load(open('$CONFIG_ACTIVE')); print(d.get('meta',{}).get('lastTouchedVersion',''))" 2>/dev/null || echo "")
  SVC_VER=$(service_version "$SERVICE_ACTIVE")
  if [ "$CONFIG_VER" = "$SVC_VER" ]; then
    ok "Config version matches service ($CONFIG_VER)"
  else
    warn "Config version ($CONFIG_VER) does not match service version ($SVC_VER)"
  fi

  # 5. Stale plugin entries
  STALE=$(python3 - "$CONFIG_ACTIVE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
allowed  = set(d.get('plugins', {}).get('allow', []))
builtins = {'telegram', 'whatsapp', 'zalo', 'googlechat', 'open-prose'}
entries  = set(d.get('plugins', {}).get('entries', {}).keys())
stale = entries - allowed - builtins
print('\n'.join(sorted(stale)))
PY
)
  if [ -z "$STALE" ]; then
    ok "No stale plugin entries in config"
  else
    warn "Stale plugin entries: $STALE"
  fi

  echo ""
  if [ $FAIL -eq 0 ]; then
    ok "All checks passed"
  else
    err "Some checks failed"; return 1
  fi
}

# ── Config cleanup ────────────────────────────────────────────────────────────
fix_config() {
  local TARGET_VER="$1"
  python3 - "$CONFIG_ACTIVE" "$TARGET_VER" <<'PY'
import json, sys

path, target_ver = sys.argv[1], sys.argv[2]
with open(path) as f:
    d = json.load(f)

d.setdefault('meta', {})['lastTouchedVersion'] = target_ver

allowed  = set(d.get('plugins', {}).get('allow', []))
builtins = {'telegram', 'whatsapp', 'zalo', 'googlechat', 'open-prose'}
valid    = allowed | builtins
entries  = d.get('plugins', {}).get('entries', {})
for k in [k for k in list(entries) if k not in valid]:
    print(f"  Removing stale plugin entry: {k}")
    del entries[k]

print(f"  meta.lastTouchedVersion → {target_ver}")

with open(path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
PY
}

# ── Swap ──────────────────────────────────────────────────────────────────────
cmd_swap() {
  require_cmd systemctl
  require_cmd python3
  require_cmd curl

  [ -f "$SERVICE_ACTIVE" ] || { err "No active service file at $SERVICE_ACTIVE"; exit 1; }
  [ -f "$SERVICE_BAK"    ] || { err "No backup service file at $SERVICE_BAK";    exit 1; }

  FROM_VER=$(service_version "$SERVICE_ACTIVE")
  TO_VER=$(service_version "$SERVICE_BAK")

  echo "OpenClaw Version Rollback — Swap"
  echo ""
  echo "  From : $FROM_VER"
  echo "  To   : $TO_VER"
  echo ""

  if ! $DRY_RUN; then
    read -r -p "Proceed? [y/N] " CONFIRM
    [[ "$CONFIRM" =~ ^[Yy]$ ]] || { log "Aborted."; exit 0; }
    echo ""
  fi

  # 1. Timestamped config snapshot
  TS=$(date -u +"%Y%m%d-%H%M%S")
  CONFIG_SNAP="$OPENCLAW_DIR/openclaw.json.snap-$TS"
  log "Saving config snapshot → $(basename "$CONFIG_SNAP")"
  run cp "$CONFIG_ACTIVE" "$CONFIG_SNAP"

  # 2. Swap service files (via temp to make it atomic on same filesystem)
  log "Swapping service files..."
  TMP_SVC="${SERVICE_ACTIVE}.swap-tmp"
  run cp "$SERVICE_ACTIVE" "$TMP_SVC"
  run cp "$SERVICE_BAK"    "$SERVICE_ACTIVE"
  run cp "$TMP_SVC"        "$SERVICE_BAK"
  $DRY_RUN || rm -f "$TMP_SVC"
  ok "Service files swapped ($FROM_VER ↔ $TO_VER)"

  # 3. Fix config
  log "Cleaning config for $TO_VER..."
  if $DRY_RUN; then
    log "[dry-run] would: fix meta.lastTouchedVersion, remove stale plugin entries"
  else
    fix_config "$TO_VER"
    ok "Config cleaned"
  fi

  # 4. Clear jiti cache
  log "Clearing jiti cache ($JITI_CACHE)..."
  run rm -rf "$JITI_CACHE"
  ok "jiti cache cleared"

  # 5. Google Chat unthread patch
  if [ -f "$UNTHREAD_SCRIPT" ]; then
    log "Re-applying Google Chat unthread patch..."
    if $DRY_RUN; then
      log "[dry-run] would run: bash $UNTHREAD_SCRIPT --apply"
    else
      bash "$UNTHREAD_SCRIPT" --apply 2>&1 | sed 's/^/    /' || \
        warn "Unthread script returned non-zero (may already be applied)"
      ok "Google Chat unthread patch applied"
    fi
  else
    warn "Unthread script not found at $UNTHREAD_SCRIPT — skipping"
  fi

  # 6. Zulip openclaw symlink
  if [ -d "$ZULIP_EXT_DIR" ]; then
    if [ ! -e "$ZULIP_EXT_DIR/openclaw" ]; then
      log "Creating Zulip openclaw symlink..."
      run ln -s "$OPENCLAW_GLOBAL_PKG" "$ZULIP_EXT_DIR/openclaw"
      ok "Zulip symlink created"
    else
      ok "Zulip symlink already present"
    fi
  else
    warn "Zulip extension not found — skipping symlink"
  fi

  # 7. Reload + restart
  log "Reloading systemd and restarting gateway..."
  run systemctl --user daemon-reload
  run systemctl --user restart openclaw-gateway.service
  ok "Gateway restarted"

  # 8. Verify
  if $DRY_RUN; then
    echo ""
    ok "Swap complete (dry-run): $FROM_VER → $TO_VER"
    return 0
  fi

  echo ""
  log "Waiting for gateway to start..."
  sleep 3

  VERIFY_EXIT=0
  cmd_verify || VERIFY_EXIT=$?

  echo ""
  if [ $VERIFY_EXIT -eq 0 ]; then
    ok "Swap complete: $FROM_VER → $TO_VER"
    log "Config snapshot: $CONFIG_SNAP"
    log "To undo: run this script again (swap is bidirectional)"
  else
    err "Swap completed but verification failed — review errors above"
    log "Config snapshot preserved: $CONFIG_SNAP"
    log "To undo: run this script again"
    exit 1
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
COMMAND="${1:-}"
if [ "$COMMAND" = "--dry-run" ]; then
  DRY_RUN=true
  COMMAND="${2:-}"
fi

case "$COMMAND" in
  swap)   cmd_swap   ;;
  status) cmd_status ;;
  verify) cmd_verify ;;
  "")
    echo "Usage: $0 [--dry-run] {swap|status|verify}"
    echo ""
    echo "  swap     Swap active ↔ backup versions (run again to undo)"
    echo "  status   Show current/backup version info"
    echo "  verify   Check gateway health, routes, config, and plugin state"
    exit 1
    ;;
  *)
    err "Unknown command: $COMMAND"
    echo "Usage: $0 [--dry-run] {swap|status|verify}"
    exit 1
    ;;
esac
