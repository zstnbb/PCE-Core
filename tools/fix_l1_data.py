"""PCE L1 Data Fix — One-time migration script.

Fixes identified by audit_l1_quality.py:
1. Delete stale sessions with provider='www' (pre-fix Kimi captures)
2. Delete orphan sessions with only 1 message and wrong role
3. Backfill token_estimate for all existing messages
4. Re-attribute moonshot sessions from 'www' provider

Usage:
    python tools/fix_l1_data.py              # dry-run (default)
    python tools/fix_l1_data.py --apply      # actually modify the database
    python tools/fix_l1_data.py --db path    # custom DB path
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> Path:
    data_dir = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
    return data_dir / "pce.db"


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _estimate_tokens(text: str) -> int:
    if not text:
        return 1
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
              or '\u3040' <= c <= '\u30ff'
              or '\uac00' <= c <= '\ud7af')
    words = len(text.split())
    estimate = int(cjk * 1.5 + (words - cjk) * 1.3)
    return max(estimate, 1)


def run_fixes(db_path: Path, apply: bool = False):
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    conn = get_connection(db_path)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"{'=' * 60}")
    print(f"  PCE L1 Data Fix [{mode}]")
    print(f"  Database: {db_path}")
    print(f"{'=' * 60}")

    # ── Fix 1: Delete provider='www' sessions (stale Kimi data) ──────
    print("\n[Fix 1] Delete provider='www' sessions (stale pre-fix Kimi)")
    www_sessions = conn.execute(
        "SELECT id, title_hint, message_count FROM sessions WHERE provider = 'www'"
    ).fetchall()
    print(f"  Found: {len(www_sessions)} sessions")
    for s in www_sessions:
        print(f"    {s['id'][:8]}: '{s['title_hint']}' ({s['message_count']} msgs)")
    if apply and www_sessions:
        ids = [s['id'] for s in www_sessions]
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM messages WHERE session_id IN ({ph})", ids)
        conn.execute(f"DELETE FROM sessions WHERE id IN ({ph})", ids)
        conn.commit()
        print(f"  -> Deleted {len(www_sessions)} sessions + their messages")

    # ── Fix 2: Delete incomplete single-message sessions ─────────────
    print("\n[Fix 2] Delete incomplete 1-message sessions (missing user OR assistant)")
    # Sessions with exactly 1 message where the expected counterpart is missing
    incomplete = conn.execute("""
        SELECT s.id, s.provider, s.title_hint, s.message_count,
               m.role as only_role, m.content_text
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        WHERE s.message_count <= 1
        AND s.provider IN ('unknown', 'huggingface')
        GROUP BY s.id
        HAVING COUNT(*) = 1
    """).fetchall()
    print(f"  Found: {len(incomplete)} sessions")
    for s in incomplete:
        ct = (s['content_text'] or '')[:50]
        print(f"    {s['id'][:8]} [{s['provider']}]: role={s['only_role']}, text='{ct}'")
    if apply and incomplete:
        ids = [s['id'] for s in incomplete]
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM messages WHERE session_id IN ({ph})", ids)
        conn.execute(f"DELETE FROM sessions WHERE id IN ({ph})", ids)
        conn.commit()
        print(f"  -> Deleted {len(incomplete)} sessions + their messages")

    # ── Fix 3: Delete moonshot sessions with only assistant (no user) ─
    print("\n[Fix 3] Delete moonshot sessions with only assistant messages (pre-DOM-fix)")
    moonshot_broken = conn.execute("""
        SELECT s.id, s.title_hint, s.message_count
        FROM sessions s
        WHERE s.provider = 'moonshot'
        AND NOT EXISTS (
            SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
        )
        AND EXISTS (
            SELECT 1 FROM messages m WHERE m.session_id = s.id
        )
    """).fetchall()
    print(f"  Found: {len(moonshot_broken)} sessions")
    for s in moonshot_broken:
        print(f"    {s['id'][:8]}: '{s['title_hint']}' ({s['message_count']} msgs)")
    if apply and moonshot_broken:
        ids = [s['id'] for s in moonshot_broken]
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM messages WHERE session_id IN ({ph})", ids)
        conn.execute(f"DELETE FROM sessions WHERE id IN ({ph})", ids)
        conn.commit()
        print(f"  -> Deleted {len(moonshot_broken)} sessions")

    # ── Fix 4: Backfill token_estimate ────────────────────────────────
    print("\n[Fix 4] Backfill token_estimate for messages where NULL")
    null_tokens = conn.execute(
        "SELECT COUNT(*) as c FROM messages WHERE token_estimate IS NULL"
    ).fetchone()["c"]
    print(f"  Found: {null_tokens} messages with NULL token_estimate")
    if apply and null_tokens > 0:
        rows = conn.execute(
            "SELECT id, content_text FROM messages WHERE token_estimate IS NULL"
        ).fetchall()
        updated = 0
        for r in rows:
            est = _estimate_tokens(r["content_text"] or "")
            conn.execute(
                "UPDATE messages SET token_estimate = ? WHERE id = ?",
                (est, r["id"]),
            )
            updated += 1
        conn.commit()
        print(f"  -> Updated {updated} messages with token estimates")

    # ── Fix 5: Update sessions.total tokens (recalculate message_count) ─
    print("\n[Fix 5] Reconcile sessions.message_count with actual message count")
    mismatches = conn.execute("""
        SELECT s.id, s.message_count as stored,
               (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as actual
        FROM sessions s
        WHERE s.message_count != (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id)
    """).fetchall()
    print(f"  Found: {len(mismatches)} sessions with mismatched message_count")
    for mm in mismatches[:5]:
        print(f"    {mm['id'][:8]}: stored={mm['stored']}, actual={mm['actual']}")
    if apply and mismatches:
        for mm in mismatches:
            conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (mm['actual'], mm['id']),
            )
        conn.commit()
        print(f"  -> Fixed {len(mismatches)} sessions")

    # ── Fix 6: Delete truncated streaming fragment messages ───────
    print("\n[Fix 6] Delete truncated streaming fragments (assistant, <4 chars, Latin-only)")
    truncated = conn.execute("""
        SELECT m.id, m.session_id, m.role, m.content_text, s.provider
        FROM messages m JOIN sessions s ON m.session_id = s.id
        WHERE m.role = 'assistant'
        AND LENGTH(TRIM(m.content_text)) < 4
        AND m.content_text NOT GLOB '*[\u4e00-\u9fff]*'
        AND LOWER(TRIM(m.content_text)) NOT IN ('ok', 'no', 'hi', 'yes')
    """).fetchall()
    print(f"  Found: {len(truncated)} truncated fragment messages")
    for t in truncated:
        print(f"    {t['id'][:8]} [{t['provider']}]: role={t['role']}, text='{t['content_text']}'")
    if apply and truncated:
        ids = [t['id'] for t in truncated]
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM messages WHERE id IN ({ph})", ids)
        # Update session message_count
        affected_sessions = set(t['session_id'] for t in truncated)
        for sid in affected_sessions:
            actual = conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()["c"]
            conn.execute("UPDATE sessions SET message_count = ? WHERE id = ?", (actual, sid))
        conn.commit()
        print(f"  -> Deleted {len(truncated)} messages, updated {len(affected_sessions)} sessions")

    # ── Fix 7: Delete single-message sessions missing their counterpart ──
    print("\n[Fix 7] Delete single-message sessions (only user OR only assistant, msg_count=1)")
    singletons = conn.execute("""
        SELECT s.id, s.provider, s.title_hint, m.role as only_role
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        WHERE s.message_count = 1
        GROUP BY s.id
        HAVING COUNT(*) = 1
    """).fetchall()
    print(f"  Found: {len(singletons)} single-message sessions")
    for s in singletons:
        print(f"    {s['id'][:8]} [{s['provider']}]: role={s['only_role']}, title='{(s['title_hint'] or '')[:40]}'")
    if apply and singletons:
        ids = [s['id'] for s in singletons]
        ph = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM messages WHERE session_id IN ({ph})", ids)
        conn.execute(f"DELETE FROM sessions WHERE id IN ({ph})", ids)
        conn.commit()
        print(f"  -> Deleted {len(singletons)} sessions")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    if not apply:
        print("  DRY-RUN complete. Re-run with --apply to execute changes.")
    else:
        print("  All fixes applied successfully.")
    print(f"{'=' * 60}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="PCE L1 Data Fix")
    parser.add_argument("--db", type=str, default=None, help="Path to pce.db")
    parser.add_argument("--apply", action="store_true", help="Actually apply fixes (default is dry-run)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db_path()
    run_fixes(db_path, apply=args.apply)


if __name__ == "__main__":
    main()
