# MODEL_TIERS.md

This is the live model catalog for `agent_group_discuss`.
Use this file for actual role assignment. `MODEL_TIERS.example.md` is only a template.

## Reasoning level classification

| Level | Description | Suitable for |
|-------|-------------|-------------|
| R4 | Deep multi-step reasoning, formal proofs, adversarial critique | theorem verification, correctness review, PSPACE reductions, final refereeing |
| R3 | Strong structured reasoning | planning, synthesis, algorithm design, structured review |
| R2 | Solid general reasoning | edge-case review, specialist analysis, support roles |
| R1 | Fast summarization and lightweight exploration | scouting, brainstorming, clarity review |

## OpenClaw model catalog

| Model | Reasoning | Speed | Best for |
|-------|-----------|-------|----------|
| `{{ MODEL_ID }}` | R4 | medium | lead verifier, judge, referee, proof-heavy roles |
| `{{ MODEL_ID }}` | R4 | medium | fallback lead verifier and high-stakes research |
| `{{ MODEL_ID }}` | R3 | medium | strong structured review and analysis fallback |
| `{{ MODEL_ID }}` | R3 | medium | balanced reasoning fallback |
| `{{ MODEL_ID }}` | R3 | medium | structured analysis and synthesis |
| `{{ MODEL_ID }}` | R3 | medium | bounded support analysis |
| `groq/{{ MODEL_ID }}` | R2 | fast | lightweight support or scouting |

## Hard override for research tasks

For research, proof, manuscript-correctness, or other high-stakes mathematical review tasks:

- `STRONG_REASONER` -> `{{ MODEL_ID }}`
- `BALANCED_MODEL` -> `{{ MODEL_ID }}`
- `FAST_MODEL` -> `{{ MODEL_ID }}`

Fallback order:

1. `{{ MODEL_ID }}`
2. `{{ MODEL_ID }}`
3. `{{ MODEL_ID }}`
4. `{{ MODEL_ID }}`

## Profiles

### math-heavy

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| `STRONG_REASONER` | `{{ MODEL_ID }}` | max | 3-5 min |
| `BALANCED_MODEL` | `{{ MODEL_ID }}` | max | 3-5 min |
| `FAST_MODEL` | `{{ MODEL_ID }}` | max | 3-5 min |

### premium

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| `STRONG_REASONER` | `{{ MODEL_ID }}` | max | 2-4 min |
| `BALANCED_MODEL` | `{{ MODEL_ID }}` | max | 2-3 min |
| `FAST_MODEL` | `{{ MODEL_ID }}` | max | 1-2 min |

### balanced

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| `STRONG_REASONER` | `{{ MODEL_ID }}` | max | 2-3 min |
| `BALANCED_MODEL` | `{{ MODEL_ID }}` | high | 1-2 min |
| `FAST_MODEL` | `groq/{{ MODEL_ID }}` | default | 30-90s |

### budget

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| `STRONG_REASONER` | `{{ MODEL_ID }}` | high | 1-2 min |
| `BALANCED_MODEL` | `groq/{{ MODEL_ID }}` | default | 30-90s |
| `FAST_MODEL` | `groq/{{ MODEL_ID }}` | default | 15-60s |
