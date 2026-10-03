import json
import os
from typing import Any

# Ensure the fields are in the correct order
REQUIRED_FIELDS = [
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

SEPARATOR = "_"


def flatten_dict(
    d: dict[str, Any], parent_key: str = "", is_root: bool = True
) -> dict[str, str]:
    """Flatten a nested dictionary, skipping the 'matches' key and ensuring field order."""
    items: list[tuple[str, Any]] = []

    for k, v in d.items():
        if k == "matches":  # Skip the 'matches' key
            continue

        # Create a new key with the separator
        new_key = f"{parent_key}{SEPARATOR}{k}" if parent_key else k

        if isinstance(v, dict):
            # Recursively flatten the dictionary
            items.extend(flatten_dict(v, new_key, is_root=False).items())  # type: ignore
        else:
            items.append((new_key, v))

    flattened_dict = dict(items)

    # If at the root level, enforce ordering and add default values
    if is_root:
        return {
            field: flattened_dict.get(field, [] if field == "pinyin" else "")
            for field in REQUIRED_FIELDS
        }

    # If not at the root level, return the flattened items as-is
    return flattened_dict


def convert_to_jsonl(input_file: str, output_file: str) -> None:
    """Convert a JSON file to JSONL format with flattened entries."""
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(output_file, "w", encoding="utf-8") as f:
        for entry in data:
            flattened_entry = flatten_dict(entry)
            f.write(json.dumps(flattened_entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    input_file = "dictionary.json"
    output_file = "dictionary.jsonl"
    # dictionary.jsonl now holds the fixes logged in changelog.jsonl; the
    # pipeline (README.md) maintains it, so never regenerate it from here.
    if os.path.exists(output_file):
        raise SystemExit(f"{output_file} already exists; not overwriting it (see README.md)")
    convert_to_jsonl(input_file, output_file)
