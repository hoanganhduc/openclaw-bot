# NCBI Gene

- Base URL: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`
- Best for: gene IDs, gene metadata, and links from genes to other NCBI databases
- API key optional but recommended for higher rate limits
- Core pattern: `esearch` -> `esummary` -> optional `elink`
- Useful when a gene symbol must be resolved to official NCBI records
