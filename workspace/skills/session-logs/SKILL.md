---
name: session-logs
description: Use when the user asks about earlier conversations, prior outputs, historical context, or past work that may live in OpenClaw memories, QMD stores, or session logs.
user-invocable: true
disable-model-invocation: false
---

# Session Logs

Use this skill to recover prior context from local OpenClaw artifacts.

## Search order

1. `/workspace/MEMORY.md`
2. `/workspace/memory/`
3. `{{ PRIVATE_DATA_DIR }}/sessions/`
4. `~/.openclaw/agents/*/qmd/sessions/`
5. `~/.openclaw/agents/*/sessions/`

## When to use

- "What did we say before?"
- "Find the previous discussion about X"
- "Search older sessions"
- "What did OpenClaw decide last time?"

## Tools

- prefer `rg` for filename and content filtering
- use `jq` for structured extraction from JSONL transcripts

## Useful patterns

Search memories first:

```bash
rg -n "phrase" /workspace/MEMORY.md /workspace/memory
```

Search indexed and raw sessions:

```bash
rg -l "phrase" {{ PRIVATE_DATA_DIR }}/sessions ~/.openclaw/agents/*/qmd/sessions ~/.openclaw/agents/*/sessions 2>/dev/null
```

Extract assistant text from JSONL:

```bash
jq -r 'select(.type == "response_item" and .payload.type == "message" and .payload.role == "assistant") | .payload.content[]? | select(.type == "output_text" or .type == "input_text") | .text' <session>.jsonl
```

## Rules

- Prefer memory notes before raw transcripts.
- Quote only the lines needed.
- Include the source path when it helps verification.
- If memory notes and raw session logs disagree, say so.
