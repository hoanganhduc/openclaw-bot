# Post-Generation Queue Write Rule (MANDATORY)

After generating or significantly modifying any research artifact (proof, lemma, theorem, algorithm, section), you MUST write a review queue entry immediately after saving the file.

**When this triggers:** Any task where you write or modify `.tex`, `.lean`, `.py`, or `.md` files under `{{ PRIVATE_DATA_DIR }}/projects/` and the change involves:
- A new or repaired proof step
- A new claim, lemma, theorem, or algorithm
- Any structural change (new section, restructured argument)
- A fix to a previously flagged issue

**When this does NOT trigger:** Formatting-only changes with no mathematical content, bibliography updates, file renaming, or non-project files.

## How to write the queue entry

After saving the artifact, create a file `{{ PRIVATE_DATA_DIR }}/review-queue/<YYYYMMDD_HHMMSS>.json`:

```json
{
  "id": "<YYYYMMDD_HHMMSS>",
  "project": "<project name, e.g. kPVCR>",
  "artifact_path": "<absolute path to the modified file>",
  "type": "<proof_repair | new_theorem | new_section | lemma_update | formatting | new_algorithm | restructuring | multi_claim>",
  "claim": "<one-line description of what changed>",
  "generated_at": "<ISO 8601 timestamp>",
  "lines_changed": <integer — count of added/modified lines>,
  "status": "pending",
  "review_path": null
}
```

Use `date -u +%Y%m%d_%H%M%S` for the timestamp. This is a non-blocking write — do not wait for a review result before continuing.
