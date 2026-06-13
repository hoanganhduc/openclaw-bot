# Knowledge Accumulation Rules

## Concept Evolution Files (MANDATORY)

Maintain `memory/concepts/<concept>.md` for every recurring domain concept encountered during work.

**Trigger:** After any proof work, paper reading, or research session involving a named concept (e.g., token sliding, PSPACE-hardness, caterpillar graphs, fixed-parameter tractability), check if a concept file exists in `memory/concepts/`. If yes, append new findings. If no, create it.

**File format:**
```markdown
---
concept: <name>
domain: <field>
last_updated: <ISO timestamp>
---

## Definition
<current best definition>

## Known results
- <result> — source: <citekey or description>, added: <date>

## Open problems

## Proof techniques associated

## Related concepts

## History of updates
- <date>: <what was added>
```

Keep entries concise. One file per concept. Update `last_updated` on every write.

---

## Post-Session Knowledge Capture (MANDATORY)

At the END of every substantial work session (proof work, paper analysis, system changes), before closing:

1. Write a brief knowledge capture entry to `memory/knowledge/<YYYY-MM-DD>_<topic>.md`:
   - New concepts or definitions encountered
   - Decisions made and the reasoning behind them
   - Corrections to prior beliefs or outdated information
   - Techniques or patterns that proved useful

2. If any entry contradicts something in an existing concept file, update the concept file.

3. If a paper was accessed and has a memory entry in `memory/papers/` or `memory/books/` with `_To be filled_` sections, fill them in with what you now know.

**File format:**
```markdown
---
topic: <topic>
session_date: <date>
---
- <bullet: new fact, decision, correction, or technique>
- ...
```

This rule applies even for short sessions. Two bullet points are better than nothing.
