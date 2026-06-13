---
name: smart_model_router
description: Inspect model availability, manage the global default/fallbacks, and route hard tasks to a stronger model with bounded thinking.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"requires":{"bins":["openclaw","python3","bash"]}}}
---

# Smart Model Router

Use this skill when the user wants to inspect available models, change the global default primary model, manage fallbacks, or run a task through a stronger model.

## Quick reference

**List models:**
```
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh list-models --json
```

**Doctor / health:**
```
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh doctor --json
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh doctor --json --probe
```

**Set primary model:**
```
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh set-primary <provider/model-or-alias>
```

**Manage fallbacks:**
```
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh add-fallback <provider/model-or-alias>
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh remove-fallback <provider/model-or-alias>
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh clear-fallbacks
```

**Recommend a model for a task:**
```
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh recommend '<task>'
exec: /workspace/skills/smart-model-router/run_smart_model_router.sh suggest-session-switch '<task>'
```

## Behavior rules

- Use the helper for all deterministic inspection and config changes.
- Never invent a model not present in the configured catalog.
- Prefer the current primary model for ordinary tasks.
- For research tasks, the priority order in `AGENTS.md` overrides ordinary defaults: `{{ MODEL_ID }}` -> `{{ MODEL_ID }}` -> `{{ MODEL_ID }}` -> `{{ MODEL_ID }}`.
- Prefer the strongest configured reasoning model for hard math/proof/complexity/formal tasks.
