---
name: docling
description: Use when the user wants to parse, convert, chunk, or structurally analyze PDFs, DOCX, PPTX, HTML, images, audio transcripts, or similar documents with Docling. Prefer this skill for local document parsing before ad hoc text extraction.
user-invocable: true
disable-model-invocation: false
metadata: {"openclaw":{"requires":{"bins":["python3","bash"]}}}
---

# Docling

Use this skill for high-quality local document parsing and structured export.

## When to use

Use this skill when the user wants to:

- parse a PDF, DOCX, PPTX, HTML page, image, or similar document
- convert a document to Markdown, JSON, HTML, or plain text
- extract tables, headings, figures, formulas, or reading order
- chunk a document for RAG or downstream indexing
- inspect document structure before review or synthesis
- handle OCR-heavy or layout-heavy documents more robustly than plain-text extraction

For paper retrieval, keep the existing routing order:

- `zotero` first
- `calibre` second for review tasks needing the document
- online fallback only after those library checks

Docling is the parsing layer **after** you have the document.

## Runtime command

```bash
exec: /workspace/skills/docling/run_docling.sh <doctor|convert|extract|chunk> [args...]
```

## Supported subcommands

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

Chunk:

```bash
exec: /workspace/skills/docling/run_docling.sh chunk --source "/path/to/file.pdf" --mode hierarchical
```

## Recommended settings

Use these references when needed:

- `references/pipelines.md`
- `references/settings.md`
- `references/chunking.md`
- `references/remote-services.md`

## Safety notes

- Prefer local parsing by default.
- Use remote services only as an explicit opt-in.
- For review workflows, keep parsing in this skill and judgment in `paper-review` or `annotated-review`.
