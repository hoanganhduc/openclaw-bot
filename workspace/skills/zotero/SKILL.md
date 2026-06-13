---
name: zotero
description: "ALWAYS use this skill when the user asks to send, get, retrieve, find, share, add, or search for a paper. This skill manages the user's Zotero library with 10,000+ papers and can retrieve PDFs, create share links, add new papers, and search. Prefer this over getscipapers for any request involving sending/getting/finding papers."
user-invocable: false
disable-model-invocation: false
metadata:
  version: "0.9"
  phase: "9"
---

# Zotero Library Manager

**IMPORTANT: Use this skill FIRST whenever the user asks to send, get, retrieve, find, fetch, or share a paper.** The user has a Zotero library with 10,000+ papers. Always search the library first before trying other approaches.

This skill is preferred over getscipapers for paper requests. If Zotero does not satisfy the request, follow the `AGENTS.md` routing order before handing off to `getscipapers_requester`.

## When to use this skill

Use this skill when the user asks to:
- **Send / get / retrieve / find a paper** — run `zot get "<query>"` to search library + fetch PDF from WebDAV, then send the file
- **Share a paper link** — run `zot get --link "<query>"` for Google Drive share link
- **Add a paper** / DOI / arXiv ID to Zotero — run `zot add`
- **Search their library** — run `zot search`
- Manage collections (list, create, file papers)
- Check Zotero system health
- Anything involving "Zotero", "my library", "my papers", "my collections", or any paper by title/author

## Quick reference — most common actions

**User asks "send me / get me / find the paper about X":**
```
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send <CHANNEL> <TARGET>
```
If result has `"multiple": true`, show the list and ask user to pick a number, then:
```
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --index N --send <CHANNEL> <TARGET>
```
The `--send` flag downloads AND sends the file in one step. Check the `"send"` field in the JSON response to confirm delivery (`"status":"ok"` means the file was sent). **NEVER tell the user a file was sent unless the JSON response contains `"send":{"status":"ok"}`.**

CHANNEL and TARGET come from the conversation metadata (sender_id field):
- Telegram: `--send telegram <SENDER_ID>`
- WhatsApp: `--send whatsapp <SENDER_PHONE>`
- Google Chat: `--send googlechat <SENDER_SPACE>`

**Do NOT use send_file.sh separately.** Always use `--send` so that download + delivery happens in one exec call.

**User asks to share a link:**
```
exec: /workspace/skills/zotero/run_zot.sh --json get --link "<query>"
```

**User asks to add a paper:**
```
exec: /workspace/skills/zotero/run_zot.sh add "<DOI or arXiv or URL>" --collection "<name>"
```

## All commands

### Retrieve paper (PDF via WebDAV) — use for "send me", "get me", "find"
```
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send <CHANNEL> <TARGET>
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --index N --send <CHANNEL> <TARGET>
```

### Share paper (Google Drive link)
```
exec: /workspace/skills/zotero/run_zot.sh --json get --link "<query>"
```

### Add paper
```
exec: /workspace/skills/zotero/run_zot.sh add "<DOI or arXiv ID or URL or ISBN>" --collection "<name>" --collection "<name2>"
exec: /workspace/skills/zotero/run_zot.sh add "<identifier>" --no-pdf
exec: /workspace/skills/zotero/run_zot.sh --dry-run add "<identifier>"
exec: /workspace/skills/zotero/run_zot.sh add --file dois.txt --collection "Batch Import"
exec: /workspace/skills/zotero/run_zot.sh add --from-manifest manifest.json
```

### Share paper (Google Drive link)
```
exec: python3 /workspace/skills/zotero/zot.py --json get --link "<query>"
```

### Search library
```
exec: python3 /workspace/skills/zotero/zot.py search "<query>"
exec: python3 /workspace/skills/zotero/zot.py search "<query>" --json
exec: python3 /workspace/skills/zotero/zot.py search "<query>" --bibtex
```

### Update existing item
```
exec: /workspace/skills/zotero/run_zot.sh update <key> --attach-pdf
exec: /workspace/skills/zotero/run_zot.sh update <key> --item-type manuscript
exec: /workspace/skills/zotero/run_zot.sh update <key> --add-collection "X" --remove-collection "Y"
```

### List / create collections
```
exec: python3 /workspace/skills/zotero/zot.py list-collections --tree
exec: python3 /workspace/skills/zotero/zot.py list-collections --tree --json
exec: python3 /workspace/skills/zotero/zot.py create-collection "<name>" --parent "<parent>"
```

### Remove from collection (item stays in library)
```
exec: python3 /workspace/skills/zotero/zot.py remove-from-collection <key> --collection "<name>"
```

### Move to trash
```
exec: python3 /workspace/skills/zotero/zot.py --json trash "search query"
exec: python3 /workspace/skills/zotero/zot.py --json trash --key <key>
exec: python3 /workspace/skills/zotero/zot.py --json trash "query" --index N
exec: python3 /workspace/skills/zotero/zot.py --dry-run trash "query"
```

### List trash / empty trash
```
exec: python3 /workspace/skills/zotero/zot.py --json list-trash
exec: python3 /workspace/skills/zotero/zot.py --dry-run empty-trash
exec: python3 /workspace/skills/zotero/zot.py --json empty-trash
```

### Health check
```
exec: python3 /workspace/skills/zotero/zot.py doctor
```

### Auto-catalog from digests
```
exec: python3 /workspace/skills/zotero/scripts/auto-catalog.py --source all --min-score 80
```

## Sending files to the user

**Always use `--send` with `zot get` to download AND send in one step.** Do NOT call `send_file.sh` separately — that two-step approach is unreliable.

```
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send telegram <SENDER_ID>
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send whatsapp <SENDER_PHONE>
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send googlechat <SENDER_SPACE>
```

The JSON response will include a `"send"` field with the delivery result. **Only confirm delivery to the user if `"send":{"status":"ok"}` is in the response.** If `"send":{"status":"error",...}`, tell the user what went wrong.

Channel behavior:
- **Telegram**: sends file directly via Bot API (fast)
- **WhatsApp**: sends file via host queue worker
- **Google Chat/Zalo**: generates a Google Drive share link (no local file upload)

## Multi-turn retrieval flow

When the user asks to retrieve or send a paper with a partial/ambiguous query:
1. Run `zot get "<query>" --json --send <CHANNEL> <TARGET>` — if multiple results, show a numbered list with title, authors, year (the `--send` is harmless when multiple results are returned since no file is downloaded yet)
2. Ask: "Which one? Reply with the number."
3. Wait for the user's reply (a number)
4. Run `zot get "<query>" --index <N> --json --send <CHANNEL> <TARGET>` to download AND send the selected paper

Do NOT guess which paper the user wants when multiple results match. Always show the list and let the user pick.

If only one result matches, the file is downloaded and sent automatically in one step. Check the `"send"` field in the response to confirm delivery.

## MANDATORY: Paper ingest rules (ALWAYS follow when adding papers)

**1. PDF naming:** The `zot add` command automatically renames PDFs using the ZotFile pattern `{%a_}{%y_}{%t} {[%T]}` (e.g., `Parker et al_2016_Decision Comfort [Journal Article].pdf`). This is handled by the tool — do NOT rename manually. But verify in the output that the filename follows this pattern. If the output shows an un-renamed file (e.g., a raw DOI or hash filename), report it as a bug.

**2. Item type for preprints/arXiv:** ALL preprints, arXiv papers, manuscripts, and author self-published versions MUST have itemType set to `manuscript` (NOT `preprint`). After `zot add`, if the source is arXiv or the item type is `preprint`, immediately run:
```
exec: /workspace/skills/zotero/run_zot.sh update <key> --item-type manuscript
```

**3. Deduplication policy:** Different versions of the same paper (arXiv, conference, journal, tech report) are intentionally kept as separate items. Only deduplicate by DOI match — NEVER flag arXiv + journal versions of the same paper as duplicates.

**4. Collection assignment:** Never silently add papers without a collection. Always follow the collection assignment flow below.

## Collection assignment flow

When adding a paper without a `--collection` flag:
1. Run `zot list-collections --tree --json` to get the collection hierarchy
2. Extract paper topics from the metadata (title keywords, venue, subject tags)
3. Suggest matching collections from the existing tree + offer "Create new collection..."
4. User can pick one or more (e.g., "1,3") or ask to create a new one
5. If user picks a collection that has subcollections, show the subcollection tree and ask which level
6. Run `zot add <DOI> --collection "<chosen1>" --collection "<chosen2>"`

Do NOT silently add papers without a collection unless the user says `--no-collection` or explicitly skips.

## Disambiguation with getscipapers

- "Download this paper" → search Zotero first; if not in the library and a raw external PDF is still needed, hand off to `getscipapers_requester`
- "Add this paper to my library" / "Save this to Zotero" → **zotero** (library + metadata + WebDAV)
- "Search my library for X" → **zotero**
- "Get me this paper" (ambiguous) → use Zotero first, then follow the `AGENTS.md` fallback route if not found
- "That paper has no PDF, try downloading it" → `zot update <key> --attach-pdf`
- "Move that paper from Auto-cataloged to Graph Theory" → `zot update <key> --add-collection "Graph Theory" --remove-collection "Auto-cataloged"`
- "Remove that paper from the Graph Theory collection" → `zot remove-from-collection <key> --collection "Graph Theory"`
- "Delete that paper" / "Trash it" → `zot trash --key <key>` (moves to trash, recoverable)
- "What's in the trash?" → `zot list-trash`
- "Empty the trash" → `zot empty-trash` (permanent, ask for confirmation first)
