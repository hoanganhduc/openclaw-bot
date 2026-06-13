---
name: database-lookup
description: Use when the user wants structured information from public scientific, biomedical, regulatory, materials, patent, or economic databases. This is a reference-first skill for selecting the right database and query strategy.
user-invocable: true
disable-model-invocation: false
---

# Database Lookup

Use this skill when the user wants structured data from public databases such as:

- compounds, drugs, assays, and targets
- genes, proteins, pathways, variants, and expression resources
- clinical trials and disease resources
- patents and regulatory datasets
- economic and fiscal data

## Intended role

This skill is primarily:

- a routing and reference skill
- a database-selection guide
- a source for query strategy and identifier mapping

It is **not** a replacement for:

- `zotero` for library lookup
- `paper-lookup` for literature discovery
- `deep-research-workflow` for broader synthesis

## High-value references

Start with:

- `references/pubchem.md`
- `references/chembl.md`
- `references/bindingdb.md`
- `references/uniprot.md`
- `references/reactome.md`
- `references/ensembl.md`
- `references/ncbi-gene.md`
- `references/gtex.md`
- `references/clinvar.md`
- `references/clinicaltrials.md`
- `references/opentargets.md`
- `references/fred.md`
- `references/treasury.md`
- `references/uspto.md`

## Routing guidance

- Use `database-lookup` when the user is asking for data records or identifiers.
- Use `paper-lookup` when the user is asking for papers.
- Use `deep-research-workflow` when the user wants broader explanation or synthesis.
