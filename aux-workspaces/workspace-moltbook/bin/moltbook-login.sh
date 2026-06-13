#!/usr/bin/env bash
set -euo pipefail
openclaw browser create-profile --name "moltbook" --color "#8844FF" >/dev/null 2>&1 || true
openclaw browser --browser-profile "moltbook" start
openclaw browser --browser-profile "moltbook" open "https://www.moltbook.com"
