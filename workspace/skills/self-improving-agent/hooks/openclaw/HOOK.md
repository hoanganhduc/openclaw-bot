---
name: self-improvement
description: "Injects dynamic self-improvement reminder at bootstrap and before resets"
metadata: {"openclaw":{"emoji":"🧠","events":["agent:bootstrap","command:reset"]}}
---

# Self-Improvement Hook

Injects a context-aware learning capture reminder.

## What It Does

- **`agent:bootstrap`**: Reads `.learnings/` and injects a dynamic summary — shows pending count, high-priority items, and recent entry titles. Shows "no pending items" message when backlog is clear.
- **`command:reset`**: When the user resets the session, injects a reminder to capture learnings before context is lost (only fires if there are pending items).

## Configuration

No configuration needed. Enable with:

```bash
openclaw hooks enable self-improvement
```
