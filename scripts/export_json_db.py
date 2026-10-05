"""Carry the fixes in dictionary.jsonl into dictionary.json and dictionary.db.

dictionary.json is rebuilt from dictionary.original.json, the untouched nested
original: each field that differs between dictionary.jsonl and
dictionary.original.jsonl is written into its nested entry, and everything
else (matches, key order, formatting) stays as it was. Flattening the result
must give back dictionary.jsonl exactly, or nothing is written.
dictionary.db is then rebuilt from dictionary.json with build_db.py.

This runs automatically whenever a phase writes dictionary.jsonl; run it by
hand only to check or repair the derived files.

    python3 scripts/export_json_db.py            # dry run: report what would change
    python3 scripts/export_json_db.py --write    # write dictionary.json and dictionary.db
"""

import argparse
import json
import os
from typing import Any

import build_db
from common import DATA_DIR, DICT_PATH, GOLDEN_DIR, ORIGINAL_PATH, Entry, load_entries
from convert_to_jsonl import flatten_dict

JSON_PATH = os.path.join(DATA_DIR, "dictionary.json")
ORIGINAL_JSON_PATH = os.path.join(GOLDEN_DIR, "dictionary.original.json")
DB_PATH = os.path.join(DATA_DIR, "dictionary.db")

ENTRY_KEYS = ["character", "definition", "pinyin", "decomposition", "etymology", "radical", "matches"]
ETYMOLOGY_KEYS = ["type", "phonetic", "semantic", "hint"]


def with_changes(entry: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """A nested entry with flat field changes applied. Blank values are left
    out, as in the original, where a missing key means blank."""
    out = dict(entry)
    etymology = dict(out.get("etymology") or {})
    for field, value in changes.items():
        if field.startswith("etymology_"):
            key = field.removeprefix("etymology_")
            if value:
                etymology[key] = value
            else:
                etymology.pop(key, None)
        elif value or field == "pinyin":
            out[field] = value
        else:
            out.pop(field, None)
    if any(f.startswith("etymology_") for f in changes):
        if not etymology:
            out.pop("etymology", None)
        elif set(etymology) == set(entry.get("etymology") or {}):
            out["etymology"] = etymology  # same keys: keep their original order
        else:
            out["etymology"] = {k: etymology[k] for k in ETYMOLOGY_KEYS if k in etymology}
    order = [k for k in ENTRY_KEYS if k in out] + [k for k in out if k not in ENTRY_KEYS]
    return {k: out[k] for k in order}


def build(fixed: list[Entry]) -> tuple[list[dict[str, Any]], int]:
    """The nested entries for dictionary.json, and how many differ from the
    original. Raises ValueError unless they flatten back to `fixed` exactly."""
    with open(ORIGINAL_JSON_PATH, encoding="utf-8") as f:
        original_json = json.load(f)
    original_flat = load_entries(ORIGINAL_PATH)
    if not len(original_json) == len(original_flat) == len(fixed):
        raise ValueError("dictionary.original.json, dictionary.original.jsonl and dictionary.jsonl differ in length")
    out, changed = [], 0
    for nested, flat, new in zip(original_json, original_flat, fixed):
        if not nested["character"] == flat["character"] == new["character"]:
            raise ValueError(f"character order differs at {new['character']}")
        changes = {k: v for k, v in new.items() if v != flat[k]}
        out.append(with_changes(nested, changes) if changes else nested)
        changed += bool(changes)
    for nested, new in zip(out, fixed):
        if flatten_dict(nested) != new:
            raise ValueError(f"{new['character']}: dictionary.json entry does not flatten back to dictionary.jsonl")
    return out, changed


def serialize(entries: list[dict[str, Any]]) -> str:
    """The original file's format: indent=4, no trailing newline."""
    return json.dumps(entries, ensure_ascii=False, indent=4)


def write(entries: list[dict[str, Any]]) -> None:
    tmp = JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(serialize(entries))
    os.replace(tmp, JSON_PATH)
    tmp_db = DB_PATH + ".tmp"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    build_db.main(JSON_PATH, tmp_db)
    os.replace(tmp_db, DB_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write dictionary.json and dictionary.db (default: dry run)")
    args = ap.parse_args()

    entries, changed = build(load_entries(DICT_PATH))
    with open(JSON_PATH, encoding="utf-8") as f:
        in_sync = f.read() == serialize(entries)
    print(f"{changed} entries differ from dictionary.original.json; "
          f"dictionary.json is {'already in sync' if in_sync else 'out of date'}.")
    if args.write:
        write(entries)
        print("Wrote dictionary.json and rebuilt dictionary.db.")
    else:
        print("Dry run: nothing written. Re-run with --write to write dictionary.json and dictionary.db.")


if __name__ == "__main__":
    main()
