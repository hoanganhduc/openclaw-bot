#!/bin/bash
# moltbook-api.sh — Moltbook API wrapper with submolt enforcement
# Usage:
#   moltbook-api.sh feed <submolt> [sort] [limit]    — fetch submolt feed
#   moltbook-api.sh post <id>                         — fetch single post
#   moltbook-api.sh comments <post_id> [sort] [limit] — fetch comments
#   moltbook-api.sh comment <post_id> <content>       — create comment
#   moltbook-api.sh comment <post_id> --file <path>   — create comment from file
#   moltbook-api.sh verify <code> <answer>            — solve verification
#   moltbook-api.sh me                                — fetch own profile

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONF_FILE="$SCRIPT_DIR/approved-submolts.conf"
APPROVED_SUBMOLTS="$(tr '\n' ' ' < "$CONF_FILE" | xargs)"
API_BASE="https://www.moltbook.com/api/v1"
CURL_REAL="$SCRIPT_DIR/curl-real"

if [ -z "${MOLTBOOK_AUTH:-}" ]; then
    echo "ERROR: MOLTBOOK_AUTH environment variable not set" >&2
    exit 1
fi

auth_header="Authorization: Bearer $MOLTBOOK_AUTH"

api_get() {
    local url="$1"
    local tmp
    local status
    tmp="$(mktemp)"
    if status="$("$CURL_REAL" -sS -w '%{http_code}' -o "$tmp" -H "$auth_header" "$url")"; then
        :
    else
        local rc=$?
        rm -f "$tmp"
        echo "ERROR: Moltbook API request failed (curl exit $rc)" >&2
        exit "$rc"
    fi
    if [[ ! "$status" =~ ^2[0-9][0-9]$ ]]; then
        rm -f "$tmp"
        printf '{"status":"error","http_status":%s,"message":"Moltbook API request failed"}\n' "$status"
        exit 1
    fi
    cat "$tmp"
    rm -f "$tmp"
}

api_post_json() {
    local url="$1"
    local payload="$2"
    local tmp
    local status
    tmp="$(mktemp)"
    if status="$("$CURL_REAL" -sS -w '%{http_code}' -o "$tmp" -X POST -H "$auth_header" -H "Content-Type: application/json" -d "$payload" "$url")"; then
        :
    else
        local rc=$?
        rm -f "$tmp"
        echo "ERROR: Moltbook API request failed (curl exit $rc)" >&2
        exit "$rc"
    fi
    if [[ ! "$status" =~ ^2[0-9][0-9]$ ]]; then
        cat "$tmp"
        rm -f "$tmp"
        exit 1
    fi
    cat "$tmp"
    rm -f "$tmp"
}

assert_post_submolt_approved() {
    local post_id="$1"
    local post_json
    local submolt
    post_json="$(api_get "$API_BASE/posts/$post_id")"
    submolt="$(printf '%s' "$post_json" | jq -er '.post.submolt.name // .post.submolt_name')"
    if ! echo "$APPROVED_SUBMOLTS" | grep -qw '\*' && ! echo "$APPROVED_SUBMOLTS" | grep -qw "$submolt"; then
        echo "BLOCKED: post '$post_id' belongs to submolt '$submolt'. Approved submolts: $APPROVED_SUBMOLTS" >&2
        exit 1
    fi
}

case "${1:-}" in
    feed)
        submolt="${2:?Usage: moltbook-api.sh feed <submolt> [sort] [limit]}"
        sort="${3:-new}"
        limit="${4:-25}"
        if ! echo "$APPROVED_SUBMOLTS" | grep -qw '\*' && ! echo "$APPROVED_SUBMOLTS" | grep -qw "$submolt"; then
            echo "BLOCKED: submolt '$submolt' is not approved. Approved submolts: $APPROVED_SUBMOLTS" >&2
            exit 1
        fi
        api_get "$API_BASE/submolts/$submolt/feed?sort=$sort&limit=$limit"
        ;;
    post)
        post_id="${2:?Usage: moltbook-api.sh post <id>}"
        api_get "$API_BASE/posts/$post_id"
        ;;
    comments)
        post_id="${2:?Usage: moltbook-api.sh comments <post_id> [sort] [limit]}"
        sort="${3:-new}"
        limit="${4:-25}"
        api_get "$API_BASE/posts/$post_id/comments?sort=$sort&limit=$limit"
        ;;
    comment)
        post_id="${2:?Usage: moltbook-api.sh comment <post_id> <content>|--file <path>}"
        if [ "${3:-}" = "--file" ]; then
            file_path="${4:?Usage: moltbook-api.sh comment <post_id> --file <path>}"
            content="$(cat "$file_path")"
        else
            content="${3:?Usage: moltbook-api.sh comment <post_id> <content>|--file <path>}"
        fi
        assert_post_submolt_approved "$post_id"
        payload="$(jq -n --arg content "$content" '{content: $content}')"
        api_post_json "$API_BASE/posts/$post_id/comments" "$payload"
        ;;
    verify)
        verification_code="${2:?Usage: moltbook-api.sh verify <code> <answer>}"
        answer="${3:?Usage: moltbook-api.sh verify <code> <answer>}"
        payload="$(jq -n --arg verification_code "$verification_code" --arg answer "$answer" '{verification_code: $verification_code, answer: $answer}')"
        api_post_json "$API_BASE/verify" "$payload"
        ;;
    me)
        api_get "$API_BASE/agents/me"
        ;;
    *)
        echo "Usage: moltbook-api.sh {feed|post|comments|comment|verify|me} [args...]" >&2
        echo "Approved submolts: $APPROVED_SUBMOLTS" >&2
        exit 1
        ;;
esac
