---
name: dictionary-corrections
description: Apply a batch of correction patches to the Chinese character dictionary (dictionary.jsonl) safely. Each patch is checked against the value it expects to replace, logged, and committed. Use this whenever the user pastes or hands over corrections.jsonl lines, patch lines from a Chinese character study session, or JSON lines with "character", "field", "old" and "new" keys. Also use it when they say things like "apply these corrections", "add these fixes to the dictionary", "update dictionary.jsonl", or "here's another batch", even if they don't mention the skill or the script by name.
---

# Dictionary corrections

The user studies Chinese characters with Claude in a separate chat project. That project reads from `dictionary.jsonl`, a 9,574-entry character dictionary with one JSON object per line. When the study sessions spot an error, they produce **patch lines**, and the user brings batches of them here to be applied to the real file.

The goal is that every fix lands exactly once, never overwrites something unexpected, and can be traced afterwards. The project's script enforces this, so the patching itself is deterministic; your job is to drive it and explain the results plainly.

## Files

All of these live in the dictionary project folder, the git repository with `data/` and `scripts/` folders:

| File | Role |
|---|---|
| `data/dictionary.jsonl` | The live dictionary. Only the script edits it. |
| `data/dictionary.original.jsonl` | The pristine backup. Never modify it. Patches may match against it. |
| `data/corrections.jsonl` | Every manual patch ever accepted. It only ever grows. |
| `data/changelog.jsonl` | One line per change actually made, by the automated fixes and by these patches. |
| `data/dictionary.json`, `data/dictionary.db` | Nested and SQLite copies of the dictionary. The script regenerates them on every write. |
| `review/*.csv` | Reports of suspected errors that need a human decision. |

This skill's `scripts/apply_corrections.py` is a launcher. It finds the project's own `scripts/apply_corrections.py` from the current directory and runs it with the same arguments. Keeping a single implementation means the skill and the project can't disagree about how a patch is applied. Run the commands from the project folder, or any folder inside it.

## Patch format

```json
{"character": "吓", "field": "pinyin", "old": ["xià"], "new": ["xià", "hè"], "note": "hè in 恐吓", "source": "study 2026-10-02"}
```

- `character`, `field`, `old`, and `new` are required. `note` and `source` are optional but worth keeping.
- `field` must be one of `definition`, `pinyin`, `decomposition`, `etymology_type`, `etymology_semantic`, `etymology_phonetic`, or `etymology_hint`.
- `pinyin` values are lists of strings. Every other field's value is a string, and an empty value is `""`.
- A patch that removes a reading on purpose also needs `"allow_removal": true` (see the rules below).

## Workflow

### 1. Check that you're in the project

If the launcher can't find `data/dictionary.jsonl` and `scripts/apply_corrections.py`, it stops with exit code 2. Ask the user where the project is.

If the script reports that `data/dictionary.original.jsonl` is missing, stop and ask the user where the pristine copy is. Never recreate it from `dictionary.jsonl`, which already contains fixes.

### 2. Save the batch

Write the lines the user gave you to a temporary file, such as `/tmp/batch.jsonl`, exactly as given. The script tolerates the markdown code fences and blank lines that come with text pasted from chat, so don't hand-edit the lines.

### 3. Validate, then add

```bash
python3 <skill-dir>/scripts/apply_corrections.py check /tmp/batch.jsonl
python3 <skill-dir>/scripts/apply_corrections.py add /tmp/batch.jsonl
```

`check` reports how many lines are valid, how many are duplicates of patches already in `corrections.jsonl` (same character, field, `old` and `new`; they're skipped), and which are invalid and why. `add` appends the valid new lines, but only if the whole batch is valid; the batch is all-or-nothing.

If anything is invalid, don't fix the line yourself. Show the user the line and the reason. The patch content comes from careful linguistic checking in the study session, and a "repaired" patch could quietly encode the wrong correction. The exception is an obvious mechanical slip, such as a stray character, where the intent is unambiguous; in that case, propose the corrected line and wait for the user's OK.

### 4. Dry run

```bash
python3 <skill-dir>/scripts/apply_corrections.py apply
```

Each patch ends up in one of four states:

- **APPLY:** the current value (or the original backup's value) matches `old`, so the patch will be applied.
- **already applied:** the value already equals `new`, so the patch is skipped. This is normal, because the script re-checks every patch in the file on each run.
- **CONFLICT:** the value matches neither `old` nor `new`, or the patch would drop a reading. The patch is never forced.
- **INVALID:** the patch fails validation.

### 5. Write

If there are no conflicts or invalid patches, apply the changes for real:

```bash
python3 <skill-dir>/scripts/apply_corrections.py apply --write
```

The script validates the result before replacing the file, then regenerates `data/dictionary.json` and `data/dictionary.db`. Show the user each change as a short "before → after" summary. Git makes the change easy to undo, so a clean batch doesn't need a separate confirmation.

If there are any conflicts, stop before writing and walk the user through each one (see "Handling conflicts"). Write only after they decide.

### 6. Refresh the review reports

```bash
.venv/bin/python scripts/phase2_reports.py --write
```

Patches often resolve rows in the `review/` reports, and this drops them. It needs the project's virtual environment (`.venv`, for OpenCC). If `.venv` is missing, skip this step and tell the user.

### 7. Commit

Commit `data/` and `review/` together: the dictionary, its JSON and SQLite copies, `corrections.jsonl`, `changelog.jsonl`, and any changed reports. Name the characters in the message, for example:

```
corrections: 廷 吓 (3 patches)
```

### 8. Remind the user to re-upload

The Claude chat project holds its own read-only copy of `dictionary.jsonl`. End every run with a one-line reminder to replace that project file with the updated `data/dictionary.jsonl`; otherwise, the study sessions keep seeing the old data and will flag the same bugs again.

## Handling conflicts

A conflict means the patch was written against a value that isn't there. Look at the current value, the original value, and the patch's `old` and `new`. Then check `data/changelog.jsonl` for that character to explain what happened. The usual causes are:

- **An automated fix got there first.** For example, the bulk reading fix may have added `hè` to 吓 in a different order than the patch expected. If the current value already contains everything the patch wanted, suggest removing the patch line from `corrections.jsonl`.
- **The patch would delete a reading an automated fix added.** This shows up with a `reason` line. Usually the right move is to rewrite the patch so `new` keeps that reading, after checking with the user that the reading is real.
- **An earlier manual patch changed the same field.** If the new patch is still needed, it should be rewritten with the current value as `old`.
- **The patch has a typo in `old`.**

Lay out what you found and recommend one option, but let the user decide. Never edit `dictionary.jsonl` by hand to make a patch fit; the next write would reject the unlogged change anyway. Any edit to `corrections.jsonl` (removing or rewriting a line) needs the user's OK, because that file is the record of what was decided.

## Rules the script enforces (and why)

- **Readings are never removed by a patch**, unless the patch says `"allow_removal": true`. The dictionary under-reports readings far more often than it lists wrong ones, so a patch that drops a reading is almost always a mistake. If the user really wants a reading removed, confirm it with them, then add `"allow_removal": true` to the patch and give the reason in its `note`.
- **Every change is logged** to `data/changelog.jsonl` as exactly `{"character", "field", "old", "new", "rule"}`, with the rule `"manual"`. The patch's `note` and `source` stay in `corrections.jsonl`.
- **Each write is validated before it replaces the file.** The line count, character order, fields and formatting must be unchanged, and replaying the changelog over the backup must reproduce the result. Untouched lines stay byte-for-byte identical, so `git diff` shows only the real changes.
- **Re-running never replays a chain.** A patch is skipped when a later patch for the same character and field has already taken effect.
- **Readings are compared after Unicode normalization (NFC),** so the same tone mark typed two different ways still matches.
- **The `radical` and `character` fields can't be patched,** since lookups depend on them.

The exit code is 0 when everything is clean, 1 when there are conflicts or invalid patches, and 2 when a file is missing.
