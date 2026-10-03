"""Shared helpers for the dictionary fix pipeline.

Phase scripts:
    phase1_fixes.py       automated fixes (Fix A, Fix B)
    phase2_reports.py     review reports in review/
    apply_corrections.py  manual patches from corrections.jsonl

Every write goes through `commit_changes`, which validates the new state
before replacing dictionary.jsonl, validates the file again afterwards, and
carries the fixes into dictionary.json and dictionary.db (export_json_db.py).
"""

import json
import os
import re
import sys
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
DICT_PATH = os.path.join(ROOT, "dictionary.jsonl")
ORIGINAL_PATH = os.path.join(ROOT, "dictionary.original.jsonl")
CHANGELOG_PATH = os.path.join(ROOT, "changelog.jsonl")
CORRECTIONS_PATH = os.path.join(ROOT, "corrections.jsonl")
REVIEW_DIR = os.path.join(ROOT, "review")
SOURCES_DIR = os.path.join(ROOT, "sources")

UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
UNIHAN_ZIP = os.path.join(SOURCES_DIR, "Unihan.zip")
CJKVI_IDS_URL = "https://raw.githubusercontent.com/cjkvi/cjkvi-ids/master/ids.txt"
CJKVI_IDS_PATH = os.path.join(SOURCES_DIR, "cjkvi-ids.txt")

# Field order as it appears in every line of dictionary.jsonl.
FIELDS = [
    "character",
    "definition",
    "pinyin",
    "decomposition",
    "etymology_type",
    "etymology_phonetic",
    "etymology_semantic",
    "etymology_hint",
    "radical",
]
ETYMOLOGY_TYPES = {"", "ideographic", "pictographic", "pictophonetic"}

# Fields each changelog rule is allowed to touch.
MANUAL_FIELDS = [
    "definition",
    "pinyin",
    "decomposition",
    "etymology_type",
    "etymology_semantic",
    "etymology_phonetic",
    "etymology_hint",
]
RULE_FIELDS = {
    "fix_a": {"etymology_type", "etymology_phonetic", "etymology_semantic"},
    "fix_b": {"pinyin"},
    "manual": set(MANUAL_FIELDS),
}
CHANGELOG_KEYS = ["character", "field", "old", "new", "rule"]

Entry = dict[str, Any]


# ---------------------------------------------------------------------------
# Reading and writing


def dumps(obj: Any) -> str:
    """Serialize exactly like the original file: default separators, no escaping."""
    return json.dumps(obj, ensure_ascii=False)


def load_entries(path: str) -> list[Entry]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_changelog() -> list[dict[str, Any]]:
    if not os.path.exists(CHANGELOG_PATH):
        return []
    with open(CHANGELOG_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def change(character: str, field: str, old: Any, new: Any, rule: str) -> dict[str, Any]:
    return {"character": character, "field": field, "old": old, "new": new, "rule": rule}


def manually_patched(changelog: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """(character, field) pairs set by a manual patch. Automated fixes leave
    these alone so that re-running an earlier phase can't undo a patch."""
    return {(c["character"], c["field"]) for c in changelog if c["rule"] == "manual"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ---------------------------------------------------------------------------
# IDS parsing

TERNARY_OPERATORS = {"⿲", "⿳"}


def is_ids_operator(ch: str) -> bool:
    return 0x2FF0 <= ord(ch) <= 0x2FFB


IDSNode = str | tuple[str, list["IDSNode"]]


def parse_ids(s: str) -> IDSNode:
    """Parse an IDS string into a tree. Leaves are single characters ('?' is an
    unknown component); ⿲ and ⿳ take three parts, every other operator two."""
    pos = 0

    def node() -> IDSNode:
        nonlocal pos
        if pos >= len(s):
            raise ValueError(f"truncated IDS {s!r}")
        ch = s[pos]
        pos += 1
        if is_ids_operator(ch):
            arity = 3 if ch in TERNARY_OPERATORS else 2
            return (ch, [node() for _ in range(arity)])
        return ch

    tree = node()
    if pos != len(s):
        raise ValueError(f"trailing characters in IDS {s!r}: {s[pos:]!r}")
    return tree


def ids_text(node: IDSNode) -> str:
    if isinstance(node, str):
        return node
    return node[0] + "".join(ids_text(child) for child in node[1])


def top_level_parts(s: str) -> list[str]:
    """The IDS strings of the top-level parts ([s] if s has no operator)."""
    tree = parse_ids(s)
    if isinstance(tree, str):
        return [tree]
    return [ids_text(child) for child in tree[1]]


def ids_components(s: str) -> list[str]:
    """Every component character in an IDS string (no operators, no '?')."""
    return [ch for ch in s if not is_ids_operator(ch) and ch != "?"]


# ---------------------------------------------------------------------------
# Third-party source data


def ensure_download(url: str, path: str) -> None:
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"Downloading {url} -> {os.path.relpath(path, ROOT)}", file=sys.stderr)
    tmp = path + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, path)


# 'location:reading' tokens. Several locations sharing one reading are joined
# with commas ('0758.081,0758.091:ma'); kXHC1983 locations may end in '*'.
XHC_TOKEN = re.compile(r"^\d{4}\.\d{3}\*?(?:,\d{4}\.\d{3}\*?)*:(\S+)$")
TGHZ_TOKEN = re.compile(r"^\d{3}\.\d{3}(?:,\d{3}\.\d{3})*:(\S+)$")


def parse_located_readings(value: str, token_re: re.Pattern[str]) -> list[str]:
    """The distinct readings of a space-separated 'location:reading' value, in order."""
    readings: list[str] = []
    for token in value.split(" "):
        m = token_re.match(token)
        if not m:
            raise ValueError(f"unexpected token {token!r} in {value!r}")
        reading = nfc(m.group(1))
        if reading not in readings:
            readings.append(reading)
    return readings


def codepoint_char(cp: str) -> str:
    return chr(int(cp.removeprefix("U+"), 16))


@dataclass
class Unihan:
    version: str
    xhc1983: dict[str, list[str]]  # kXHC1983: 现代汉语词典 (1983) readings
    mandarin: dict[str, str]  # first kMandarin value: the most customary reading
    tghz2013: dict[str, set[str]]  # kTGHZ2013: 通用规范汉字字典 (2013) readings
    simplified: dict[str, list[str]]  # kSimplifiedVariant, excluding the character itself

    def modern_readings(self, ch: str) -> tuple[set[str] | None, str]:
        """kTGHZ2013 readings for ch, or for its simplified form(s) when ch is a
        traditional character the 2013 standard doesn't list. (None, "") if
        there is no data either way."""
        if ch in self.tghz2013:
            return self.tghz2013[ch], ch
        via = [s for s in self.simplified.get(ch, []) if s in self.tghz2013]
        if via:
            return set().union(*(self.tghz2013[s] for s in via)), "".join(via)
        return None, ""


def load_unihan() -> Unihan:
    ensure_download(UNIHAN_URL, UNIHAN_ZIP)
    u = Unihan("unknown", {}, {}, {}, {})
    with zipfile.ZipFile(UNIHAN_ZIP) as z:
        for name in ("Unihan_Readings.txt", "Unihan_Variants.txt"):
            with z.open(name) as f:
                for raw in f:
                    line = raw.decode("utf-8").rstrip("\n")
                    if line.startswith("# Unicode Version"):
                        u.version = line.removeprefix("# Unicode Version").strip()
                    if not line or line.startswith("#"):
                        continue
                    codepoint, field, value = line.split("\t")
                    ch = codepoint_char(codepoint)
                    if field == "kXHC1983":
                        u.xhc1983[ch] = parse_located_readings(value, XHC_TOKEN)
                    elif field == "kMandarin":
                        u.mandarin[ch] = nfc(value.split(" ")[0])
                    elif field == "kTGHZ2013":
                        u.tghz2013[ch] = set(parse_located_readings(value, TGHZ_TOKEN))
                    elif field == "kSimplifiedVariant":
                        u.simplified[ch] = [s for s in map(codepoint_char, value.split(" ")) if s != ch]
    return u


# ---------------------------------------------------------------------------
# Validation


def validate_lines(
    lines: list[str],
    original: list[Entry],
    changelog: list[dict[str, Any]],
) -> list[str]:
    """Check a would-be dictionary.jsonl (as lines, without newlines) against
    the original and the full changelog. Returns a list of problems."""
    errors: list[str] = []
    if len(lines) != len(original):
        errors.append(f"line count {len(lines)} != original {len(original)}")
        return errors

    entries: list[Entry] = []
    for i, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
        except json.JSONDecodeError as ex:
            errors.append(f"line {i}: does not parse: {ex}")
            continue
        if not isinstance(e, dict) or list(e.keys()) != FIELDS:
            errors.append(f"line {i}: wrong fields or field order")
            continue
        if dumps(e) != line:
            errors.append(f"line {i}: formatting differs from json.dumps(ensure_ascii=False)")
        if not (isinstance(e["pinyin"], list) and all(isinstance(p, str) for p in e["pinyin"])):
            errors.append(f"line {i}: pinyin is not a list of strings")
        for k in FIELDS:
            if k != "pinyin" and not isinstance(e[k], str):
                errors.append(f"line {i}: {k} is not a string")
        entries.append(e)
    if errors:
        return errors

    chars = [e["character"] for e in entries]
    if len(set(chars)) != len(chars):
        errors.append("character values are not unique")
    if chars != [e["character"] for e in original]:
        errors.append("character order differs from the original")
    if errors:
        return errors

    # Replay the changelog over the original; it must reproduce the new state
    # exactly, and every rule may only touch its own fields.
    state = {e["character"]: dict(e) for e in original}
    touched: dict[tuple[str, str], set[str]] = {}
    for n, c in enumerate(changelog, 1):
        if list(c.keys()) != CHANGELOG_KEYS:
            errors.append(f"changelog line {n}: keys {list(c.keys())}")
            continue
        ch, field, rule = c["character"], c["field"], c["rule"]
        if rule not in RULE_FIELDS:
            errors.append(f"changelog line {n}: unknown rule {rule!r}")
            continue
        if field not in RULE_FIELDS[rule]:
            errors.append(f"changelog line {n}: rule {rule} may not change {field}")
            continue
        if ch not in state:
            errors.append(f"changelog line {n}: unknown character {ch}")
            continue
        if state[ch][field] != c["old"]:
            errors.append(f"changelog line {n}: old value does not match replay state for {ch}.{field}")
        if c["old"] == c["new"]:
            errors.append(f"changelog line {n}: no-op change for {ch}.{field}")
        if rule == "fix_b":
            lost = {nfc(p) for p in c["old"]} - {nfc(p) for p in c["new"]}
            if lost:
                errors.append(f"changelog line {n}: fix_b removed reading(s) {sorted(lost)} from {ch}")
        state[ch][field] = c["new"]
        touched.setdefault((ch, field), set()).add(rule)

    for orig, e in zip(original, entries):
        ch = e["character"]
        for k in FIELDS:
            if e[k] == orig[k]:
                continue
            if (ch, k) not in touched:
                errors.append(f"{ch}.{k} differs from the original but no rule logged it")
            elif e[k] != state[ch][k]:
                errors.append(f"{ch}.{k} does not match the changelog replay")
        for k in ("character", "radical"):
            if e[k] != orig[k]:
                errors.append(f"{ch}.{k} must never change")
    return errors


def validate_file() -> list[str]:
    """Validate dictionary.jsonl on disk."""
    with open(DICT_PATH, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as ex:
        return [f"not valid UTF-8: {ex}"]
    if not text.endswith("\n"):
        return ["file does not end with a newline"]
    lines = text[:-1].split("\n")
    return validate_lines(lines, load_entries(ORIGINAL_PATH), load_changelog())


def commit_changes(entries: list[Entry], new_changes: list[dict[str, Any]]) -> None:
    """Validate, then atomically replace dictionary.jsonl, append to the
    changelog, and carry the fixes into dictionary.json and dictionary.db.
    Nothing is written if validation fails."""
    if not new_changes:
        errors = validate_file()
        if errors:
            raise SystemExit("VALIDATION FAILED:\n  " + "\n  ".join(errors[:50]))
        print("No changes; nothing written. Existing file validates.")
        return
    lines = [dumps(e) for e in entries]
    changelog = load_changelog() + new_changes
    errors = validate_lines(lines, load_entries(ORIGINAL_PATH), changelog)
    if errors:
        raise SystemExit("Validation failed, nothing written:\n  " + "\n  ".join(errors[:50]))
    import export_json_db  # imported here because it imports this module

    try:
        exported, _ = export_json_db.build(entries)
    except ValueError as ex:
        raise SystemExit(f"dictionary.json export failed, nothing written: {ex}")

    tmp = DICT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, DICT_PATH)
    with open(CHANGELOG_PATH, "a", encoding="utf-8", newline="\n") as f:
        for c in new_changes:
            f.write(dumps(c) + "\n")

    errors = validate_file()
    if errors:
        raise SystemExit("POST-WRITE VALIDATION FAILED:\n  " + "\n  ".join(errors[:50]))
    export_json_db.write(exported)
    print(f"Wrote {os.path.relpath(DICT_PATH, ROOT)} and {len(new_changes)} changelog line(s); validation passed. "
          "Updated dictionary.json and dictionary.db.")


def fmt(v: Any) -> str:
    return dumps(v) if isinstance(v, list) else repr(v) if v == "" else v
