---
name: graph_verifier
description: Verify small graph-theoretic claims using a local Python helper.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"emoji":"📐","requires":{"bins":["bash"]}}}
---

# Graph Verifier

Use this skill when the user asks to sanity-check a finite graph claim, inspect a small construction, or validate a graph encoding.

## How to use

1. Save JSON input to `/tmp/graph_input.json` with the graph data.
2. Run:
```
exec: /workspace/skills/graph-verifier/run_graph_verifier.sh --input /tmp/graph_input.json
```
3. Read the JSON result from stdout.

## Supported input shapes

- `graph_data`: NetworkX node-link JSON
- `edges`: list like `[[1,2],[2,3]]`
- `adjacency`: object mapping nodes to neighbor lists
- `expected`: optional expected values such as `{"connected": true, "bipartite": false}`

## When to use SageMath instead

For heavy computations beyond NetworkX's capabilities, use the SageMath skill instead:
- Chromatic polynomial, Tutte polynomial
- Automorphism group computation
- Spectral analysis (characteristic polynomial, spectral radius)
- Finite field linear algebra
- Genus, planarity dual, fractional chromatic number

Use this graph-verifier for simple/fast checks: connectivity, bipartiteness, degree sequence, basic properties.
