# annotated-review

Produces three synchronized, fully verified outputs from a paper (LaTeX source or PDF):
1. **Annotated LaTeX PDF** — metadata header + `\listoftodos` overview + inline reviewer/verifier todos at exact line positions
2. **Annotated PDF** — prepended metadata page + PyMuPDF highlights/sticky notes + companion HTML
3. **Zotero child note** — structured HTML with full reviewer + verifier content (ONLY when user explicitly requests it)

---

## Trigger phrases

Use this skill only when the request explicitly includes both:
- an annotation signal such as "annotate", "annotation", or "annotated"
- a review signal such as "review"

Examples that should trigger this skill:
- "Annotate and review this paper"
- "Give me an annotated review"
- "Annotate this paper, then review it"
- "Annotate this paper and add the review to Zotero"

Examples that should **not** trigger this skill by themselves:
- "Review this paper"
- "Critique this paper"
- "Do a hard review of this paper"
- "Find issues in this paper"
- "Review and add to Zotero"

Routing rule for review-only requests:
- single-agent review request -> use `paper-review`, not this skill
- multi-agent review request -> use the multi-agent review scheme, not this skill

---

## STRICT RULE: Zotero is OFF by default

**NEVER pass `--zotero-key` or `--zotero-doi` unless the user has explicitly said to add to Zotero.**

- "Review this paper" → do NOT use this skill. Use the normal review flow instead.
- For document lookup on review tasks: check Zotero first, then Calibre, and only then use an online path.
- "Review and add to Zotero" → still do NOT use this skill unless annotation is also requested.
- After finishing a review without Zotero, you MAY mention: "Say 'add to Zotero' to store the review note in your library." — do not act until the user replies.
- This rule overrides all other defaults.

---

## Review-only routing

- Review-only requests should not trigger `annotated-review`.
- If the user wants one review voice, use `paper-review`.
- If the user explicitly wants multiple agents, a panel, or multi-agent review, use the multi-agent review skill/workflow.
- When a local PDF or document export is available and structure matters, prefer `docling` before ad hoc extraction.

## Workflow overview

```
User request
    |
    v
[Pre-compile]  (LaTeX path only)
  Run: run_review.sh --precompile --source <dir>
  → prints path to lined_preview.pdf
  → reviewer reads this PDF to get lineno line numbers
    |
    v
[Phase A: Review]
  Bot reads lined_preview.pdf (or original PDF)
  Generates meta + annotations
  Writes to /tmp/review_<timestamp>.json
    |
    v
[Phase B: Independent Verification]
  NEW agent, clean context (no shared history with reviewer)
  Reads same paper fresh — no prior context
  Receives only the annotations array (not meta, not reasoning)
  Verifies each annotation, finds additional issues
  Produces verification block
  Bot merges verification into review JSON
    |
    v
[Phase C: Trust Verification]
  SEPARATE agent, clean context
  Receives annotation list + verification results only
  Extracts all external citations
  Checks: Zotero → CrossRef → arXiv → Semantic Scholar
  Produces trust_verification block
  Bot merges trust_verification into review JSON
  File is now complete.
    |
    v
[Phase D: Script execution]
  run_review.sh --review-file /tmp/review_<ts>.json \
                --source <dir> | --pdf <file> \
                [--zotero-key <key>] [--send telegram <id>]
    |
    v
Bot reads output JSON, sends annotated file, reports summary.
```

---

## Pre-compile step (LaTeX papers only)

Before Phase A, generate a lined preview so the reviewer can cite lineno line numbers.

```
exec: /workspace/skills/annotated-review/run_review.sh --precompile --source <path_to_source_dir>
```

Read the output JSON. If `status=ok`, use the `pdf` field — this is the `lined_preview.pdf` path.
Tell the reviewing agent: "Read this PDF to get line numbers. All `pdf_line_start`/`pdf_line_end` values MUST be the lineno numbers visible in this PDF."

If pre-compile fails (`status=error`): fall back to source-file review. Each annotation should include `"pdf_line_note": "source lines — pre-compile failed"` in its body. Note `pre_compile_error` in the final output.

---

## Phase A — Reviewer instructions

Generate the `meta` block:
- `reviewed_at`: current UTC time with local timezone offset (ISO 8601, e.g. `2026-03-22T15:42:00+07:00`)
- `focus`: from user request, or `"all"` if not specified
- `agents`: list every reviewing agent with exact model ID and thinking level used

Generate each annotation:
- `pdf_line_start`/`pdf_line_end`: **MUST be lineno PDF line numbers** — never source file line numbers
- `quote`: copy the first 30–50 characters of the problematic text **verbatim** — this is the insertion anchor
- `file`: relative path within the source tree (ok to omit if uncertain)
- `severity`: `critical` (logic error, broken proof, false claim) / `major` (significant gap) / `minor` (notation inconsistency) / `suggestion` (clarity, presentation)
- `type`: `logic` / `math` / `consistency` / `notation` / `presentation` / `missing` / `unsupported`
- `body`: state the logical chain of why it is wrong — exactly what fails and why — cite PDF line numbers — never use vague language

**Example body:**
```
The proof of Lemma 3.2 (lines 387–392) claims every token configuration
reachable from s is also reachable from t, using an exchange argument on
line 389. This argument requires G to be bipartite — it swaps tokens across
a 2-coloring. That condition is never stated in the lemma hypothesis (line 383)
nor established anywhere in the paper. Example 2.1 (page 3, lines 156–158)
gives a triangle K₃ satisfying all stated hypotheses but G is not bipartite,
making the exchange step inapplicable. The lemma is unproven for non-bipartite G.
```

Write completed review JSON to `/tmp/review_<timestamp>.json` with only `meta` and `annotations` keys (no verification yet).

---

## Phase B — Verifier instructions

**The verifier agent MUST have strictly clean context. Launch a fresh agent invocation with no shared history from the reviewer.**

1. Give the new agent the paper (same files or PDF). Nothing else.
2. New agent reads the paper completely and independently.
3. Provide only the `annotations` array to the new agent (not `meta`, not reviewer reasoning, not session history).
4. For each annotation, the verifier checks:
   - Is the line reference accurate? (Does the quoted text appear at that PDF location?)
   - Is the criticism logically correct?
   - Is the severity appropriate?
   - Is there a part of the paper that addresses or refutes the concern?
5. Verifier also notes any significant issues the reviewer missed.
6. Verifier produces the `verification` block:
   - `agent`: verifier agent info (`role`, `model`, `thinking`)
   - `verified_at`: ISO 8601 timestamp
   - `results`: one entry per annotation (`annotation_index`, `status`, `comment`)
   - `additional_issues`: new issues using the same annotation schema

**Status values:** `confirmed` / `disputed` / `partial`
- If disputing: cite the exact PDF line/page that makes the criticism wrong
- Do NOT rubber-stamp. Confirming every annotation without independent reasoning defeats the purpose.
- For additional issues: same concreteness standard (PDF line numbers, verbatim quotes)

Bot merges the `verification` block into the review JSON.

---

## Phase C — Trust verification instructions

**Separate fresh agent, clean context. No shared history with reviewer or verifier.**

1. Agent receives only: the annotation list + verification results (no session history)
2. Extract all external citations: papers cited by name/author/year, attributed theorems, named datasets
3. For each citation, attempt in order:
   - Zotero library (`ZoteroClient.search()` / `search_by_doi()`)
   - CrossRef API (`api.crossref.org/works?query=...`)
   - arXiv API
   - Semantic Scholar (`api.semanticscholar.org/graph/v1/paper/search?query=...`)
4. First source returning matching title + at least one matching author = `verified`. Stop there.
5. Classify each: `verified` / `unverified` / `suspicious`
6. Produce `trust_verification` block with `agent`, `verified_at`, `references_checked`, `summary`

**NOT checked:** within-document cross-references, standard math definitions, the paper's own bibliography.

If all external sources fail for a citation: mark `unverified` with note "(all sources unavailable — verify manually)".

Bot merges `trust_verification` into the review JSON. File is now complete.

---

## Phase D — Script execution

Call the script with the complete review JSON:

```bash
# Standard review — LaTeX source, no Zotero (default)
exec: /workspace/skills/annotated-review/run_review.sh \
  --review-file /tmp/review_<timestamp>.json \
  --source {{ PRIVATE_DATA_DIR }}/projects/MyPaper/ \
  --send telegram <SENDER_ID>

# PDF path, no Zotero (default)
exec: /workspace/skills/annotated-review/run_review.sh \
  --review-file /tmp/review_<timestamp>.json \
  --pdf {{ PRIVATE_DATA_DIR }}/projects/paper.pdf \
  --send telegram <SENDER_ID>

# With Zotero note — ONLY when user explicitly requested it
exec: /workspace/skills/annotated-review/run_review.sh \
  --review-file /tmp/review_<timestamp>.json \
  --source {{ PRIVATE_DATA_DIR }}/projects/MyPaper/ \
  --zotero-key ABCD1234 \
  --send telegram <SENDER_ID>

# With merged PDF + Zotero attachment storage
exec: /workspace/skills/annotated-review/run_review.sh \
  --review-file /tmp/review_<timestamp>.json \
  --source {{ PRIVATE_DATA_DIR }}/projects/MyPaper/ \
  --merged-pdf \
  --store-annotated \
  --zotero-key ABCD1234 \
  --send telegram <SENDER_ID>
```

Read the output JSON from stdout:
- `outputs.latex_pdf` — annotated PDF path (null if compile failed or PDF path)
- `outputs.pdf_markup` — PyMuPDF annotated PDF (null if LaTeX path)
- `outputs.companion_html` — always present
- `compile_error` — first error block from compile.log (null on success)
- `warnings` — list of non-fatal issues
- `annotation_count` — `{critical, major, minor, suggestion}`
- `verification_count` — `{confirmed, disputed, partial, additions}`
- `trust_count` — `{total, verified, unverified, suspicious}`

Report the severity summary + verification summary + trust summary. Send the best available output file (`latex_pdf > pdf_markup > companion_html`).

---

## Output guarantee

Companion HTML and PDF markup are **always produced** regardless of LaTeX compile status.
The annotated LaTeX PDF is a bonus — its failure degrades gracefully.

---

## Quality requirements

### Reviewer
- All line numbers are lineno PDF line numbers — never source file line numbers
- `quote` is verbatim text from the paper (30–50 chars)
- `body` explains exactly what fails and why — logical chain, cross-references, counterexamples
- Never use vague language ("unclear", "seems wrong") without a specific reason

### Verifier
- Read the paper completely before examining any annotation
- All line numbers consistent with reviewer (PDF/lineno)
- State confirmed / disputed / partial with concrete reason
- If disputing: cite exact PDF line/page that makes the criticism incorrect
- Do not rubber-stamp — confirming everything without reasoning defeats the purpose
- Additional issues: same concreteness standard as reviewer

### Trust verifier
- Attempt all four sources before classifying as unverified
- Distinguish "suspicious" (found but details conflict) from "unverified" (not found anywhere)
- Do not block output if APIs are unavailable — mark accordingly

---

## Focus filters

`--focus` is a **bot-side instruction** to the reviewer, not a script flag.

| Focus | Reviewer instruction |
|-------|---------------------|
| `proofs` | Focus on proof gaps, missing cases, unjustified steps |
| `consistency` | Focus on cross-referencing claims, definitions, examples |
| `notation` | Focus on undefined symbols, notation drift, ambiguous overloading |
| `presentation` | Focus on clarity, structure, missing references |
| `all` (default) | No filter |

The verifier always operates at `all` scope regardless of reviewer focus.

---

## NL command table

| User says | Agent does |
|-----------|-----------|
| "Review the TSTJPG paper source" | LaTeX path only — NO Zotero |
| "Hard review of the chordal paper, proofs only" | Annotations focused on proofs — NO Zotero |
| "Review paper 10.1234/abc" | `zot get` to fetch PDF → review — NO Zotero |
| "Review and add to Zotero" | LaTeX/PDF path + `--zotero-key` |
| "Review paper X and store the note in my library" | LaTeX/PDF path + `--zotero-key` |
| "Add the review note to Zotero" (after review done) | Run Zotero steps using prior review JSON |
| "Review and store the annotated copy in my library" | `--zotero-key` + `--store-annotated` |
| "Show my review notes for the Smith paper" | `zot notes "Smith" --tag annotated-review` |
| "Re-review the Jones paper and add to Zotero" | Create new note after existing ones — never delete prior reviews |
| "Give me a merged PDF of the review" | `--merged-pdf` |

---

## Zotero: prior notes rule

NEVER delete prior `annotated-review` notes. Each review is a separate child note — append-only history.
If existing `annotated-review` notes are found, create the new note immediately after them without asking.

---

## Review JSON schema (reference)

```json
{
  "meta": {
    "reviewed_at": "2026-03-22T15:42:00+07:00",
    "focus": "all",
    "agents": [
      { "role": "Main Reviewer", "model": "claude-opus-4-6", "thinking": "high" }
    ]
  },
  "annotations": [
    {
      "file":           "sections/proof.tex",
      "pdf_line_start": 387,
      "pdf_line_end":   392,
      "page":           3,
      "quote":          "using the exchange argument, we conclude",
      "severity":       "critical",
      "type":           "logic",
      "title":          "Exchange argument assumes bipartiteness — never established",
      "body":           "..."
    }
  ],
  "verification": {
    "agent": { "role": "Independent Verifier", "model": "claude-opus-4-6", "thinking": "high" },
    "verified_at": "2026-03-22T15:58:00+07:00",
    "results": [
      { "annotation_index": 0, "status": "confirmed", "comment": "..." }
    ],
    "additional_issues": []
  },
  "trust_verification": {
    "agent": { "role": "Trust Verifier", "model": "claude-opus-4-6", "thinking": "high" },
    "verified_at": "2026-03-22T16:10:00+07:00",
    "references_checked": [
      {
        "cited_in": "annotation_0",
        "citation": "Bonamy and Bousquet 2018",
        "type": "external_paper",
        "lookup_attempts": ["zotero", "crossref", "arxiv", "semantic_scholar"],
        "status": "verified",
        "source": "zotero",
        "note": "Found in library (key: XY12AB34)"
      }
    ],
    "summary": { "total": 1, "verified": 1, "unverified": 0, "suspicious": 0 }
  }
}
```

---

## Related commands

```bash
# Check existing review notes for a paper
exec: /workspace/skills/zotero/run_zot.sh notes "paper title" --tag annotated-review

# Pre-compile to get lined preview PDF
exec: /workspace/skills/annotated-review/run_review.sh --precompile --source <path>

# Full review (Phase D call)
exec: /workspace/skills/annotated-review/run_review.sh \
  --review-file /tmp/review_<ts>.json \
  --source <path> \
  --send telegram <SENDER_ID>
```
