---
name: vnthuquan
description: Search and manage Vietnam Thu Quan / vnthuquan / vietnamthuquan.eu ebook workflows in OpenClaw. Use when the user explicitly asks for Vietnam Thu Quan discovery, metadata, categories, formats, mirrors, controlled ebook downloads, local validation, download queues, archive inspection, or Calibre handoff for validated EPUB/PDF files from vnthuquan.
---

# vnthuquan

Use this skill only for Vietnam Thu Quan site-specific ebook work:

- search Vietnam Thu Quan books
- list categories, formats, latest books, authors, ranked lists, or category contents
- inspect book metadata and shareable source links
- check mirrors and wrapper/package health
- inspect the wrapper-managed download archive
- run controlled dry-run/executed downloads, queue execution, validation, and guarded Calibre handoff

Do not use this skill as the first route for generic papers, DOI, ISBN, or book retrieval. Keep the existing library-first routing: Zotero and Calibre workflows remain primary unless the user specifically asks for Vietnam Thu Quan / vnthuquan.

## Runtime

Use the OpenClaw workspace runner:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh diagnose --json
```

OpenClaw-local state defaults:

- state: `{{ PRIVATE_DATA_DIR }}/vnthuquan/state/`
- run manifests/logs: `{{ PRIVATE_DATA_DIR }}/vnthuquan/runs/`
- downloads: `{{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/`
- config: `{{ PRIVATE_DATA_DIR }}/vnthuquan/state/config.json`
- archive: `{{ PRIVATE_DATA_DIR }}/vnthuquan/state/downloads.jsonl`
- cache: `{{ PRIVATE_DATA_DIR }}/vnthuquan/cache/http-cache.json`

If `diagnose` reports the package missing, install or expose the `vnthuquan` CLI to the OpenClaw workspace before running live workflows. The wrapper searches `VNTHUQUAN_BIN`, `PATH`, `/workspace/.local/venv_vnthuquan/bin/vnthuquan`, `{{ USER_HOME }}/.vnthuquan_venv/bin/vnthuquan`, and configured source directories.

## Common Commands

Read-only discovery:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh search "Kim Dung" --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh categories list --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh formats --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh show --title "TITLE" --links --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh archive list --json
```

Controlled download workflow:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --dry-run --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --execute --yes --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh validate {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --json
```

Calibre handoff for validated EPUB/PDF files:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --dry-run --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --execute --yes --duplicates-reviewed --json
```

Queue workflow:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh queue --query "Kim Dung" --limit 5 --format epub --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh execute-queue {{ PRIVATE_DATA_DIR }}/vnthuquan/runs/queue-YYYYMMDD-HHMMSS.json --yes --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh requeue-failed {{ PRIVATE_DATA_DIR }}/vnthuquan/runs/queue-result-YYYYMMDD-HHMMSS.json --json
```

## Safety Rules

- Prefer `--json` for machine-readable output.
- If multiple books match, show numbered candidates and ask the user to choose.
- Downloads are dry-run by default unless `--execute --yes` is present.
- Executed downloads must keep the wrapper-managed archive.
- Listing queues require `--limit` or `--pages` so the crawl is bounded.
- Calibre writes are allowed only after validation, Calibre doctor, duplicate search, dry-run review, and `--execute --yes --duplicates-reviewed`.
- Use `--allow-duplicate` only when duplicate candidates are intentionally accepted as a separate Calibre entry.
- Text and audio downloads are valid archive artifacts, but Calibre handoff accepts only EPUB/PDF until a conversion workflow exists.
- Do not mutate package default config; use the wrapper-managed OpenClaw config path.

## Detailed Workflow

Read `references/workflows.md` when the task involves downloads, queues, Calibre handoff, setup, or recovery.
