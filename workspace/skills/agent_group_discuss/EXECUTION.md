# Execution Guide

This file is the execution reference for running imported research and review templates with OpenClaw session tools.
Template definitions live in `TEMPLATES.md`.

## Runtime rules

1. Before any `sessions_spawn` call, show the plan and get explicit user confirmation.
2. For research, proof, manuscript-correctness, or other high-stakes review tasks, use the highest reasoning model available for all spawned agents by default.
3. For complex correctness reviews, default timeout is 45 minutes and persistent progress checkpoints are required every 15 minutes.
4. Default execution is foreground unless the user explicitly asks to let it run in the background.

## Model mapping

Resolve models through `MODEL_TIERS.md`.

Practical default mapping:

| Reasoning tier | Research default | Non-research baseline | Use for |
|----------------|------------------|-----------------------|---------|
| `R4` | `{{ MODEL_ID }}` | `{{ MODEL_ID }}` | proofs, formal math, correctness verification, refereeing |
| `R3` | `{{ MODEL_ID }}` | `{{ MODEL_ID }}` | planning, synthesis, structured review |
| `R2` | `{{ MODEL_ID }}` | `{{ MODEL_ID }}` | edge-case review, support analysis |
| `R1` | `{{ MODEL_ID }}` | `groq/{{ MODEL_ID }}` | scouting, brainstorming, clarity review |

## Execution pattern

### Launching role agents

Each role is a separate `sessions_spawn` call.

Independent roles in the same round should be launched in parallel when possible.

### Round structure

- Round 1 independent first pass: use fresh agents with no cross-role contamination.
- Later rounds: reuse or respawn based on independence and token hygiene.
- Referee or synthesis roles run only after the prior round results are in.
- Compress prior results before relaying them. Keep only decisive findings, not full transcripts.

### Waiting and cleanup

- Wait once per round or per critical batch.
- Do not busy-poll.
- Close or let sessions expire after the run when they are no longer useful.

## Role prompt template

Every role should receive a self-contained prompt with:

1. role name and objective
2. round number
3. topic or claim
4. compressed prior-round context when applicable
5. exact required output format
6. hard rules: work independently, be concrete, distinguish proved from conjectural

## State management

Run directory:

- `{{ PRIVATE_DATA_DIR }}/runs/<run_id>/`

Files written by the orchestrator:

- `plan.md`
- `state.json`
- `round_01.md`, `round_02.md`, ...
- `final.md`

The orchestrator should update `state.json` before spawning each batch and immediately after each response arrives.
