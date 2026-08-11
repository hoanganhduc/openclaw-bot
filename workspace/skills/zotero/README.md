# zot — Headless Zotero CLI

Manage your Zotero library from the command line. Add papers by DOI/arXiv/ISBN/URL, retrieve PDFs via WebDAV, share via Google Drive, organize collections, and export BibTeX.

## Quick Start

```bash
# Search your library
zot search "token sliding"
zot search "Demaine" --bibtex

# Add a paper
zot add 10.4230/LIPIcs.FSTTCS.2025.31 --collection "Reconfiguration"
zot add arXiv:2301.12345 --no-pdf --collection "Graph Theory"

# Preview without creating
zot --dry-run add 10.1093/jcr/ucw010

# Retrieve a paper (WebDAV → local PDF)
zot get "vertex cover P3"
zot get "token sliding" --index 2

# Share via Google Drive link
zot get --link "vertex cover"

# Update existing items
zot update ABC12345 --attach-pdf
zot update ABC12345 --add-collection "Graph Theory" --remove-collection "Auto-cataloged"

# Collections
zot list-collections --tree
zot create-collection "Token Sliding" --parent "Graph Theory"

# Batch operations
zot add --file dois.txt --collection "Batch Import"
zot add --from-manifest manifest.json

# Maintenance
zot doctor
zot sync-cache
zot clean-staging
```

## Architecture

```
DOI/arXiv/ISBN/URL
  → Translation Server (metadata)
  → Duplicate check (DOI-only)
  → PDF download chain (getscipapers → Semantic Scholar → arXiv)
  → PDF verification (magic bytes, page count, aspect ratio, title match)
  → ZotFile rename ({Author}_{Year}_{Title} [Type].pdf)
  → Create attachment item (Zotero API)
  → Zip + upload to WebDAV
  → Zotero desktop syncs on next refresh
```

## Components

| Component | Purpose |
|-----------|---------|
| `run_zot.sh` | Direct OpenClaw entrypoint and restored-credential boundary |
| `zot.py` | CLI implementation (invoke through `run_zot.sh`) |
| `lib/config.py` | Config loader (SecretRef-aware) |
| `lib/metadata.py` | Translation Server client (auto-detect DOI/arXiv/ISBN/URL) |
| `lib/zotero_client.py` | pyzotero wrapper (exponential backoff on 429/5xx) |
| `lib/downloader.py` | PDF download chain (branched by input type) |
| `lib/verifier.py` | PDF validation (reject stubs, slides, wrong papers) |
| `lib/renamer.py` | ZotFile pattern engine |
| `lib/webdav.py` | WebDAV upload/download (Zotero zip format) |
| `lib/gdrive.py` | Google Drive scoped search + share links |
| `lib/cache.py` | Local metadata cache (offline search fallback) |
| `lib/doctor.py` | Health checks for all components |

## Configuration

**Zotero skill JSON authority** is selected only by
`AAS_ZOTERO_SKILL_SECRETS_FILE` and defaults to the workspace-private
`.config/ai-agents-skills/zotero-secrets.json`. Shared and legacy selectors are
not accepted:
- `ZOTERO_API_KEY` — from https://www.zotero.org/settings/keys
- `WEBDAV_PASSWORD` — WebDAV apps password
- `GDRIVE_CREDENTIALS` — Google service account JSON string
- `SEMANTIC_SCHOLAR_API_KEY` — optional Semantic Scholar Graph API key. Never
  put this key in `skills/zotero/config.json`.
- `TELEGRAM_BOT_TOKEN` is not projected through the Zotero skill authority. It
  is read only by the host delivery consumer from
  `OPENCLAW_HOME/secrets.json`.

`send_file.sh` is only an untrusted queue producer. It receives no token,
Remote Bridge authority, or provider credential. The install-attested host
consumer validates the queue request, requires an exact opt-in target, and
accepts files only from Zotero staging, Calibre staging, or
`OPENCLAW_WORKSPACE/data/exports`. It snapshots the approved no-follow
descriptor into an owner-private host spool. Telegram uses fixed
`/usr/bin/curl`; other channels use the attested OpenClaw CLI and its normal
host-side channel configuration.

The separate portable policy authority is
`OPENCLAW_HOME/file-delivery-policy.json`; it contains the same `schema` and
`delivery_policy` but no token. Start from
`config/file-delivery-policy.json.template`, whose lists are intentionally
empty. The umbrella materializer owns the migration and merge contract; the
workspace state is never the sole backed authority. Missing policy is
`NOT_CONFIGURED`, and bot or channel credentials never authorize a target.

**Config** (`skills/zotero/config.json`):
- `zotero_user_id` — numeric user ID
- `webdav_url`, `webdav_user` — WebDAV endpoint
- `gdrive_folder_id` — Google Drive folder for Zotero PDFs
- `zotfile_pattern` — PDF rename pattern (default: `{%a_}{%y_}{%t} {[%T]}`)
- `translation_server` — Translation Server URL (default: `http://localhost:1969`)

## Testing

```bash
# Unit + mocked tests (no credentials needed)
python3 -m pytest tests/ -v

# Live integration tests (requires credentials)
python3 -m pytest tests/ --live -v
```

## Cron Jobs

Run `scripts/setup-cron.sh` to install:
- **Watch poller** — every 4 hours, auto-attaches PDFs when watches find them
- **Cache sync** — daily at 3am, pulls full library to local cache

## Automation

```bash
# Auto-catalog papers from research/RSS digests
python3 scripts/auto-catalog.py --source all --min-score 80

# Poll watches and attach found PDFs
python3 scripts/watch-poller.py
```
