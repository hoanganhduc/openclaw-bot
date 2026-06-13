# OpenClaw Research Quick Actions

This is an operator reference for the active OpenClaw research stack.

- Route to the correct skill first.
- Treat `AGENTS.md` as the source of truth for mandatory paper/review routing; this file is only a command reference.
- Prefer the existing `/workspace/skills/...` entrypoints.
- Keep the library-first paper workflow intact.
- For nontrivial research, run the visible `Review -> Validate -> Fix` loop before treating an answer as complete.
- Do not copy Codex paths, runtime wrappers, model profiles, or reasoning defaults; use OpenClaw-native skills and current OpenClaw model-health policy.

## Universal research loop

Use this for research reports, literature reviews, paper reviews, proof audits, database lookups, digest-based summaries, and multi-agent research:

1. `Review`: state scope, relevant files/sources/tool outputs, and required evidence.
2. `Validate`: check each substantive claim against inspected evidence; preserve source IDs, paper IDs, DOI/arXiv IDs, or digest item paths when available.
3. `Fix`: remove or narrow unsupported claims, gather missing evidence if feasible, and write `incomplete analysis` when material scope remains unchecked.
4. Repeat until the output is either evidence-backed or explicitly scoped as provisional.

For substantial work, start with a compact `Research Brief` and end with a compact `Delivery Check`.

## Quick routing map

- paper in library -> `zotero`
- review-only -> `paper-review`
- annotate + review -> `annotated-review`
- parse local document structure -> `docling`
- metadata/discovery -> `paper-lookup`
- structured public records -> `database-lookup`
- external retrieval -> `getscipapers_requester`
- compute preflight -> `get-available-resources`
- phased citation-preserving research -> `deep-research-workflow`
- heavy remote compute -> `modal-research-compute`
- prior-session lookup -> `session-logs`
- tracked digest -> `research-digest-wrapper`
- RSS digest -> `rss-news-digest`
- multi-agent review or research -> `agent_group_discuss` or `prose`
- Vietnam Thu Quan / vnthuquan ebook work -> `vnthuquan`

## Paper and library command references

Use these commands after the routing order in `AGENTS.md` has selected the relevant skill.

### Zotero

Search and retrieve:

```bash
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>"
```

### Calibre

Search:

```bash
exec: /workspace/skills/calibre/run_cal.sh search "<query>"
```

Retrieve:

```bash
exec: /workspace/skills/calibre/run_cal.sh get "<query>"
```

### Vietnam Thu Quan

Use only when the user explicitly asks for Vietnam Thu Quan, vnthuquan, or
vietnamthuquan.eu. Keep generic book and paper requests on the library-first
route above.

Diagnose:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh diagnose --json
```

Search or inspect:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh search "Kim Dung" --json
```

Dry-run and execute an EPUB/PDF download:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --dry-run --json
```

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh download --title "TITLE" --format epub --execute --yes --json
```

Calibre handoff after validation and duplicate review:

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --dry-run --json
```

```bash
exec: /workspace/skills/vnthuquan/run_vnthuquan.sh add-to-calibre {{ PRIVATE_DATA_DIR }}/vnthuquan/downloads/book.epub --execute --yes --duplicates-reviewed --json
```

### Paper lookup

Use for DOI, PMID, arXiv, venue, or OA discovery before retrieval.

### GetSciPapers

Only after the `AGENTS.md` local-library path is exhausted:

```bash
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- getpapers --doi <DOI>
```

```bash
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh resolve auto "<title>" --best
```

## Document parsing

### Docling

Doctor:

```bash
exec: /workspace/skills/docling/run_docling.sh doctor
```

Convert:

```bash
exec: /workspace/skills/docling/run_docling.sh convert --source "/path/to/file.pdf" --to md
```

Extract structure:

```bash
exec: /workspace/skills/docling/run_docling.sh extract --source "/path/to/file.pdf"
```

## Deep research scaffold

Doctor:

```bash
exec: /workspace/skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

Initialize a scaffold:

```bash
exec: /workspace/skills/deep-research-workflow/run_deep_research_workflow.sh init --dir {{ PRIVATE_DATA_DIR }}/research
```

## Structured public databases

### Database lookup

Use `database-lookup` when the task is about data records or identifiers from public databases, not papers or broad synthesis.

Examples:
- compounds, targets, assays -> PubChem, ChEMBL, BindingDB
- genes, proteins, pathways, variants -> UniProt, Ensembl, NCBI Gene, Reactome, ClinVar, GTEx, Open Targets
- clinical studies -> ClinicalTrials.gov
- economic data -> FRED, Treasury Fiscal Data
- patents -> USPTO / PatentsView

## Remote compute

### Modal research compute

Doctor:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh doctor
```

Plan:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh plan /path/to/job.json
```

Submit:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh submit /path/to/job.json
```

Wait:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh wait <job_id>
```

Fetch:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh fetch <job_id> --dest /path/to/output
```

## Review routing

- `paper-review` for normal single-agent review
- `annotated-review` only when annotation is explicitly requested
- `agent_group_discuss` for panel or multi-agent review

## Resource preflight

```bash
exec: /workspace/skills/get-available-resources/run_get_available_resources.sh --output /workspace/.openclaw_resources.json
```

## Session history

Search memories first, then session stores:

```bash
rg -n "phrase" /workspace/MEMORY.md /workspace/memory {{ PRIVATE_DATA_DIR }}/sessions ~/.openclaw/agents/*/qmd/sessions ~/.openclaw/agents/*/sessions 2>/dev/null
```
