# SPDX-License-Identifier: Apache-2.0
"""Archive old raw_captures to gzip files and optionally delete from DB.

Usage:
    python -m tools.archive_old_captures                  # dry-run, 90 days
    python -m tools.archive_old_captures --days 60 --apply
"""

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def _default_db_path() -> Path:
    data_dir = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
    return data_dir / "pce.db"


def archive_captures(db_path: Path, days: int = 90, apply: bool = False):
    cutoff = time.time() - days * 86400
    archive_dir = db_path.parent / "archives"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Count eligible captures
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_captures WHERE created_at < ?", (cutoff,)
    ).fetchone()[0]

    print(f"{'=' * 60}")
    print(f"  PCE Raw Captures Archive {'[APPLY]' if apply else '[DRY-RUN]'}")
    print(f"  Database: {db_path}")
    print(f"  Cutoff: {days} days ago ({time.strftime('%Y-%m-%d', time.localtime(cutoff))})")
    print(f"  Eligible captures: {count}")
    print(f"{'=' * 60}")

    if count == 0:
        print("\n  No captures to archive.")
        conn.close()
        return

    if not apply:
        # Show size estimate
        size = conn.execute(
            "SELECT SUM(LENGTH(body_text_or_json)) FROM raw_captures WHERE created_at < ?",
            (cutoff,),
        ).fetchone()[0] or 0
        print(f"\n  Estimated body data: {size / 1024 / 1024:.1f} MB")
        print(f"\n  DRY-RUN. Re-run with --apply to execute.")
        conn.close()
        return

    # Step 1: Export to gzip JSONL
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts_str = time.strftime("%Y%m%d_%H%M%S")
    archive_file = archive_dir / f"raw_captures_{ts_str}.jsonl.gz"

    print(f"\n  [1/4] Exporting to {archive_file}...")
    rows = conn.execute(
        "SELECT * FROM raw_captures WHERE created_at < ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()

    exported = 0
    with gzip.open(str(archive_file), "wt", encoding="utf-8") as f:
        for row in rows:
            line = json.dumps(dict(row), ensure_ascii=False, default=str)
            f.write(line + "\n")
            exported += 1

    print(f"  Exported {exported} captures to archive.")

    # Step 2: Verify archive integrity
    print("  [2/4] Verifying archive integrity...")
    verified = 0
    with gzip.open(str(archive_file), "rt", encoding="utf-8") as f:
        for line in f:
            json.loads(line)
            verified += 1

    if verified != exported:
        print(f"  ERROR: Verification failed! Expected {exported}, got {verified}")
        print(f"  Archive kept but no data deleted.")
        conn.close()
        return

    print(f"  Verified {verified} records.")

    # Step 3: Delete from DB
    print("  [3/4] Deleting archived captures from database...")
    conn.execute("DELETE FROM raw_captures WHERE created_at < ?", (cutoff,))
    conn.commit()
    print(f"  Deleted {exported} captures.")

    # Step 4: VACUUM
    print("  [4/4] Running VACUUM to reclaim space...")
    conn.execute("VACUUM")
    print("  VACUUM complete.")

    # Summary
    remaining = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
    archive_size = archive_file.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  Archive: {archive_file.name} ({archive_size:.1f} MB)")
    print(f"  Archived: {exported} captures")
    print(f"  Remaining: {remaining} captures")
    print(f"{'=' * 60}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Archive old PCE raw captures")
    parser.add_argument("--days", type=int, default=90, help="Archive captures older than N days (default: 90)")
    parser.add_argument("--apply", action="store_true", help="Actually execute (default: dry-run)")
    parser.add_argument("--db", type=str, default=None, help="Path to pce.db")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db_path()
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    archive_captures(db_path, days=args.days, apply=args.apply)


if __name__ == "__main__":
    main()
