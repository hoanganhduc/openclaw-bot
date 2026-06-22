---
name: lean-explore-cli
description: Search Lean 4 / Mathlib declarations (theorems, definitions, lemmas, instances) by name or by informal meaning. Direct-CLI adapter for the OpenClaw sandbox (OpenClaw is not an MCP client, so this replaces the lean-explore MCP server).
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["bash"]}}}
---

# Lean Explore (CLI)

Use this skill to search Lean 4 mathematical declarations from Mathlib and other packages — by declaration name (e.g. `List.map`, `Nat.add`) or by informal natural-language meaning (e.g. "a continuous function on a compact set", "prime number divisibility").

OpenClaw cannot use MCP servers, so this is a direct CLI wrapper around the `lean_explore` API (the same backend the LeanExplore MCP server uses).

## Usage

```
exec: /workspace/skills/lean-explore-cli/run_lean_explore.sh search "<query>" [-n <limit>] [-p <package>]
```

- `<query>` — declaration name or natural-language description
- `-n` / `--limit` — number of results (default 5)
- `-p` / `--package` — restrict to a package (e.g. `-p Mathlib`); repeatable

Output is a single JSON object (results with declaration name, type, docstring, source link, etc.).

The first call bootstraps a workspace-local Python venv (~30s, one time). The API key is read from the workspace secrets file; no key value is ever printed.

## Examples

```
exec: /workspace/skills/lean-explore-cli/run_lean_explore.sh search "prime number divisibility" -n 3 -p Mathlib
exec: /workspace/skills/lean-explore-cli/run_lean_explore.sh search "List.map"
```

## Readiness

```
exec: /workspace/skills/lean-explore-cli/run_lean_explore.sh doctor
```
Reports whether the venv/`lean_explore` are present and whether the API key is configured (`auth_status: present|missing`).
