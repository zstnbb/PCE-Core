"""Smoke-verify migration 0012 (L3h CLI wrapper source) on a fresh DB.

Run::

    python scripts/_l3h_verify.py

Creates a tmp DB, applies all migrations, asserts the schema version is
the expected ``12`` and that both L3g and L3h source rows are present.
Prints a one-line PASS / FAIL summary and exits with status 0 / 1.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from pce_core.db import init_db
from pce_core.migrations import EXPECTED_SCHEMA_VERSION


def main() -> int:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    db_path = Path(tmp.name)
    try:
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        version = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, source_type, tool_name, install_mode "
            "FROM sources WHERE id LIKE 'l3%' ORDER BY id"
        ).fetchall()
        conn.close()

        print(f"schema_version: expected={EXPECTED_SCHEMA_VERSION} actual={version}")
        for row in rows:
            print(dict(row))

        ids = {r["id"] for r in rows}
        ok = (
            version == EXPECTED_SCHEMA_VERSION
            and "l3g-local-persistence-default" in ids
            and "l3h-cli-wrapper-default" in ids
        )
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
