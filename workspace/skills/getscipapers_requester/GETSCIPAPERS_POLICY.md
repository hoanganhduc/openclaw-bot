# GetSciPapers Policy

Use `getscipapers_requester` as an external fallback, not as the default paper path. `AGENTS.md` is the source of truth for the mandatory routing order; this file only clarifies this skill's boundary.

## Required order

1. `zotero` first for paper retrieval.
2. `calibre` second for review tasks that need the document itself.
3. `paper-lookup` when the task is metadata discovery, DOI/PMID resolution, or open-access checking.
4. `getscipapers_requester` only when a real external retrieval or request step is still needed.

## Rules

- Do not go straight to publisher URLs, `curl`, or `wget`.
- When multiple title matches exist, show candidates and ask the user to choose.
- Use `make-manifest` for pasted multi-paper lists instead of ad hoc loops.
- Prefer `doctor` when troubleshooting setup or path issues.
- Preserve the canonical OpenClaw state tree under `{{ PRIVATE_DATA_DIR }}/research/getscipapers_bot/`.
