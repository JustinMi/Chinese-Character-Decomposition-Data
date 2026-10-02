#!/bin/sh
# Rebuild dictionary.jsonl, changelog.jsonl and review/ from
# dictionary.original.jsonl by running every phase in order.
set -eu
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "Set up the environment first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
if git rev-parse --git-dir >/dev/null 2>&1 &&
    [ -n "$(git status --porcelain -- dictionary.jsonl changelog.jsonl)" ]; then
    echo "dictionary.jsonl or changelog.jsonl has uncommitted changes; commit or discard them first." >&2
    exit 1
fi

cp dictionary.original.jsonl dictionary.jsonl
chmod u+w dictionary.jsonl
rm -f changelog.jsonl

"$PY" phase1_fixes.py --write
"$PY" apply_corrections.py --write
# Phase 2 only reads the dictionary, so running it last doesn't change any
# result; it just makes the reports reflect the patched dictionary.
"$PY" phase2_reports.py --write
