---
name: getscipapers_requester
description: Resolve DOI/ISBN details, prepare manifests from text or txt files, use getscipapers for retrieval/search, and manage request watches safely.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["python3","getscipapers","openclaw"]}}}
---

Use this skill whenever the user asks to get, fetch, download, obtain, request, or look up a paper, article, book, ebook, DOI, ISBN, or a list of papers/books from pasted text or a .txt attachment.

Core operating rules:
- If the user asks to get/download/request something, actually use getscipapers rather than just describing it.
- If DOI/ISBN is missing, first try local extraction and automatic lookup before asking the user.
- If the user pastes a paragraph or uploads a .txt file, prefer manifest mode so extracted identifiers are tracked and deduplicated.
- Only ask for identifiers/details after extraction plus automatic lookup both fail or remain ambiguous.
- Show the exact getscipapers command before running it when the action is high-risk, large batch, or request-posting related.

Environment:
- Helper launcher: `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh`
- Download folder: `{{ OPENCLAW_WORKSPACE }}/research/getscipapers_bot/downloads`
- State folder: `{{ OPENCLAW_WORKSPACE }}/research/getscipapers_bot/state`
- Manifest folder: `{{ OPENCLAW_WORKSPACE }}/research/getscipapers_bot/state/manifests`

Use these helper commands:

1) Environment / capability checks
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh doctor`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh doctor --network`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh introspect`

2) Identifier extraction / resolution
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh extract <text-or-file>`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh resolve auto <query> --best`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh resolve paper <query> --best`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh resolve book <query> --best`

3) Batch preparation from pasted text or .txt files
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh make-manifest auto <text-or-file>`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh make-manifest paper <text-or-file>`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh make-manifest book <text-or-file>`

4) Actual getscipapers execution
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 --dry-run -- <actual args...>`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- <actual args...>`

5) Download inspection
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh latest-downloads --limit 10`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh file-info <path>`

6) Watch management for posted requests
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh create-watch --kind <paper|book> --label <label> --identifier-type <doi|isbn|search> --identifier <value> --services <comma-list> --notes <text>`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh list-watches`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh update-watch <watch_id> --status <value> --last-note <text> --bump-check`
- `{{ OPENCLAW_HOME }}/skills/getscipapers_requester/run_gsp_helper.sh update-watch <watch_id> --sent-file-hash <sha256>`

Workflow:

A. Retrieval / download requests
- If the user already provides DOI or ISBN, use it directly.
- Otherwise:
  1. try `extract`
  2. if needed, run `resolve ... --best`
  3. if the result is ambiguous, show the top ranked candidates and ask the user to choose
- Prefer `getscipapers getpapers` for retrieval/search.
- Inspect local help first with `introspect` and use only flags/subcommands that the local installation actually exposes.
- When possible, prefer:
  - single DOI: `getscipapers getpapers --doi <doi>`
  - DOI file: `getscipapers getpapers --doi-file <path>`
  - title search: `getscipapers getpapers --search <query>`
- Prefer `--non-interactive` and a download-folder flag only if the local help shows they exist.
- For a paragraph or txt file containing multiple papers/books, use `make-manifest` first. If a DOI file is generated, prefer `getpapers --doi-file`.
- Before a large batch or a request-posting action, use `--dry-run` first and tell the user the exact command.

B. Confidence / ambiguity policy
- The helper returns ranked candidates and an automatic selection only when confidence is strong enough.
- If automatic selection is not strong enough, do not guess. Show the likely candidates and ask the user to confirm.

C. Request posting when retrieval fails
- If no file is available, ask the user whether they want a request posted to the named services.
- Only use request-related getscipapers commands that appear in the local help on this host.
- After a successful post, create a watch record immediately.
- Keep request tracking idempotent: if the same active watch already exists, reuse it instead of creating duplicates.

D. Monitoring every 4 hours for 3 days
- If the user wants follow-up monitoring, create an OpenClaw cron job that re-attempts retrieval every 4 hours for 3 days.
- Each run should:
  1. reuse the stored watch identifier/search terms
  2. re-run getscipapers retrieval
  3. inspect the newest downloads
  4. if a new file appears, notify the user and, when possible, attach it in Telegram
  5. record the file hash in the watch so the same file is not sent repeatedly
  6. if the deadline expires, notify the user that monitoring gave up or ask whether to continue

E. Telegram file sending
- If the current chat is Telegram and the located file size is below the configured threshold, use OpenClaw’s chat-bound message/media sending path to upload the file.
- If the file is too large or sending fails, report the local path, size, and checksum instead of pretending the upload succeeded.

F. Stay within getscipapers’ actual scope
- Treat the locally installed help as authoritative.
- Do not invent unsupported flags or subcommands.
- If a request service is broken or unsupported locally, say so clearly and suggest manual fallback.
