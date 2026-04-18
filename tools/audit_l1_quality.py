# SPDX-License-Identifier: Apache-2.0
"""PCE L1 Data Quality Audit

Reads the live PCE database and produces a per-provider quality report
covering 10 check dimensions. Does NOT modify any data.

Usage:
    python tools/audit_l1_quality.py
    python tools/audit_l1_quality.py --db path/to/pce.db
    python tools/audit_l1_quality.py --provider openai --verbose
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Resolve DB path (same logic as pce_core.config)
# ---------------------------------------------------------------------------

def _default_db_path() -> Path:
    import os
    data_dir = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
    return data_dir / "pce.db"


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


# ---------------------------------------------------------------------------
# Audit checks
# ---------------------------------------------------------------------------

class AuditResult:
    def __init__(self, check_id: str, label: str, severity: str):
        self.check_id = check_id
        self.label = label
        self.severity = severity  # HIGH / MEDIUM / LOW
        self.count = 0
        self.total = 0
        self.details: list[str] = []

    @property
    def passed(self) -> bool:
        return self.count == 0

    @property
    def pct(self) -> float:
        if self.total == 0:
            return 100.0
        return (1 - self.count / self.total) * 100

    def status_str(self) -> str:
        if self.passed:
            return f"  {self.check_id} {self.label:<40s} {self.count:>4d}/{self.total:<5d} OK"
        icon = "FAIL" if self.severity == "HIGH" else "WARN"
        return f"  {self.check_id} {self.label:<40s} {self.count:>4d}/{self.total:<5d} {icon} [{self.severity}]"


def _is_truncated_content(text: str) -> bool:
    """Detect genuinely truncated content vs valid short messages.

    Valid short messages: CJK greetings (你好, 谢谢), test responses (PONG),
    single-word answers, etc.
    Truncated: partial words ending mid-sentence without punctuation, like "He", "I h", "Ref".
    """
    if not text:
        return True

    # CJK characters are information-dense; 2+ CJK chars is meaningful
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3040' <= c <= '\u30ff'
                    or '\uac00' <= c <= '\ud7af')
    if cjk_count >= 2:
        return False

    # Known valid short responses (case-insensitive)
    _VALID_SHORT = {"pong", "ping", "ok", "yes", "no", "hi", "hey", "done", "sure", "thanks"}
    if text.strip().lower() in _VALID_SHORT:
        return False

    # If it's a complete word/sentence with punctuation, it's valid
    if text.rstrip()[-1:] in '.!?。！？…':
        return False

    # Very short Latin text without complete words is likely truncated
    words = text.split()
    if len(words) <= 2 and len(text) < 5 and cjk_count == 0:
        return True

    return False


def audit_provider(conn: sqlite3.Connection, provider: str, verbose: bool = False) -> list[AuditResult]:
    """Run all quality checks for one provider. Returns list of AuditResult."""
    results = []

    # Fetch sessions for this provider
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE provider = ? ORDER BY started_at DESC",
        (provider,),
    ).fetchall()

    if not sessions:
        return results

    # Fetch all messages for these sessions
    session_ids = [s["id"] for s in sessions]
    placeholders = ",".join("?" * len(session_ids))
    messages = conn.execute(
        f"SELECT * FROM messages WHERE session_id IN ({placeholders}) ORDER BY ts",
        session_ids,
    ).fetchall()

    # Build session -> messages map
    sess_msgs: dict[str, list] = defaultdict(list)
    for m in messages:
        sess_msgs[m["session_id"]].append(m)

    total_msgs = len(messages)
    total_sessions = len(sessions)

    # ── Q1: content_text residual raw JSON ────────────────────────────
    q1 = AuditResult("Q1", "content_text residual raw JSON", "HIGH")
    q1.total = total_msgs
    for m in messages:
        ct = m["content_text"] or ""
        stripped = ct.strip()
        if stripped.startswith("{") and ("parts" in stripped[:80] or "content_type" in stripped[:80]):
            q1.count += 1
            if verbose:
                sid = m["session_id"][:8]
                q1.details.append(f"  msg {m['id'][:8]} (session {sid}): {stripped[:100]}")
    results.append(q1)

    # ── Q2: content_text empty or too short ───────────────────────────
    q2 = AuditResult("Q2", "content_text empty or genuinely truncated", "HIGH")
    q2.total = total_msgs
    for m in messages:
        ct = (m["content_text"] or "").strip()
        if not ct:
            q2.count += 1
            if verbose:
                q2.details.append(f"  msg {m['id'][:8]} role={m['role']}: EMPTY")
        elif _is_truncated_content(ct):
            q2.count += 1
            if verbose:
                q2.details.append(f"  msg {m['id'][:8]} role={m['role']}: '{ct}' (likely truncated)")
    results.append(q2)

    # ── Q3: session missing user message ──────────────────────────────
    q3 = AuditResult("Q3", "session missing user message", "HIGH")
    q3.total = total_sessions
    for s in sessions:
        roles = {m["role"] for m in sess_msgs.get(s["id"], [])}
        if "user" not in roles:
            q3.count += 1
            if verbose:
                q3.details.append(f"  session {s['id'][:8]}: roles={roles}, msgs={s['message_count']}")
    results.append(q3)

    # ── Q4: session missing assistant message ─────────────────────────
    q4 = AuditResult("Q4", "session missing assistant message", "HIGH")
    q4.total = total_sessions
    for s in sessions:
        roles = {m["role"] for m in sess_msgs.get(s["id"], [])}
        if "assistant" not in roles:
            q4.count += 1
            if verbose:
                q4.details.append(f"  session {s['id'][:8]}: roles={roles}, msgs={s['message_count']}")
    results.append(q4)

    # ── Q5: duplicate messages within session ─────────────────────────
    q5 = AuditResult("Q5", "duplicate messages (same role+text prefix)", "MEDIUM")
    q5.total = total_msgs
    for sid, msgs in sess_msgs.items():
        seen = set()
        for m in msgs:
            key = f"{m['role']}:{(m['content_text'] or '')[:200]}"
            if key in seen:
                q5.count += 1
                if verbose:
                    q5.details.append(f"  session {sid[:8]}: dupe '{key[:60]}'")
            seen.add(key)
    results.append(q5)

    # ── Q6: session missing title_hint ────────────────────────────────
    q6 = AuditResult("Q6", "session missing title_hint", "LOW")
    q6.total = total_sessions
    for s in sessions:
        if not (s["title_hint"] or "").strip():
            q6.count += 1
    results.append(q6)

    # ── Q7: session missing session_key ───────────────────────────────
    q7 = AuditResult("Q7", "session missing session_key", "LOW")
    q7.total = total_sessions
    for s in sessions:
        if not (s["session_key"] or "").strip():
            q7.count += 1
            if verbose:
                q7.details.append(f"  session {s['id'][:8]}: title='{(s['title_hint'] or '')[:40]}'")
    results.append(q7)

    # ── Q8: content_json expected but missing ─────────────────────────
    q8 = AuditResult("Q8", "content_json expected but missing", "MEDIUM")
    markers = {"[Image]", "[File:", "[Audio", "[Document:", "[Tool call:", "[Citation]", "[Code"}
    q8.total = total_msgs
    for m in messages:
        ct = m["content_text"] or ""
        has_marker = any(marker in ct for marker in markers)
        has_json = bool((m["content_json"] or "").strip())
        if has_marker and not has_json:
            q8.count += 1
            if verbose:
                q8.details.append(f"  msg {m['id'][:8]}: text has markers but no content_json")
    results.append(q8)

    # ── Q9: token_estimate missing ────────────────────────────────────
    q9 = AuditResult("Q9", "token_estimate missing", "LOW")
    q9.total = total_msgs
    for m in messages:
        if m["token_estimate"] is None:
            q9.count += 1
    results.append(q9)

    # ── Q10: orphan sessions (0 messages) ─────────────────────────────
    q10 = AuditResult("Q10", "orphan sessions (0 messages)", "MEDIUM")
    q10.total = total_sessions
    for s in sessions:
        if not sess_msgs.get(s["id"]):
            q10.count += 1
            if verbose:
                q10.details.append(f"  session {s['id'][:8]}: message_count field={s['message_count']}")
    results.append(q10)

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_provider_report(provider: str, sessions_count: int, messages_count: int,
                          results: list[AuditResult], verbose: bool = False):
    high_issues = sum(1 for r in results if not r.passed and r.severity == "HIGH")
    med_issues = sum(1 for r in results if not r.passed and r.severity == "MEDIUM")
    low_issues = sum(1 for r in results if not r.passed and r.severity == "LOW")

    print(f"\n{'─' * 70}")
    print(f"  Provider: {provider}")
    print(f"  Sessions: {sessions_count} | Messages: {messages_count}")
    print(f"{'─' * 70}")

    for r in results:
        print(r.status_str())
        if verbose and r.details:
            for d in r.details[:5]:
                print(f"    {d}")
            if len(r.details) > 5:
                print(f"    ... and {len(r.details) - 5} more")

    # Summary line
    if high_issues == 0 and med_issues == 0:
        print(f"  -> CLEAN (low issues: {low_issues})")
    elif high_issues == 0:
        print(f"  -> OK (medium: {med_issues}, low: {low_issues})")
    else:
        print(f"  -> NEEDS FIX (high: {high_issues}, medium: {med_issues}, low: {low_issues})")


def run_audit(db_path: Path, filter_provider: Optional[str] = None, verbose: bool = False):
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    conn = get_connection(db_path)

    print("=" * 70)
    print("  PCE L1 Quality Audit")
    print(f"  Database: {db_path}")
    print(f"  Size: {db_path.stat().st_size / 1024 / 1024:.1f} MB")
    print("=" * 70)

    # Get all providers
    providers_rows = conn.execute(
        "SELECT provider, COUNT(*) as sess_count FROM sessions GROUP BY provider ORDER BY sess_count DESC"
    ).fetchall()

    if not providers_rows:
        print("\n  No sessions found in database.")
        conn.close()
        return

    # Global stats
    total_sessions = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
    total_messages = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
    total_captures = conn.execute("SELECT COUNT(*) as c FROM raw_captures").fetchone()["c"]
    print(f"\n  Total: {total_captures} raw_captures → {total_sessions} sessions → {total_messages} messages")

    # Global issue accumulators
    global_high = 0
    global_med = 0
    global_low = 0
    provider_grades: list[tuple[str, str]] = []

    for row in providers_rows:
        provider = row["provider"]
        if filter_provider and provider != filter_provider:
            continue

        sess_count = row["sess_count"]
        msg_count = conn.execute(
            "SELECT COUNT(*) as c FROM messages m JOIN sessions s ON m.session_id = s.id WHERE s.provider = ?",
            (provider,),
        ).fetchone()["c"]

        results = audit_provider(conn, provider, verbose=verbose)
        print_provider_report(provider, sess_count, msg_count, results, verbose=verbose)

        high = sum(1 for r in results if not r.passed and r.severity == "HIGH")
        med = sum(1 for r in results if not r.passed and r.severity == "MEDIUM")
        low = sum(1 for r in results if not r.passed and r.severity == "LOW")
        global_high += high
        global_med += med
        global_low += low

        if high > 0:
            provider_grades.append((provider, "NEEDS FIX"))
        elif med > 0:
            provider_grades.append((provider, "OK"))
        else:
            provider_grades.append((provider, "CLEAN"))

    # ── Global summary ────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("  OVERALL SUMMARY")
    print(f"{'=' * 70}")
    for prov, grade in provider_grades:
        icon = "[OK]" if grade == "CLEAN" else ("[!!]" if grade == "OK" else "[XX]")
        print(f"  {icon} {prov:<25s} {grade}")

    clean = sum(1 for _, g in provider_grades if g == "CLEAN")
    total_p = len(provider_grades)
    print(f"\n  {clean}/{total_p} providers fully clean")
    print(f"  Issues: {global_high} HIGH, {global_med} MEDIUM, {global_low} LOW")

    if global_high > 0:
        print(f"\n  ** ACTION REQUIRED: {global_high} HIGH severity issues found. **")
    elif global_med > 0:
        print(f"\n  Some MEDIUM issues found. Consider fixing for better quality.")
    else:
        print(f"\n  All providers clean at HIGH/MEDIUM level.")

    print(f"{'=' * 70}")
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PCE L1 Data Quality Audit")
    parser.add_argument("--db", type=str, default=None, help="Path to pce.db")
    parser.add_argument("--provider", type=str, default=None, help="Audit only this provider")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show details for each issue")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db_path()
    run_audit(db_path, filter_provider=args.provider, verbose=args.verbose)


if __name__ == "__main__":
    main()
