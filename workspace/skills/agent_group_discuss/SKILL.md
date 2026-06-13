---
name: agent_group_discuss
description: Dynamically plan a multi-agent discussion, review, or research workflow with role selection, model assignment, round control, and recovery-aware orchestration.
user-invocable: true
disable-model-invocation: true
---

# Agent Group Discuss

You are the orchestrator for dynamic multi-agent work.

Use this skill when the user asks in normal language for:
- a group discussion
- a panel of agents
- multiple agents to discuss something
- a multi-agent review
- a review-only request that explicitly asks for multiple agents or a panel
- a multi-agent research session
- agents with different roles or different models

If the request is review-only and does not ask for annotation, keep it in this skill for the multi-agent route; do not route it to `annotated-review` unless annotation is also explicitly requested.

## Supporting files

Use these local support docs when relevant:

- `TEMPLATES.md` for named review and research templates
- `EXECUTION.md` for round topology, time estimates, and orchestration rules
- `MODEL_TIERS.md` for the live model catalog and research-task override
- `MODEL_TIERS.example.md` only as a customization template
- `README.md` for the short request shape

If the user requests a named template, or the task clearly matches one, open `TEMPLATES.md` and `EXECUTION.md` before running the session.

## Clarification policy

If the request is missing information, ask only for the minimum needed.

Use this compact question when needed:
“Before I start: do you want discussion, review, or research? How many rounds? Any hard constraints? I can choose roles and models automatically if you want.”

If the user gives no preference, default to:
- mode: infer from the task
- rounds: 2
- roles: 3
- interaction: auto
- role/model selection: automatic

## Routing to structured workflows

If the request is clearly better suited to an installed OpenProse workflow, say so briefly and ask:
“I can run this as a conversational skill or as a more structured workflow. Which do you want?”

Prefer OpenProse if the user says:
- structured workflow
- use prose
- compile the workflow
- run the workflow
- deterministic

Otherwise stay in this skill.

## High-level goal

Given a task, you must:
1. classify the task
2. decide which roles are needed
3. decide how many subagents are useful
4. assign a model to each role
5. choose how the subagents interact
6. run the requested number of rounds
7. synthesize the result
8. maintain durable recovery state on disk

## Required review-validate-fix loop

Use this loop for every multi-agent discussion, review, or research run:

1. **Review** — inspect the user goal, selected template, role plan, model assignments, constraints, and prior round outputs.
2. **Validate** — confirm the plan is OpenClaw-native, user-approved, scoped, and free of unsupported claims or copied external settings.
3. **Fix** — adjust roles, rounds, prompts, evidence requirements, or conclusions before continuing.
4. Repeat after every round and before final delivery.

For research outputs, the final synthesis must include a compact `Review Findings` pass and a `Delivery Check`. If material evidence remains unchecked, write `incomplete analysis` and list what remains unchecked.

When the task is about adapting another system into OpenClaw, treat external workflows as source patterns only. Do not copy external model choices, path layouts, runtime wrappers, or agent defaults unless the user explicitly asks for a compatibility experiment.

## Input format

Accept either free-form text or a structured block such as:

topic: <topic>
mode: discussion | review | research | mixed
rounds: <integer>
max_agents: <integer>
interaction: auto | star | debate | panel_judge
output: <desired final output format>
constraints:
- <constraint>
- <constraint>

If the user specifies rounds, obey it.

## Role selection

Prefer 3 roles by default.
Use 4 or more only when the task clearly benefits.

Typical roles by mode:

### Discussion
- Optimist
- Skeptic
- Pragmatist
- Judge

### Review
- Correctness reviewer
- Edge-case reviewer
- Clarity reviewer
- Synthesizer

### Research
- Literature scout
- Hypothesis generator
- Critic / falsifier
- Synthesizer

## Model assignment

Each role gets a model and thinking level via `sessions_spawn`'s `model` and `thinking` parameters.
Refer to `MODEL_TIERS.md` for the model catalog, reasoning classifications, and profiles.

### Reasoning level classification

Models are classified R1–R4 by reasoning capability:

| Level | Capability | Assign to |
|-------|-----------|-----------|
| R4 — expert | Multi-step proofs, formal math, adversarial reasoning | Theorem verification, PSPACE reductions, correctness critique |
| R3 — strong | Solid structured reasoning | Planning, synthesis, algorithm design, structured review |
| R2 — solid | Adequate for most tasks | Edge-case review, advocacy, specialist analysis |
| R1 — basic | Fast generation and summarization | Scouting, brainstorming, clarity review |

### Profile selection

Detect the task's reasoning demands and select a profile:

| Task signal | Profile | Lead model level |
|-------------|---------|-----------------|
| Formal proof, theorem, correctness verification, PSPACE, NP-hard | **math-heavy** | R4 with extended thinking |
| Research paper review, algorithm design, critical decision | **premium** | R4 leads, R3 supports |
| General discussion, code review, exploration | **balanced** (default) | R3 leads, R2 supports |
| Quick sanity check, opinion gathering, lightweight summary | **budget** | R2 leads, R1 supports |

If the user specifies a profile (e.g., "use budget models"), select all models from that profile.
If the user specifies a model for a specific role (e.g., "use opus for the judge"), override that role only.
If no preference is given, auto-detect from task signals or default to **balanced**.

### Tier assignment by role type

| Role type | Tier | Reasoning need |
|-----------|------|---------------|
| planner, judge, synthesizer, critic, correctness reviewer | STRONG_REASONER | Must reason deeply and catch subtle errors |
| advocate, specialist reviewer, edge-case reviewer | BALANCED_MODEL | Solid reasoning for specific angles |
| scout, brainstormer, pragmatist, clarity reviewer | FAST_MODEL | Speed and breadth over depth |

Record the chosen profile, per-role model, and thinking level in `state.json`.

### Token management

Do not promise token overflow cannot happen.
Keep prompts compact and summarize prior rounds before relaying them.
If a subagent response is truncated (ends mid-sentence or hits output limit):
1. Note the truncation in `state.json` under `notes`
2. Re-spawn the role with a shorter context (summarize prior rounds more aggressively)
3. If re-spawn also truncates, extract what is usable and continue with a note
4. Never silently discard a truncated response — always flag it in the final output

## Interaction design

Choose:
- star
- debate
- panel_judge

If the user requests one explicitly, obey it.

## Round control

If rounds is provided, obey it.
Otherwise:
- default to 2 rounds for discussion/review
- default to 2 or 3 rounds for research depending on complexity

Never exceed 5 rounds unless the user explicitly asks.

## Timeouts

Per-round timeout: 10 minutes (default). If a spawned role does not respond within this window:
1. Mark the role as timed out in `responses_received`
2. Add a note to `pending_work`
3. Continue the round with the responses that were received
4. Do not block the entire run for one unresponsive role

Total run timeout: 30 minutes. If exceeded, write the best available synthesis from completed rounds and set `status: "failed"` with a note explaining what was incomplete.

## Required durable state

Create a run folder under `data/runs/<run_id>/`.

Before spawning subagents:
1. Create `data/runs/<run_id>/lock` (a file containing the current timestamp). If the lock file already exists and is less than 30 minutes old, abort with an error — another run is in progress. If the lock is older than 30 minutes, remove it (stale lock) and proceed.
2. Write `data/runs/<run_id>/plan.md`
3. Write `data/runs/<run_id>/state.json`
4. **Show the plan to the user** before executing (unless the user said "just run it")

Before each spawn batch, validate the state file against the current plan. If roles, model assignments, constraints, or expected outputs changed, fix `plan.md` and `state.json` before continuing.

After the run completes (success or failure), remove the lock file.

### Plan output format

The plan.md and the user-facing plan summary must include:

```
## Run Plan: <run_id>

**Topic:** <topic>
**Mode:** <mode>
**Profile:** <profile>
**Interaction:** <pattern>
**Rounds:** <N>
**Estimated total time:** <X-Y minutes>

### Subagent assignments

| # | Role | Model | Reasoning | Thinking | Est. time |
|---|------|-------|-----------|----------|-----------|
| 1 | Planner | {{ MODEL_ID }} | R4 | extended | 2-4 min |
| 2 | Correctness Reviewer | {{ MODEL_ID }} | R3 | default | 1-2 min |
| 3 | Edge-case Reviewer | {{ MODEL_ID }} | R2 | off | 30-60s |
| 4 | Synthesizer | {{ MODEL_ID }} | R4 | extended | 2-4 min |

### Execution plan

- Round 1: Spawn roles 1-3 in parallel, then role 4 synthesizes
- Round 2: Roles 1-3 rebut, role 4 produces final synthesis

### Risk notes
- <token budget concerns>
- <any roles that may need fallback models>
```

Time estimation formula:
- Per role: look up "Est. time per role" from the profile in MODEL_TIERS.md
- Parallel roles in the same round: use the slowest role's time
- Sequential steps (synthesis after parallel): add times
- Multiply by number of rounds
- Add 1-2 minutes for orchestration overhead

The state file must follow this schema:

```json
{
  "run_id": "string — unique run identifier",
  "topic": "string — the discussion/review/research topic",
  "mode": "discussion | review | research",
  "profile": "premium | balanced | budget | math-heavy",
  "interaction": "star | debate | panel_judge",
  "roles": ["string — role names in order"],
  "models": {
    "role_name": {
      "model": "provider/model-id",
      "thinking": "extended | default | off",
      "reasoning_level": "R4 | R3 | R2 | R1"
    }
  },
  "rounds_requested": 2,
  "current_round": 0,
  "status": "planning | running | paused | completed | failed",
  "spawned_sessions": {
    "role_name": "session-id or null"
  },
  "responses_received": {
    "role_name": true
  },
  "pending_work": ["string — remaining tasks"],
  "start_time": "ISO 8601 timestamp",
  "estimated_duration_minutes": 10,
  "recovery_needed": false,
  "notes": ["string — post-hoc observations"]
}
```

After each round, write (use zero-padded numbering):
- `data/runs/<run_id>/round_01.md`
- `data/runs/<run_id>/round_02.md`

After synthesis, write:
- `data/runs/<run_id>/final.md`

## Spawning policy

The main agent is the orchestrator.
Prefer leaf subagents spawned directly by the main agent.

Subagent tool restrictions: spawned roles should only have access to tools they need for analysis and reasoning. They must NOT have access to:
- `gateway`, `cron` — infrastructure management
- `sessions_spawn` — no nested subagent spawning
- `group:automation` — no automated actions
- `write`, `edit` — only the orchestrator writes run state files

Subagents may use: `read`, `web_search`, `web_fetch`, `group:memory` (for context retrieval).

Use `sessions_spawn` for each participant contribution.
Each spawned task must include:
- the role
- the topic
- the role objective
- the expected response format
- the current round number
- only the minimum prior context needed

For opening statements, ask for:
- short position
- strongest argument
- one uncertainty or caveat

For later rounds, ask each role to:
- respond to the strongest counterpoint
- refine or defend its position
- provide one concession or one rebuttal

## Recovery behavior

### Subagent failure

Subagent announce-back can fail.
If a result is missing or the run was disrupted:
1. Read `state.json` — check `status`, `current_round`, `responses_received`, and `pending_work`
2. Check which `round_XX.md` files exist to find the last completed round
3. Identify missing roles by comparing `responses_received` against `roles`
4. Set `recovery_needed: true` and `status: "running"` in state.json
5. Re-spawn only the missing roles from the last incomplete round
6. Never discard already completed rounds unless the user asks
7. If a role fails 2 consecutive times, skip it and note the skip in `pending_work`

### Token exhaustion

If a subagent's response is cut short (truncated output, token limit reached):
1. Save whatever was received to the round file with a `[TRUNCATED]` marker
2. Re-spawn the same role with compressed context (summarize all prior rounds into 1-2 paragraphs)
3. If the model's context window is too small, fall back to a model with a larger context window from the same tier or one tier down
4. Update `state.json` with the model change and reason

### Gateway restart / inactivity interruption

If the orchestrator session is interrupted (gateway restart, inactivity timeout, connection drop):
1. All state is on disk in `data/runs/<run_id>/`
2. On resume, the orchestrator (or user) says: "resume run <run_id>" or "continue the discussion"
3. The orchestrator reads `state.json` and all existing `round_XX.md` files
4. It determines the last completed round and which roles responded
5. It re-spawns only the missing work from the incomplete round
6. Completed rounds and their files are never re-run

To make this work, the orchestrator must:
- Write `state.json` **before** spawning each batch of subagents (not after)
- Update `state.json` immediately after each response is received
- Write each `round_XX.md` as soon as all responses for that round are in

### Pause and resume

The user can pause a run at any time by saying "pause" or "stop for now".
On pause:
1. Set `status: "paused"` in `state.json`
2. Note the pause reason and timestamp in `notes`
3. Do not spawn any new subagents
4. Wait for any in-flight subagents to complete and save their responses
5. Tell the user what was completed and what remains

To resume:
1. The user says "resume", "continue", or "resume run <run_id>"
2. Read `state.json` — verify `status` is `"paused"` or `"failed"`
3. Set `status: "running"` and `recovery_needed: true`
4. Continue from the last consistent checkpoint

## Research templates

The user can request a template by name, or the orchestrator auto-selects based on task signals. If auto-selecting, briefly state which template was chosen and why before proceeding. The user can override.

### Template auto-selection

| Task signal | Recommended template |
|-------------|---------------------|
| "verify my proof", "check this theorem", "stress-test", "find holes" | **Lakatos Proof & Refutation** |
| "attack this problem", "explore complexity", "is this hard or easy", "open problem" | **Pólya Multi-Strategy** |
| "review my draft", "pre-submission review", "check exposition", "camera-ready" | **Knuth Manuscript Review** |
| General math/TCS claim, algorithm analysis, combinatorial argument | **Structured Research Team** |
| Token sliding/jumping, reduction proof, gadget verification, reconfiguration, PSPACE reduction | **Graph Reconfiguration Specialist** |
| "formalize this lemma", "Lean proof", "fix this sorry", "formalization" | **Lean Formalization Team** |

If multiple templates match, prefer the more domain-specific one. If uncertain, ask: "I'd recommend the [template name] for this — shall I proceed, or would you prefer a different template?"

### Template chaining

When a task spans multiple concerns, chain templates as sequential phases within a single run. Each phase uses one template, and the output of one phase feeds into the next.

**Common chains:**

| Task | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| "Verify this reduction and review the draft" | Graph Reconfig Specialist (correctness) | Knuth Manuscript Review (exposition) | — |
| "Explore this problem, then formalize" | Pólya Multi-Strategy (exploration) | Structured Research Team (pin down the claim) | Lean Formalization (formalize) |
| "Check my proof, fix it, then prepare for submission" | Lakatos (find holes) | Graph Reconfig Specialist (repair) | Knuth Manuscript Review (polish) |
| "Verify a gadget family and formalize the key lemma" | Graph Reconfig Specialist (verify) | Lean Formalization (formalize surviving lemma) | — |

**How chaining works:**
1. Run Phase 1 to completion. Produce the standard final ledger.
2. Pass only the **Accepted** claims and **strongest surviving proof skeleton** to Phase 2 as input context. Drop rejected/unresolved items unless Phase 2 is specifically about resolving them.
3. Each phase gets its own round files: `round_P1_01.md`, `round_P1_02.md`, `round_P2_01.md`, etc.
4. The final output combines all phase ledgers into one, with phase annotations.

**The orchestrator auto-detects chaining opportunities** from task signals (e.g., "verify and review" → two phases). State the planned chain before starting: "I'll run this in 2 phases: (1) Graph Reconfiguration Specialist to verify correctness, (2) Knuth Review for exposition. OK?"

The user can also request chaining explicitly: "run Lakatos then Knuth on this draft."

### Mandatory plan (all templates)

Before any template begins, the orchestrator must produce the **plan output** (see "Plan output format" section above) showing:
- Which model is assigned to each role (with reasoning tier R1-R4 and thinking level)
- Estimated time per role and total run time
- Profile used (math-heavy / premium / balanced / budget)
- Round execution order (which roles run in parallel, which are sequential)

For chained templates, show the plan for all phases upfront so the user can see the total estimated time and model usage before committing.

### Mandatory preamble (all research templates)

Before any template begins, the orchestrator must also produce a **Step 0 restatement**:
1. Rewrite the target claim in exact mathematical terms
2. List all assumptions explicitly
3. Separate what is **given**, what is **to be proved**, and what is **only conjectured**
4. Identify the notation and definitions in use

Show this to the user and confirm before spawning agents. This catches misunderstandings before wasting compute.

### Stop rule (all research templates)

If a decisive counterexample or fatal gap is found during any round:
1. Stop defending the broken claim immediately
2. Switch to diagnosis: what exactly fails and why
3. Determine the strongest defensible corrected claim
4. Do NOT continue expanding a broken proof across further rounds

### Default output format for research-mode templates

The final synthesis must contain these sections:

**1. Accepted** — for each: statement, status (valid/partially valid), evidence, dependence list

**2. Rejected** — for each: statement, status (invalid), decisive reason, smallest counterexample if available

**3. Unresolved** — for each: statement, status (unclear), exact gap, missing evidence needed

**4. Strongest surviving proof skeleton** — shortest outline that currently survives all objections

**5. Verification status** — what was checked (SageMath, SAT/SMT, manual), what passed, what failed, what remains unchecked

**6. Single recommended next action** — choose exactly one: formalize / brute-force test / search for counterexample / weaken statement / redesign proof / redesign reduction / isolate sublemma

---

### Template: Lakatos Proof and Refutation

Based on: Lakatos, I. (1976). *Proofs and Refutations: The Logic of Mathematical Discovery*. Cambridge University Press.

**When to use:** Stress-testing a new theorem or proof draft. Finding edge cases before submission.

**Mode:** review
**Profile:** math-heavy
**Interaction:** debate
**Roles (4):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Prover | R4 | Present the proof, defend the argument, propose fixes when flaws are found |
| 2 | Counterexample Hunter | R4 | Systematically search for counterexamples, boundary cases, degenerate inputs (empty graph, K₁, disconnected graphs, directed vs undirected). Use SageMath for computational checks. |
| 3 | Monster-Barrer / Refiner | R3 | When a counterexample is found, determine whether to restrict the hypothesis or strengthen the proof. Propose refined theorem statements. |
| 4 | Formalist | R4 | Check logical structure: are all quantifiers explicit? Are assumptions tracked? Does the proof use all hypotheses? Flag steps relying on unstated assumptions. |

**Rounds (3):**

Round 1 prompt for each role:
- Prover: "Present the claim and proof sketch. Identify the key steps and what each depends on."
- Counterexample Hunter: "Read the claim. List the hypothesis space. Identify the most likely failure points. Generate candidate counterexamples for small cases (n ≤ 7). Use SageMath if available."
- Monster-Barrer: "Read the claim. What graph classes or parameter ranges are NOT covered by the hypothesis? Where is the boundary?"
- Formalist: "Read the proof. List every assumption used (explicit and implicit). Check quantifier order. Flag any step that is not justified."

Round 2: Each role responds to the others' findings. Prover defends or concedes. Counterexample Hunter narrows the search based on Formalist's identified weak points.

Round 3: Synthesis. Formalist produces final assessment: (a) is the claim correct as stated? (b) what is the strongest version that survives? (c) what remains open?

### Template: Pólya Multi-Strategy Problem Solving

Based on: Pólya, G. (1945). *How to Solve It*. Princeton University Press. See also: Pólya, G. (1954). *Mathematics and Plausible Reasoning*, Vols. I–II. Princeton University Press.

**When to use:** Attacking an open problem or conjecture where the right approach is unclear. Exploring the complexity boundary (hard vs tractable).

**Mode:** research
**Profile:** premium
**Interaction:** star (orchestrator collects, cross-pollinates, then re-distributes)
**Roles (3):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Specializer | R3 | Attack the problem on restricted instances (trees, paths, bounded treewidth, planar, small n). Find the boundary between easy and hard. Use SageMath for computational experiments. |
| 2 | Generalizer | R4 | Look for connections to known results. Can a known technique (Courcelle's theorem, color-coding, iterative compression, sunflower lemma) be adapted? Is this a special case of a solved problem? |
| 3 | Reducer | R4 | Assume the problem is hard. Search for a reduction source (3-SAT, NCL, Token Sliding on known-hard classes). Sketch gadget constructions. Identify which features make the problem hard. |

**Rounds (3):**

Round 1 prompt for each role:
- Specializer: "Solve or characterize the problem for: paths, cycles, trees, bounded treewidth ≤ 3, bipartite graphs, and small n (≤ 6). Report which cases are easy and which resist your attempts. Use SageMath to enumerate if needed."
- Generalizer: "Survey known results for related problems. List at least 3 known techniques that might apply. For each, explain why it might work and what obstacle might prevent it."
- Reducer: "Identify the 3 most promising reduction sources. For the top candidate, sketch the high-level gadget idea: what does each component encode? What invariant is maintained?"

Round 2: Orchestrator shares all findings. Each role reads the others.
- Specializer: "Given the Reducer's hardness sketch, can you find a small counterexample to the reduction? Given the Generalizer's technique suggestions, do any of them solve your remaining open cases?"
- Generalizer: "Given the Specializer's easy/hard boundary, can you identify the structural property that separates them? Does this match any known dichotomy theorem?"
- Reducer: "Given the Specializer's computational data, does it support or contradict your hardness conjecture? Refine your reduction sketch."

Round 3: Synthesis. Which strategy is most promising? What concrete next steps should the researcher take? Produce a ranked list of approaches with estimated difficulty and expected outcome.

### Template: Knuth Structured Manuscript Review

Based on: Knuth, D.E., Larrabee, T., & Roberts, P.M. (1989). *Mathematical Writing*. MAA Notes No. 14. Mathematical Association of America. See also: Krantz, S.G. (1997). *A Primer of Mathematical Writing*. American Mathematical Society.

**When to use:** Reviewing a paper draft before submission. Preparing camera-ready version. Responding to referee reports.

**Mode:** review
**Profile:** premium
**Interaction:** panel_judge (three independent reviews, then judge synthesizes)
**Roles (3):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Correctness Reviewer | R4 | Read every proof line by line. Check: does each step follow? Are all cases covered? Are quantifiers correct? Are there unstated assumptions? Use SageMath to verify computational claims. Produce issues with severity: critical / minor / cosmetic. |
| 2 | Exposition Reviewer | R3 | Read as a non-specialist in the exact subfield. Check: is notation introduced before use? Are definitions self-contained? Is the paper readable top-to-bottom? Are examples helpful? Does the introduction motivate the work? Flag unclear passages. |
| 3 | Literature Reviewer | R3 | Check positioning: are all relevant prior results cited? Is comparison to related work accurate and fair? Are there missing connections? Are claimed novelties actually novel? Verify citations exist (no fabrication). |

**Rounds (2):**

Round 1 prompt for each role:
- Correctness: "Review the manuscript for mathematical correctness. For each issue, state: section, claim/proof affected, severity (critical/minor/cosmetic), and a specific fix suggestion. If a claim can be verified computationally, do so."
- Exposition: "Review the manuscript for clarity and readability. For each issue, state: section, the problem (unclear definition, notation clash, missing motivation, etc.), and a concrete rewrite suggestion. Follow Knuth's principle: a mathematical text should be readable as a story."
- Literature: "Review the manuscript's positioning. For each issue, state: the claim about novelty or relation to prior work, whether it is accurate, and what correction is needed. List any missing references that should be cited."

Round 2: Orchestrator collects all three reviews. Synthesizer (orchestrator itself) reconciles overlapping issues, removes duplicates, and produces a single prioritized action list:
1. Critical correctness issues (must fix)
2. Significant exposition problems (should fix)
3. Missing/wrong citations (should fix)
4. Minor issues (nice to fix)
5. Cosmetic suggestions (optional)

Output integrates with instruction.md §28 (review validation) and STATE.md workflow.

### Template: Structured Research Team

Adapted from a protocol designed for rigorous mathematical/TCS claim verification with independent roles and structured outputs.

**When to use:** Verifying a specific claim, proof, reduction, or structural characterization. The most rigorous template — use when correctness is paramount (e.g., before submitting a paper, or when a proof has already been questioned).

**Mode:** research
**Profile:** math-heavy
**Interaction:** star (independent first pass, then cross-critique)
**Roles (4):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Builder | R4 | Propose the strongest plausible proof strategy, algorithm, reduction, or structural characterization |
| 2 | Breaker | R4 | Search aggressively for hidden assumptions, quantifier mistakes, edge cases, counterexamples, broken gadgets, invalid inductions, missing directions, boundary failures |
| 3 | Alternative Builder | R4 | Produce a genuinely different route — not a paraphrase of the Builder. Must explain why the route is different and what advantage it offers. |
| 4 | Referee / Verifier | R4 | Synthesize only what survives scrutiny and external checking. Produce the final ledger. |

**Rounds (3, with conditional round 4):**

Round 1 — independent first pass. Builder, Breaker, and Alternative Builder work independently. Do not share results between them.

Builder must output:
- A. Precise claim version being attempted
- B. Strategy outline
- C. Intermediate lemmas needed
- D. Most fragile step (the step most likely to be wrong)
- E. Suggested external checks (SageMath computation, SAT encoding, brute-force, etc.)

Breaker must output:
- A. Strongest objection
- B. Exact failing step or most suspicious step
- C. Candidate counterexample or obstruction
- D. Whether the issue is fatal or plausibly repairable
- E. Minimal fix, if any

Alternative Builder must output:
- A. Alternative route
- B. Why it is genuinely different from the Builder's approach
- C. Key bottleneck
- D. Expected advantage over the Builder's route
- E. Likely failure mode of this alternative

Round 2 — one critique round. Each role critiques only decisive issues in the others' proposals.
- Focus on correctness, not style.
- If a decisive counterexample was found in Round 1, the Builder must not defend the broken claim — instead, propose the strongest defensible corrected version.
- Alternative Builder evaluates whether their route avoids the Breaker's objection.

Round 3 — external verification (run by orchestrator). Whenever feasible, run one or more of:
- Brute-force verification on smallest nontrivial instances (via SageMath)
- Python/Sage search for counterexamples to the claimed statement
- Explicit dependency audit (does each step actually use only what it claims?)
- SAT/SMT/ILP encoding for finite verification
- Citation/reference check for any imported fact (mark unverified as [unchecked])

Round 4 — optional repair round. Run ONLY if:
- There is a concrete local repair (not a fundamental redesign)
- The Breaker confirms the repair addresses the specific failure
If the issue is structural, skip Round 4 and recommend "weaken statement" or "redesign proof" in the final ledger.

**Final output:** Uses the mandatory research output format (Accepted / Rejected / Unresolved / Proof skeleton / Verification status / Single next action).

**Hard rules for all roles:**
- Correctness over elegance
- Distinguish explicitly: proved / heuristic / conjectural / unverified
- Never suppress dependence on an unproved subclaim
- Check both directions of every iff/equivalence
- Check boundary cases explicitly (n=0,1,2,3; empty graph; disconnected; directed vs undirected)
- Do not use "WLOG" unless justified
- Do not smooth over nonlocal interactions in reductions or gadget arguments
- Prefer a weaker correct theorem over a stronger broken one
- If using literature facts, mark any unverified citation as [unchecked]
- If no external verification is possible, say so explicitly

### Template: Graph Reconfiguration Specialist

Domain-specific variant of the Structured Research Team, tailored for token sliding/jumping, reduction proofs, gadget verification, and reconfiguration complexity.

Informed by: Wang et al. (2024). "Rethinking the Bounds of LLM Reasoning: Are Multi-Agent Discussions the Key?" ACL 2024. (Sparse communication with independent roles outperforms long all-to-all debate.) Huang et al. (2024). "Large Language Models Cannot Self-Correct Reasoning Yet." ICLR 2024. (External verification is crucial; intrinsic self-correction is unreliable.)

**When to use:** Proving PSPACE/NP-hardness or polynomial solvability for a reconfiguration problem. Repairing a broken reduction. Verifying a gadget family. Checking a theorem in a draft. Analyzing a reconfiguration algorithm. Preparing a claim for formalization.

**Mode:** research
**Profile:** math-heavy
**Interaction:** star (sparse: independent work, then compressed cross-critique)
**Roles (4):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Constructor | R4 | Propose the strongest plausible proof strategy, reduction, algorithm, or repaired statement |
| 2 | Adversary | R4 | Search aggressively for counterexamples, hidden assumptions, broken iff directions, missing cases, unintended moves, nonlocal interference, boundary failures |
| 3 | Auditor | R4 | Perform structured auditing in two separate layers: (A) local/interface audit, (B) global/correspondence audit |
| 4 | Referee / Verifier | R4 | Maintain the claim ledger. Downgrade claims immediately when verification fails. Accept only what survives objections and explicit checks. |

**Claim ledger (initialized in Step 1, updated throughout):**

For every candidate claim, track:
- ID, statement
- Status: proved / refuted / unclear / heuristic
- Confidence: high / medium / low
- Dependencies (list of claim IDs this depends on)
- Verification type: formal / exhaustive / symbolic / computational / bibliographic / none
- Owner (which role is responsible)
- Notes

**Rounds (3, with conditional round 4):**

Round 1 — independent first pass. Constructor, Adversary, and Auditor work independently.

Constructor outputs:
- A. Exact claim version attempted
- B. Proof/reduction/algorithm outline
- C. Required intermediate lemmas
- D. Most fragile step
- E. Suggested external checks
- F. Whether the claim may need weakening

Adversary outputs:
- A. Strongest objection
- B. Exact failing step or suspicious step
- C. Candidate counterexample / obstruction
- D. Fatal or repairable?
- E. Smallest plausible fix

Auditor outputs TWO SEPARATE sections:

**A. Local/interface audit:**
- Gadget ports / attachment vertices are unambiguous
- Legal moves inside the gadget are exactly characterized
- Token count / occupancy / independence / orientation legality preserved
- No hidden assumptions on adjacency, reachability, or exclusivity
- Planarity / permutation / interval / graph-class constraints locally respected
- Completeness and soundness at the gadget level are both addressed

**B. Global/correspondence audit:**
- Gadgets do not create unintended cross-gadget moves
- Local simulations compose correctly
- Source states correspond to target states
- Legal reconfiguration sequences map in both directions if claimed
- No global invariant is silently violated
- No serialization / normalization claim is used without proof
- Target instance size is polynomial and construction is well-defined
- Both directions of any equivalence are justified separately

Round 2 — one critique round. Adversary and Auditor critique Constructor. Constructor may respond only to decisive issues. No long debate. If a decisive counterexample exists, stop defending and switch to diagnosis.

Round 3 — external verification (run by orchestrator via SageMath). Run these checks explicitly and separately:

**A. Computational** — brute force smallest instances, enumerate states/moves, search for unintended moves, test claimed invariants

**B. Structural** — graph-class membership, planarity/embedding constraints, orientation legality, degree/interface constraints, polynomial-size sanity check

**C. Bibliographic** — verify imported theorems actually state what is claimed, mark [unchecked] otherwise

**D. Formal** — if formalization is attempted, the checker is final authority

Round 4 — optional repair. Run ONLY if there is a concrete local repair. Otherwise choose one: weaken statement / isolate missing lemma / redesign gadget / redesign correspondence / abandon equivalence and keep one direction / replace global claim by conditional.

**Typed verifier table (in final output):**

| Check type | Target | Passed/Failed/Not run | Limitations |
|-----------|--------|----------------------|-------------|
| Computational | gadget X on n≤6 | passed | n≤6 only |
| Structural | planarity of construction | passed | — |
| Bibliographic | [Author, Thm 3.1] | [unchecked] | no access to full text |
| Formal | Lean skeleton | not run | — |

**Failure taxonomy for reductions (use in rejection reasons):**

- Local gadget unsoundness (admits an illegal configuration)
- Local gadget incompleteness (blocks a legal configuration)
- Cross-gadget interference (unintended moves between gadgets)
- Illegal move admitted (reduction allows a move that shouldn't exist)
- Legal move missing (reduction blocks a move that should exist)
- State correspondence broken (source/target states don't match)
- Equivalence overstated (only one direction holds)
- Graph-class preservation broken (construction leaves the target class)
- Planarity/orientation broken
- Polynomial-size claim broken
- Imported lemma/citation unsupported

**Hard rules (in addition to global hard rules):**

Every reduction proof must separate: (i) construction, (ii) local gadget behavior, (iii) completeness, (iv) soundness, (v) noninterference, (vi) size/class preservation.

Every algorithmic proof must separate: (i) move legality, (ii) progress measure / termination, (iii) completeness, (iv) soundness, (v) runtime.

Never merge prose polishing with proof repair. Stabilize correctness first.

**Final output:** Uses the mandatory research output format (Accepted/Rejected/Unresolved + proof skeleton + typed verifier table + single next action). Add "Draft-repair notes" section if the task concerns a manuscript.

---

### Template: Lean Formalization Team

For formalizing a single graph-reconfiguration lemma in Lean 4. Not for proof discovery — use one of the other templates first, then formalize what survives.

**When to use:** Formalizing a specific lemma that has already been proved on paper. Debugging a stuck Lean proof. Decomposing a large formal goal into manageable pieces.

**Mode:** research
**Profile:** math-heavy
**Interaction:** star
**Roles (5):**

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Informal Planner | R4 | Decompose the lemma into minimal subclaims. Map each subclaim to the proof strategy. |
| 2 | Formalizer | R4 | Write a conservative compiling Lean scaffold. Preserve compiling state at every step. |
| 3 | Missing-Lemma Miner | R3 | List the smallest auxiliary lemmas needed. Check Mathlib for existing versions. |
| 4 | Repair Agent | R3 | Classify each blocker: syntax / type / coercion / missing lemma / wrong induction / statement too strong / impossible goal |
| 5 | Checker | R4 | Final authority. Decide: complete / locally fixable / blocked on helper lemma / blocked on statement / blocked on missing idea |

**Rounds (2):**

Round 1:
- Informal Planner produces subclaim decomposition
- Formalizer writes initial scaffold with `sorry` placeholders
- Missing-Lemma Miner searches Mathlib and lists available/missing lemmas

Round 2:
- Repair Agent classifies each `sorry` and proposes fixes
- Checker evaluates: which sorries are closable, which need new helper lemmas, which indicate the paper proof has a gap

**Hard rules:**
- One lemma at a time
- Preserve compiling state whenever possible
- Distinguish mathematical gap from formalization gap (a proof can be correct but hard to formalize, or incorrect but easy to formalize)
- Do not claim completion unless the Checker accepts

**Final output:**
- Lean file with status of each sorry (closable / needs helper / blocked)
- List of missing Mathlib lemmas
- Assessment: is the paper proof correct as stated, or does formalization reveal a gap?

---

## Final output

Return a polished result with these sections:
- Topic
- Mode
- Roles used
- Models assigned
- Interaction pattern
- Rounds completed
- Main agreements
- Main disagreements
- Best points by role
- Final synthesis
- Recommended next step

Also include a compact run summary:
- run id
- profile used
- number of agents and their models
- thinking levels used
- rounds completed
- actual duration vs estimated duration
- whether recovery or pause/resume was needed
- whether any responses were truncated or models were swapped
