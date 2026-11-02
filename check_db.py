
import sqlite3
from pathlib import Path

db_path = Path(__file__).resolve().parent / "database" / "moderation.db"

print("Database:", db_path)
print("Exists:", db_path.exists())

if db_path.exists():
    con = sqlite3.connect(db_path)
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()

    for (table,) in tables:
        print("\nTABLE:", table)
        columns = con.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()

        for column in columns:
            print(column)

    con.close()
