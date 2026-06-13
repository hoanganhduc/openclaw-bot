---
name: get-available-resources
description: Use at the start of computationally intensive local tasks to detect CPU, GPU, memory, and disk availability, and to emit a `.openclaw_resources.json` planning file with strategy recommendations.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"requires":{"bins":["python3"]}}}
---

# Get Available Resources

Use this skill before heavy local computation such as:

- large document parsing batches
- large dataset processing
- `sagemath` or other heavy math runs
- memory-sensitive workflows

## Runtime command

```bash
exec: /workspace/skills/get-available-resources/run_get_available_resources.sh
```

Optional custom output:

```bash
exec: /workspace/skills/get-available-resources/run_get_available_resources.sh --output /workspace/.openclaw_resources.json
```

## Output

Default output file:

- `.openclaw_resources.json`

It contains:
- CPU info
- memory info
- disk info
- GPU/backend availability when detectable
- strategy suggestions for parallelism, memory use, and acceleration

Use this skill selectively, not for trivial tasks.
