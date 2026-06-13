# arXiv

- Base URL: `https://export.arxiv.org/api/query`
- Format: Atom XML, not JSON
- Best for: direct arXiv ID lookup, title search, category/date browsing
- Key params: `search_query`, `id_list`, `start`, `max_results`, `sortBy`, `sortOrder`
- Important limit: keep to roughly 1 request every 3 seconds
