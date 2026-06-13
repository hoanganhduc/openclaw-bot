#!/bin/bash
# Docker entrypoint — shadow /usr/bin/curl with the enforcing wrapper
# This runs before the agent gets control, so it can't bypass it
mkdir -p /tmp/bin
cp /workspace/bin/curl /tmp/bin/curl
cp /usr/bin/curl /tmp/bin/curl-real

# Rewrite the wrapper to use curl-real
cat > /tmp/bin/curl <<'WRAPPER'
#!/bin/bash
APPROVED_SUBMOLTS="introduction general research"
REAL_CURL="/tmp/bin/curl-real"
for arg in "$@"; do
    if echo "$arg" | grep -qE 'moltbook\.com/api/v1/submolts/([^/]+)/'; then
        submolt=$(echo "$arg" | grep -oP 'moltbook\.com/api/v1/submolts/\K[^/]+')
        if [ -n "$submolt" ] && ! echo "$APPROVED_SUBMOLTS" | grep -qw "$submolt"; then
            echo "BLOCKED: submolt '$submolt' is not approved. Approved: $APPROVED_SUBMOLTS" >&2
            exit 1
        fi
    fi
done
exec "$REAL_CURL" "$@"
WRAPPER
chmod +x /tmp/bin/curl /tmp/bin/curl-real

# Override PATH so /tmp/bin comes first and also shadows /usr/bin/curl
export PATH="/tmp/bin:/workspace/bin:$PATH"

exec "$@"
