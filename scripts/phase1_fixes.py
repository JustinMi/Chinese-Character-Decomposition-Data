"""Phase 1: automated fixes.

Fix A  Entries labeled ideographic/pictographic whose own hint says a
       component provides the pronunciation become pictophonetic, with
       etymology_phonetic filled in, plus etymology_semantic when the
       decomposition makes it unambiguous. etymology_hint is left alone.
Fix B  Add the readings from Unihan kXHC1983 that pinyin is missing, ordered:
       first kMandarin reading, then existing readings, then the added ones.
       kXHC1983 (1983) predates the 1985 审音表, so a reading is only added
       if kTGHZ2013 (the 2013 standard) also lists it, checking traditional
       characters through their simplified form; the rest are held back for
       review (phase 2). Readings are never removed.

Dry run by default; pass --write to apply.

    python3 scripts/phase1_fixes.py            # dry run
    python3 scripts/phase1_fixes.py --write    # apply, log, validate
"""

import argparse
import collections
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from common import (
    DICT_PATH,
    Entry,
    change,
    commit_changes,
    dumps,
    fmt,
    ids_text,
    load_changelog,
    load_entries,
    load_unihan,
    manually_patched,
    Unihan,
    nfc,
    parse_ids,
)

# ---------------------------------------------------------------------------
# Fix A

# Hint phrasings that name a phonetic, most specific first. Each pattern
# captures the phonetic and, for one phrasing, the semantic.
PHRASINGS = [
    ("X provides the meaning while Y provides the pronunciation",
     re.compile(r"(?P<semantic>\S)\s+provides the meaning while\s+(?P<phonetic>\S)\s+provides the pronunciation")),
    ("X provides the pronunciation and meaning",
     re.compile(r"(?P<phonetic>\S)\s+provides the pronunciation and meaning")),
    ("X provides the meaning and pronunciation",
     re.compile(r"(?P<phonetic>\S)\s+provides the meaning and pronunciation")),
    ("with X providing the pronunciation",
     re.compile(r"with\s+(?P<phonetic>\S)\s+providing the pronunciation")),
    ("X provides the pronunciation; compare Z",
     re.compile(r"(?P<phonetic>\S)\s+provides the pronunciation;\s*compare\b")),
    ("Xalso provides the pronunciation (no space)",
     re.compile(r"(?P<phonetic>\S)also provides the pronunciation")),
    ("X also provides the pronunciation",
     re.compile(r"(?P<phonetic>\S)\s+also provides the pronunciation")),
    ("X provides the pronunciation",
     re.compile(r"(?P<phonetic>\S)\s+provides the pronunciation")),
]
# The phonetic is said to carry the meaning too, so the remaining component
# isn't attested as the semantic: fill the phonetic only.
PHONETIC_ALSO_MEANING = {
    "X provides the pronunciation and meaning",
    "X provides the meaning and pronunciation",
}


def is_fix_a_candidate(e: Entry) -> bool:
    return (
        e["etymology_type"] in ("ideographic", "pictographic")
        and "pronunciation" in e["etymology_hint"].lower()
    )


def is_cjk(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith(("CJK ", "KANGXI RADICAL"))


@dataclass
class FixA:
    outcome: str  # "full", "partial", or "skipped"
    phrasing: str
    phonetic: str
    semantic: str
    reason: str
    parts: list[str]


def analyze_fix_a(e: Entry) -> FixA | None:
    """Fix A's decision for one entry, or None if the entry isn't a candidate."""
    if not is_fix_a_candidate(e):
        return None
    hint, dec = e["etymology_hint"], e["decomposition"]

    for phrasing, pattern in PHRASINGS:
        m = pattern.search(hint)
        if m:
            break
    else:
        return FixA("skipped", "unrecognized", "", "", "hint mentions pronunciation in an unrecognized phrasing", [])

    phonetic = m.group("phonetic")
    hint_semantic = m.groupdict().get("semantic") or ""
    if not is_cjk(phonetic):
        return FixA("skipped", phrasing, phonetic, "", f"text before the phrase is {phonetic!r}, not a character", [])
    if phonetic not in dec:
        return FixA("skipped", phrasing, phonetic, "", f"phonetic {phonetic} does not appear in the decomposition", [])

    tree = parse_ids(dec)
    parts = [tree] if isinstance(tree, str) else [ids_text(p) for p in tree[1]]

    def partial(reason: str) -> FixA:
        return FixA("partial", phrasing, phonetic, "", reason, parts)

    if phrasing in PHONETIC_ALSO_MEANING:
        return partial(f"hint says {phonetic} provides both the pronunciation and the meaning")
    if hint_semantic:
        if hint_semantic in dec:
            return FixA("full", phrasing, phonetic, hint_semantic, "semantic named by the hint", parts)
        return partial(f"hint names semantic {hint_semantic}, which is not in the decomposition")
    if phonetic not in parts:
        return partial(f"phonetic {phonetic} is nested, not a top-level part")
    rest = list(parts)
    rest.remove(phonetic)
    if len(rest) != 1:
        return partial(f"{len(rest)} other top-level parts ({' '.join(rest)})")
    other = rest[0]
    if other == "?":
        return partial("the other top-level part is unknown (?)")
    if len(other) > 1:
        return partial(f"the other top-level part is a compound ({other}), not a single component")
    return FixA("full", phrasing, phonetic, other, "", parts)


def apply_fix_a(e: Entry, result: FixA) -> dict[str, Any]:
    """The new values Fix A sets for an entry (empty dict if skipped)."""
    if result.outcome == "skipped":
        return {}
    new = {"etymology_type": "pictophonetic", "etymology_phonetic": result.phonetic}
    if result.outcome == "full":
        new["etymology_semantic"] = result.semantic
    return {k: v for k, v in new.items() if e[k] != v}


# ---------------------------------------------------------------------------
# Fix B


@dataclass
class FixB:
    pinyin: list[str]  # the new list
    added: list[str]
    held_back: list[str]  # missing kXHC1983 readings that kTGHZ2013 doesn't list
    not_in_xhc: list[str]  # existing readings kXHC1983 doesn't list (kept)
    modern: set[str] | None  # the kTGHZ2013 readings used as the filter
    modern_via: str  # the character whose kTGHZ2013 data was used


def analyze_fix_b(e: Entry, unihan: Unihan) -> FixB | None:
    """Fix B's decision for one entry, or None if it has no kXHC1983 data."""
    ch = e["character"]
    readings = unihan.xhc1983.get(ch)
    if not readings:
        return None
    existing = list(e["pinyin"])
    have = {nfc(p) for p in existing}
    modern, via = unihan.modern_readings(ch)
    missing = [r for r in readings if r not in have]
    added = [r for r in missing if modern is None or r in modern]
    held_back = [r for r in missing if r not in added]
    combined = existing + added
    first = unihan.mandarin.get(ch)
    keys = [nfc(p) for p in combined]
    if first in keys:
        i = keys.index(first)
        combined = [combined[i]] + combined[:i] + combined[i + 1:]
    not_in_xhc = [p for p in existing if nfc(p) not in readings]
    return FixB(combined, added, held_back, not_in_xhc, modern, via)


def has_tone_mark(p: str) -> bool:
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", p))


# ---------------------------------------------------------------------------


def template(s: str) -> str:
    return "".join("X" if is_cjk(c) else c for c in s)


def phrase_clause(hint: str) -> str:
    """The hint clause containing 'pronunciation', with characters as X."""
    t = template(hint)
    k = t.lower().find("pronunciation")
    start = max(t.rfind(";", 0, k), t.rfind(".", 0, k)) + 1
    return t[start:].strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="apply the changes (default: dry run)")
    ap.add_argument("--samples", type=int, default=20, help="Fix A samples to show (Fix B shows 1.5x)")
    ap.add_argument("--seed", type=int, default=1, help="random seed for samples")
    args = ap.parse_args()

    entries = load_entries(DICT_PATH)
    protected = manually_patched(load_changelog())
    unihan = load_unihan()
    rng = random.Random(args.seed)

    changes: list[dict[str, Any]] = []
    new_entries: list[Entry] = []

    # ---- Fix A
    a_results: list[tuple[Entry, FixA, dict[str, Any]]] = []
    a_protected: list[str] = []
    for e in entries:
        e = dict(e)
        result = analyze_fix_a(e)
        if result is not None:
            if any((e["character"], f) in protected for f in ("etymology_type", "etymology_phonetic", "etymology_semantic")):
                a_protected.append(e["character"])
            else:
                new = apply_fix_a(e, result)
                a_results.append((dict(e), result, new))
                for field, value in new.items():
                    changes.append(change(e["character"], field, e[field], value, "fix_a"))
                    e[field] = value
        new_entries.append(e)

    # ---- Fix B (on top of Fix A; the two touch different fields)
    b_all: list[tuple[str, list[str], FixB]] = []  # every entry with kXHC1983 data
    b_no_data = 0
    b_protected: list[str] = []
    for e in new_entries:
        ch = e["character"]
        if (ch, "pinyin") in protected:
            b_protected.append(ch)
            continue
        result = analyze_fix_b(e, unihan)
        if result is None:
            b_no_data += 1
            continue
        b_all.append((ch, list(e["pinyin"]), result))
        if result.pinyin != e["pinyin"]:
            changes.append(change(ch, "pinyin", e["pinyin"], result.pinyin, "fix_b"))
            e["pinyin"] = result.pinyin
    b_results = [(c, o, r.pinyin) for c, o, r in b_all if r.pinyin != o]

    # ---- Report
    print(f"Phase 1 {'WRITE' if args.write else 'DRY RUN'}  (Unihan {unihan.version})")
    print()
    print("=" * 78)
    print("FIX A: ideographic/pictographic entries whose hint names a phonetic")
    print("=" * 78)
    candidates = [r for _, r, _ in a_results]
    print(f"candidates: {len(candidates) + len(a_protected)}"
          + (f"  ({len(a_protected)} left alone: manually patched)" if a_protected else ""))
    print()
    print("Distinct phrasings of the pronunciation clause (characters shown as X):")
    clauses = collections.Counter(phrase_clause(e["etymology_hint"]) for e, _, _ in a_results)
    for clause, n in clauses.most_common():
        ex = next(e for e, _, _ in a_results if phrase_clause(e["etymology_hint"]) == clause)
        print(f"  {n:4d}  {clause!r}")
        print(f"        e.g. {ex['character']}: {ex['etymology_hint']}")
    print()
    print("Handling by phrasing:")
    by_phrasing = collections.Counter((r.phrasing, r.outcome) for r in candidates)
    for phrasing, _ in PHRASINGS:
        row = {o: by_phrasing.get((phrasing, o), 0) for o in ("full", "partial", "skipped")}
        if any(row.values()):
            print(f"  {phrasing:<60} full {row['full']:3d}  partial {row['partial']:2d}  skipped {row['skipped']:2d}")
    print()
    outcomes = collections.Counter(r.outcome for r in candidates)
    print(f"fixed fully (type + phonetic + semantic): {outcomes['full']}")
    print(f"fixed partly (type + phonetic only):      {outcomes['partial']}")
    print(f"skipped (-> review/leftover_fix_a.csv):   {outcomes['skipped']}")
    print()
    for outcome in ("partial", "skipped"):
        rows = [(e, r) for e, r, _ in a_results if r.outcome == outcome]
        print(f"All {outcome} entries ({len(rows)}):")
        for e, r in rows:
            print(f"  {e['character']}  {e['decomposition']:<14} phonetic {r.phonetic or '-'}  | {r.reason}")
        print()
    full_doubled = [e["character"] for e, r, _ in a_results if r.outcome == "full" and r.semantic == r.phonetic]
    if full_doubled:
        print(f"Full fixes where semantic == phonetic (doubled component): {' '.join(full_doubled)}")
        print()
    changed_a = [(e, r, new) for e, r, new in a_results if new]
    print(f"Random sample of {min(args.samples, len(changed_a))} Fix A changes (seed {args.seed}):")
    for e, r, new in sorted(rng.sample(changed_a, min(args.samples, len(changed_a))), key=lambda x: x[0]["character"]):
        print(f"  {e['character']}  {e['decomposition']}  [{r.outcome}]  hint: {e['etymology_hint']}")
        for field, value in new.items():
            print(f"        {field}: {fmt(e[field])} -> {fmt(value)}")
    print()

    print("=" * 78)
    print("FIX B: add missing kXHC1983 readings that kTGHZ2013 confirms, kMandarin first")
    print("=" * 78)
    added_any = [(c, o, r) for c, o, r in b_all if r.added]
    reorder_only = [(c, o, n) for c, o, n in b_results if len(n) == len(o)]
    first_changed = [(c, o, n) for c, o, n in b_results if o and o[0] != n[0]]
    toneless_added = [(c, a) for c, _, r in b_all for a in r.added if not has_tone_mark(a)]
    held = [(c, o, r) for c, o, r in b_all if r.held_back]
    unfiltered = [(c, r) for c, _, r in added_any if r.modern is None]
    via_simplified = sum(1 for c, _, r in added_any if r.modern is not None and r.modern_via != c)
    mandarin_missing = [(c, r.pinyin) for c, _, r in b_all
                        if unihan.mandarin.get(c) not in {nfc(p) for p in r.pinyin}]
    print(f"entries with kXHC1983 data: {len(b_all)}   without (left untouched): {b_no_data}"
          + (f"   manually patched (left alone): {len(b_protected)}" if b_protected else ""))
    print(f"entries changed: {len(b_results)}")
    print(f"  readings added: {len(added_any)} entries, {sum(len(r.added) for _, _, r in added_any)} readings")
    print(f"    checked against kTGHZ2013 via the simplified form: {via_simplified} entries")
    print(f"    no kTGHZ2013 data at all, added unfiltered: {len(unfiltered)} entries, "
          f"{sum(len(r.added) for _, r in unfiltered)} readings")
    print(f"  reordered only (kMandarin moved first): {len(reorder_only)}")
    print(f"  first reading changed: {len(first_changed)}")
    print(f"  added readings with no tone mark (neutral tone): {len(toneless_added)}")
    print(f"held back (in kXHC1983, not in kTGHZ2013; -> review/xhc_readings_held_back.csv): "
          f"{sum(len(r.held_back) for _, _, r in held)} readings in {len(held)} entries")
    print(f"entries whose first kMandarin value is not among their readings (not reordered): {len(mandarin_missing)}")
    for c, readings in mandarin_missing:
        print(f"  {c}  {dumps(readings)}  kMandarin {unihan.mandarin.get(c, '-')}")
    after = collections.Counter(len(e["pinyin"]) for e in new_entries)
    print(f"entries with 2+ readings after Fix B: {sum(n for k, n in after.items() if k > 1)}  {dict(sorted(after.items()))}")
    print()
    print(f"Entries whose first reading changes ({len(first_changed)}):")
    for c, o, n in first_changed:
        print(f"  {c}  {dumps(o)} -> {dumps(n)}")
    print()
    if reorder_only:
        print(f"Reordered without additions ({len(reorder_only)}):")
        for c, o, n in reorder_only:
            print(f"  {c}  {dumps(o)} -> {dumps(n)}")
        print()
    if toneless_added:
        print("Neutral-tone readings added:", " ".join(f"{c}:{r}" for c, r in toneless_added))
        print()
    if unfiltered:
        print("Added without a kTGHZ2013 check:", " ".join(f"{c}:{','.join(r.added)}" for c, r in unfiltered))
        print()
    print("Held back:", " ".join(f"{c}:{','.join(r.held_back)}" for c, _, r in held))
    print()
    k = min(args.samples * 3 // 2, len(b_results))
    print(f"Random sample of {k} Fix B changes (seed {args.seed}):")
    for c, o, n in sorted(rng.sample(b_results, k)):
        print(f"  {c}  {dumps(o)} -> {dumps(n)}    (kXHC1983 {' '.join(unihan.xhc1983[c])}; "
              f"kMandarin {unihan.mandarin.get(c, '-')})")
    print()
    not_in_xhc = [(c, r.not_in_xhc) for c, _, r in b_all if r.not_in_xhc]
    print(f"Existing readings not in kXHC1983 (kept; -> review/readings_not_in_xhc.csv): "
          f"{sum(len(x) for _, x in not_in_xhc)} readings in {len(not_in_xhc)} entries")
    for c, extra in not_in_xhc[:40]:
        print(f"  {c}  {' '.join(extra):<10} kXHC1983: {' '.join(unihan.xhc1983[c])}")
    if len(not_in_xhc) > 40:
        print(f"  ... and {len(not_in_xhc) - 40} more")
    print()

    print("=" * 78)
    per_rule = collections.Counter(c["rule"] for c in changes)
    per_rule_entries = {rule: len({c["character"] for c in changes if c["rule"] == rule}) for rule in per_rule}
    for rule in sorted(per_rule):
        print(f"{rule}: {per_rule_entries[rule]} entries, {per_rule[rule]} field changes")
    if args.write:
        commit_changes(new_entries, changes)
    else:
        print("Dry run: nothing written. Re-run with --write to apply.")


if __name__ == "__main__":
    main()
