---
name: formal_skeleton_helper
description: Generate a minimal Lean-style theorem skeleton locally.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"emoji":"∎","requires":{"bins":["bash"]}}}
---

# Formal Skeleton Helper

Use this skill when the user asks for a Lean-style theorem skeleton, namespace wrapper, or a local file containing a formal statement stub.

## How to use

1. Save JSON input to `/tmp/formal_input.json` with keys: `claim_name`, `statement`, `imports`, `namespace`, `variables`.
2. Run:
```
exec: /workspace/skills/formal-skeleton-helper/run_formal_skeleton.sh --input /tmp/formal_input.json
```
3. Return the generated file path and the preview from stdout.
