# Research Templates

Use `EXECUTION.md` for the actual round topology, role-prompt structure, timeouts, and orchestration rules.

The user can request a template by name, or the orchestrator can auto-select based on task signals. If auto-selecting, briefly state which template was chosen and why before proceeding. The user can override.

## Template auto-selection

| Task signal | Recommended template |
|-------------|---------------------|
| "verify my proof", "check this theorem", "stress-test", "find holes" | **Lakatos Proof & Refutation** |
| "attack this problem", "explore complexity", "is this hard or easy", "open problem" | **Pólya Multi-Strategy** |
| "review my draft", "pre-submission review", "check exposition", "camera-ready" | **Knuth Manuscript Review** |
| General math or TCS claim, algorithm analysis, combinatorial argument | **Structured Research Team** |
| Token sliding or jumping, reduction proof, gadget verification, reconfiguration, PSPACE reduction | **Graph Reconfiguration Specialist** |
| "formalize this lemma", "Lean proof", "fix this sorry", "formalization" | **Lean Formalization Team** |

## Mandatory plan

Before any template begins, the orchestrator must show:

- model assigned to each role
- reasoning tier and thinking level
- estimated time per role and total time
- execution order by round

## Mandatory preamble

Before any high-stakes research template begins, restate:

1. the exact target claim
2. the explicit assumptions
3. what is given, to be proved, and only conjectured
4. the notation and definitions in use

## Template summaries

### Lakatos Proof & Refutation

- Mode: `review`
- Best for: theorem stress-tests and proof repair
- Roles: prover, counterexample hunter, refiner, formalist

### Pólya Multi-Strategy

- Mode: `research`
- Best for: open problems and complexity-boundary exploration
- Roles: specializer, generalizer, reducer

### Knuth Manuscript Review

- Mode: `review`
- Best for: pre-submission review and camera-ready critique
- Roles: correctness reviewer, exposition reviewer, literature reviewer

### Structured Research Team

- Mode: `research`
- Best for: high-stakes verification of a claim, proof, reduction, or characterization
- Roles: builder, breaker, alternative builder, referee

### Graph Reconfiguration Specialist

- Mode: `research`
- Best for: token sliding, token jumping, gadget verification, and reduction proofs
- Roles: constructor, adversary, auditor, referee

### Lean Formalization Team

- Mode: `research`
- Best for: turning a surviving argument into a formal skeleton
- Roles: informal planner, formalizer, missing-lemma miner, repair agent, checker
