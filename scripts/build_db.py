import json
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS characters (
    character          TEXT PRIMARY KEY,
    definition         TEXT,
    pinyin             TEXT NOT NULL,
    decomposition      TEXT NOT NULL,
    etymology_type     TEXT,
    etymology_hint     TEXT,
    etymology_phonetic TEXT,
    etymology_semantic TEXT,
    radical            TEXT NOT NULL,
    matches            TEXT NOT NULL
);
"""

INSERT = """
INSERT OR REPLACE INTO characters
    (character, definition, pinyin, decomposition,
     etymology_type, etymology_hint, etymology_phonetic, etymology_semantic,
     radical, matches)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def main(
    json_path: str = os.path.join(DATA_DIR, "dictionary.json"),
    db_path: str = os.path.join(DATA_DIR, "dictionary.db"),
) -> None:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    con = sqlite3.connect(db_path)
    con.execute(CREATE_TABLE)

    with con:
        for entry in data:
            etym = entry.get("etymology") or {}
            con.execute(INSERT, (
                entry["character"],
                entry.get("definition"),
                json.dumps(entry["pinyin"], ensure_ascii=False),
                entry["decomposition"],
                etym.get("type"),
                etym.get("hint"),
                etym.get("phonetic"),
                etym.get("semantic"),
                entry["radical"],
                json.dumps(entry["matches"], ensure_ascii=False),
            ))

    con.close()
    print(f"Done. {len(data)} characters inserted.")


if __name__ == "__main__":
    main()
