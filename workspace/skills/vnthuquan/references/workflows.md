# vnthuquan OpenClaw Workflows

## Setup And Diagnosis

Start with:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh diagnose --json
```

Use `doctor --json` for live site and package health. `diagnose` is mostly local; `doctor` may touch the configured Vietnam Thu Quan mirror.

The runner resolves the `vnthuquan` package in this order:

1. `VNTHUQUAN_BIN`
2. `vnthuquan` on `PATH`
3. `/workspace/.local/venv_vnthuquan/bin/vnthuquan`
4. `{{ USER_HOME }}/.vnthuquan_venv/bin/vnthuquan`
5. `python -m vnthuquan` from `VNTHUQUAN_SOURCE_DIR`
6. `python -m vnthuquan` from `/workspace/vendor/vnthuquan`
7. `python -m vnthuquan` from `{{ USER_HOME }}/vnthuquan`

For portable OpenClaw deployments, prefer a workspace-local install at `/workspace/.local/venv_vnthuquan` or set `VNTHUQUAN_BIN`.

## Discovery

Use read-only commands first:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh mirrors list --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh mirrors check --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh categories list --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh categories show 23 --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh formats --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh list latest --limit 10 --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh search "QUERY" --json
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh show --title "TITLE" --links --json
```

If results are ambiguous, show title, author, year or format when available, then ask the user which index to use.

## Controlled Downloads

Downloads are dry-run by default:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --dry-run --json
```

Execute only after explicit user approval:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --execute --yes --json
```

The wrapper refuses executed downloads with `--no-archive` because the OpenClaw archive is the recovery record.

Supported download formats are `epub`, `pdf`, `text`, and `audio`. Only EPUB/PDF may be handed to Calibre.

## Queues

Queue creation is dry-run and bounded:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh queue --query "Kim Dung" --limit 5 --format epub --json
```

Execute a reviewed queue:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh execute-queue {{ PRIVATE_DATA_DIR }}/vnthuquan/runs/queue-YYYYMMDD-HHMMSS.json --jobs 1 --yes --json
```

Create a retry manifest from failed items:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh requeue-failed {{ PRIVATE_DATA_DIR }}/vnthuquan/runs/queue-result-YYYYMMDD-HHMMSS.json --json
```

## Calibre Handoff

Start with a dry-run:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --dry-run --json
```

The wrapper:

- validates the file through `vnthuquan validate`
- accepts only `.epub` and `.pdf`
- runs `/workspace/skills/calibre/run_cal.sh doctor`
- searches Calibre for duplicate candidates
- runs `calibre add ... --dry-run`
- returns duplicate candidates and the write gate state

Write only after the dry-run and duplicate candidates have been reviewed:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --execute --yes --duplicates-reviewed --json
```

If duplicate candidates exist, add `--allow-duplicate` only when the user intentionally wants a separate Calibre entry.

## Recovery

- Do not automatically retry Calibre writes.
- If Calibre doctor/search fails, run the Calibre skill doctor/sync workflow before trying again.
- If a queue execution has failures, use `requeue-failed` and review the generated retry manifest before execution.
- Preserve `result_path`, `archive_path`, and SHA-256 values from JSON output when reporting a completed download or write.
