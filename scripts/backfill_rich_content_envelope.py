# SPDX-License-Identifier: Apache-2.0
"""Backfill rich_content envelopes for legacy content_json payloads.

Existing rows used ``content_json.attachments`` as the render contract. The
dashboard can still read that legacy shape, but the canonical storage contract
now also includes ``content_json.rich_content`` with schema
``pce.rich_content.v1``. This script is intentionally narrow: it only updates
rows that already have legacy attachments and no rich_content envelope.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pce_core.config import DB_PATH  # noqa: E402
from pce_core.rich_content import RICH_CONTENT_SCHEMA, build_rich_content_envelope, normalize_attachments  # noqa: E402


def _load_payload(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_backfilled_payload(
    raw_content_json: str | None,
    *,
    plain_text: str | None,
) -> str | None:
    payload = _load_payload(raw_content_json)
    if payload is None:
        return None
    if isinstance(payload.get("rich_content"), dict):
        return None

    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        return None

    normalized = normalize_attachments(attachments)
    if not normalized:
        return None

    payload["attachments"] = normalized
    payload["rich_content"] = build_rich_content_envelope(plain_text, normalized)
    return json.dumps(payload, ensure_ascii=False)


def backfill(db_path: Path, *, dry_run: bool) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, content_text, content_json
            FROM messages
            WHERE content_json IS NOT NULL
            ORDER BY ts ASC
            """
        ).fetchall()

        scanned = len(rows)
        updated = 0
        skipped = 0
        for row in rows:
            next_payload = build_backfilled_payload(
                row["content_json"],
                plain_text=row["content_text"],
            )
            if next_payload is None:
                skipped += 1
                continue
            updated += 1
            if not dry_run:
                conn.execute(
                    "UPDATE messages SET content_json = ? WHERE id = ?",
                    (next_payload, row["id"]),
                )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        rich_v1 = conn.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE content_json IS NOT NULL
              AND json_valid(content_json)
              AND json_extract(content_json, '$.rich_content.schema') = ?
            """,
            (RICH_CONTENT_SCHEMA,),
        ).fetchone()[0]
        legacy_only = conn.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE content_json IS NOT NULL
              AND json_valid(content_json)
              AND json_extract(content_json, '$.attachments') IS NOT NULL
              AND json_extract(content_json, '$.rich_content') IS NULL
            """
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "rich_v1": rich_v1,
        "legacy_only": legacy_only,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    if not args.db.exists():
        print(json.dumps({"error": f"DB not found: {args.db}"}, ensure_ascii=False))
        return 1

    result = backfill(args.db, dry_run=args.dry_run)
    result["dry_run"] = int(args.dry_run)
    result["db"] = str(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
