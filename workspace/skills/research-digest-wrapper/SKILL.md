---
name: research_digest_wrapper
description: Generate a local research digest from arXiv and OpenAlex using a local Python script. Also manages tracked research topics.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"emoji":"🧠","requires":{"bins":["bash"]}}}
---

# Research Digest Wrapper

Use this skill when the user wants a research digest from tracked topics or wants to manage the tracked topic list.

If the user provides an action inline (e.g. `/research_digest_wrapper run`), execute it immediately. Otherwise ask what they want to do and wait for their reply.

## Required digest loop

For every research digest or topic-management task, use:

1. **Review** — inspect requested tag/topic filters, command bounds, digest output path, topic file state, and any script warnings.
2. **Validate** — confirm the digest exists, topic counts and priorities make sense, paper-like items keep identifiers where available, and generated summaries distinguish item metadata from inference.
3. **Fix** — rerun with safer filters, repair topic metadata only after user approval when ambiguous, or report exact remaining gaps.
4. Repeat until the digest is usable or the remaining gap is explicitly reported.

When the digest seeds deeper research, preserve identifiers and hand off items to `digest_bridge`, `zotero`, `paper-lookup`, or `deep-research-workflow` instead of treating digest text as final evidence.

## Quick reference

**Run digest:** `run` (options: `--tag TAG`, `--min-priority N`, `--use-llm-summary`, `--use-llm-scoring`)
**List topics:** `list-topics`
**Add topic:** `add-topic "name" --tag TAG --priority N`
**Doctor:** `doctor`

## Execution

```
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh <COMMAND AND ARGS>
```

After running, read the digest file and summarize the top findings.

Validation before summarizing:

- confirm the command completed successfully
- confirm the digest path exists and is non-empty
- compare the summarized findings against the digest contents
- report empty topic sets, disabled topics, missing identifiers, or script warnings
- use bounded filters for scheduled or chat-delivered runs

## Other commands

```
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh edit-topic "<NAME>" --tag <TAG> --priority <N>
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh disable-topic "<NAME>"
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh enable-topic "<NAME>"
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh remove-topic "<NAME>"
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh backup-topics --reason "<REASON>"
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh list-topic-backups
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh restore-topic-backup
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh export-topics --output /tmp/topics.tsv
exec: /workspace/skills/research-digest-wrapper/run_research_digest.sh import-topics /tmp/topics.tsv
```

## Paths

- Topics: `{{ PRIVATE_DATA_DIR }}/research/alerts/topics.tsv`
- Digest: `{{ PRIVATE_DATA_DIR }}/research/alerts/digests/latest-digest.md`
