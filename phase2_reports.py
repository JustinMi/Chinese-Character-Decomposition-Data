"""Phase 2: review reports. Detects problems that need a human decision and
writes one CSV per check to review/. Never changes the dictionary.

    traditional_components.csv  simplified headwords decomposed with traditional parts
    blank_phonetic.csv          pictophonetic entries with a blank phonetic or semantic
    leftover_fix_a.csv          entries Fix A skipped or only partly fixed
    readings_not_in_xhc.csv     existing readings that kXHC1983 doesn't list
    xhc_readings_held_back.csv  kXHC1983 readings Fix B didn't add (not in kTGHZ2013)

Reports reflect the current dictionary.jsonl, so rows drop off as patches
resolve them. Needs OpenCC (see requirements.txt).

Dry run by default; pass --write to write the CSVs.

    .venv/bin/python phase2_reports.py            # dry run
    .venv/bin/python phase2_reports.py --write    # write review/*.csv
"""

import argparse
import csv
import os
import random
import re
from typing import Any

try:
    import opencc
except ImportError:
    raise SystemExit("OpenCC is missing: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")

from common import (
    CJKVI_IDS_PATH,
    CJKVI_IDS_URL,
    DICT_PATH,
    ORIGINAL_PATH,
    REVIEW_DIR,
    ROOT,
    Entry,
    Unihan,
    ensure_download,
    ids_components,
    is_ids_operator,
    load_entries,
    load_unihan,
    validate_file,
)
from phase1_fixes import analyze_fix_a, analyze_fix_b

Row = dict[str, Any]

COLUMNS = {
    "traditional_components.csv": [
        "character", "check", "reason", "decomposition", "traditional", "traditional_decomposition",
        "suggested", "etymology_phonetic", "etymology_semantic",
    ],
    "blank_phonetic.csv": [
        "character", "reason", "decomposition", "etymology_semantic", "etymology_phonetic",
        "etymology_hint", "traditional", "traditional_decomposition", "traditional_phonetic",
        "traditional_semantic",
    ],
    "leftover_fix_a.csv": [
        "character", "outcome", "reason", "phonetic_from_hint", "decomposition", "top_level_parts",
        "note", "original_type", "etymology_type", "etymology_phonetic", "etymology_semantic",
        "etymology_hint",
    ],
    "readings_not_in_xhc.csv": [
        "character", "not_in_xhc", "pinyin", "kXHC1983", "kTGHZ2013", "kTGHZ2013_via", "kMandarin",
        "definition",
    ],
    "xhc_readings_held_back.csv": [
        "character", "held_back", "pinyin", "kXHC1983", "kTGHZ2013", "kTGHZ2013_via", "kMandarin",
        "definition",
    ],
}


# ---------------------------------------------------------------------------
# OpenCC and cjkvi-ids


class Chinese:
    def __init__(self) -> None:
        self.t2s_cc = opencc.OpenCC("t2s")
        self.s2t_cc = opencc.OpenCC("s2t")
        # All traditional candidates for one-to-many mappings (发 -> 發 髮).
        path = os.path.join(os.path.dirname(opencc.__file__), "dictionary", "STCharacters.txt")
        self.st: dict[str, list[str]] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                simplified, traditional = line.rstrip("\n").split("\t")
                self.st[simplified] = traditional.split(" ")

    def t2s(self, ch: str) -> str:
        return self.t2s_cc.convert(ch)

    def is_simplified_headword(self, ch: str) -> bool:
        """A simplified form with a distinct traditional form (拣, not 扛)."""
        return self.t2s(ch) == ch and self.s2t_cc.convert(ch) != ch

    def traditional_forms(self, ch: str) -> list[str]:
        forms = self.st.get(ch) or [self.s2t_cc.convert(ch)]
        return [t for t in forms if t != ch]

    def simplify_components(self, ids: str) -> str:
        return "".join(c if is_ids_operator(c) or c == "?" else self.t2s(c) for c in ids)


class CjkviIds:
    """cjkvi-ids decompositions. Region tags like [GTJKV] mark glyph-specific
    IDS; an untagged IDS applies to every region."""

    def __init__(self) -> None:
        ensure_download(CJKVI_IDS_URL, CJKVI_IDS_PATH)
        self.ids: dict[str, list[str]] = {}
        with open(CJKVI_IDS_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith(("#", ";")):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    self.ids[parts[1]] = parts[2:]

    def mainland(self, ch: str) -> str:
        """The mainland-China (G) decomposition: the first G-tagged IDS, else the
        first untagged one. '' if none, or if cjkvi treats ch as atomic."""
        variants = self.ids.get(ch, [])
        for v in variants:
            m = re.fullmatch(r"(.*)\[([A-Z]+)\]", v)
            if m and "G" in m.group(2):
                return "" if m.group(1) == ch else m.group(1)
        for v in variants:
            if not re.search(r"\[[A-Z]+\]$", v):
                return "" if v == ch else v
        return ""

    def reachable(self, ids: str) -> set[str]:
        """Every component in ids, expanded recursively through cjkvi G
        decompositions (so 浏 = ⿰汶刂 still contains 氵)."""
        seen: set[str] = set()
        stack = ids_components(ids)
        while stack:
            c = stack.pop()
            if c not in seen:
                seen.add(c)
                stack.extend(ids_components(self.mainland(c)))
        return seen


# ---------------------------------------------------------------------------
# Reports


def traditional_components(entries: list[Entry], zh: Chinese, cjkvi: CjkviIds) -> list[Row]:
    by = {e["character"]: e for e in entries}
    rows = []
    for e in entries:
        h, dec = e["character"], e["decomposition"]
        if not zh.is_simplified_headword(h):
            continue
        checks, reasons = [], []
        compared = ""

        converted = [(c, zh.t2s(c)) for c in dict.fromkeys(ids_components(dec)) if zh.t2s(c) != c]
        if converted:
            checks.append("traditional_component")
            reasons += [f"component {c} converts to {s}" for c, s in converted]

        for t in zh.traditional_forms(h):
            if t not in by or not ids_components(dec):  # '?' == '?' is not a copy
                continue
            tdec = by[t]["decomposition"]
            if tdec == dec:
                checks.append("copied_decomposition")
                reasons.append(f"identical to {t}'s decomposition")
                compared = t
                break
            # A copy that only swapped in simplified radicals (練 ⿰糹柬 ->
            # 练 ⿰纟柬). Flag it when a component carried over unchanged is
            # gone from cjkvi's mainland glyph of the simplified character.
            if zh.simplify_components(tdec) == dec:
                g_s, g_t = cjkvi.mainland(h), cjkvi.mainland(t)
                if g_s and g_t:
                    kept = set(ids_components(dec)) & set(ids_components(tdec))
                    in_s, in_t = cjkvi.reachable(g_s), cjkvi.reachable(g_t)
                    gone = sorted(c for c in kept if c in in_t and c not in in_s)
                    if gone:
                        checks.append("copied_decomposition")
                        reasons.append(
                            f"{t}'s decomposition {tdec} with simplified components; "
                            f"cjkvi's mainland glyph {g_s} has no {' '.join(gone)}"
                        )
                        compared = t
                        break

        if checks:
            t = compared or next((t for t in zh.traditional_forms(h) if t in by), "")
            rows.append({
                "character": h,
                "check": "+".join(checks),
                "reason": "; ".join(reasons),
                "decomposition": dec,
                "traditional": t,
                "traditional_decomposition": by[t]["decomposition"] if t in by else "",
                "suggested": cjkvi.mainland(h),
                "etymology_phonetic": e["etymology_phonetic"],
                "etymology_semantic": e["etymology_semantic"],
            })
    return rows


def fix_a_leftovers(entries: list[Entry], original: list[Entry]) -> list[tuple[Entry, Entry, Any]]:
    """(current, original, FixA result) for entries Fix A skipped or partly
    fixed that no later change has resolved."""
    out = []
    for cur, orig in zip(entries, original):
        result = analyze_fix_a(orig)
        if result is None or result.outcome == "full":
            continue
        if result.outcome == "partial" and cur["etymology_semantic"]:
            continue
        if result.outcome == "skipped" and (
            cur["etymology_type"] != orig["etymology_type"] or cur["etymology_phonetic"]
        ):
            continue
        out.append((cur, orig, result))
    return out


def inside_note(phonetic: str, dec: str, by: dict[str, Entry]) -> str:
    """Where the phonetic hides one or more levels down, via the dictionary's
    own decompositions (莅 ⿱艹位: 立 is inside 位 = ⿰亻立)."""
    for top in dict.fromkeys(ids_components(dec)):
        seen: set[str] = set()
        stack = [top]
        while stack:
            c = stack.pop()
            if c in seen or c not in by:
                continue
            seen.add(c)
            sub = ids_components(by[c]["decomposition"])
            if phonetic in sub:
                return f"{phonetic} appears inside {top} ({by[top]['decomposition']})"
            stack.extend(sub)
    return ""


def leftover_fix_a(entries: list[Entry], original: list[Entry]) -> list[Row]:
    by = {e["character"]: e for e in entries}
    rows = []
    for cur, orig, r in fix_a_leftovers(entries, original):
        note = ""
        if r.outcome == "skipped" and r.phonetic:
            note = inside_note(r.phonetic, cur["decomposition"], by)
        elif "nested" in r.reason:
            note = next((f"{r.phonetic} is inside {p}" for p in r.parts if r.phonetic in p), "")
        rows.append({
            "character": cur["character"],
            "outcome": r.outcome,
            "reason": r.reason,
            "phonetic_from_hint": r.phonetic,
            "decomposition": cur["decomposition"],
            "top_level_parts": " ".join(r.parts),
            "note": note,
            "original_type": orig["etymology_type"],
            "etymology_type": cur["etymology_type"],
            "etymology_phonetic": cur["etymology_phonetic"],
            "etymology_semantic": cur["etymology_semantic"],
            "etymology_hint": cur["etymology_hint"],
        })
    return rows


def blank_phonetic(entries: list[Entry], original: list[Entry], zh: Chinese) -> list[Row]:
    by = {e["character"]: e for e in entries}
    rows = []
    for e, orig in zip(entries, original):
        if e["etymology_type"] != "pictophonetic":
            continue
        if analyze_fix_a(orig) is not None:  # relabeled by Fix A: see leftover_fix_a.csv
            continue
        blank = [k for k in ("phonetic", "semantic") if not e[f"etymology_{k}"]]
        if not blank:
            continue
        t = ""
        if zh.is_simplified_headword(e["character"]):
            t = next((t for t in zh.traditional_forms(e["character"]) if t in by), "")
        rows.append({
            "character": e["character"],
            "reason": "blank " + " and ".join(blank),
            "decomposition": e["decomposition"],
            "etymology_semantic": e["etymology_semantic"],
            "etymology_phonetic": e["etymology_phonetic"],
            "etymology_hint": e["etymology_hint"],
            "traditional": t,
            "traditional_decomposition": by[t]["decomposition"] if t else "",
            "traditional_phonetic": by[t]["etymology_phonetic"] if t else "",
            "traditional_semantic": by[t]["etymology_semantic"] if t else "",
        })
    return rows


def modern_columns(ch: str, unihan: Unihan) -> dict[str, str]:
    modern, via = unihan.modern_readings(ch)
    return {
        "kTGHZ2013": " ".join(sorted(modern)) if modern is not None else "",
        "kTGHZ2013_via": via if via and via != ch else "",
        "kMandarin": unihan.mandarin.get(ch, ""),
    }


def readings_not_in_xhc(entries: list[Entry], unihan: Unihan) -> list[Row]:
    rows = []
    for e in entries:
        r = analyze_fix_b(e, unihan)
        if r is None or not r.not_in_xhc:
            continue
        rows.append({
            "character": e["character"],
            "not_in_xhc": " ".join(r.not_in_xhc),
            "pinyin": " ".join(e["pinyin"]),
            "kXHC1983": " ".join(unihan.xhc1983[e["character"]]),
            **modern_columns(e["character"], unihan),
            "definition": e["definition"],
        })
    return rows


def xhc_readings_held_back(entries: list[Entry], unihan: Unihan) -> list[Row]:
    rows = []
    for e in entries:
        r = analyze_fix_b(e, unihan)
        if r is None or not r.held_back:
            continue
        rows.append({
            "character": e["character"],
            "held_back": " ".join(r.held_back),
            "pinyin": " ".join(e["pinyin"]),
            "kXHC1983": " ".join(unihan.xhc1983[e["character"]]),
            **modern_columns(e["character"], unihan),
            "definition": e["definition"],
        })
    return rows


# ---------------------------------------------------------------------------


def write_csv(name: str, rows: list[Row]) -> None:
    os.makedirs(REVIEW_DIR, exist_ok=True)
    path = os.path.join(REVIEW_DIR, name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS[name], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {os.path.relpath(path, ROOT)} ({len(rows)} rows)")


def show(rows: list[Row], keys: list[str]) -> None:
    for row in rows:
        print("  " + " | ".join(f"{k}={row[k]}" for k in keys if row.get(k) != ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write review/*.csv (default: dry run)")
    ap.add_argument("--samples", type=int, default=4, help="sample rows to show per report")
    ap.add_argument("--seed", type=int, default=1, help="random seed for samples")
    args = ap.parse_args()

    errors = validate_file()
    if errors:
        raise SystemExit("dictionary.jsonl does not validate:\n  " + "\n  ".join(errors[:20]))
    entries = load_entries(DICT_PATH)
    original = load_entries(ORIGINAL_PATH)
    zh, cjkvi, unihan = Chinese(), CjkviIds(), load_unihan()
    rng = random.Random(args.seed)

    reports = {
        "traditional_components.csv": traditional_components(entries, zh, cjkvi),
        "blank_phonetic.csv": blank_phonetic(entries, original, zh),
        "leftover_fix_a.csv": leftover_fix_a(entries, original),
        "readings_not_in_xhc.csv": readings_not_in_xhc(entries, unihan),
        "xhc_readings_held_back.csv": xhc_readings_held_back(entries, unihan),
    }
    breakdown_key = {
        "traditional_components.csv": "check",
        "blank_phonetic.csv": "reason",
        "leftover_fix_a.csv": "outcome",
    }

    print(f"Phase 2 {'WRITE' if args.write else 'DRY RUN'}")
    for name, rows in reports.items():
        print()
        print(f"== review/{name}: {len(rows)} rows")
        key = breakdown_key.get(name)
        if key:
            counts: dict[str, int] = {}
            for row in rows:
                counts[row[key]] = counts.get(row[key], 0) + 1
            for value, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"   {n:4d}  {value}")
        if name == "traditional_components.csv":
            show(rows, ["character", "decomposition", "traditional", "reason", "suggested"])
        else:
            sample = sorted(rng.sample(rows, min(args.samples, len(rows))), key=lambda r: r["character"])
            show(sample, COLUMNS[name][:7])

    if args.write:
        print()
        for name, rows in reports.items():
            write_csv(name, rows)
    else:
        print()
        print("Dry run: nothing written. Re-run with --write to write review/*.csv.")


if __name__ == "__main__":
    main()
