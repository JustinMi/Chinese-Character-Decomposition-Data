#!/usr/bin/env python3
"""Run the dictionary project's own scripts/apply_corrections.py.

The project keeps the one implementation of patching, next to the validator
that replays changelog.jsonl and the export that keeps dictionary.json and
dictionary.db in sync. This launcher finds that script from the current
directory (or any folder above it) and runs it with the same arguments, so
the skill and the project can't drift apart.

Subcommands (handled by the project script):
  check BATCH            Validate a batch of patch lines without touching anything.
  add BATCH              Validate a batch and append its new lines to data/corrections.jsonl.
  apply [--write]        Apply every patch in data/corrections.jsonl. Dry run unless --write.

Exit codes: 0 = clean, 1 = conflicts or invalid patches found, 2 = usage/file error.
"""
import os
import sys


def find_project_script(start):
    """scripts/apply_corrections.py in the nearest folder that also has data/golden/dictionary.jsonl."""
    folder = os.path.abspath(start)
    while True:
        script = os.path.join(folder, "scripts", "apply_corrections.py")
        if os.path.isfile(script) and os.path.isfile(os.path.join(folder, "data", "golden", "dictionary.jsonl")):
            return script
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def main():
    script = find_project_script(os.getcwd())
    if script is None:
        print("error: run this from the dictionary project folder "
              "(the one with data/golden/dictionary.jsonl and scripts/apply_corrections.py)", file=sys.stderr)
        return 2
    os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
