---
name: rss_news_digest
description: Fetch ranked RSS digests by tag; list, search, add, edit, import, export, disable, enable, or remove feeds; and run feed health checks.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["python3"]}}}
---

# RSS News Digest

Use this skill when the user asks for RSS-based research updates, jobs, events, general news, feed management, or feed health diagnostics.

If the user provides an action inline (e.g. `/rss_news_digest general`), execute it immediately. Otherwise ask what they want to do and wait for their reply.

## Required digest loop

For every digest or feed-management task, use:

1. **Review** — inspect the requested tag/profile/action, command bounds, JSON output, digest paths, item counts, and feed errors.
2. **Validate** — confirm the digest file exists, links/titles are present, item counts match the command output, and failures are called out.
3. **Fix** — rerun with safer bounds, run `doctor`, or report exact feed/config problems. Do not silently treat missing output as success.
4. Repeat until the digest is usable or the remaining gap is explicitly reported.

For research-facing summaries, separate observed feed items from your own interpretation. End with a short `Delivery Check` when the digest is being used as evidence for a broader report.

## Quick reference

**User specifies a tag:** `run --tag <TAG>` (valid: research, general, events, jobs, video)
**User says "all":** `run --all-tags`
**User says "list feeds":** `list-feeds`
**User says "doctor":** `doctor`

Run options: `--max-items N`, `--per-feed-limit N`, `--profile NAME`, `--include-disabled`, `--no-mark-seen`

## Execution

```
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh <COMMAND AND ARGS>
```

After running a digest, read the digest file from the JSON output `outputs` field and summarize the top items.

Validation before summarizing:

- confirm the JSON command result reports a successful run
- confirm every referenced digest path exists
- compare reported item counts with the summarized items
- mention disabled feeds, feed errors, or empty tags when present
- use bounded options such as `--max-items` and `--per-feed-limit` for cron or chat delivery

## Other commands

```
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh search-feeds "<QUERY>"
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh add-feed "<URL>" --tag <TAG> --priority <N>
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh edit-feed "<URL>" --tag <TAG> --priority <N>
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh disable-feed "<URL>"
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh enable-feed "<URL>"
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh remove-feed "<URL>"
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh backup-feeds --reason "<REASON>"
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh list-backups
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh restore-feeds-backup
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh export-feeds-tsv --output /tmp/feeds.tsv
exec: /workspace/skills/rss-news-digest/run_rss_news_digest.sh import-feeds-tsv /tmp/feeds.tsv
```
