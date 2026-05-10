"""Quick verification helper: dump L3g raw_captures rows.

Run::

    python scripts/_l3g_verify.py

Reads :data:`pce_core.config.DB_PATH` and prints all rows whose ``source_id``
matches the L3g default source registered by migration ``0011``.
"""
from __future__ import annotations

import json
import sqlite3

from pce_core.config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, source_id, source, direction, host, path, provider, "
        "session_hint, length(body_text_or_json) AS body_len, meta_json "
        "FROM raw_captures WHERE source_id = ? ORDER BY created_at",
        ("l3g-local-persistence-default",),
    ).fetchall()
    print(f"L3g rows: {len(rows)}")
    for row in rows:
        d = dict(row)
        meta = d.pop("meta_json", None)
        if meta:
            try:
                d["meta_keys"] = sorted(json.loads(meta).keys())
            except Exception:  # noqa: BLE001 - best effort preview
                d["meta_keys"] = "<unparseable>"
        print(d)


if __name__ == "__main__":
    main()
