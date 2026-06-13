---
name: deep-research-workflow
description: Use when a research task benefits from an explicit phased workflow with structured source handoff across search, analysis, and writing, and when preserving citations across phases matters.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["bash"]}}}
---

# Deep Research Workflow

This skill provides a phased research workflow:

1. search
2. analyze
3. write

Use it when the user wants a deeper research pass than a normal quick synthesis and when source preservation matters.

## Required research loop

For every nontrivial research task, repeat this loop until the output is defensible:

1. **Review** — inspect the current scope, sources, claim mapping, and unresolved gaps.
2. **Validate** — check that claims still have source support, paper-like sources have a verification status, and observations are separated from inference.
3. **Fix** — repair missing evidence, narrow overbroad claims, add `incomplete analysis` when material scope remains unchecked, then repeat the loop.

Start with a short `Research Brief` for scope/evidence planning and end with a short `Delivery Check` before presenting the report as complete.

## Runtime helper

Initialize a scaffold with:

```bash
exec: /workspace/skills/deep-research-workflow/run_deep_research_workflow.sh init --dir {{ PRIVATE_DATA_DIR }}/research
```

Verify the helper setup with:

```bash
exec: /workspace/skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

## When to use

- deep topic research
- report-style synthesis
- research with explicit citation preservation
- tasks where search, interpretation, and final writing should be kept separate

## Routing boundary

Prefer the normal browse-and-synthesize path for lightweight current-information lookups.

Prefer this skill when:

- the user wants an explicit phased workflow
- you need a structured handoff between search, analysis, and writing
- preserving source linkage across phases is part of the task quality bar

## Workflow

### Phase 1 — Search

- review the user request and write a compact `Research Brief` covering goal, scope, constraints, evidence plan, workflow, and risks
- gather relevant sources
- prefer primary sources when practical
- record source metadata with stable `S1`, `S2`, ... identifiers
- separate observed facts from tentative interpretations
- validate that each source record has enough metadata to be cited later
- fix the source ledger before moving to analysis

Use `templates/deep-research-sources.md` when helpful.

### Zotero cross-check

Between Phase 1 and Phase 2, treat every paper-like source as a library-check task:

- search the local library with `zotero`
- assign exactly one verification status
- preserve that status in the source ledger

Allowed status values:

- `[IN_LIBRARY]`
- `[NOT_IN_LIBRARY]`
- `[NOT_A_PAPER]`
- `[UNVERIFIED]`

### Phase 2 — Analyze

- review the source ledger and exclude only with an explicit reason
- group findings into themes
- identify conflicts, uncertainties, and gaps
- preserve source mapping for each important claim
- keep `S*` ids stable across all phases
- validate every important claim against one or more source ids
- fix unsupported claims by adding evidence, weakening the claim, or marking it unresolved

Detailed handoff structure:

- `references/source-handoff.md`
- `templates/deep-research-analysis.md`

### Phase 3 — Write

- review the analysis matrix before drafting
- produce a structured output
- include only citations that survive from earlier phases
- distinguish observation, inference, and recommendation
- say `incomplete analysis` if material scope remains unchecked
- validate the draft with `Review Findings` before delivery
- fix any unsupported claim, scope drift, missing date, or overconfident wording
- finish with a visible `Delivery Check` that states readiness and remaining gaps

Output structure guidance:

- `references/output-structure.md`
- `templates/deep-research-report.md`

## Skill handoffs

- Use `docling` before or between Phases 1 and 2 when local PDFs, HTML exports, or office documents need structure-aware parsing.
- Use `paper-lookup` during Phase 1 when external literature metadata or discovery is needed after the local library-first workflow.
- Use digest skills to seed Phase 1 when the task starts from tracked topics, alerts, or feeds.
- Use `agent_group_discuss` when a validation/fix cycle needs independent reviewers or adversarial critique.

## Escalation rules

- Stay in this skill for single-agent phased deep research.
- Escalate to `prose` when the user explicitly wants structured multi-agent research-and-synthesis orchestration.
- Escalate to `agent_group_discuss` when the user wants panel-style discussion, debate, or multi-agent research perspectives.
