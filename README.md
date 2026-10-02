# Character Decomposition Data

`dictionary.jsonl` is a Chinese character dictionary (9,574 entries, one JSON
object per line) flattened from Make Me a Hanzi, with fixes applied on top of
the untouched original.

| File | What it is |
|---|---|
| `dictionary.jsonl` | the fixed dictionary |
| `dictionary.original.jsonl` | the original, read-only. Never edit it. |
| `changelog.jsonl` | every change: `{"character", "field", "old", "new", "rule"}` |
| `corrections.jsonl` | manual patches, one per line |
| `review/*.csv` | problems that need a human decision |
| `phase1_fixes.py`, `phase2_reports.py`, `apply_corrections.py` | one script per phase |
| `common.py` | shared code: IDS parser, Unihan loader, validator |
| `rebuild.sh` | re-runs every phase from the original |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Only phase 2 needs the venv (for OpenCC); phases 1 and 3 use just the
standard library. The scripts download their source data into `sources/`
(git-ignored) on first use: `Unihan.zip` from unicode.org (this was built with Unicode 18.0.0) and
`ids.txt` from github.com/cjkvi/cjkvi-ids.

## Running

Every script is a dry run unless you pass `--write`. A write is validated
first, and nothing is written if validation fails. The checks: the line count
and character order match the original; every line parses, with the same
fields, field order and JSON formatting; and replaying `changelog.jsonl` over
the original reproduces the file exactly, with each rule touching only its
own fields. Running a script twice makes no new changes.

```bash
.venv/bin/python phase1_fixes.py --write       # 1. automated fixes
.venv/bin/python phase2_reports.py --write     # 2. review/*.csv (only reads the dictionary)
.venv/bin/python apply_corrections.py --write  # 3. manual patches, applied last so they win
```

To rebuild everything from `dictionary.original.jsonl` (refuses to run if
`dictionary.jsonl` or `changelog.jsonl` have uncommitted changes):

```bash
./rebuild.sh
```

## Adding corrections

Append patches to `corrections.jsonl`:

```json
{"character": "吓", "field": "pinyin", "old": ["xià"], "new": ["xià", "hè"], "note": "...", "source": "study 2026-10-02"}
```

Then dry-run, apply, refresh the reports, and commit (e.g. `corrections: 吓 辟 扛`):

```bash
python3 apply_corrections.py
python3 apply_corrections.py --write
.venv/bin/python phase2_reports.py --write
```

A patch applies when the field's current value, or its value in the original,
equals `old`. A patch whose `new` is already in effect is skipped silently.
Anything else is reported as a conflict and not applied. Allowed fields:
`definition`, `pinyin`, `decomposition`, `etymology_type`,
`etymology_semantic`, `etymology_phonetic`, `etymology_hint`. Re-running
phase 1 never touches a field a patch has set.

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
checking traditional characters through their simplified form. The rest go
to `review/xhc_readings_held_back.csv`. Readings are compared after NFC
normalization and are never removed.

**Phase 2 reports.**

- `traditional_components.csv` checks simplified headwords (OpenCC). It flags
  a component that OpenCC converts, and a decomposition copied from the
  traditional form. A copy is either identical, or identical once its
  components are simplified while cjkvi-ids shows a carried-over component
  missing (练 `⿰纟柬`). `suggested` is cjkvi-ids' mainland (G) decomposition:
  the first G-tagged one, else the untagged one. Circled numbers such as ④
  are cjkvi's placeholders for components Unicode doesn't encode.
- `blank_phonetic.csv` lists pictophonetic entries with a blank phonetic or
  semantic, excluding Fix A's entries. Many blanks are deliberate (仅, 动).
- `leftover_fix_a.csv` lists the entries Fix A skipped or only partly fixed.
- `readings_not_in_xhc.csv` lists existing readings that `kXHC1983` lacks.

Reports reflect the current `dictionary.jsonl`, so rows drop off as patches
resolve them.

**Don't run `convert_to_jsonl.py`.** It regenerates `dictionary.jsonl` from
`dictionary.json` and would discard every fix. If you do run it,
`git checkout dictionary.jsonl` restores the fixed file. `dictionary.json` and
`dictionary.db` (built by `build_db.py`) don't include these fixes.
