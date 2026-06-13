# LaTeX / PDF Writing Style Rule

**MANDATORY when generating any LaTeX or PDF document.** Read `{{ WRITING_STYLE_FILE }}` before any LaTeX writing task. Also follow `instruction.md §29` for full style rules. The unique/verbatim rules below are NOT in §29 — apply them exactly.

## Verbatim notation (copy precisely)
- Preliminaries opening: `"We use $V(G)$ and $E(G)$ to denote the sets of vertices and edges of a (simple, undirected) graph $G$, respectively."`
- Closed neighborhood: `"is **simply** the set $N_G(v) \cup \{v\}$"` — word "simply" is a stylistic fingerprint.
- Set shorthand: introduce `"$A - B$"` and `"$A + B$"` explicitly as shorthand for $A \setminus B$ and $A \cup B$.
- Parameters: `"Let $k \geq 2$ be a **fixed** positive integer"` — "fixed" is mandatory for complexity-theoretic parameters.
- Complexity classes: `$\mathsf{P}$`, `$\mathsf{NP}$`, `$\mathsf{PSPACE}$` using `\mathsf{}`.
- Definition close: `"Such a [object], if exists, is called a [name]."` — commas around "if exists" are required.

## Key language rules not in §29
- Hardness: `"[Problem] is [class]-complete **even for** [restricted class]."` — "even for" always present.
- Hardness proof order: membership first → announce reduction → `"We construct [object] as follows."` → correctness via (⇒) and (⇐).
- Parallel proof cases in `.tex`: use `itemize` with explicit item text such as
  `\item \textbf{Case 1: ...}`. Do not use optional `\item[...]` labels for
  case headings, and use one emphasis style consistently.
- Inline `\displaystyle`: use it only for tall or stacked operators such as
  `\frac`, `\binom`, `\sum`, `\prod`, `\bigcup`, and `\bigcap`; do not add it
  to ordinary inline formulas.
- Local notation: define the domain, codomain when applicable, rule, and local
  scope before repeated use.
- Prefer terminology from cited sources or standard frameworks over draft-only
  near-synonyms.
- Result summaries and table entries should be concise but self-contained; omit
  open cases unless the table tracks open problems.
- Never: "It is obvious that," "Clearly," "very"/"quite" before technical adjectives, bare "This" to start a sentence.
- Conclusions first sentence: `"In this paper, we have [shown/proved/established] that [main result]."`

## Characteristic phrases (use naturally)
- `"For an overview of this research area, we refer readers to the recent surveys [...]."` — always in introduction.
- `"In this paper, we initiate the study of [problem]..."` — for new problems.
- `"As far as we know, [X] has not been explored yet."` — novelty claims.
- `"[instance] is a yes-instance of [Problem]"` — instance labeling.
- `"It remains [open/unknown] whether..."` — open problems.
