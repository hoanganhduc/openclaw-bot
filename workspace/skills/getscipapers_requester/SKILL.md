---
name: getscipapers_requester
description: Resolve DOI/ISBN details, prepare manifests from text or txt files, use getscipapers for external retrieval/search after library-first routing, and manage request watches safely.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["python3","getscipapers","openclaw"]}}}
---

# GetSciPapers Requester

Use this skill for external paper or book retrieval after the local-library path is exhausted.

Once `AGENTS.md` routing has selected this external retrieval path, actually use getscipapers rather than just describing it. If DOI/ISBN is missing, try local extraction and automatic lookup before asking.

## Routing boundary

- `AGENTS.md` is the source of truth for mandatory paper/review routing.
- `zotero` stays first for paper retrieval.
- `calibre` stays second for review tasks that need a local book or document.
- `paper-lookup` is the metadata/discovery layer when the identifier is unclear.
- `getscipapers_requester` is the actual external retrieval or request workflow.

Do not replace the existing library-first routing with this skill.

See `GETSCIPAPERS_POLICY.md` for the fallback and disambiguation rules.

## Quick reference

**Get a paper by DOI:**
```
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- getpapers --doi <DOI>
```

**Find a paper by title:**
```
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh resolve auto "<title>" --best
```

**Batch from pasted text:**
```
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh make-manifest auto "<text-or-file>"
```

**Doctor / setup check:**
```
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh doctor
```

**Latest downloads:**
```
exec: /workspace/skills/getscipapers_requester/run_gsp_helper.sh latest-downloads --limit 10
```

## Workflow

1. If DOI/ISBN provided, use it directly
2. Otherwise: `extract` → `resolve ... --best` → if ambiguous, show candidates and ask
3. For multiple papers: use `make-manifest` first
4. Before large batches: use `--dry-run` first
5. If retrieval fails: ask about posting a request, then `create-watch`

## Telegram file sending

If the file is below the size threshold, send it directly. If too large, report the local path and checksum.
