#!/usr/bin/env python3
"""Library knowledge ingestion — creates searchable memory entries from Zotero/Calibre metadata.

Modes:
  --source zotero --data '<json>'    On-demand from zot.py (item data already loaded)
  --source calibre --data '<json>'   On-demand from cal.py (book data already loaded)
  --batch [--limit N]                Trickle: process N priority-ordered Calibre items
  --bootstrap                        Reference-first: process items cited in .tex/.bib files
  --check --source <s> --id <id>     Check if already ingested (returns JSON)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "{{ OPENCLAW_WORKSPACE }}")
MEMORY_DIR = Path(WORKSPACE) / "memory"
INGESTED_FILE = Path(WORKSPACE) / "data" / "library" / "ingested.json"
INDEX_FILE = MEMORY_DIR / "library-index.md"
CALIBRE_CACHE = Path(WORKSPACE) / "data" / "calibre" / "cache" / "library.json"
ZOTERO_SKILL = Path(WORKSPACE) / "skills" / "zotero" / "zot.py"

RESEARCH_TAGS = {
    "graph", "algorithm", "combinatorics", "complexity", "theory",
    "reconfiguration", "math", "logic", "proof", "discrete", "topology",
    "optimization", "computation", "formal", "probability",
}


# --------------------------------------------------------------------------- #
# Tracking
# --------------------------------------------------------------------------- #

def load_ingested():
    if INGESTED_FILE.exists():
        with open(INGESTED_FILE) as f:
            return json.load(f)
    return []


def save_ingested(records):
    INGESTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INGESTED_FILE, "w") as f:
        json.dump(records, f, indent=2)


def is_ingested(source, item_id):
    return any(
        r["source"] == source and str(r["id"]) == str(item_id)
        for r in load_ingested()
    )


def mark_ingested(source, item_id):
    records = load_ingested()
    records.append({
        "source": source,
        "id": str(item_id),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })
    save_ingested(records)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def slugify(s):
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[\s_-]+", "_", s)[:60].strip("_")


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def truncate(s, n=600):
    return s[:n] + ("..." if len(s) > n else "")


def extract_citekey(data):
    """Extract citekey from Zotero extra field, or construct LastNameYear."""
    for line in (data.get("extra") or "").splitlines():
        if line.lower().startswith("citation key:"):
            return line.split(":", 1)[1].strip()
    creators = data.get("creators", [])
    authors = [c for c in creators if c.get("creatorType") == "author"]
    last = (authors[0].get("lastName") or "unknown") if authors else "unknown"
    year = (data.get("date") or "")[:4] or "0000"
    return f"{last}{year}"


def format_authors_zotero(creators):
    names = []
    for c in creators:
        if c.get("creatorType") != "author":
            continue
        if c.get("lastName"):
            names.append(f"{c['lastName']}, {(c.get('firstName') or '')[:1]}.")
        elif c.get("name"):
            names.append(c["name"])
    return names


def _update_index(item_id, title, authors, year, item_type, source, path, ts):
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    author_str = "; ".join(authors[:2]) if authors else ""
    line = (
        f"| {str(item_id)[:30]} | {title[:55]} | {author_str[:35]} "
        f"| {year} | {item_type} | {source} | {str(ts)[:10]} |\n"
    )
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text(
            "# Library Index\n\n"
            "| ID | Title | Authors | Year | Type | Source | Processed |\n"
            "|----|----|----|----|----|----|----|\n"
            + line
        )
    else:
        with open(INDEX_FILE, "a") as f:
            f.write(line)
        # Rotate if oversized: keep header + latest 4000 data rows
        with open(INDEX_FILE) as f:
            lines = f.readlines()
        if len(lines) > 5000:
            header = lines[:3]
            trimmed = header + lines[3:][-4000:]
            INDEX_FILE.write_text("".join(trimmed))


def _find_existing_memory_file(isbn=None, doi=None):
    """Scan memory/papers/ and memory/books/ for a file with matching ISBN or DOI."""
    if not isbn and not doi:
        return None
    for subdir in ("papers", "books"):
        search_dir = MEMORY_DIR / subdir
        if not search_dir.exists():
            continue
        for md_file in search_dir.glob("*.md"):
            try:
                text = md_file.read_text(errors="ignore")
                if isbn and f'isbn: "{isbn}"' in text:
                    return md_file
                if doi and f'doi: "{doi}"' in text:
                    return md_file
            except Exception:
                pass
    return None


def _merge_source_into_file(md_file, source, source_id, citekey=None):
    """Update an existing memory file to add a second library source."""
    try:
        text = md_file.read_text()
        if source == "zotero":
            text = text.replace('  zotero: null', f'  zotero: "{citekey or source_id}"')
            text = text.replace('  zotero_key: null', '')
        elif source == "calibre":
            text = text.replace('  calibre: null', f'  calibre: "{source_id}"')
        md_file.write_text(text)
        # Move to papers/ if currently in books/ (Zotero items are papers)
        if source == "zotero" and md_file.parent.name == "books":
            new_path = MEMORY_DIR / "papers" / md_file.name
            (MEMORY_DIR / "papers").mkdir(parents=True, exist_ok=True)
            md_file.rename(new_path)
            return new_path
    except Exception:
        pass
    return md_file


# --------------------------------------------------------------------------- #
# Ingest: Zotero
# --------------------------------------------------------------------------- #

def ingest_zotero(data):
    key = data.get("key", "")
    citekey = extract_citekey(data)

    if is_ingested("zotero", citekey):
        return None  # already done

    title = data.get("title", "Unknown")
    doi = (data.get("DOI") or "").strip()
    isbn = ""  # Zotero items rarely have ISBN in top-level; check extra field
    for line in (data.get("extra") or "").splitlines():
        if line.lower().startswith("isbn:"):
            isbn = line.split(":", 1)[1].strip()

    # Cross-library dedup: check if Calibre already has this item
    existing = _find_existing_memory_file(isbn=isbn or None, doi=doi or None)
    if existing:
        updated = _merge_source_into_file(existing, "zotero", citekey, citekey=citekey)
        mark_ingested("zotero", citekey)
        return str(updated)
    authors = format_authors_zotero(data.get("creators", []))
    year = (data.get("date") or "")[:4] or ""
    abstract = (data.get("abstractNote") or "").strip()
    url = (data.get("url") or "").strip()
    item_type = data.get("itemType", "journalArticle")
    tags = [t["tag"] for t in data.get("tags", []) if t.get("tag")]
    journal = data.get("publicationTitle") or data.get("proceedingsTitle") or ""

    type_map = {
        "journalArticle": "paper", "conferencePaper": "paper",
        "preprint": "paper", "book": "book", "bookSection": "book",
        "thesis": "paper", "report": "paper",
    }
    entry_type = type_map.get(item_type, "paper")

    out_dir = MEMORY_DIR / "papers"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{slugify(citekey)}.md"

    domain = ", ".join(tags[:5]) if tags else ""
    summary = truncate(abstract) if abstract else "_No abstract available. To be filled on first access._"
    now = datetime.now(timezone.utc).isoformat()

    content = f"""\
---
title: "{title.replace('"', "'")}"
authors: {json.dumps(authors)}
year: "{year}"
type: {entry_type}
sources:
  zotero: "{citekey}"
  zotero_key: "{key}"
  calibre: null
tags: {json.dumps(tags)}
domain: "{domain}"
journal: "{journal}"
doi: "{doi}"
url: "{url}"
full_text_available: false
processed_at: "{now}"
---

## Summary

{summary}

## Key results / main ideas

_To be filled when item is accessed or reviewed._

## Techniques

_To be filled when item is accessed or reviewed._

## Connections to current research

_To be filled when item is accessed or reviewed._

## Notes

"""
    out_file.write_text(content)
    mark_ingested("zotero", citekey)
    _update_index(citekey, title, authors, year, entry_type, "zotero", str(out_file), now)
    return str(out_file)


# --------------------------------------------------------------------------- #
# Ingest: Calibre
# --------------------------------------------------------------------------- #

def ingest_calibre(data):
    book_id = str(data.get("id", ""))

    if is_ingested("calibre", book_id):
        return None

    title = data.get("title", "Unknown")
    authors = data.get("authors", [])
    year = str(data.get("year") or "")
    tags = data.get("tags", [])
    series = data.get("series") or ""
    description = strip_html(data.get("description") or data.get("comments") or "")
    identifiers = data.get("identifiers") or {}
    isbn = identifiers.get("isbn", "")
    doi = identifiers.get("doi", "")
    formats = data.get("formats", [])

    # Cross-library dedup: check if Zotero already has this item
    existing = _find_existing_memory_file(isbn=isbn or None, doi=doi or None)
    if existing:
        updated = _merge_source_into_file(existing, "calibre", book_id)
        mark_ingested("calibre", book_id)
        return str(updated)

    if series or any(t.lower() in ("textbook", "book", "monograph") for t in tags):
        entry_type = "book"
    else:
        entry_type = "paper"

    first_author = (authors[0].split(",")[0] if authors else "unknown").lower()
    slug = slugify(f"{first_author}_{title[:30]}_{year}")

    out_dir = MEMORY_DIR / "books"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{slug}.md"

    domain = ", ".join(tags[:5]) if tags else ""
    summary = truncate(description) if description else "_No description available. To be filled on first access._"
    now = datetime.now(timezone.utc).isoformat()

    content = f"""\
---
title: "{title.replace('"', "'")}"
authors: {json.dumps(authors)}
year: "{year}"
type: {entry_type}
sources:
  zotero: null
  calibre: "{book_id}"
tags: {json.dumps(tags)}
domain: "{domain}"
series: "{series}"
isbn: "{isbn}"
doi: "{doi}"
formats: {json.dumps(formats)}
full_text_available: true
processed_at: "{now}"
---

## Summary

{summary}

## Key results / main ideas

_To be filled when item is accessed or reviewed._

## Techniques

_To be filled when item is accessed or reviewed._

## Connections to current research

_To be filled when item is accessed or reviewed._

## Notes

"""
    out_file.write_text(content)
    mark_ingested("calibre", book_id)
    _update_index(book_id, title, authors, year, entry_type, "calibre", str(out_file), now)
    return str(out_file)


# --------------------------------------------------------------------------- #
# Batch mode (trickle)
# --------------------------------------------------------------------------- #

def batch_mode(limit):
    if not CALIBRE_CACHE.exists():
        print(json.dumps({"status": "error", "message": "Calibre cache not found"}))
        return 0

    with open(CALIBRE_CACHE) as f:
        data = json.load(f)

    # Cache may be a bare list or {"items": [...], ...} dict
    if isinstance(data, dict):
        all_books = data.get("items", [])
    else:
        all_books = data

    ingested_ids = {r["id"] for r in load_ingested() if r["source"] == "calibre"}

    def priority(b):
        score = 0
        ids = b.get("identifiers") or {}
        if ids.get("isbn") or ids.get("doi"):
            score += 10
        tags_lower = [t.lower() for t in (b.get("tags") or [])]
        score += sum(3 for t in tags_lower if any(rt in t for rt in RESEARCH_TAGS))
        if b.get("comments") or b.get("description"):
            score += 2
        return -score

    candidates = [b for b in all_books if isinstance(b, dict) and str(b.get("id", "")) not in ingested_ids]
    candidates.sort(key=priority)

    processed = 0
    for book in candidates[:limit]:
        try:
            path = ingest_calibre(book)
            if path:
                print(json.dumps({"status": "ok", "source": "calibre",
                                   "id": book.get("id"), "path": path}))
                processed += 1
        except Exception as e:
            print(json.dumps({"status": "error", "source": "calibre",
                               "id": book.get("id"), "error": str(e)}), file=sys.stderr)

    return processed


# --------------------------------------------------------------------------- #
# Bootstrap mode (reference-first)
# --------------------------------------------------------------------------- #

def bootstrap_mode():
    projects_dir = Path(WORKSPACE) / "data" / "projects"
    if not projects_dir.exists():
        print(json.dumps({"status": "error", "message": "projects dir not found"}))
        return 0

    cite_keys = set()
    for pattern in ("**/*.tex", "**/*.bib"):
        for f in projects_dir.glob(pattern):
            try:
                text = f.read_text(errors="ignore")
                for k in re.findall(r"\\cite[a-z*]*\{([^}]+)\}", text):
                    cite_keys.update(k.strip() for k in k.split(","))
                cite_keys.update(re.findall(r"@\w+\{([^,\s]+),", text))
            except Exception:
                pass

    ingested_zotero = {r["id"] for r in load_ingested() if r["source"] == "zotero"}
    processed = 0

    for key in sorted(cite_keys - ingested_zotero):
        try:
            result = subprocess.run(
                [sys.executable, str(ZOTERO_SKILL), "search", key, "--json"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ},
            )
            if result.returncode == 0:
                out = json.loads(result.stdout)
                items = out.get("results", [])
                if items:
                    path = ingest_zotero(items[0]["data"])
                    if path:
                        print(json.dumps({"status": "ok", "key": key, "path": path}))
                        processed += 1
        except Exception as e:
            print(json.dumps({"status": "skip", "key": key, "reason": str(e)}),
                  file=sys.stderr)

    return processed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(description="Library knowledge ingestion")
    p.add_argument("--source", choices=["zotero", "calibre"])
    p.add_argument("--data", help="JSON metadata string")
    p.add_argument("--id", help="Item ID (for --check)")
    p.add_argument("--check", action="store_true", help="Check if already ingested")
    p.add_argument("--batch", action="store_true", help="Trickle batch mode")
    p.add_argument("--bootstrap", action="store_true", help="Reference-first mode")
    p.add_argument("--limit", type=int, default=10, help="Items to process in batch mode")
    args = p.parse_args()

    if args.check:
        print(json.dumps({"ingested": is_ingested(args.source, args.id)}))
        return

    if args.batch:
        n = batch_mode(args.limit)
        print(json.dumps({"status": "ok", "processed": n}))
        return

    if args.bootstrap:
        n = bootstrap_mode()
        print(json.dumps({"status": "ok", "processed": n}))
        return

    if args.source and args.data:
        data = json.loads(args.data)
        if args.source == "zotero":
            path = ingest_zotero(data)
        else:
            path = ingest_calibre(data)
        if path:
            print(json.dumps({"status": "ok", "path": path}))
        else:
            print(json.dumps({"status": "skipped", "reason": "already ingested"}))
        return

    p.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
