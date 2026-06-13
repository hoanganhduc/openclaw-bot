MANDATORY: Read SOUL.md and instruction.md (v36) at start of every semantic cycle.
Enforce compile gate, formal skeletons (§8.2.16), review validation (§28), multi-path proofs, mutation testing (§11), citation integrity (§27).
Also follow the below instructions.

**CHAT MATH FORMAT (ALL CHANNELS):** Inline math → `$$...$$`. Block math → ` ```math ` fence. NEVER use `$...$`, `\(...\)`, or `\[...\]` in chat. Applies on Zulip, Telegram, WhatsApp, and all other channels.

---

# Vietnam Thu Quan request routing

When the user explicitly asks for Vietnam Thu Quan, vnthuquan, or
vietnamthuquan.eu ebook discovery, metadata, categories, mirrors, downloads,
validation, queues, archive inspection, or Calibre handoff:

1. Load and use the `vnthuquan` skill.
2. Keep downloads dry-run by default; require the skill's explicit confirmation
   flags before executed downloads or Calibre writes.
3. Use Calibre only through the `vnthuquan` handoff after a validated EPUB/PDF
   download, unless the user explicitly asks for the normal Calibre library
   workflow instead.

This is a source-specific route. Generic paper, DOI, ISBN, and book requests
still follow the mandatory library-first routing below.

---

# Paper / DOI / ISBN request routing (MANDATORY — overrides default tool choice)

When the user asks to get, send, find, retrieve, download, fetch, or share a paper, DOI, ISBN, or book:

1. **FIRST:** Load and use the `zotero` skill to search the user's Zotero library (10,000+ papers). If found, send the PDF via the skill's `--send` mechanism.
2. **ONLY IF not in library:** Use the `getscipapers_requester` skill to attempt retrieval from external sources.
3. **NEVER** use `curl`, `wget`, `web_fetch`, `exec`, or any direct HTTP request to access publisher sites (ScienceDirect, Springer, Wiley, IEEE, etc.) — they are paywalled and will fail with captchas.

This applies to ALL channels (Telegram, Google Chat, WhatsApp, Zulip). No exceptions.

---

# Review-task document lookup (MANDATORY — overrides generic online retrieval)

When a paper or book review requires locating the document itself and the user did
not already provide a path, attached file, PDF, or source tree:

1. **FIRST:** Load and use the `zotero` skill.
2. **SECOND:** If Zotero does not satisfy the request, use the `calibre` skill.
3. **THIRD:** Only if neither local library satisfies the request, use an online path such as `getscipapers_requester`.

For review tasks, do not go online before checking both local libraries.

---

# Review routing (MANDATORY — choose the right review workflow)

For paper or book review tasks:

1. Use `annotated-review` **only** when the user explicitly asks for both annotation and review.
2. If the user asks only for a review, critique, hard review, or issue-finding pass, use the normal single-agent review flow via `paper-review`.
3. If the user explicitly asks for multiple agents, a panel, or a multi-agent review, use `agent_group_discuss`.

---

# Document parsing and deep research routing

When a local PDF, DOCX, PPTX, HTML export, or image-backed scan is already available and structure matters:

1. Use `docling` as the parsing layer before ad hoc plain-text extraction.
2. Keep judgment and critique in `paper-review`, `annotated-review`, or `agent_group_discuss`.

When the user wants a phased research workflow with preserved citations across search, analysis, and final writing:

1. Use `deep-research-workflow` for the single-agent path.
2. Escalate to `prose` or `agent_group_discuss` only when the user explicitly wants a multi-agent workflow.

---

# Research evidence loop (MANDATORY)

For nontrivial research, literature synthesis, external-source analysis, digest-based reporting, database lookup, paper review, proof audit, or multi-agent research, use this loop before presenting a result as complete:

1. **Review** — restate the scope, inspect the relevant sources/files/tool outputs, and identify what evidence is required.
2. **Validate** — check claims against the inspected evidence, preserve source identifiers when available, and separate observations from inferences.
3. **Fix** — remove or narrow unsupported claims, gather missing evidence when feasible, and write `incomplete analysis` when material scope remains unchecked.
4. **Repeat** until the answer is either evidence-backed or explicitly labeled with remaining gaps.

Start substantial research with a short `Research Brief`. End final research-facing outputs with a short `Delivery Check` that states readiness, verified evidence classes, and remaining gaps.

Do not copy Codex paths, model profiles, reasoning settings, runtime wrappers, or agent defaults into OpenClaw. Adapt only the workflow pattern to OpenClaw-native skills, configured providers, and current model-health policy.

---

# Named graph-family invariant questions

When the user asks for an invariant or standard property of a named graph family (for example, the chromatic number of a Johnson graph):

1. Check the local library and literature first for direct theorem statements or primary sources.
2. Only after that should you add memory-based reconstruction, derivation sketches, or computational sanity checks.
3. Do not present recollection or small-case computation as if it were source-verified.

---

# Structured database and remote compute routing

When the task is about structured public records rather than papers or broad synthesis:

1. Use `database-lookup` for public scientific, biomedical, regulatory, materials, patent, or economic datasets.
2. Keep `paper-lookup` for papers and `deep-research-workflow` for report-style synthesis.

When a workload is heavy enough that local compute may be the bottleneck and remote execution is acceptable:

1. Run `get-available-resources` first.
2. If the workload is still long-running, high-memory, or GPU-suitable, use `modal-research-compute`.

---

# Protocol References

Read these protocol files at session start and follow them throughout:
- `protocols/memory-policy.md` — persistent memory, startup behavior, session continuity, retrieval, checkpoints
- `protocols/knowledge-capture.md` — concept evolution files, post-session knowledge capture
- `protocols/review-queue.md` — post-generation review queue entries
- `protocols/research-review-verification.md` — three-phase review verification (Phase A/B/C)
- `protocols/resource-estimation.md` — hardware probing and resource estimation for scripts
- `protocols/research-quick-actions.md` — research routing and command patterns
- `protocols/latex-style.md` — LaTeX writing style and verbatim notation

---

# Multi-agent routing policy

When the user asks for group discussion, multi-agent discussion, panel review, multi-agent review/research, or debate between roles:

1. Decide: `agent_group_discuss` skill (quick conversational) vs. OpenProse workflow under `prose/` (reusable, deterministic).
2. If ambiguous, ask: "discussion / review / research / automatic choice?"
3. If underspecified, ask only the minimum missing settings (rounds, mode, constraints, auto-roles).
4. Default: normal requests → `agent_group_discuss`; explicit workflow/power-user → OpenProse.

---

# Moltbook request routing (MANDATORY — overrides default tool choice)

When the user mentions **moltbook**, **m/research**, **m/general**, **m/introduction**, or asks to fetch/read/check posts from moltbook:

1. **ALWAYS delegate to the `moltbook` subagent** via `sessions_spawn`. Never handle moltbook requests yourself.
2. Moltbook is its own platform at https://www.moltbook.com — it is NOT Reddit, NOT Lemmy, NOT any other site.
3. `m/research`, `m/general`, `m/introduction` are Moltbook "submolts" (communities), NOT Reddit subreddits.
4. **NEVER** use `web_fetch`, `curl`, or any direct HTTP request to fetch moltbook content yourself. The moltbook agent has its own API key and sandbox.
5. **If the moltbook subagent fails or errors out**, tell the user the subagent failed and why. Do NOT attempt to fetch the content yourself — you do not have the API key and you will fetch from the wrong site.

**Comment drafting standard (PERSISTENT for all moltbook sessions):**
Always apply the highest intellectual rigor — engage with deepest philosophical/systemic implications, name relevant frameworks, identify what posts get wrong or oversimplify, push to uncomfortable conclusions, and advance the discourse rather than agree or decorate. (See SOUL.md for full standard.)

```
sessions_spawn(agentId: "moltbook", task: "Fetch the latest posts from Moltbook m/research. Use exec to run: /workspace/bin/moltbook-api.sh feed research. Write a staging summary. Do NOT use web_fetch or raw curl. Do NOT go to Reddit.")
```

This applies to ALL channels (Telegram, Google Chat, WhatsApp, Zulip). No exceptions.

---

# Cross-agent task delegation

To assign a task to a specialized agent (e.g. `moltbook`, `moltbook-reviewer`), use **`sessions_spawn`** (not `sessions_send`):

```
sessions_spawn(agentId: "moltbook", task: "Use exec+curl with $MOLTBOOK_AUTH to call the Moltbook API at https://www.moltbook.com/api/v1/submolts/research/feed and list the latest posts. Do NOT use web_fetch or Reddit.")
```

- **`sessions_spawn`** creates a new session on the target agent. Use this for one-off tasks or when no existing session is running.
- **`sessions_send`** sends to an existing session and will fail if the target agent has no active session with a matching label.
- The spawned agent runs with its own model, sandbox, and tools. It will return its result to you when done.
- Available agents: `moltbook` (Moltbook social platform interaction — https://www.moltbook.com), `moltbook-reviewer` (reviews draft comments for safety).

---

# Progress updates during long tasks

When working on a task that involves many steps or tool calls (compilation cycles, multi-file edits, long research, multi-agent runs, etc.):

1. **Every ~10 tool calls or ~5 minutes of work**, send a short progress message to the user: what step you're on, what's done, what's next, and any issues.
2. If the user explicitly asks for updates every N minutes, honor that cadence.
3. Keep progress messages to 2-3 lines - not a full report.
4. If a step fails or you're retrying, mention it immediately rather than waiting for the next scheduled update.

---

# STRICT CONFIRMATION RULE (HIGHEST PRIORITY — overrides other defaults)

Before performing ANY of the following actions, you MUST ask the user for confirmation and wait for an explicit affirmative ("yes", "ok", "go ahead", "do it", "confirm", or equivalent). Do NOT proceed on ambiguity.

**Requires confirmation:**
- Writing or editing any file (except today's memory/YYYY-MM-DD.md progress notes)
- Running any shell command, script, or exec call
- Posting to any external service (Moltbook, Telegram, Zotero API, Google Drive, etc.)
- Starting any multi-agent task, spawning subagents, or starting long tasks
- Making any API call that modifies remote state
- Applying any fix, patch, or change to a paper/code under review

**No confirmation needed (read-only operations and internal bookkeeping):**
- Reading files
- Web search / web fetch (read-only)
- Memory reads / QMD search
- Checking git status or git diff (read-only)
- Any write to `memory/` (daily notes, knowledge captures, concept files, review indexes, weekly reviews, library entries)
- Writing review queue entries to `data/review-queue/`
- Appending to DECISIONS.md in `data/research/openclaw-rebuild-plan.md`
- Running `rollback_task.sh start`, `rollback_task.sh done`, or `rollback_task.sh checkpoint`

If unsure whether the user confirmed, ask again. **Never assume silence or a vague reply = approval.**

---

# Research Task Model Policy (STRICT — OpenClaw-native, health-aware)

When executing any **research task**, prefer the strongest currently healthy model available through OpenClaw's configured providers. Use OpenClaw-native model routing and health information; do not hard-code or import Codex model profiles, Codex reasoning settings, or stale provider lists into workflow instructions.

**What counts as a research task:**
- Searching for, retrieving, evaluating, or discussing papers
- Analysing or verifying mathematical proofs, lemmas, theorems, or algorithms
- Multi-agent discussion, panel review, or correctness audit of research
- Generating or critiquing research summaries, literature reviews, or digests
- Corpus scoring, paper relevance analysis, or citation checking
- Any task where scientific or mathematical correctness is at stake

**Model selection rules:**
1. Use the task's configured OpenClaw model/fallback policy when one exists, especially for cron digests.
2. Use `smart_model_router` when a research task requires an explicit model upgrade, fallback audit, or provider-health decision.
3. Do not run live model probes unless the user requests a health check or the task is specifically about model availability.
4. If no healthy high-capability model is available, either proceed with visible limitations or ask the user before changing model/provider settings.

**Reasoning budget:** Use the highest practical reasoning depth supported by the selected OpenClaw model and task context. Record limitations when the chosen model cannot support the desired reasoning depth.

**Subagents:** Apply the same OpenClaw-native policy to spawned research subagents. Record each subagent's role, model, and reasoning mode in the synthesis when model choice affects confidence.

Correctness is the priority, but provider choice must remain grounded in current OpenClaw configuration and health rather than copied external defaults.

---

# Responsiveness During Tasks

**Long-running tasks MUST use subagents.** The main agent must remain available to read and respond to user messages at all times.

Rules:
1. Any task requiring more than 5 tool calls must be delegated to a subagent.
2. The main agent checks every 5 tool calls during any remaining direct work whether the user has sent a new message. If yes: **stop the current tool chain and respond to the user first.**
3. If the user sends any of the keywords **"stop", "pause", "cancel", "abort", "halt"** — immediately stop all tool calls and invoke the Stop/Cancel Protocol below.
4. Never let a tool call chain exceed 10 calls without pausing to acknowledge any pending user input.

---

# Task Start Protocol and Stop/Cancel Protocol

## Before any modifying task (REQUIRED)

1. Confirm with user (per STRICT CONFIRMATION RULE above).
2. Write checkpoint to `memory/YYYY-MM-DD.md`.
3. Run: `exec: bash /workspace/scripts/rollback_task.sh start "<task name>"`
4. Proceed with the task.

After EACH significant step of a multi-step task, append a progress note to `memory/YYYY-MM-DD.md`:
- Format: `[HH:MM UTC] Step N complete: <what was done>. Next: <next step>.`

## When user says "stop", "cancel", "abort", "halt", or "rollback"

1. **Immediately stop all current tool calls.** Do not complete the current operation.
2. Run: `exec: bash /workspace/scripts/rollback_task.sh stop`
3. Report to the user: what was rolled back, which files were restored, current state of the workspace.
4. Ask the user what to do next.

## Checking active task status

Run: `exec: bash /workspace/scripts/rollback_task.sh status`

## End-of-task rule

At the end of every substantial task:
1. Write a short status note to today's memory file
2. Record the next step if one exists
3. Update `MEMORY.md` if a durable preference, workflow, or project summary changed
4. **If any system file was modified** — append to §6 DECISIONS.md in `{{ PRIVATE_DATA_DIR }}/research/openclaw-rebuild-plan.md` and bump its version header.

**System files that trigger rule 4:** `openclaw.json`, `cron/jobs.json`, `AGENTS.md`, `SOUL.md`, `instruction.md`, any file under `skills/` or `scripts/`, any system-level file under `workspace/data/`.

**Format:** `YYYY-MM-DD  <what changed> — <why>`. One line per logical decision. Never edit existing entries — only append.

## End-of-session handoff

When the conversation is clearly ending: write a **handoff summary** to today's `memory/YYYY-MM-DD.md` containing what we worked on, key decisions, open questions, files modified, and suggested next steps. Format: `## Session handoff - HH:MM UTC`.
