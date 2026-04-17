# Migrations

Framework for PCE's SQLite schema evolution. Added in P0 (2026-04-17).

## How it works

1. Each migration is a Python module named `NNNN_short_name.py` living in this directory.
2. It exposes two functions:
   ```python
   def upgrade(conn: sqlite3.Connection) -> None: ...
   def downgrade(conn: sqlite3.Connection) -> None: ...
   ```
3. `pce_core.migrations.apply_migrations(conn)` is called from `db.init_db()` on every startup. It discovers all migrations in version order and applies the pending ones.
4. Version state lives in the `schema_meta` (single row) and `schema_migrations` (history) tables, created automatically on first use.
5. `EXPECTED_SCHEMA_VERSION` in `__init__.py` must be bumped whenever a new migration is added.

## Rules

- **Never delete a migration** after it has been released. Existing installs still need it.
- **Never rename** an already-shipped migration file.
- **Add-only**: prefer `ALTER TABLE ADD COLUMN`, `CREATE TABLE IF NOT EXISTS`, and new indexes. Destructive operations (`DROP COLUMN`, `DROP TABLE`) require an ADR and a release note.
- **Idempotent**: `upgrade()` must be safe to call on a database that already has the target shape (e.g. use `IF NOT EXISTS`, check `PRAGMA table_info`).
- **`downgrade()`** is best-effort and optional. Treat it as a developer convenience, not a production feature.
- **Commit per migration**: the runner commits after each successful `upgrade()`. If a migration raises, it rolls back its own transaction and aborts startup.

## Creating a new migration

1. Pick the next version number, e.g. `0003`.
2. Create `pce_core/migrations/0003_short_name.py` with `upgrade`/`downgrade`.
3. Bump `EXPECTED_SCHEMA_VERSION` to `3` in `__init__.py`.
4. Add a unit test in `tests/test_migrations.py` that:
   - starts from an empty DB → ends at version 3
   - starts from a DB at version 2 → ends at version 3 with `3` in history
5. If the migration changes user-visible semantics, add a line to the completion note for the corresponding phase (`Docs/docs/decisions/2026-??-??-P?-completion.md`).

## Version downgrade safety

If a user installs an older PCE build on a database already at a newer schema version, `apply_migrations` refuses to start with:

```
Database schema_version=N is newer than this build expects (M).
```

This prevents silent data loss from code running against tables it does not understand.

## Testing

```
pytest tests/smoke/test_migrations_smoke.py
```

covers:
- Fresh DB → latest version
- Legacy DB (tables exist, no `schema_meta`) → latest version
- Already-up-to-date DB → no-op
- DB at future version → refuses to start
