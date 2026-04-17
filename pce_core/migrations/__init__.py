"""PCE Core – Schema migration framework.

Migrations are numbered Python modules (``0001_*.py``, ``0002_*.py``, …)
that live next to this file and expose two functions::

    def upgrade(conn: sqlite3.Connection) -> None: ...
    def downgrade(conn: sqlite3.Connection) -> None: ...

Version state is tracked in a ``schema_meta`` table (single row) created
by the runner on first use. Applying migrations is idempotent and ordered.

This replaces the ad-hoc ``ALTER TABLE`` calls that previously lived in
``pce_core.db.init_db``. Those calls are still run for legacy databases
so existing installs continue to work; they are wrapped by migration
``0001_baseline``.

Policy:
- Never delete a migration after release.
- New fields must be added-only (no renames, no destructive drops).
- If a migration cannot run, raise and abort startup loudly.
- ``downgrade`` is best-effort; it exists for development convenience.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, NamedTuple

logger = logging.getLogger("pce.migrations")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Version that the code expects the database to be at after
# ``apply_migrations`` completes. Bumped when a new migration is added.
EXPECTED_SCHEMA_VERSION = 3


class Migration(NamedTuple):
    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]
    downgrade: Callable[[sqlite3.Connection], None] | None


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply all pending migrations. Returns the resulting schema version.

    Safe to call on:
    - Fresh databases (no tables yet)
    - Legacy databases (tables exist, no ``schema_meta``)
    - Up-to-date databases (no-op)

    Raises:
        RuntimeError: if the database is at a version newer than the code
            knows about, indicating the user downgraded PCE. Startup must
            abort in that case so we don't silently corrupt newer data.
    """
    _ensure_meta_table(conn)
    current = get_current_version(conn)

    if current > EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema_version={current} is newer than this build "
            f"expects ({EXPECTED_SCHEMA_VERSION}). You appear to have "
            f"downgraded PCE. Refusing to start to avoid data loss. "
            f"Reinstall a newer PCE or restore a backup taken before the "
            f"downgrade."
        )

    migrations = list(discover_migrations())
    pending = [m for m in migrations if m.version > current]

    if not pending:
        logger.debug("schema is up to date at version %d", current)
        return current

    for mig in pending:
        logger.info(
            "applying migration",
            extra={"event": "migrations.apply", "pce_fields": {
                "version": mig.version, "name": mig.name,
            }},
        )
        try:
            mig.upgrade(conn)
        except Exception:
            conn.rollback()
            logger.exception(
                "migration failed",
                extra={"event": "migrations.failed", "pce_fields": {
                    "version": mig.version, "name": mig.name,
                }},
            )
            raise
        _set_version(conn, mig.version, mig.name)
        conn.commit()

    final = get_current_version(conn)
    logger.info(
        "migrations done",
        extra={"event": "migrations.done", "pce_fields": {
            "from_version": current, "to_version": final,
            "applied": [m.version for m in pending],
        }},
    )
    return final


def get_current_version(conn: sqlite3.Connection) -> int:
    """Return the database's current schema version. 0 if uninitialised."""
    _ensure_meta_table(conn)
    row = conn.execute(
        "SELECT version FROM schema_meta WHERE id = 1"
    ).fetchone()
    return int(row[0]) if row else 0


def get_migration_history(conn: sqlite3.Connection) -> list[dict]:
    """Return the list of migrations that have been applied to this DB."""
    _ensure_meta_table(conn)
    rows = conn.execute(
        "SELECT version, name, applied_at FROM schema_migrations "
        "ORDER BY version"
    ).fetchall()
    return [{"version": r[0], "name": r[1], "applied_at": r[2]} for r in rows]


def discover_migrations() -> Iterable[Migration]:
    """Yield every Migration defined in this package, ordered by version."""
    pkg_path = Path(__file__).parent
    found: list[Migration] = []
    for mod_info in pkgutil.iter_modules([str(pkg_path)]):
        name = mod_info.name
        if not name or not name[0].isdigit():
            continue
        try:
            version = int(name.split("_", 1)[0])
        except ValueError:
            continue
        module = importlib.import_module(f"{__name__}.{name}")
        up = getattr(module, "upgrade", None)
        down = getattr(module, "downgrade", None)
        if not callable(up):
            logger.warning(
                "migration %s missing upgrade() – skipped", name,
            )
            continue
        found.append(Migration(
            version=version,
            name=name,
            upgrade=up,
            downgrade=down if callable(down) else None,
        ))
    found.sort(key=lambda m: m.version)
    _validate_sequence(found)
    return found


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Create the schema_meta / schema_migrations tables if missing."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            version    INTEGER NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at REAL NOT NULL
        );
        """
    )


def _set_version(conn: sqlite3.Connection, version: int, name: str) -> None:
    import time
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (id, version, updated_at) "
        "VALUES (1, ?, ?)",
        (version, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_migrations (version, name, applied_at) "
        "VALUES (?, ?, ?)",
        (version, name, now),
    )


def _validate_sequence(migrations: list[Migration]) -> None:
    seen: set[int] = set()
    for m in migrations:
        if m.version in seen:
            raise RuntimeError(
                f"Duplicate migration version {m.version} – check filenames"
            )
        seen.add(m.version)
    if migrations and migrations[-1].version > EXPECTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"EXPECTED_SCHEMA_VERSION ({EXPECTED_SCHEMA_VERSION}) is "
            f"behind highest discovered migration "
            f"({migrations[-1].version}). Update EXPECTED_SCHEMA_VERSION in "
            f"pce_core/migrations/__init__.py when adding a new migration."
        )
