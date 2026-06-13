---
name: paper-review
description: Use for review-only requests for papers or books when the user did not explicitly ask for annotation. Handles the normal single-agent review flow.
user-invocable: true
disable-model-invocation: false
---

# Paper Review

Use this skill for the normal single-agent review flow.

## Trigger rule

Use this skill when the user asks for a review-only pass such as:

- review this paper
- critique this paper
- hard review
- find issues in this paper
- review and add to Zotero

Do **not** use this skill when the user explicitly asks for both annotation and review.
Use `annotated-review` instead.

If the user explicitly asks for multiple agents, a panel, or a multi-agent review,
use `agent_group_discuss` instead.

## Document lookup order for review tasks

Follow `AGENTS.md` as the source of truth. In short:

If the user did not already provide a source path, attached file, PDF, or source tree:

1. check `zotero`
2. if not found there, check `calibre`
3. only if neither local library has the document, use an online path such as `getscipapers_requester`

For review tasks, do not go online before checking both local libraries.

## Document parsing preference

When you have the document as a local PDF, office file, HTML export, or image-backed scan, prefer `docling` for structure-aware parsing before relying on ad hoc plain-text extraction.

Use Docling especially when the review depends on:

- section hierarchy
- table extraction
- figure or picture detection
- reading order in complex layouts
- OCR on scanned pages

## Review expectations

- Keep the review single-agent by default.
- Focus on correctness, argument quality, clarity, missing assumptions, and important edge cases.
- Use `references/common_issues.md` and `references/reporting_standards.md` as internal checklists when useful.
- Summarize the main issues clearly, with evidence from the provided or retrieved document.
- Zotero writes are off by default unless the user explicitly asks for them.

## Recommended output format

### Summary

- paper title, authors, venue/year when available
- overall assessment

### Issues

For each issue:

- **Severity**: critical / major / minor / suggestion
- **Type**: logic / math / consistency / notation / presentation / missing / unsupported
- **Location**: page, section, line, or paragraph reference
- **Quote**: short supporting quote when helpful
- **Description**: what fails and why

### Strengths

- key contributions
- what works well

### Recommended actions

- prioritized fixes, highest severity first

## Routing boundary

- review-only -> this skill
- annotate + review -> `annotated-review`
- multi-agent review -> `agent_group_discuss`
