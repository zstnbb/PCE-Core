"""Rebuild the FTS5 index for the messages table.

Usage:
    python tools/rebuild_fts.py
    python tools/rebuild_fts.py --db path/to/pce.db
"""

import argparse
import os
import sys
from pathlib import Path


def _default_db_path() -> Path:
    data_dir = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
    return data_dir / "pce.db"


def main():
    parser = argparse.ArgumentParser(description="Rebuild PCE FTS5 index")
    parser.add_argument("--db", type=str, default=None, help="Path to pce.db")
    args = parser.parse_args()

    # Use pce_core to ensure schema is up to date
    if args.db:
        os.environ["PCE_DATA_DIR"] = str(Path(args.db).parent)

    from pce_core.db import init_db, get_connection

    db_path = Path(args.db) if args.db else _default_db_path()
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    # Ensure schema (including FTS table) exists
    init_db(db_path)

    conn = get_connection(db_path)
    try:
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        fts_before = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        print(f"Messages: {msg_count}")
        print(f"FTS entries before: {fts_before}")

        print("Rebuilding FTS index...")
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()

        fts_after = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        print(f"FTS entries after:  {fts_after}")
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
