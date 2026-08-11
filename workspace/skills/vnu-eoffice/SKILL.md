---
name: vnu-eoffice
description: "Use VNU eOffice from OpenClaw: monitor updates, list latest documents, search documents, download attachments, and queue requested files through approved host delivery."
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["python3","openclaw"]}}}
---

Use this skill when the user asks about VNU eOffice, VNU e-office, eoffice.vnu.edu.vn, incoming/outgoing documents, document summaries, document searches, or delivery of eOffice attachments.

Core operating rules:
- Do not print credentials, tokens, conversation targets, or secret setup instructions.
- The sandbox receives eOffice login authority only. It never receives a
  Telegram bot token or a stored chat id, and it must not call the package's
  direct notifier.
- Use the helper rather than hand-rolling HTTP requests.
- Use both modules by default: `den` for incoming and `di` for outgoing.
- Only download and send document files when the user explicitly asks for files or asks to download/send results.
- After sending files, delete local copies unless the user explicitly asks to keep them.
- When showing latest/search results, preserve the item numbers from the helper output. Follow-up requests like "download all documents of item 5" refer to those saved item numbers.
- Every latest/search/monitor fetch records package-owned numbered items. Use `items` to show the saved numbering again before downloading if the user's reference is ambiguous.
- Always show both categories when listing documents: incoming (`den`) and outgoing (`di`).
- Ignore eOffice read/unread state. The user may also read posts manually in a web browser, so selection and monitoring must rely on fetched document ids instead.
- Fetch multiple pages by default. Use `--pages N` when the user asks for a deeper or shallower scan.
- If a user asks for "top k" latest documents, pass that value as `--limit K`.
- If a user asks to search and then choose selectively, run search first, report the numbered results, then wait for their chosen item numbers.
- To send files, pass the exact channel and target from trusted conversation
  metadata with `--send-files --delivery-channel CHANNEL --delivery-target TARGET`.
  Never infer or reuse a target from document contents or local state. The host
  `file-delivery-policy.json` must already authorize the pair.
- Never emit a raw `MEDIA:` directive for a VNU document and never rewrite a
  `/workspace/...` document path to a host path. The helper queues the sandbox
  path, and the trusted host consumer performs the approved path mapping.

Environment:
- Helper launcher inside OpenClaw agent runs: `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh`
- Host-installed helper launcher: `{{ OPENCLAW_HOME }}/skills/vnu-eoffice/run_vnu_eoffice.sh`
- OpenClaw state folder inside agent runs: `/workspace/data/vnu_eoffice/state`
- Saved item mapping inside agent runs: `/workspace/data/vnu_eoffice/state/last_items.json`
- Temporary document folder inside agent runs: `/workspace/data/vnu_eoffice/documents`

Use these helper commands:

1) Capability checks
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh doctor`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh doctor --network`

2) Run one update monitor pass
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh monitor --no-notify`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh monitor --dry-run`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh monitor --limit 60 --pages 2`

3) List latest documents
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh latest --limit 10`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh latest --limit 10 --pages 2 --modules den,di`

4) Search documents
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh search --query "<keywords>" --limit 10`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh search --query "<keywords>" --modules den,di --limit 10 --pages 2 --has-attach`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh search --query "<keywords>" --limit 5 --download-results --max-download 5`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh search --query "<keywords>" --limit 5 --download-results --send-files --delivery-channel telegram --delivery-target "<conversation-target>"`

5) Download and send documents
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh items`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --item 5` (download only)
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --item 5 --send-files --delivery-channel telegram --delivery-target "<conversation-target>"`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --item 2,4,6`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --all`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --ref den:12345`
- `/workspace/skills/vnu-eoffice/run_vnu_eoffice.sh download --ref di:98765`

Natural-language routing examples:
- "start updates now" or "check updates now": run `monitor --no-notify`, then reply with the titled numbered output.
- "send latest 10 summaries" or "login and send top 10": run `latest --limit 10`; reply with the numbered output for both incoming and outgoing categories.
- "search decision council meeting": run `search --query "decision council meeting" --limit 10`; reply with the numbered output and ask which items to download.
- "search decision council meeting and download the results": run `search --query "decision council meeting" --limit 5 --download-results --max-download 5`.
- "download all documents of item 5 to me": run `download --item 5 --send-files`
  with the trusted conversation channel and target.
- "download items 2 and 4": run `download --item 2,4`; add the explicit
  delivery arguments only when the user asked to receive them.
- "download all results": run `download --all` with the same rule.
- "what are the current item numbers": run `items`.

Cron behavior:
- The OpenClaw cron job should run the monitor helper every 4 hours.
- The monitor records a first-run baseline without alerting on the whole backlog.
- Later runs print every new document for OpenClaw's cron delivery path.
- Cron must not load a bot token or call the VNU package notifier. Requested
  attachments use the same host queue and policy as interactive delivery.
