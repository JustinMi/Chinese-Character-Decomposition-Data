"""Phase 3: apply manual patches from corrections.jsonl.

One patch per line:
    {"character": "吓", "field": "pinyin", "old": ["xià"], "new": ["xià", "hè"],
     "note": "...", "source": "study 2026-10-02"}

For each patch, in file order:
  - the field already equals "new"      -> skipped silently
  - the field's current value, or its value in dictionary.original.jsonl,
    equals "old"                        -> set to "new", logged with rule "manual"
  - otherwise                           -> conflict, reported, not applied
A patch is also skipped silently when a later patch for the same character
and field has already taken effect, so re-running never replays a chain.

Patches win over the automated fixes. Allowed fields: definition, pinyin,
decomposition, etymology_type, etymology_semantic, etymology_phonetic,
etymology_hint.

Dry run by default; pass --write to apply.

    python3 apply_corrections.py            # dry run
    python3 apply_corrections.py --write    # apply, log, validate
"""

import argparse
import json
from typing import Any

from common import (
    CORRECTIONS_PATH,
    DICT_PATH,
    ETYMOLOGY_TYPES,
    MANUAL_FIELDS,
    ORIGINAL_PATH,
    change,
    commit_changes,
    fmt,
    load_changelog,
    load_entries,
    nfc,
    parse_ids,
)

REQUIRED_KEYS = ("character", "field", "old", "new")


def load_patches() -> list[tuple[int, dict[str, Any]]]:
    patches = []
    with open(CORRECTIONS_PATH, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                patches.append((n, json.loads(line)))
    return patches


def check_patch(p: Any, by: dict[str, dict[str, Any]]) -> str:
    """Why a patch is invalid, or '' if it's fine."""
    if not isinstance(p, dict):
        return "not a JSON object"
    missing = [k for k in REQUIRED_KEYS if k not in p]
    if missing:
        return f"missing {', '.join(missing)}"
    if p["field"] not in MANUAL_FIELDS:
        return f"field {p['field']!r} may not be patched (allowed: {', '.join(MANUAL_FIELDS)})"
    if p["character"] not in by:
        return f"character {p['character']!r} is not in the dictionary"
    for k in ("old", "new"):
        v = p[k]
        if p["field"] == "pinyin":
            if not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
                return f"{k} must be a list of readings"
        elif not isinstance(v, str):
            return f"{k} must be a string"
    if p["field"] == "etymology_type" and p["new"] not in ETYMOLOGY_TYPES:
        return f"etymology_type must be one of {sorted(ETYMOLOGY_TYPES)}"
    if p["field"] == "decomposition":
        try:
            parse_ids(p["new"])
        except ValueError as ex:
            return f"new decomposition is not valid IDS: {ex}"
    return ""


def normalized(field: str, value: Any) -> Any:
    return [nfc(x) for x in value] if field == "pinyin" else value


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="apply the patches (default: dry run)")
    args = ap.parse_args()

    entries = load_entries(DICT_PATH)
    by = {e["character"]: e for e in entries}
    original = {e["character"]: e for e in load_entries(ORIGINAL_PATH)}
    patches = load_patches()
    # Which rule last set each field, to say what a patch overrides.
    set_by = {(c["character"], c["field"]): c["rule"] for c in load_changelog()}

    applied: list[tuple[int, dict[str, Any], Any, str]] = []
    skipped: list[int] = []
    conflicts: list[tuple[int, dict[str, Any], Any]] = []
    rejected: list[tuple[int, str]] = []
    changes: list[dict[str, Any]] = []

    valid = []
    for n, p in patches:
        problem = check_patch(p, by)
        if problem:
            rejected.append((n, problem))
        else:
            valid.append((n, {**p, "old": normalized(p["field"], p["old"]), "new": normalized(p["field"], p["new"])}))

    for i, (n, p) in enumerate(valid):
        ch, field = p["character"], p["field"]
        e = by[ch]
        cur, orig = e[field], original[ch][field]
        later = [q for _, q in valid[i + 1:] if q["character"] == ch and q["field"] == field]
        if cur == p["new"] or any(q["new"] == cur for q in later):
            skipped.append(n)
        elif cur == p["old"] or orig == p["old"]:
            applied.append((n, p, cur, set_by.get((ch, field), "") if cur != orig else ""))
            changes.append(change(ch, field, cur, p["new"], "manual"))
            e[field] = p["new"]
            set_by[(ch, field)] = "manual"
        else:
            conflicts.append((n, p, cur))

    print(f"Phase 3 {'WRITE' if args.write else 'DRY RUN'}: {len(patches)} patches in corrections.jsonl")
    print()
    print(f"{'Applied' if args.write else 'Would apply'}: {len(applied)}")
    for n, p, cur, overrides in applied:
        orig = original[p["character"]][p["field"]]
        print(f"  line {n:3d}  {p['character']} {p['field']}: {fmt(cur)} -> {fmt(p['new'])}")
        if overrides == "manual":
            print(f"            replaces an earlier manual patch (original: {fmt(orig)})")
        elif overrides:
            print(f"            overrides {overrides} (original: {fmt(orig)})")
        if p["field"] == "pinyin":
            dropped = [r for r in cur if r not in p["new"]]
            if dropped:
                lost_original = [r for r in dropped if r in orig]
                print(f"            note: drops reading(s) {' '.join(dropped)}"
                      + (f", including original {' '.join(lost_original)}" if lost_original else ""))
        if p.get("note"):
            print(f"            ({p['note']})")
    print()
    print(f"Skipped, already in effect: {len(skipped)}")
    print()
    print(f"Conflicts (not applied): {len(conflicts)}")
    for n, p, cur in conflicts:
        print(f"  line {n:3d}  {p['character']} {p['field']}: patch expects {fmt(p['old'])}, "
              f"but the current value is {fmt(cur)} and the original is {fmt(original[p['character']][p['field']])}"
              f"; wanted {fmt(p['new'])}")
    print()
    print(f"Rejected: {len(rejected)}")
    for n, problem in rejected:
        print(f"  line {n:3d}  {problem}")
    print()

    if args.write:
        commit_changes(entries, changes)
    else:
        print("Dry run: nothing written. Re-run with --write to apply.")


if __name__ == "__main__":
    main()
