"""Phase 3: manual patches in data/corrections.jsonl.

    python3 scripts/apply_corrections.py check BATCH  # validate new patch lines
    python3 scripts/apply_corrections.py add BATCH    # validate, then append to corrections.jsonl
    python3 scripts/apply_corrections.py              # dry run of every patch
    python3 scripts/apply_corrections.py --write      # apply, log, validate, refresh json/db
    (`apply` and `apply --write` work too.)

One patch per line:
    {"character": "吓", "field": "pinyin", "old": ["xià"], "new": ["xià", "hè"],
     "note": "...", "source": "study 2026-10-02"}

For each patch in corrections.jsonl, in file order:
  - the field already equals "new"     -> already applied, skipped silently
  - the current value, or the value in dictionary.original.jsonl, equals
    "old"                              -> APPLY: set to "new", logged as rule "manual"
  - otherwise                          -> CONFLICT, never forced
A patch is also skipped when a later patch for the same character and field
has already taken effect, so re-running never replays a chain.

Patches never remove a pinyin reading, including one an automated fix added,
unless the patch says "allow_removal": true. Allowed fields: definition,
pinyin, decomposition, etymology_type, etymology_semantic, etymology_phonetic,
etymology_hint. Batch files may contain blank lines and markdown code fences.

Exit status: 0 clean, 1 conflicts or invalid patches, 2 missing file.
"""

import argparse
import json
import os
import sys
from typing import Any

from common import (
    CORRECTIONS_PATH,
    DICT_PATH,
    ETYMOLOGY_TYPES,
    MANUAL_FIELDS,
    ORIGINAL_PATH,
    ROOT,
    change,
    commit_changes,
    dumps,
    fmt,
    load_changelog,
    load_entries,
    nfc,
    parse_ids,
)

REQUIRED_KEYS = ("character", "field", "old", "new")
Patch = dict[str, Any]


def parse_lines(text: str, label: str) -> tuple[list[tuple[str, Any, str]], list[str]]:
    """(where, patch, raw line) for each JSON line, skipping blank lines and
    the code fences that come with text pasted from chat."""
    patches, errors = [], []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        try:
            patches.append((f"{label}:{n}", json.loads(s), s))
        except json.JSONDecodeError as ex:
            errors.append(f"{label}:{n}: not valid JSON ({ex.msg})")
    return patches, errors


def normalized(field: str, value: Any) -> Any:
    return [nfc(x) for x in value] if field == "pinyin" else value


def check_patch(p: Any, by: dict[str, dict[str, Any]]) -> list[str]:
    """Why a patch is invalid; an empty list if it's fine."""
    if not isinstance(p, dict):
        return ["not a JSON object"]
    missing = [k for k in REQUIRED_KEYS if k not in p]
    if missing:
        return [f"missing {', '.join(missing)}"]
    field = p["field"]
    if field not in MANUAL_FIELDS:
        return [f"field {field!r} may not be patched (allowed: {', '.join(MANUAL_FIELDS)})"]
    problems = []
    if p["character"] not in by:
        problems.append(f"character {p['character']!r} is not in the dictionary")
    for k in ("old", "new"):
        v = p[k]
        if field == "pinyin":
            if not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
                problems.append(f"{k} must be a list of readings")
        elif not isinstance(v, str):
            problems.append(f"{k} must be a string")
    if not isinstance(p.get("allow_removal", False), bool):
        problems.append("allow_removal must be true or false")
    if problems:
        return problems
    old, new = normalized(field, p["old"]), normalized(field, p["new"])
    if old == new:
        problems.append("'old' and 'new' are identical")
    if field == "pinyin" and not p.get("allow_removal"):
        dropped = [r for r in old if r not in new]
        if dropped:
            problems.append(f"removes reading(s) {' '.join(dropped)}; patches never remove readings "
                            'unless they say "allow_removal": true')
    if field == "etymology_type" and new not in ETYMOLOGY_TYPES:
        problems.append(f"etymology_type must be one of {sorted(ETYMOLOGY_TYPES)}")
    if field == "decomposition":
        try:
            parse_ids(new)
        except ValueError as ex:
            problems.append(f"new decomposition is not valid IDS: {ex}")
    return problems


def read_corrections() -> tuple[list[tuple[str, Any, str]], list[str]]:
    if not os.path.exists(CORRECTIONS_PATH):
        return [], []
    with open(CORRECTIONS_PATH, encoding="utf-8") as f:
        return parse_lines(f.read(), "corrections.jsonl")


def cmd_check(batch_path: str, append: bool) -> int:
    if not os.path.exists(batch_path):
        print(f"error: {batch_path} not found", file=sys.stderr)
        return 2
    by = {e["character"]: e for e in load_entries(DICT_PATH)}
    with open(batch_path, encoding="utf-8") as f:
        patches, errors = parse_lines(f.read(), os.path.basename(batch_path))
    # A patch with the same character, field, old and new as one already
    # recorded is a duplicate, whatever its note says.
    def key(p: Patch) -> str:
        return dumps([p["character"], p["field"], normalized(p["field"], p["old"]), normalized(p["field"], p["new"])])

    existing = {key(p) for _, p, _ in read_corrections()[0] if not check_patch(p, by)}
    good: list[str] = []
    dupes = 0
    for where, p, _ in patches:
        problems = check_patch(p, by)
        if problems:
            errors += [f"{where}: {problem}" for problem in problems]
        elif key(p) in existing:
            dupes += 1
        else:
            existing.add(key(p))
            good.append(dumps(p))
    print(f"valid new patches: {len(good)}   duplicates skipped: {dupes}   invalid: {len(errors)}")
    for e in errors:
        print(f"  INVALID  {e}")
    if append and errors:
        print("nothing appended: fix the invalid lines first (the batch is all-or-nothing)")
    elif append and good:
        needs_newline = False
        if os.path.exists(CORRECTIONS_PATH) and os.path.getsize(CORRECTIONS_PATH):
            with open(CORRECTIONS_PATH, "rb") as f:
                f.seek(-1, os.SEEK_END)
                needs_newline = f.read(1) != b"\n"
        with open(CORRECTIONS_PATH, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n" if needs_newline else "")
            f.writelines(line + "\n" for line in good)
        print(f"appended {len(good)} patch(es) to {os.path.relpath(CORRECTIONS_PATH, ROOT)}")
    return 1 if errors else 0


def cmd_apply(write: bool) -> int:
    entries = load_entries(DICT_PATH)
    by = {e["character"]: e for e in entries}
    original = {e["character"]: e for e in load_entries(ORIGINAL_PATH)}
    patches, errors = read_corrections()
    # Which rule last set each field, to say what a patch overrides.
    set_by = {(c["character"], c["field"]): c["rule"] for c in load_changelog()}

    valid: list[tuple[str, Patch]] = []
    for where, p, _ in patches:
        problems = check_patch(p, by)
        if problems:
            errors += [f"{where}: {problem}" for problem in problems]
        else:
            valid.append((where, {**p, "old": normalized(p["field"], p["old"]),
                                  "new": normalized(p["field"], p["new"])}))

    applied: list[tuple[str, Patch, Any, str]] = []
    conflicts: list[tuple[str, Patch, Any, str]] = []
    already = 0
    changes: list[dict[str, Any]] = []
    for i, (where, p) in enumerate(valid):
        ch, field = p["character"], p["field"]
        cur, orig = by[ch][field], original[ch][field]
        later = [q for _, q in valid[i + 1:] if q["character"] == ch and q["field"] == field]
        dropped = []
        if field == "pinyin" and not p.get("allow_removal"):
            dropped = [r for r in cur if nfc(r) not in p["new"]]
        if cur == p["new"] or any(q["new"] == cur for q in later):
            already += 1
        elif dropped:
            conflicts.append((where, p, cur, f"would remove reading(s) {' '.join(dropped)}, "
                                             "which were added after the patch was written"))
        elif cur == p["old"] or orig == p["old"]:
            applied.append((where, p, cur, set_by.get((ch, field), "") if cur != orig else ""))
            changes.append(change(ch, field, cur, p["new"], "manual"))
            by[ch][field] = p["new"]
            set_by[(ch, field)] = "manual"
        else:
            conflicts.append((where, p, cur, ""))

    print(f"[{'WRITE' if write else 'DRY RUN'}] {'applied' if write else 'to apply'}: {len(applied)}   "
          f"already applied: {already}   conflicts: {len(conflicts)}   invalid: {len(errors)}")
    for where, p, cur, overrides in applied:
        orig = original[p["character"]][p["field"]]
        print(f"  APPLY    {where}  {p['character']} {p['field']}: {fmt(cur)} -> {fmt(p['new'])}")
        if overrides == "manual":
            print(f"           replaces an earlier manual patch (original: {fmt(orig)})")
        elif overrides:
            print(f"           overrides {overrides} (original: {fmt(orig)})")
        if p.get("note"):
            print(f"           ({p['note']})")
    for where, p, cur, reason in conflicts:
        print(f"  CONFLICT {where}  {p['character']} {p['field']}")
        if reason:
            print(f"           reason            = {reason}")
        print(f"           patch expects old = {fmt(p['old'])}")
        print(f"           current value     = {fmt(cur)}")
        print(f"           original value    = {fmt(original[p['character']][p['field']])}")
        print(f"           patch new         = {fmt(p['new'])}")
    for e in errors:
        print(f"  INVALID  {e}")

    if write:
        commit_changes(entries, changes)
    else:
        print("Dry run: nothing written. Re-run with --write to apply.")
    return 1 if conflicts or errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", dest="write_top", action="store_true", help="apply the patches (default: dry run)")
    sub = ap.add_subparsers(dest="cmd")
    for name, help_text in (("check", "validate new patch lines"), ("add", "validate, then append them")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("batch", help="file of new patch lines")
    s = sub.add_parser("apply", help="apply every patch in corrections.jsonl (dry run unless --write)")
    s.add_argument("--write", action="store_true", help="apply the patches (default: dry run)")
    args = ap.parse_args()

    for path in (DICT_PATH, ORIGINAL_PATH):
        if not os.path.exists(path):
            print(f"error: {os.path.relpath(path, ROOT)} not found", file=sys.stderr)
            return 2
    if args.cmd in ("check", "add"):
        return cmd_check(args.batch, append=args.cmd == "add")
    return cmd_apply(args.write_top or getattr(args, "write", False))


if __name__ == "__main__":
    sys.exit(main())
