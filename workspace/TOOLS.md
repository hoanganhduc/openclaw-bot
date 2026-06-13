# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

## ⚠ MANDATORY: Sending files to user via Telegram/WhatsApp/Google Chat

**You CANNOT send files by just saying you sent them.** Text messages (`sendMessage`) do NOT deliver files. You MUST run an `exec` command to actually deliver a file.

**For Zotero papers** — use `--send` with `zot get`:
```
exec: /workspace/skills/zotero/run_zot.sh --json get "<query>" --send <CHANNEL> <SENDER_ID>
```
Where CHANNEL and SENDER_ID come from the conversation metadata (e.g., telegram + sender_id field).

**For any other file** (project PDFs, TeX sources, archives, etc.) — use `send_file.sh`:
```
exec: /workspace/skills/zotero/send_file.sh <CHANNEL> <SENDER_ID> "<FILE_PATH>" "<CAPTION>"
```

**Rules:**
1. You MUST run the exec command. Typing "I sent the file" does NOT send the file.
2. You MUST check the JSON output for `"status":"ok"` before confirming delivery.
3. If the exec output shows `"status":"error"`, tell the user what went wrong.
4. NEVER skip the exec call. NEVER claim a file was sent without running the command.

---

Add whatever helps you do your job. This is your cheat sheet.

### File Writing in Sandbox

- For files that must be compiled/executed via `exec` (Lean, Python scripts), prefer `exec`-based writes (`cat > file << 'EOF'`) over the `write` tool — the workspace bridge may cause visibility issues.
- When a subagent and coordinator both need to write to the same file, use unique filenames or wait for subagent completion first. Last writer wins.
- Always verify file content (`wc -c`, `head`) after writing before attempting `edit`.

### Research workflow reminders

- For named graph-family invariant questions, do a literature-first pass before reconstructing from memory.
- Treat small computational checks as sanity checks, not as substitutes for direct theorem-source verification.
