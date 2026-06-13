# Research Review Verification Policy (STRICT — applies to ALL research review tasks)

Every research review task MUST follow a three-phase verification structure. No review output is final until all three phases complete.

Each phase must use the OpenClaw research evidence loop:

1. **Review** the source material, task scope, and prior phase output.
2. **Validate** each substantive claim against inspected evidence.
3. **Fix** unsupported, overbroad, or stale claims before moving to the next phase.
4. Repeat when fixes introduce new claims or change scope.

If material evidence remains unchecked, write `incomplete analysis` and list the unchecked scope. Do not mark the review as final.

Model metadata should record the OpenClaw-selected model and reasoning mode actually used. Do not hard-code Codex model profiles, Codex reasoning settings, or copied provider lists into this verification policy.

**What counts as a research review task:**
- Reviewing, critiquing, or annotating a paper (proof correctness, logic, notation, presentation)
- Literature review generation or evaluation
- Proof correctness audit or gap analysis
- Multi-agent panel review of any research claim
- Summarising or evaluating external papers for relevance or correctness
- Any task where an AI agent makes substantive factual claims about a paper's content

## Phase A — Primary Review

The reviewing agent(s) read the material and produce structured criticism. Requirements:
- Criticism must be brutally concrete: name exact lines/pages, state the logical chain of failure, cross-reference contradicting evidence
- Never use vague language ("unclear", "could be improved") — state what is wrong and why
- Record agent role, model, and thinking level used

## Phase B — Independent Verification

A **separate agent with clean context** (no shared history with Phase A) independently verifies every claim in the review.

**Strict rules:**
- The verification agent MUST be a fresh invocation — not a continuation of the reviewer's session
- The verifier reads the material first, independently, before seeing any review output
- The verifier is then given ONLY the structured annotation list — not the reviewer's reasoning session, not the meta block
- The verifier must not rubber-stamp: every confirmed annotation requires independent evidence; every disputed annotation must cite the exact location in the material that makes the criticism wrong
- Status values: `confirmed` | `disputed` | `partial`
- The verifier may also report additional issues missed by the reviewer
- Record verifier agent role, model, thinking level, and timestamp

## Phase C — Trust Verification

A **dedicated trust verification agent** checks that no external reference cited by any agent (reviewer or verifier) is hallucinated or invented.

**Scope:** Every external citation in Phase A and Phase B output — papers by name/author/year, theorems attributed to other works, datasets, algorithms described as existing results.

**Does NOT apply to:** within-document cross-references (e.g. "see Lemma 3.2 in this paper"), widely known definitions (e.g. "bipartite graph"), or basic mathematical facts.

**Verification process per reference:**
1. Search the user's Zotero library first (fastest — if found, verified)
2. Try DOI resolution via CrossRef
3. Try arXiv API lookup
4. Try Semantic Scholar API (free, no key required)
5. If none resolve: status = `unverified`; if partial match with conflicting details: status = `suspicious`

**Output:** A `trust_verification` block in the review JSON listing every checked reference with its status. `unverified` and `suspicious` references are flagged visually in every output format.

## Output requirements

Every review output (PDF, HTML, Zotero note) MUST include:
- Agent metadata for all phases: role, model, thinking level, timestamp
- Verification result for each annotation (confirmed/disputed/partial + comment)
- Trust verification summary: counts of verified/unverified/suspicious references
- Any additional issues found by the verifier, clearly labelled
- Any unverified or suspicious references, clearly flagged

These requirements apply to all skills and workflows that perform research review tasks, including but not limited to: `annotated-review`, literature review generation, proof audit workflows, and any multi-agent research panel.
