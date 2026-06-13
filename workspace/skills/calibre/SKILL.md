# Calibre Library Manager Skill

Manages a Calibre ebook library stored on Google Drive. Reads and writes
`metadata.db` directly (no `calibredb` binary required). Book files (EPUB,
PDF, MOBI) are stored in the Drive folder tree and downloaded on demand.

## Setup

1. Copy `config.json.example` to `config.json` and set `gdrive_folder_id`
   to the Google Drive folder ID containing your Calibre library
   (the folder that holds `metadata.db`).
2. Ensure `GDRIVE_CREDENTIALS` (service account JSON) is in the secrets file.
3. Run `cal sync` to pull `metadata.db` and build the local cache.

## Commands

### Search

```
run_cal.sh search "García Márquez"
run_cal.sh search "ring" --format epub
run_cal.sh search "" --tag fiction --limit 50
run_cal.sh search "dune" --series "Dune Chronicles"
```

Output: `{results: [...], count: N, source: "cache"|"live", cache_age_hours: N}`

Each result: `{id, title, authors, year, publisher, tags, series,
series_index, formats, identifiers, has_cover, path}`

### Add a book

```
# Add EPUB file (metadata auto-extracted from file):
run_cal.sh add /workspace/book.epub

# Add with ISBN lookup (fetches metadata from Open Library):
run_cal.sh add /workspace/book.epub --isbn 9780140449136

# Add with manual metadata:
run_cal.sh add /workspace/book.pdf --title "Dune" --author "Frank Herbert" \
  --year 1965 --publisher "Chilton Books" --tag "sci-fi,classic"

# Dry run (preview only, no changes):
run_cal.sh add /workspace/book.epub --dry-run
```

Add workflow:
1. Extract metadata from file (EPUB OPF / PDF header)
2. If --isbn given, enrich from Open Library API
3. CLI flags override all other metadata
4. Insert into local metadata.db
5. Upload book file to Drive: `Author/Title (Year)/filename.ext`
6. Push updated metadata.db back to Drive
7. Update local cache

### Retrieve and send a book

```
# Download book to staging (returns local_path):
run_cal.sh get "One Hundred Years"

# Download and send to Telegram:
run_cal.sh get "Dune" --send "telegram:CHAT_ID"

# Send to Zulip stream:
run_cal.sh get "Dune" --send "zulip:Research:books"

# Pick format:
run_cal.sh get "Dune" --format pdf

# Use book ID directly:
run_cal.sh get --id 42 --send "telegram:CHAT_ID"

# Select from multiple results:
run_cal.sh get "ring"        # returns list with indices
run_cal.sh get "ring" --index 0 --send "telegram:CHAT_ID"
```

`--send` format: `channel:target` where channel is `telegram`, `zulip`,
`googlechat`, or `whatsapp`. Uses `send_file.sh` from the zotero skill.

### Update metadata

```
run_cal.sh update --id 42 --title "New Title"
run_cal.sh update --id 42 --author "First Author; Second Author"
run_cal.sh update --id 42 --tags "fiction,classic,translated"
run_cal.sh update --id 42 --series "Dune Chronicles" --series-index 1
run_cal.sh update --id 42 --year 1965 --publisher "Chilton Books"
run_cal.sh update --id 42 --isbn 9780441013593
```

Notes:
- `--author` uses semicolons to separate multiple authors
- `--tags` replaces ALL existing tags (use add-tag/remove-tag for incremental)
- Changes are pushed to Drive immediately

### Tags

```
# Add a single tag (non-destructive):
run_cal.sh add-tag --id 42 --tag "to-read"

# Remove a tag:
run_cal.sh remove-tag --id 42 --tag "to-read"

# List all tags with book counts:
run_cal.sh list-shelves
run_cal.sh list-shelves --tags
run_cal.sh list-shelves --series
run_cal.sh list-shelves --publishers
```

### Sync

```
# Pull latest metadata.db from Drive (skips if already current):
run_cal.sh sync

# Force full re-download:
run_cal.sh sync --force
```

Run this at the start of a session if you've modified the library from
Calibre desktop or another device.

### Remove a book

```
# Search and confirm:
run_cal.sh remove "old title"
run_cal.sh remove "old title" --index 0

# By ID:
run_cal.sh remove --id 42

# Dry run:
run_cal.sh remove --id 42 --dry-run
```

Removes all DB records AND moves the book folder to Drive trash.

### Convert format

Requires `ebook-convert` (Calibre) to be installed on the host.

```
run_cal.sh convert --id 42 --to epub
run_cal.sh convert --id 42 --to pdf --from epub
```

Converted file is added to the library (new format entry) and uploaded to Drive.

### Export metadata

```
run_cal.sh export --id 42
run_cal.sh export --id 42 --format bibtex
```

### Health check

```
run_cal.sh doctor
```

Checks: Drive credentials, Drive folder access, local metadata.db integrity,
staging dir writability, cache status, ebook-convert availability, ebooklib.

### Clean staging

```
run_cal.sh clean
```

Removes files older than 24 hours from the staging directory.

---

## Credentials

Required in secrets file (`/workspace/.secrets.json`):
- `GDRIVE_CREDENTIALS`: service account JSON (same key as used by Zotero skill)

Optional override in secrets:
- `CALIBRE_GDRIVE_FOLDER_ID`: overrides the folder ID from config.json

---

## Notes

- `metadata.db` is always pulled to `{{ PRIVATE_DATA_DIR }}/calibre/cache/metadata.db`
  before any read/write operation. After writes, it is pushed back to Drive.
- A file lock (`metadata.db.lock`) prevents concurrent write conflicts.
- The local cache (`library.json`) is rebuilt on `sync` and updated incrementally
  after `add`, `update`, `add-tag`, `remove-tag`, and `remove`.
- If Drive is unavailable, search falls back to the last-known local cache.
- Book files are never stored permanently in `/workspace`; they are downloaded
  to staging on demand and cleaned up by `clean`.
