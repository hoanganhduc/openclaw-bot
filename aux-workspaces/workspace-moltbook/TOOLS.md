# TOOLS.md - Local Notes

## Available Tools

- **exec**: Primary tool for Moltbook interactions via `/workspace/bin/moltbook-api.sh`. Also for other shell commands.
- **web_fetch**: For non-Moltbook HTTP fetches only. **NEVER for moltbook.com** (returns 403).
- **read/write**: For reading workspace files and writing to staging/ directory only.
- **memory**: For reading/writing durable memory entries.

## CRITICAL: Fetching Moltbook content

**ALWAYS use `/workspace/bin/moltbook-api.sh` for ALL Moltbook API access.** This script:
- Handles authentication automatically ($MOLTBOOK_AUTH)
- Enforces the approved submolt list (introduction, general, research)
- Blocks any unapproved submolt with an error

```bash
/workspace/bin/moltbook-api.sh feed research          # fetch research feed
/workspace/bin/moltbook-api.sh feed general new 10    # fetch general, newest 10
/workspace/bin/moltbook-api.sh post POST_ID           # fetch single post
/workspace/bin/moltbook-api.sh comments POST_ID       # fetch comments
/workspace/bin/moltbook-api.sh comment POST_ID --file staging/comment.md  # post approved comment
/workspace/bin/moltbook-api.sh verify CODE ANSWER     # complete comment verification
/workspace/bin/moltbook-api.sh me                     # own profile
```

**NEVER use raw `curl` to call moltbook.com.** NEVER use `web_fetch` on moltbook.com.

## Denied Tools

browser, edit, web_search, canvas, image, message, cron, sessions_spawn, and all runtime/sessions/automation groups.

## Environment

- API base: https://www.moltbook.com/api/v1
- API key: Available as `$MOLTBOOK_AUTH` environment variable inside the sandbox
- Output goes to: /workspace/staging/
- Logs go to: /workspace/memory/
