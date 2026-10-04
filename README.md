# Character Decomposition Data

`data/dictionary.jsonl` is a Chinese character dictionary (9,574 entries, one
JSON object per line) flattened from Make Me a Hanzi, with fixes applied on
top of the untouched original.

```
data/
  dictionary.jsonl           the fixed dictionary
  dictionary.json            the same data in nested form, kept in sync
  dictionary.db              the same data in SQLite, kept in sync
  dictionary.original.jsonl  the original, read-only. Never edit it.
  dictionary.original.json   the original nested data, read-only. Never edit it.
  corrections.jsonl          manual patches, one per line
  changelog.jsonl            every change: {"character", "field", "old", "new", "rule"}
  sources/                   downloaded Unihan and cjkvi-ids data (git-ignored)
review/                      problems that need a human decision (CSV)
scripts/
  phase1_fixes.py            phase 1: automated fixes
  phase2_reports.py          phase 2: review reports
  apply_corrections.py       phase 3: manual patches
  rebuild.sh                 re-runs every phase from the originals
  common.py                  shared code: IDS parser, Unihan loader, validator
  export_json_db.py          carries the fixes into dictionary.json and dictionary.db
  build_db.py                builds dictionary.db from dictionary.json
  convert_to_jsonl.py        made the original dictionary.jsonl from dictionary.json
docs/
  dictionary_schema_description.md   the Make Me a Hanzi data format
.claude/skills/
  dictionary-corrections/    Claude skill for applying patch batches (/dictionary-corrections)
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Only phase 2 needs the venv (for OpenCC); phases 1 and 3 use just the
standard library. The scripts download their source data into
`data/sources/` on first use: `Unihan.zip` from unicode.org (this was built
with Unicode 18.0.0) and `ids.txt` from github.com/cjkvi/cjkvi-ids.

## Running

Every script is a dry run unless you pass `--write`, and can be run from any
directory. A write is validated first, and nothing is written if validation
fails. The checks: the line count and character order match the original;
every line parses, with the same fields, field order and JSON formatting; and
replaying `changelog.jsonl` over the original reproduces the file exactly,
with each rule touching only its own fields. Running a script twice makes no
new changes.

Every write also refreshes `dictionary.json` and `dictionary.db`.
`export_json_db.py` rebuilds `dictionary.json` from
`dictionary.original.json`, writing in only the fields that changed (stroke
`matches` and formatting stay as they were). It writes nothing unless
`convert_to_jsonl.py`'s flattening of the result reproduces
`dictionary.jsonl` exactly. Then `build_db.py` rebuilds `dictionary.db`.

```bash
.venv/bin/python scripts/phase1_fixes.py --write       # 1. automated fixes
.venv/bin/python scripts/phase2_reports.py --write     # 2. review/*.csv (only reads the dictionary)
.venv/bin/python scripts/apply_corrections.py --write  # 3. manual patches, applied last so they win
```

To rebuild everything from the originals (refuses to run if any of the files
it rewrites have uncommitted changes):

```bash
scripts/rebuild.sh
```

## Adding corrections

A patch is one JSON line:

```json
{"character": "吓", "field": "pinyin", "old": ["xià"], "new": ["xià", "hè"], "note": "...", "source": "study 2026-10-02"}
```

Save a batch of them to a file (text pasted from chat, code fences and all, is
fine). Check it, add it to `data/corrections.jsonl`, dry-run, apply, refresh
the reports, and commit everything that changed, including `dictionary.json`
and `dictionary.db` (e.g. `corrections: 吓 辟 扛 (3 patches)`):

```bash
python3 scripts/apply_corrections.py check batch.jsonl
python3 scripts/apply_corrections.py add batch.jsonl
python3 scripts/apply_corrections.py
python3 scripts/apply_corrections.py --write
.venv/bin/python scripts/phase2_reports.py --write
```

`add` skips patches already recorded (same character, field, `old` and
`new`) and adds nothing unless the whole batch is valid. A patch applies when
the field's current value, or its value in the original, equals `old`. A
patch whose `new` is already in effect is skipped silently. Anything else is
reported as a conflict and not applied. Allowed fields: `definition`,
`pinyin`, `decomposition`, `etymology_type`, `etymology_semantic`,
`etymology_phonetic`, `etymology_hint`.

Patches never remove a pinyin reading, including one an automated fix added,
unless they say `"allow_removal": true`. Re-running phase 1 never touches a
field a patch has set. The exit status is 0 when everything is clean, 1 for
conflicts or invalid patches, and 2 for a missing file.

This is the same workflow as the `dictionary-corrections` skill, whose
source is in `.claude/skills/`; its script runs this one.

## What the phases do

**Fix A.** Entries labeled `ideographic` or `pictographic` whose hint says a
component "provides the pronunciation" become `pictophonetic`. The phonetic
is the character just before that phrase and must appear in the
decomposition. The semantic is filled in only when the phonetic is a
top-level IDS part and exactly one other single-character part remains.
When the hint says the phonetic also provides the meaning (少, 私), only the
phonetic is filled in. `etymology_hint` is never changed.

**Fix B.** Adds the Unihan `kXHC1983` readings that `pinyin` is missing. It
puts the first `kMandarin` reading first, then the existing readings, then
the added ones. `kXHC1983` dates from 1983, before the 1985 审音表. So a
reading is added only if `kTGHZ2013` (the 2013 standard) also lists it,
checking traditional characters through their simplified form. Readings are
compared after NFC normalization and are never removed.

**Phase 2 reports.**

- `traditional_components.csv` checks simplified headwords (OpenCC). It flags
  a component that OpenCC converts, and a decomposition copied from the
  traditional form. A copy is either identical (unless cjkvi-ids gives both
  characters the same IDS too, like 搖/摇), or identical once its components
  are simplified while cjkvi-ids shows a carried-over component missing
  (练 `⿰纟柬`). `suggested` is cjkvi-ids' mainland (G) decomposition: the
  first G-tagged one, else the untagged one. Circled numbers such as ④ are
  cjkvi's placeholders for components Unicode doesn't encode.
- `blank_phonetic.csv` lists pictophonetic entries with a blank phonetic or
  semantic, excluding Fix A's entries. Many blanks are deliberate (仅, 动).
- `leftover_fix_a.csv` lists the entries Fix A skipped or only partly fixed.
- `readings_not_in_xhc.csv` lists existing readings that `kXHC1983` lacks.
- `xhc_readings_held_back.csv` lists the `kXHC1983` readings Fix B didn't add.

Reports reflect the current `dictionary.jsonl`, so rows drop off as patches
resolve them.

`convert_to_jsonl.py` refuses to overwrite an existing `dictionary.jsonl`.
