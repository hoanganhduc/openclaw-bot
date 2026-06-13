---
name: workspace_rearranger
description: Organize workspace files into project folders based on your current research direction, with conversational intake and safe execution modes.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"emoji":"🗂️","os":["linux","darwin","win32"],"requires":{"bins":["bash"]}}}
---

# Workspace Rearranger

Use this skill when the user asks to organize, rearrange, sort, or clean up workspace files.

## Quick reference

**Dry-run (default for general requests):**
```
exec: /workspace/skills/workspace_rearranger/organize.sh --workspace-root /workspace --mode dry-run --scope workspace --move-policy report-only
```

**Clean up staging:**
```
exec: /workspace/skills/workspace_rearranger/organize.sh --workspace-root /workspace --mode dry-run --scope staging --move-policy safe
```

**Apply (after user confirms dry-run):**
```
exec: /workspace/skills/workspace_rearranger/organize.sh --workspace-root /workspace --mode apply --scope staging --move-policy safe --confirm-apply yes
```

**Status / undo:**
```
exec: /workspace/skills/workspace_rearranger/organize.sh --workspace-root /workspace --mode status
exec: /workspace/skills/workspace_rearranger/organize.sh --workspace-root /workspace --mode undo-last --confirm-apply yes
```

## Parameters

- **scope**: `staging`, `workspace`, or `--roots <csv>`
- **mode**: `dry-run`, `apply`, `status`, `undo-last`
- **move-policy**: `report-only`, `safe`, `expanded`
- **focus**: `--focus <topic>`

## Guardrails

- Always do a dry-run first for whole-workspace apply.
- Never run apply without explicit user confirmation.
- Never delete files or silently overwrite.
