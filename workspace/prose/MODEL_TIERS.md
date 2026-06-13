# MODEL_TIERS.md

For research tasks, the strict research-model rule in `AGENTS.md` overrides the default profile heuristics below.

## Reasoning level classification

Each model is classified by reasoning capability. Use this to match task
difficulty to model strength.

| Level | Description | Suitable for |
|-------|-------------|-------------|
| R4 — expert | Deep multi-step reasoning, formal proofs, complex math | Theorem proving, correctness verification, adversarial critique, PSPACE reductions |
| R3 — strong | Good reasoning with occasional gaps on hardest problems | Planning, synthesis, structured review, algorithm design |
| R2 — solid | Adequate for most tasks, may miss subtle logical issues | Edge-case review, advocacy, specialist analysis |
| R1 — basic | Fast pattern matching, good for generation/summary | Scouting, brainstorming, clarity review, summarization |

## Model catalog

| Model | Reasoning | Context | Speed | Cost | Thinking support |
|-------|-----------|---------|-------|------|-----------------|
| {{ MODEL_ID }} | R4 | 200K | medium | high | yes (extended) |
| {{ MODEL_ID }} | R4 | 200K | medium | low | yes (extended) |
| openrouter/{{ MODEL_ID }} | R4 | 200K | medium | high | yes (extended) |
| {{ MODEL_ID }} | R3 | 128K | medium | medium | yes |
| {{ MODEL_ID }} | R3 | 128K | medium | medium | yes |
| openrouter/{{ MODEL_ID }} | R3 | 128K | slow | high | yes |
| {{ MODEL_ID }} | R3 | 128K | slow | low | yes (chain-of-thought) |
| {{ MODEL_ID }} | R2 | 128K | fast | low | limited |
| groq/{{ MODEL_ID }} | R2 | 32K | fast | low | no |
| {{ MODEL_ID }} | R2 | 128K | fast | low | no |
| {{ MODEL_ID }} | R3 | 200K | medium | low | yes |

## Profiles

### premium (R4 leads, R3 supports)

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| STRONG_REASONER | {{ MODEL_ID }} | extended | 2-4 min |
| BALANCED_MODEL | {{ MODEL_ID }} | default | 1-2 min |
| FAST_MODEL | {{ MODEL_ID }} | off | 30-60s |

### balanced (R3 leads, R2 supports — default)

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| STRONG_REASONER | {{ MODEL_ID }} | default | 1-2 min |
| BALANCED_MODEL | {{ MODEL_ID }} | off | 30-60s |
| FAST_MODEL | groq/{{ MODEL_ID }} | off | 15-30s |

### budget (R2 leads, R1 supports)

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| STRONG_REASONER | {{ MODEL_ID }} | off | 30-60s |
| BALANCED_MODEL | groq/{{ MODEL_ID }} | off | 15-30s |
| FAST_MODEL | groq/{{ MODEL_ID }} | off | 10-20s |

### math-heavy (R4 everywhere, extended thinking)

| Tier | Model | Thinking | Est. time per role |
|------|-------|----------|--------------------|
| STRONG_REASONER | {{ MODEL_ID }} | extended | 3-5 min |
| BALANCED_MODEL | {{ MODEL_ID }} | extended | 2-4 min |
| FAST_MODEL | {{ MODEL_ID }} | default | 1-2 min |

## Task-to-profile heuristic

| Task signal | Recommended profile |
|-------------|-------------------|
| Formal proof, theorem, PSPACE, NP-hard, correctness verification | math-heavy |
| Research paper review, algorithm design, critical decision | premium |
| General discussion, code review, brainstorming, exploration | balanced |
| Quick sanity check, opinion gathering, lightweight summary | budget |

## Role-to-tier mapping

| Role | Tier | Reasoning need |
|------|------|---------------|
| planner | STRONG_REASONER | Must decompose complex tasks correctly |
| judge / synthesizer | STRONG_REASONER | Must weigh competing arguments without bias |
| correctness reviewer / critic / falsifier | STRONG_REASONER | Must catch subtle logical and mathematical errors |
| advocate / edge-case reviewer / hypothesis generator | BALANCED_MODEL | Needs solid reasoning for specific angles |
| pragmatist / clarity reviewer / scout / brainstormer | FAST_MODEL | Speed and breadth over depth |
