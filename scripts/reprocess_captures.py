"""Re-process existing conversation captures through the updated normalizer.

The dedup logic in _persist_result will:
- Skip messages that already exist (by content hash)
- Enrich existing messages with new content_json if they now have attachments
- This means re-processing is safe and idempotent.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import json

db_path = os.path.expanduser("~/.pce/data/pce.db")

def main():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all conversation captures
    captures = conn.execute(
        "SELECT * FROM raw_captures WHERE direction='conversation' ORDER BY created_at"
    ).fetchall()
    
    print(f"Found {len(captures)} conversation captures to re-process\n")
    
    # Before stats
    before_cj = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE content_json IS NOT NULL"
    ).fetchone()[0]
    before_total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"Before: {before_total} messages, {before_cj} with content_json\n")
    
    conn.close()

    # Re-process each capture through the pipeline
    from pce_core.normalizer.pipeline import normalize_conversation
    
    for i, cap in enumerate(captures):
        cap_dict = dict(cap)
        try:
            session_id = normalize_conversation(
                cap_dict,
                source_id=cap_dict.get("source_id", ""),
                created_via="reprocess",
            )
            if session_id:
                print(f"  [{i+1}/{len(captures)}] Re-processed -> session {session_id[:12]}")
            else:
                print(f"  [{i+1}/{len(captures)}] Skipped (no result)")
        except Exception as e:
            print(f"  [{i+1}/{len(captures)}] Error: {e}")

    # After stats
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    after_cj = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE content_json IS NOT NULL"
    ).fetchone()[0]
    after_total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    
    print(f"\nAfter: {after_total} messages, {after_cj} with content_json")
    print(f"New content_json enrichments: {after_cj - before_cj}")
    
    # Show attachment breakdown
    att_types = {}
    for r in conn.execute("SELECT content_json FROM messages WHERE content_json IS NOT NULL").fetchall():
        try:
            for a in json.loads(r[0]).get('attachments', []):
                t = a.get('type', '?')
                att_types[t] = att_types.get(t, 0) + 1
        except:
            pass
    
    if att_types:
        print("\nAttachment types:")
        for t, c in sorted(att_types.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")
    
    conn.close()

if __name__ == "__main__":
    main()
