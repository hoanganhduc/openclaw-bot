# Multi-Agent Research Templates — Claude Code Execution Guide

This file is the execution reference for running the 6 research templates via Claude Code's Agent tool.
Read this when the user triggers a multi-agent research task.
Template definitions (roles, rounds, hard rules, output formats): see `SKILL.md` in this directory.

---

## Model Mapping

| Reasoning Tier | Agent `model` | Use for |
|---|---|---|
| R4 (expert) | `opus` | Proofs, formal math, adversarial reasoning, correctness verification |
| R3 (strong) | `sonnet` | Planning, synthesis, algorithm design, structured review |
| R2 (solid) | `sonnet` | Edge-case review, advocacy, specialist analysis |
| R1 (basic) | `haiku` | Scouting, brainstorming, clarity review, summarization |

Profile defaults:

| Profile | Default model | Override for judge/synthesizer |
|---|---|---|
| math-heavy | opus (all roles) | — |
| premium | sonnet | opus for lead roles |
| balanced | sonnet | — |
| budget | haiku | sonnet for judge |

---

## Execution Pattern

### Launching role agents

Each role is a separate Agent tool call:

```
Agent({
  model: "<tier>",
  description: "<Template> <Role> R<N>",
  prompt: "<full role briefing — see Role Prompt Template below>"
})
```

### Parallel execution

Independent roles in the same round → multiple Agent calls in a **single message**.

Example (Structured Research Team, Round 1):
```
Agent({ model: "opus", description: "SRT Builder R1", prompt: "..." })
Agent({ model: "opus", description: "SRT Breaker R1", prompt: "..." })
Agent({ model: "opus", description: "SRT Alt Builder R1", prompt: "..." })
```

### Sequential execution

Synthesis/referee roles run AFTER receiving all prior role outputs.
Compress prior results before passing to the next round (keep only decisive findings, not full text).

### Background execution

Use `run_in_background: true` only if the user wants to do other work during the run.
Default: foreground parallel calls (results returned together).

---

## Role Prompt Template

Every role agent receives a self-contained prompt with this structure:

```
You are the {ROLE_NAME} in a {TEMPLATE_NAME} multi-agent research session.

## Your role
{ROLE_DESCRIPTION — from SKILL.md template}

## Round {N} of {TOTAL}
{ROUND-SPECIFIC INSTRUCTIONS — from SKILL.md template}

## Topic / Claim
{THE CLAIM, PROOF, PAPER, OR PROBLEM — verbatim or summarized}

## Prior round context
{COMPRESSED SUMMARY of prior round outputs — omit in Round 1}
{Include only decisive findings, not full transcripts}

## Required output format
{STRUCTURED OUTPUT FORMAT — from SKILL.md template}

## Tool access
{For computation roles:}
- To run SageMath: use Bash tool with:
  bash ~/.openclaw/workspace/skills/_run.sh skills/sagemath/run_sage.sh "<sage_code>"
- To verify graph properties: use Bash tool with:
  bash ~/.openclaw/workspace/skills/_run.sh skills/graph-verifier/run_graph_verifier.sh --input '<json>'
- You may read files, search the web, and use Grep/Glob as needed.

{For pure reasoning roles:}
- Read files and search if needed, but do NOT run computations.
- Your value is independent reasoning, not computation.

## Hard rules
- Work independently. Do not assume what other roles have found.
- Be concrete: cite exact lines, pages, steps, definitions.
- Distinguish: proved / heuristic / conjectural / unverified.
- If you find a fatal flaw, say so clearly and switch to diagnosis.
- Do not write files. Return your analysis as text only.
- Correctness over elegance. Prefer a weaker correct claim over a stronger broken one.
```

---

## Template Execution Plans

### 1. Lakatos Proof & Refutation

**Profile:** math-heavy | **Rounds:** 3 | **Roles:** 4

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Prover | opus | No |
| 2 | Counterexample Hunter | opus | Yes |
| 3 | Monster-Barrer / Refiner | sonnet | No |
| 4 | Formalist | opus | No |

**Round 1** — 4 parallel Agent calls (independent first pass):
- Prover: present claim and proof sketch, identify key steps and dependencies
- Counterexample Hunter: list hypothesis space, identify failure points, generate candidates for small cases via SageMath
- Monster-Barrer: identify classes/ranges NOT covered by hypothesis, find boundaries
- Formalist: list every assumption (explicit + implicit), check quantifier order, flag unjustified steps

**Round 2** — 4 parallel Agent calls (each receives compressed Round 1 results):
- Each role responds to others' findings
- Prover defends or concedes
- Counterexample Hunter narrows search based on Formalist's weak points

**Round 3** — 1 Agent call (Formalist synthesizes):
- Receives all Round 2 results
- Produces: (a) is claim correct as stated? (b) strongest surviving version? (c) what remains open?

---

### 2. Polya Multi-Strategy Problem Solving

**Profile:** premium | **Rounds:** 3 | **Roles:** 3

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Specializer | sonnet | Yes |
| 2 | Generalizer | opus | No |
| 3 | Reducer | opus | No |

**Round 1** — 3 parallel Agent calls (independent):
- Specializer: solve/characterize for paths, cycles, trees, tw ≤ 3, bipartite, small n via SageMath
- Generalizer: survey known results, list ≥ 3 applicable techniques
- Reducer: identify 3 most promising reduction sources, sketch top candidate gadget

**Round 2** — 3 parallel Agent calls (orchestrator cross-pollinates all Round 1 findings):
- Specializer: test Reducer's sketch for small counterexamples, check Generalizer's techniques on open cases
- Generalizer: identify structural property separating easy/hard from Specializer's data
- Reducer: refine sketch based on Specializer's computational data

**Round 3** — Orchestrator synthesizes directly (or 1 opus Agent call):
- Ranked list of approaches with estimated difficulty and expected outcome

---

### 3. Knuth Manuscript Review

**Profile:** premium | **Rounds:** 2 | **Roles:** 3

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Correctness Reviewer | opus | Yes (for verifiable claims) |
| 2 | Exposition Reviewer | sonnet | No |
| 3 | Literature Reviewer | sonnet | No |

**Round 1** — 3 parallel Agent calls (independent reviews):
- Correctness: line-by-line proof review, severity classification, SageMath for computational claims
- Exposition: readability review as non-specialist, notation/definition/motivation checks
- Literature: positioning review, citation accuracy, missing references, novelty claims

**Round 2** — Orchestrator synthesizes directly:
- Reconcile overlapping issues, remove duplicates
- Produce prioritized action list: critical → significant → citation → minor → cosmetic

---

### 4. Structured Research Team

**Profile:** math-heavy | **Rounds:** 3 + conditional R4 | **Roles:** 4

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Builder | opus | No |
| 2 | Breaker | opus | Yes |
| 3 | Alternative Builder | opus | No |
| 4 | Referee / Verifier | opus | — (synthesis only) |

**Round 1** — 3 parallel Agent calls (Builder, Breaker, Alt Builder — independent):
- Builder outputs: claim version, strategy, intermediate lemmas, most fragile step, suggested checks
- Breaker outputs: strongest objection, failing step, candidate counterexample, fatal/repairable, minimal fix
- Alt Builder outputs: alternative route, why different, key bottleneck, advantage, likely failure mode

**Round 2** — 3 parallel Agent calls (cross-critique with compressed Round 1):
- Each critiques only decisive issues
- If decisive counterexample found: Builder proposes corrected version, does not defend broken claim
- Alt Builder evaluates if their route avoids Breaker's objection

**Round 3** — Orchestrator runs SageMath verification directly (no Agent call):
- Brute-force smallest instances
- Search for counterexamples
- Dependency audit
- Citation check (mark [unchecked] if not verified)

**Round 4 (conditional)** — 1 Agent call only if concrete local repair exists:
- Skip if issue is structural → recommend weaken/redesign in final ledger

**Final:** Referee/Verifier Agent (opus) produces final ledger using mandatory output format.

---

### 5. Graph Reconfiguration Specialist

**Profile:** math-heavy | **Rounds:** 3 + conditional R4 | **Roles:** 4

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Constructor | opus | No |
| 2 | Adversary | opus | Yes |
| 3 | Auditor | opus | No |
| 4 | Referee / Verifier | opus | — (synthesis only) |

**Round 1** — 3 parallel Agent calls (Constructor, Adversary, Auditor — independent):
- Constructor: claim version, proof/reduction outline, intermediate lemmas, fragile step, checks, weakening assessment
- Adversary: strongest objection, failing step, counterexample, fatal/repairable, smallest fix
- Auditor: TWO SEPARATE sections:
  - A. Local/interface audit (ports, legal moves, token count, occupancy, planarity)
  - B. Global/correspondence audit (cross-gadget moves, composition, both directions, serialization, size)

**Round 2** — 3 parallel Agent calls (cross-critique with compressed Round 1):
- Adversary and Auditor critique Constructor
- Constructor responds only to decisive issues
- Stop rule: if decisive counterexample, switch to diagnosis

**Round 3** — Orchestrator runs SageMath verification directly:
- A. Computational: brute-force instances, enumerate states/moves, test invariants
- B. Structural: class membership, planarity, orientation, degree, size
- C. Bibliographic: verify imported theorems (mark [unchecked])
- D. Formal: if attempted, checker is final authority

**Round 4 (conditional)** — 1 Agent call only if concrete local repair exists.

**Final:** Referee/Verifier Agent (opus) produces final ledger with typed verifier table and failure taxonomy.

---

### 6. Lean Formalization Team

**Profile:** math-heavy | **Rounds:** 2 | **Roles:** 5

| # | Role | Model | SageMath |
|---|------|-------|----------|
| 1 | Informal Planner | opus | No |
| 2 | Formalizer | opus | No |
| 3 | Missing-Lemma Miner | sonnet | No |
| 4 | Repair Agent | sonnet | No |
| 5 | Checker | opus | No |

**Round 1** — 3 parallel Agent calls:
- Informal Planner: decompose lemma into minimal subclaims, map to proof strategies
- Formalizer: write conservative Lean scaffold with `sorry` placeholders (preserve compiling state)
- Missing-Lemma Miner: search Mathlib, list available/missing lemmas

**Round 2** — 2 parallel Agent calls (receive Round 1 results):
- Repair Agent: classify each `sorry` (syntax/type/coercion/missing lemma/wrong induction/statement too strong/impossible goal)
- Checker: evaluate which sorries are closable, which need helper lemmas, which reveal paper proof gaps

**Final output:**
- Lean file status per sorry (closable / needs helper / blocked)
- Missing Mathlib lemmas list
- Paper proof correctness assessment

---

## State Management

Run directory: `~/.openclaw{{ PRIVATE_DATA_DIR }}/runs/<run_id>/`

Files written by the orchestrator (main Claude Code context):

| When | File | Content |
|------|------|---------|
| Before execution | `plan.md` | Roles, models, rounds, estimated time |
| Before execution | `state.json` | Full state (see schema in SKILL.md) |
| After each round | `round_01.md`, `round_02.md`, ... | Compressed role outputs |
| After completion | `final.md` | Final synthesis/ledger |

State updates:
- Set `status: "running"` before launching each round's agents
- Update `responses_received` after each Agent call returns
- Write round file as soon as all responses for that round are in
- Set `status: "completed"` after final.md is written

The `spawned_sessions` field is not applicable (Claude Code agents are ephemeral).
Track completion via `responses_received` map instead.

### Lock protocol

Before starting:
1. Check for `lock` file in run directory
2. If exists and < 30 min old → abort (another run in progress)
3. If exists and > 30 min → stale, remove and proceed
4. Write `lock` file with current timestamp
5. Remove `lock` after completion

### Recovery

If a conversation is interrupted:
1. User says "resume run <run_id>" or "continue the research session"
2. Read `state.json` — check `status`, `current_round`, `responses_received`
3. Read existing `round_XX.md` files to find last completed round
4. Re-launch only missing roles from the last incomplete round
5. Never re-run completed rounds

---

## SageMath Integration

Two modes:

### 1. Role agents running SageMath (Rounds 1-2)

Include in the role prompt:
```
To run SageMath computations:
  bash ~/.openclaw/workspace/skills/_run.sh skills/sagemath/run_sage.sh "<sage_code>"
Timeout default: 5 minutes. For longer jobs: add --timeout 1800 before the code.
```

Roles that should use SageMath:
- Counterexample Hunter (Lakatos)
- Specializer (Polya)
- Breaker / Adversary (Structured Research / Graph Reconfig)
- Correctness Reviewer (Knuth) — for verifiable computational claims

### 2. Orchestrator running verification (Round 3)

The orchestrator runs SageMath directly via Bash tool:
```bash
bash ~/.openclaw/workspace/skills/_run.sh skills/sagemath/run_sage.sh "<verification_code>"
```

This keeps verification independent from the roles being verified.

---

## Stop Rules

Embedded in every role prompt:

1. **Fatal flaw found** → Stop defending. Switch to diagnosis. Determine strongest defensible corrected claim.
2. **Decisive counterexample** → Builder/Constructor must not defend broken claim in subsequent rounds. Propose corrected version instead.
3. **Token exhaustion** → If an Agent call returns truncated output, re-launch with compressed context (summarize prior rounds into 1-2 paragraphs).

---

## Template Chaining

When a task spans multiple concerns, run templates as sequential phases.

| Task | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| Verify reduction + review draft | Graph Reconfig | Knuth Review | — |
| Explore problem + formalize | Polya | Structured Research | Lean Formalization |
| Check proof + fix + submit | Lakatos | Graph Reconfig | Knuth Review |
| Verify gadget + formalize lemma | Graph Reconfig | Lean Formalization | — |

Protocol:
1. Run Phase 1 to completion → `final.md`
2. Extract Accepted claims + strongest surviving proof skeleton
3. Pass extracted content as input to Phase 2
4. Phase round files: `round_P1_01.md`, `round_P2_01.md`, etc.
5. Final output combines all phase ledgers

Show the full chain plan to the user before starting.

---

## Template Selection (auto-detect)

| Task signal | Template |
|---|---|
| "verify my proof", "stress-test", "find holes" | Lakatos |
| "attack this problem", "explore complexity", "open problem" | Polya |
| "review my draft", "pre-submission", "camera-ready" | Knuth |
| General math/TCS claim verification | Structured Research Team |
| Token sliding/jumping, PSPACE, gadget, reconfiguration | Graph Reconfiguration Specialist |
| "formalize", "Lean proof", "fix sorry" | Lean Formalization Team |

If multiple match, prefer the more domain-specific one.
If uncertain, ask: "I'd recommend {template} for this — proceed, or prefer a different template?"

---

## Mandatory Pre-execution Steps

### 1. Show plan to user

Before launching agents, show:
- Template selected and why
- Roles with model assignments
- Number of rounds
- Estimated Agent calls (parallel × rounds)
- Estimated time
- For chains: all phases upfront

### 2. Step 0 — Claim restatement (research templates only)

Before Round 1:
1. Rewrite the target claim in exact mathematical terms
2. List all assumptions explicitly
3. Separate: given / to be proved / only conjectured
4. Identify notation and definitions
5. Confirm with user before spawning agents

---

## Quick Reference

| Template | Roles | Agents/round | Total calls | SageMath |
|----------|-------|-------------|-------------|----------|
| Lakatos | 4 | 4, 4, 1 | ~9 | Yes (Hunter) |
| Polya | 3 | 3, 3, 1 | ~7 | Yes (Specializer) |
| Knuth | 3 | 3, synth | ~3-4 | Optional |
| Structured Research | 4 | 3, 3, sage, 1+1 | ~8-9 | Yes (R3 + Breaker) |
| Graph Reconfig | 4 | 3, 3, sage, 1+1 | ~8-9 | Yes (R3 + Adversary) |
| Lean Formalization | 5 | 3, 2 | ~5 | No |
