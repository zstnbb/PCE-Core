# SPDX-License-Identifier: Apache-2.0
"""Migration 0004 – Message embeddings table for semantic search (P4.1).

Adds a narrow, additive table that stores one embedding vector per
normalized message. The design keeps embeddings **decoupled from the
messages row itself** so that:

- Back-filling is cheap (one ``INSERT OR REPLACE`` per row, no ``ALTER``).
- Swapping the backend / model is safe (we keep ``backend`` +
  ``model_name`` in the row; stale vectors can be bulk-deleted).
- Uninstalling the feature just means dropping this table — no data
  loss anywhere else.

Vectors are packed as float32 little-endian blobs via
``struct.pack(f"<{dim}f", *vec)``. Plain SQLite can read them back with
``struct.unpack``; no extension dependency. If the user later installs
``sqlite-vec`` we can add a virtual table alongside for hardware-fast
KNN without changing this schema.

Rationale for "no sqlite-vec required":

- ``sqlite-vec`` ships a loadable extension that isn't available on
  every Python build (e.g. some Windows wheels strip
  ``enable_load_extension``). Making it mandatory would block the whole
  P4.1 feature for those users.
- Brute-force cosine over 10 k messages is sub-50 ms in pure Python, so
  plain-BLOB is acceptable for PCE's personal-use scale.

See `pce_core.embeddings` for the pluggable backend layer and
`pce_core.semantic_search` for the KNN logic.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0004")


_DDL = """
CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id   TEXT PRIMARY KEY,
    backend      TEXT NOT NULL,
    model_name   TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector       BLOB NOT NULL,
    created_at   REAL NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_embeddings_model
    ON message_embeddings(backend, model_name);

-- Mirror the FTS trigger pattern: when a message is deleted, drop its
-- embedding too. SQLite's FK cascade would normally do this for us, but
-- we only enable ``PRAGMA foreign_keys=ON`` inside new connections, so
-- existing code paths that open raw ``sqlite3.connect`` (tests, adhoc
-- scripts) would otherwise leave orphaned rows behind.
CREATE TRIGGER IF NOT EXISTS message_embeddings_ad
AFTER DELETE ON messages
BEGIN
    DELETE FROM message_embeddings WHERE message_id = old.id;
END;
"""


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    logger.info("migration 0004: message_embeddings table ready")


def downgrade(conn: sqlite3.Connection) -> None:
    # Additive and reversible — just drop the artefacts we created. The
    # caller is responsible for taking a backup first.
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS message_embeddings_ad;
        DROP INDEX  IF EXISTS idx_embeddings_model;
        DROP TABLE  IF EXISTS message_embeddings;
        """
    )
