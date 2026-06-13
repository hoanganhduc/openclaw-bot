# ClinVar

- Base URLs: NCBI E-utilities plus `https://api.ncbi.nlm.nih.gov/variation/v0`
- Best for: clinical significance of variants, linked genes, conditions, and review status
- API key optional but recommended for E-utilities
- Common flow: `esearch` -> `esummary`; use Variation Services for HGVS or SPDI lookups
- Use when the task is variant or pathogenicity focused
