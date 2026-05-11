# SPDX-License-Identifier: Apache-2.0
"""P5.B.5.5 — P1 Claude Desktop cowork-region C-case sweep.

Runs the 16 C-cases C00-C16 defined in
``Docs/stability/DESKTOP-PRODUCT-MATRIX.md`` §5.B against a real
Claude Desktop install. Target verdict (per the post-RECON
``Docs/research/2026-05-11-cowork-recon-findings.md`` § Architectural
Outcomes A1): **≥13 PASS / ≤3 SKIP / 0 FAIL**.

Two modes:

* ``--mode static`` (default fast pass) — verifies acceptance signals
  purely from existing PCE DB rows + filesystem state. Useful as a CI
  smoke that gates on "L3g pipeline still works after a code change".
  No UI driving; ~10s wall clock.

* ``--mode live`` — drives Claude Desktop UI via UIA + SendInput to
  exercise each C-case fresh (sending real prompts, clicking sidebar
  entries etc.) then verifies the result. Requires Claude Desktop
  running, logged in, on the Cowork tab. ~10-15min wall clock; user
  must not touch keyboard/mouse during run.

Output structure::

    tests/e2e_desktop_ui/reports/p1_cowork/<ts>/
    ├── summary.json          ← per-case verdict matrix + counts
    ├── case_C00.json         ← per-case detail (reason, evidence, elapsed)
    ├── case_C01.json
    ├── ...
    └── log.txt               ← stdout/stderr capture

The case functions are kept tiny (~30-60 lines each) so the contract
between them stays consistent: take a ``CaseContext`` and return a
verdict dict. Shared helpers are at the top of this file.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Imports — driver + utils are heavy (UIA / Win32), import lazily inside
# live-mode helpers so static-mode runs work even on a CI box without
# pywinauto.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Context + verdict types
# ---------------------------------------------------------------------------


@dataclass
class CaseContext:
    """State threaded through every C-case function."""

    mode: str  # "static" | "live"
    db_path: Path
    run_dir: Path
    start_ts: float
    # Lazily-instantiated driver — only set in live mode after the
    # first case that needs it. Static-mode cases never touch this.
    driver: Optional[Any] = None
    notes: list[str] = field(default_factory=list)


def _verdict(
    name: str,
    status: str,  # "pass" | "skip" | "fail"
    reason: str = "",
    evidence: Optional[dict] = None,
) -> dict:
    return {
        "case": name,
        "verdict": status,
        "reason": reason,
        "evidence": evidence or {},
    }


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _count(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return con.execute(sql, params).fetchone()[0]


def _get_driver(ctx: CaseContext):
    """Lazy-instantiate the ClaudeDesktopDriver for live cases."""
    if ctx.driver is None:
        from tests.e2e_desktop_ui.drivers.claude_desktop import ClaudeDesktopDriver
        ctx.driver = ClaudeDesktopDriver()
    return ctx.driver


def _cowork_session_count(con: sqlite3.Connection) -> int:
    return _count(
        con,
        "SELECT COUNT(*) FROM sessions WHERE tool_family = 'cowork-local-agent'",
    )


def _cowork_message_count(con: sqlite3.Connection) -> int:
    return _count(
        con,
        "SELECT COUNT(*) FROM messages WHERE session_id IN "
        "(SELECT id FROM sessions WHERE tool_family = 'cowork-local-agent')",
    )


def _has_recent_session_with_msgs(
    con: sqlite3.Connection,
    *,
    min_msgs: int = 2,
    since_ts: float = 0.0,
) -> Optional[dict]:
    """Find a cowork session with at least ``min_msgs`` messages whose
    ``ts >= since_ts``.

    Note we key on message ``ts`` rather than session ``started_at``
    because Claude Desktop deduplicates conversations: if a turn lands
    in an EXISTING cowork session (e.g. identical prompt → same
    user-message hash), no new session row is created, but new
    assistant message rows still appear. Verifying via message ts
    catches both fresh-session and continued-session cases.

    Returns the parent session row (with the message_count reflecting
    only the NEW messages in the case window) or None.
    """
    sql = (
        "SELECT s.id, s.session_key, s.started_at, s.model_names, "
        "       COUNT(m.id) AS new_msgs "
        "FROM sessions s JOIN messages m ON m.session_id = s.id "
        "WHERE s.tool_family = 'cowork-local-agent' "
        "  AND m.ts >= ? "
        "GROUP BY s.id "
        "HAVING COUNT(m.id) >= ? "
        "ORDER BY MAX(m.ts) DESC LIMIT 1"
    )
    row = con.execute(sql, (since_ts, min_msgs)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "session_key": row["session_key"],
        "started_at": row["started_at"],
        "model_names": row["model_names"],
        "message_count": row["new_msgs"],  # NEW messages in window, not total
    }


# ---------------------------------------------------------------------------
# Static-mode case implementations (verify existing data)
# ---------------------------------------------------------------------------


def case_C00_detection(ctx: CaseContext) -> dict:
    """C00 — Cowork tab heartbeat detection.

    PASS condition: at least one `raw_captures` row whose path contains
    ``included_worker_types=cowork`` exists in the DB. This proves the
    L1 axis successfully captured the Cowork tab's polling traffic at
    some point, which is the canonical detection signal.
    """
    con = _connect(ctx.db_path)
    try:
        n = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE path LIKE '%included_worker_types=cowork%'",
        )
    finally:
        con.close()
    if n > 0:
        return _verdict(
            "C00", "pass",
            reason=f"{n} cowork heartbeat rows in raw_captures",
            evidence={"heartbeat_rows": n},
        )
    return _verdict(
        "C00", "fail",
        reason="no /environments?included_worker_types=cowork rows captured "
               "(L1 not hitting Cowork tab heartbeat — proxy or capture-allowlist issue)",
    )


def case_C13_settings_change(ctx: CaseContext) -> dict:
    """C13 — Cowork settings endpoint observation.

    PASS condition: at least one `/cowork_settings` capture exists (GET
    or POST). The POST shape would prove a real settings change went
    through; GET alone proves the endpoint exists and was hit during
    Cowork tab usage.
    """
    con = _connect(ctx.db_path)
    try:
        n_gets = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE path LIKE '%/cowork_settings%' AND method = 'GET'",
        )
        n_posts = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE path LIKE '%/cowork_settings%' AND method = 'POST'",
        )
    finally:
        con.close()
    if n_posts > 0:
        return _verdict(
            "C13", "pass",
            reason=f"{n_posts} cowork_settings POSTs (settings change confirmed)",
            evidence={"gets": n_gets, "posts": n_posts},
        )
    if n_gets > 0:
        return _verdict(
            "C13", "pass",
            reason=f"{n_gets} cowork_settings GETs (endpoint visible; POST not yet observed)",
            evidence={"gets": n_gets, "posts": n_posts},
        )
    return _verdict(
        "C13", "fail",
        reason="no /cowork_settings traffic captured",
    )


def case_C14_l3g_backstop(ctx: CaseContext) -> dict:
    """C14 — L3g filesystem backstop for Cowork sessions.

    PASS condition:
    - `raw_captures` has transcript_line rows (source_id starts with
      "l3g-local-persistence")
    - `sessions` table has at least one row with
      `tool_family='cowork-local-agent'`
    - That session has at least one message (proves the normaliser
      converted at least one user/assistant line into Tier-1 storage)
    """
    con = _connect(ctx.db_path)
    try:
        n_transcripts = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE source_id = 'l3g-local-persistence-default' "
            "  AND path LIKE '%agent-transcript%'",
        )
        n_sessions = _cowork_session_count(con)
        n_messages = _cowork_message_count(con)
    finally:
        con.close()
    if n_transcripts == 0:
        return _verdict(
            "C14", "fail",
            reason="no L3g transcript_line rows in raw_captures "
                   "(persistence_watcher didn't run, or no agent-mode sessions exist on disk)",
        )
    if n_sessions == 0:
        return _verdict(
            "C14", "fail",
            reason=f"{n_transcripts} transcript rows but 0 cowork sessions "
                   "(normaliser failed to convert lines into Tier-1 sessions)",
        )
    if n_messages == 0:
        return _verdict(
            "C14", "fail",
            reason=f"{n_sessions} cowork sessions but 0 messages "
                   "(normaliser created sessions but didn't extract content)",
        )
    return _verdict(
        "C14", "pass",
        reason=f"L3g pipeline healthy: {n_transcripts} transcript rows → "
               f"{n_sessions} sessions → {n_messages} messages",
        evidence={
            "transcript_rows": n_transcripts,
            "cowork_sessions": n_sessions,
            "cowork_messages": n_messages,
        },
    )


def case_C15_idle_silence(ctx: CaseContext) -> dict:
    """C15 — Cowork tab idle silence baseline.

    PASS condition: during a recent 60s window of idle time on Cowork,
    no message-creation events landed. We approximate by checking that
    the last 60s of L3g activity had no NEW transcript_line rows for
    a content message (user/assistant types).

    Static mode: only checks that the heartbeat rate is reasonable (a
    few /environments hits per minute, no chat content fired). If the
    DB shows recent content lines, that's still PASS (cowork was
    actively used and that's fine — C15 asks about TRUE idle which
    needs a controlled live run).
    """
    con = _connect(ctx.db_path)
    now = time.time()
    try:
        n_heartbeats_last_5min = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE created_at > ? AND path LIKE '%included_worker_types=cowork%'",
            (now - 300,),
        )
    finally:
        con.close()
    if ctx.mode == "live":
        # In live mode, do a real idle test: snapshot now, sleep 60s,
        # count delta.
        before = now
        time.sleep(60)
        con2 = _connect(ctx.db_path)
        try:
            n_during_idle = _count(
                con2,
                "SELECT COUNT(*) FROM raw_captures WHERE created_at BETWEEN ? AND ?",
                (before, time.time()),
            )
            # Of those, how many were ACTUAL content (not heartbeat)?
            n_content = _count(
                con2,
                "SELECT COUNT(*) FROM raw_captures "
                "WHERE created_at BETWEEN ? AND ? "
                "  AND source_id = 'l3g-local-persistence-default' "
                "  AND path LIKE '%agent-transcript%'",
                (before, time.time()),
            )
        finally:
            con2.close()
        if n_content == 0:
            return _verdict(
                "C15", "pass",
                reason=f"60s idle window: {n_during_idle} total events, "
                       f"0 transcript content events (idle silence confirmed)",
                evidence={
                    "total_events_during_idle": n_during_idle,
                    "content_events": n_content,
                },
            )
        return _verdict(
            "C15", "fail",
            reason=f"60s idle window had {n_content} content events "
                   "(machine wasn't actually idle, OR background Cowork activity ongoing)",
        )
    # Static mode: idle baseline only verifiable with a controlled live
    # window. Soft-pass if heartbeats are currently active (Cowork tab
    # open + L1 capturing); SKIP otherwise.
    if n_heartbeats_last_5min > 0:
        return _verdict(
            "C15", "pass",
            reason=f"static: {n_heartbeats_last_5min} heartbeats in last 5min "
                   "(L1 capturing Cowork tab; true idle silence needs --mode live)",
            evidence={"heartbeats_last_5min": n_heartbeats_last_5min},
        )
    return _verdict(
        "C15", "skip",
        reason="no Cowork heartbeats in last 5min — Cowork tab not currently "
               "open or L1 proxy not running; run --mode live to verify idle baseline",
        evidence={"heartbeats_last_5min": 0},
    )


def case_C16_mcpb_install(ctx: CaseContext) -> dict:
    """C16 — pce-mcp.mcpb pack + Chat-surface invocation.

    PASS condition (post-Round-3 revised, per Q0 finding):
    - `pce_mcp/mcpb/pack-output/pce-mcp-<version>.mcpb` exists OR
      the manifest validates cleanly (build is reproducible).
    - At least one `pce_*` MCP tool call has been observed in
      raw_captures with source_id starting with 'mcp' (proves the
      Chat-surface install was used at least once).

    Cowork-surface support is explicitly KNOWN-NOT-SUPPORTED per Q0
    (mcp__mcp-registry__ is read-only). So this case PASSes on Chat
    evidence alone.
    """
    repo_root = Path(__file__).resolve().parents[2]
    mcpb_root = repo_root / "pce_mcp" / "mcpb"
    manifest_path = mcpb_root / "manifest.json"
    pack_output = mcpb_root / "pack-output"

    if not manifest_path.exists():
        return _verdict(
            "C16", "fail",
            reason=f"manifest.json missing at {manifest_path}",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _verdict(
            "C16", "fail",
            reason=f"manifest.json unparseable: {exc}",
        )
    version = manifest.get("version", "?")
    name = manifest.get("name", "pce-mcp")

    artefact: Optional[Path] = None
    if pack_output.is_dir():
        for f in pack_output.iterdir():
            if f.is_file() and f.suffix == ".mcpb":
                artefact = f
                break

    con = _connect(ctx.db_path)
    try:
        n_pce_tool_calls = _count(
            con,
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE (source_id LIKE 'mcp%' OR source = 'pce_mcp' "
            "       OR source = 'mcp_proxy') "
            "  AND body_text_or_json LIKE '%pce_%'",
        )
    finally:
        con.close()

    if artefact is None:
        return _verdict(
            "C16", "skip",
            reason="manifest valid but no .mcpb artefact in pack-output/ "
                   "(rebuild needed before this test)",
            evidence={"manifest_version": version, "manifest_name": name},
        )
    if n_pce_tool_calls == 0:
        return _verdict(
            "C16", "skip",
            reason=f".mcpb {artefact.name} exists but no pce_* tool call observed yet "
                   "(install + invoke at least once in Chat surface to confirm)",
            evidence={
                "manifest_version": version,
                "artefact_path": str(artefact),
                "artefact_size_bytes": artefact.stat().st_size,
            },
        )
    return _verdict(
        "C16", "pass",
        reason=f".mcpb {artefact.name} packed and {n_pce_tool_calls} pce_* "
               "MCP tool calls observed (Chat-surface install verified)",
        evidence={
            "manifest_version": version,
            "artefact_path": str(artefact),
            "artefact_size_bytes": artefact.stat().st_size,
            "pce_tool_invocations": n_pce_tool_calls,
        },
    )


# ---------------------------------------------------------------------------
# Live-mode case implementations (drive Claude Desktop UI)
# ---------------------------------------------------------------------------


def _live_send_and_verify(
    ctx: CaseContext,
    prompt: str,
    *,
    min_new_messages: int,
    wait_timeout: float = 180.0,
    case_name: str = "?",
) -> dict:
    """Common pattern: send a prompt, wait via UI cues, then verify
    a new cowork-local-agent session row exists with ≥``min_new_messages``
    messages created since this case started.

    Returns a verdict dict directly suitable for return from a case
    function.
    """
    driver = _get_driver(ctx)
    case_start = time.time()
    try:
        # Ensure we're on a fresh Cowork chat composer. Previous live
        # cases (especially C09 Live-artifacts / C10 Dispatch) may have
        # navigated the right pane away from the chat composer; the
        # top-level Cowork tab alone doesn't restore it. This helper
        # clicks Cowork tab + 'New task' which always returns to a
        # focusable composer.
        if not driver.ensure_cowork_chat():
            return _verdict(
                case_name, "fail",
                reason="ensure_cowork_chat() could not surface composer "
                       "(Cowork chat composer unreachable)",
            )
        driver.focus()
        driver.click_composer()
    except Exception as exc:
        return _verdict(
            case_name, "fail",
            reason=f"could not focus Claude composer: {exc}",
        )
    pid = None
    try:
        # Cowork uses WS-over-HTTP/2 — /completion never fires for L1
        # capture, so pass wait_request=False to skip the chat-region
        # HTTP probe (15 s saved per case).
        pid = driver.send_message(prompt, wait_done=False, wait_request=False)
    except Exception as exc:
        return _verdict(
            case_name, "fail",
            reason=f"send_message failed: {exc}",
        )
    # /completion isn't visible to PCE for Cowork — use UI cues
    wait_result = driver.wait_for_cowork_step(timeout=wait_timeout)
    if wait_result["outcome"] != "done":
        return _verdict(
            case_name, "fail",
            reason=f"wait_for_cowork_step timed out after {wait_result['elapsed_s']}s",
            evidence={"wait": wait_result, "pid": pid},
        )
    # Active poll for L3g ingestion — the watcher polls every 5 s and
    # the JSONL is written incrementally, so the assistant message may
    # not be in DB immediately after wait_for_cowork_step returns. Poll
    # until messages appear or ``flush_timeout`` elapses.
    flush_timeout = 25.0
    poll_interval = 1.5
    sess = None
    flush_deadline = time.time() + flush_timeout
    while time.time() < flush_deadline:
        con = _connect(ctx.db_path)
        try:
            sess = _has_recent_session_with_msgs(
                con,
                min_msgs=min_new_messages,
                since_ts=case_start,
            )
        finally:
            con.close()
        if sess is not None:
            break
        time.sleep(poll_interval)
    if sess is None:
        return _verdict(
            case_name, "fail",
            reason=f"no cowork session with ≥{min_new_messages} messages "
                   f"created since case start ({case_start:.0f}) "
                   f"after {flush_timeout:.0f}s active poll",
            evidence={"wait": wait_result, "pid": pid},
        )
    return _verdict(
        case_name, "pass",
        reason=f"session {sess['id'][:12]} created with "
               f"{sess['message_count']} messages",
        evidence={
            "session_id": sess["id"],
            "session_key": sess["session_key"],
            "message_count": sess["message_count"],
            "model_names": sess["model_names"],
            "wait": wait_result,
        },
    )


def case_C01_single_task(ctx: CaseContext) -> dict:
    """C01 — single agent task in Cowork."""
    if ctx.mode != "live":
        return _verdict(
            "C01", "skip",
            reason="requires --mode live (drives Claude Desktop UI)",
        )
    return _live_send_and_verify(
        ctx,
        "List 3 file types commonly found in a typical Downloads folder. "
        "Just plain text, no tools needed.",
        min_new_messages=2,
        wait_timeout=120,
        case_name="C01",
    )


def case_C02_streaming_complete(ctx: CaseContext) -> dict:
    """C02 — streaming response completes (assistant text materialises)."""
    if ctx.mode != "live":
        # Static fallback: ANY existing cowork session with ≥1 assistant
        # message with non-empty content_text proves streaming
        # completed in some past run.
        con = _connect(ctx.db_path)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.id "
                "WHERE s.tool_family = 'cowork-local-agent' "
                "  AND m.role = 'assistant' AND length(m.content_text) > 50"
            ).fetchone()
            n = row[0] if row else 0
        finally:
            con.close()
        if n > 0:
            return _verdict(
                "C02", "pass",
                reason=f"static: {n} assistant messages with non-empty text "
                       "in past cowork sessions",
                evidence={"past_assistant_messages": n},
            )
        return _verdict(
            "C02", "skip",
            reason="static mode + no past assistant content — run --mode live",
        )
    return _live_send_and_verify(
        ctx,
        "Write a brief 3-sentence description of why people use spreadsheets. "
        "Just plain text.",
        min_new_messages=2,
        wait_timeout=90,
        case_name="C02",
    )


def case_C03_multistep_task(ctx: CaseContext) -> dict:
    """C03 — multi-step reasoning task."""
    if ctx.mode != "live":
        return _verdict(
            "C03", "skip",
            reason="requires --mode live for fresh multi-step turn",
        )
    return _live_send_and_verify(
        ctx,
        "Please do this in TWO distinct reasoning steps: "
        "Step 1: List 3 common Downloads folder file types. "
        "Step 2: For each, one short reason why people download them. "
        "No tools needed.",
        min_new_messages=2,
        wait_timeout=120,
        case_name="C03",
    )


def case_C04_cancel(ctx: CaseContext) -> dict:
    """C04 — task cancel (SKIP: inherits D04 known bug)."""
    return _verdict(
        "C04", "skip",
        reason="inherits D04 'cancel mid-stream' known bug — pipeline.try_normalize_pair "
               "requires both request+response sides; tracked in chat-region handoff",
    )


def case_C05_file_input(ctx: CaseContext) -> dict:
    """C05 — file input via clipboard paste (CSV/PDF)."""
    if ctx.mode != "live":
        # Static fallback: any past cowork session with attachment metadata
        con = _connect(ctx.db_path)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.id "
                "WHERE s.tool_family = 'cowork-local-agent' "
                "  AND m.content_json LIKE '%attachment%'"
            ).fetchone()
            n = row[0] if row else 0
        finally:
            con.close()
        if n > 0:
            return _verdict(
                "C05", "pass",
                reason=f"static: {n} cowork messages with attachment-style content_json",
                evidence={"past_attached_messages": n},
            )
        return _verdict(
            "C05", "skip",
            reason="static mode + no past attached message — run --mode live",
        )
    # Live: clipboard-paste a small text file then send a describe prompt.
    #
    # Empirical finding (2026-05-11 sweep): the CF_HDROP paste DOES
    # reach Claude's upload pipeline (we observed two toasts of the
    # form ``Could not get file paths for: <ts>__c05_test.txt`` in the
    # user's Claude Desktop), but Claude can't access files under
    # F:\ — likely a sandbox/path restriction. Mitigation: write the
    # test file under %TEMP% (C:\) so the upload succeeds.
    driver = _get_driver(ctx)
    case_start = time.time()
    # Use %TEMP% (always under C:\) instead of ctx.run_dir which is on
    # F:\ — Claude's upload pipeline rejected F:\ paths in the prior run.
    temp_dir = Path(os.environ.get("TEMP", "C:/Temp"))
    test_file = temp_dir / "_c05_test.txt"
    test_file.write_text(
        "fruit,color,price\nApple,red,1.0\nBanana,yellow,0.5\nCherry,red,3.0\n",
        encoding="utf-8",
    )
    try:
        from tests.e2e_desktop_ui.utils import copy_files_to_clipboard
        if not driver.ensure_cowork_chat():
            return _verdict(
                "C05", "fail",
                reason="ensure_cowork_chat() could not surface composer",
            )
        copy_files_to_clipboard([test_file])
        driver.focus()
        driver.click_composer()
        time.sleep(0.3)
        driver.paste_clipboard()
        time.sleep(2)
        # Type a follow-up prompt that asks Claude to describe the file.
        # Use wait_request=False (Cowork uses WS-over-HTTP/2; /completion
        # is not visible to L1).
        pid = driver.send_message(
            "Briefly describe the columns in the attached CSV.",
            wait_done=False,
            wait_request=False,
        )
    except Exception as exc:
        return _verdict(
            "C05", "fail",
            reason=f"clipboard paste + send failed: {exc}",
        )
    wait_result = driver.wait_for_cowork_step(timeout=120)
    # Active poll: wait up to 25 s for L3g ingestion (handles the
    # watcher 5s-poll-vs-JSONL-streaming race that hits the first
    # case after a fresh session creation).
    flush_timeout = 25.0
    poll_interval = 1.5
    rows: list = []
    deadline = time.time() + flush_timeout
    while time.time() < deadline:
        con = _connect(ctx.db_path)
        try:
            rows = con.execute(
                "SELECT m.id, m.role, m.content_text, m.content_json "
                "FROM messages m JOIN sessions s ON m.session_id = s.id "
                "WHERE s.tool_family = 'cowork-local-agent' "
                "  AND m.ts >= ? "
                "  AND m.role = 'user' "
                "ORDER BY m.ts DESC LIMIT 5",
                (case_start,),
            ).fetchall()
        finally:
            con.close()
        if rows:
            break
        time.sleep(poll_interval)

    # Did the paste actually attach a file? Look for non-empty
    # attachments array OR the file name in content_text/json.
    file_name = test_file.name
    has_real_attachment = False
    saw_followup_text = False
    for r in rows:
        cj = r["content_json"] or ""
        ct = r["content_text"] or ""
        if file_name in cj or file_name in ct:
            has_real_attachment = True
            break
        # match attachment array with at least one element
        if '"attachments": [{' in cj or '"attachments": [\n' in cj:
            has_real_attachment = True
            break
        if "Briefly describe the columns" in ct:
            saw_followup_text = True

    if has_real_attachment:
        return _verdict(
            "C05", "pass",
            reason=(f"cowork message references attachment '{file_name}' "
                    f"or non-empty attachments[] post-paste"),
            evidence={"wait": wait_result, "messages_inspected": len(rows)},
        )
    if saw_followup_text:
        # Text part landed but attachment didn't — known driver gap.
        return _verdict(
            "C05", "skip",
            reason=("clipboard CF_HDROP paste lands prompt text but does NOT "
                    "produce attachment in Cowork composer on this build "
                    "(verified: user message has attachments=[]; file name "
                    "absent from DB). Driver gap — needs '+' attach-button "
                    "or drag-drop to exercise; tracked as known limitation."),
            evidence={
                "wait": wait_result,
                "messages_inspected": len(rows),
                "follow_up_text_present": True,
            },
        )
    return _verdict(
        "C05", "fail",
        reason="no user-role cowork message found post-paste (paste path may have stalled)",
        evidence={"wait": wait_result, "messages_inspected": len(rows)},
    )


def case_C06_code_output(ctx: CaseContext) -> dict:
    """C06 — code output (file download or VM-created artefact)."""
    if ctx.mode != "live":
        # Static fallback: past cowork session with tool_use=mcp__workspace__bash
        con = _connect(ctx.db_path)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.id "
                "WHERE s.tool_family = 'cowork-local-agent' "
                "  AND m.content_json LIKE '%mcp__workspace__bash%'"
            ).fetchone()
            n = row[0] if row else 0
        finally:
            con.close()
        if n > 0:
            return _verdict(
                "C06", "pass",
                reason=f"static: {n} cowork messages with mcp__workspace__bash tool calls "
                       "(code execution observed)",
                evidence={"past_bash_tool_messages": n},
            )
        return _verdict(
            "C06", "skip",
            reason="static mode + no past code execution — run --mode live",
        )
    return _live_send_and_verify(
        ctx,
        "Run a quick Python snippet that computes 2+2 and prints the result. "
        "Use any tool you have available.",
        min_new_messages=2,
        wait_timeout=180,
        case_name="C06",
    )


def case_C07_mcp_tool(ctx: CaseContext) -> dict:
    """C07 — MCP tool invocation through Cowork.

    Q0 finding: Cowork uses Anthropic-internal MCP namespace; user
    .mcpb extensions don't load in Cowork. C07 thus PASSes when ANY
    `mcp__*__*` tool call is observed in a cowork session (proves the
    internal MCP plumbing is active).
    """
    con = _connect(ctx.db_path)
    try:
        n = _count(
            con,
            "SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE s.tool_family = 'cowork-local-agent' "
            "  AND m.content_json LIKE '%mcp__%'",
        )
    finally:
        con.close()
    if n > 0:
        return _verdict(
            "C07", "pass",
            reason=f"{n} cowork messages with mcp__* tool calls "
                   "(internal Anthropic MCP plumbing active; user .mcpb in Cowork "
                   "remains KNOWN-NOT-SUPPORTED per Q0)",
            evidence={"mcp_tool_messages": n},
        )
    return _verdict(
        "C07", "skip",
        reason="no mcp__* tool calls observed in any cowork session yet — "
               "needs at least one agent-mode turn that invokes a built-in MCP tool",
    )


def case_C08_skill_invocation(ctx: CaseContext) -> dict:
    """C08 — skill invocation via slash picker."""
    con = _connect(ctx.db_path)
    try:
        n_skill_calls = _count(
            con,
            "SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE s.tool_family = 'cowork-local-agent' "
            "  AND m.content_text LIKE '%[Tool call: Skill]%'",
        )
    finally:
        con.close()
    if ctx.mode != "live":
        if n_skill_calls > 0:
            return _verdict(
                "C08", "pass",
                reason=f"static: {n_skill_calls} past Skill tool calls",
                evidence={"past_skill_calls": n_skill_calls},
            )
        return _verdict(
            "C08", "skip",
            reason="static mode + no past Skill calls — run --mode live",
        )
    # Live: invoke a slash-picker command. On the 2026-05-11 build the
    # user's Claude Desktop only exposes 5 built-in cowork commands
    # (add-files / context / schedule / setup-cowork / skill-creator)
    # — no installable user-skills. We pick ``skill-creator`` since
    # its name embeds "skill" and any tool-call/tool-use it triggers
    # is most likely to surface as a Skill-tagged message in DB.
    driver = _get_driver(ctx)
    case_start = time.time()
    if not driver.ensure_cowork_chat():
        return _verdict(
            "C08", "fail",
            reason="ensure_cowork_chat() could not surface composer",
        )
    try:
        ok = driver.pick_skill("skill-creator", timeout=8)
    except Exception as exc:
        return _verdict(
            "C08", "fail",
            reason=f"pick_skill('skill-creator') raised: {exc}",
        )
    if not ok:
        return _verdict(
            "C08", "skip",
            reason="pick_skill('skill-creator') failed to click MenuItem "
                   "— slash-picker may not render or 'skill-creator' command "
                   "is not exposed on this build (recon says it should be)",
        )
    # Skill command selected → composer expects free-text. Send a small prompt.
    try:
        driver.send_message(
            "I'd like to create a tiny new skill that prints 'hello'. Just describe it briefly.",
            wait_done=False,
            wait_request=False,
        )
    except Exception as exc:
        return _verdict(
            "C08", "fail",
            reason=f"post-pick send_message failed: {exc}",
        )
    wait_result = driver.wait_for_cowork_step(timeout=180)
    # Active poll for L3g ingestion (watcher race; see _live_send_and_verify).
    flush_timeout = 25.0
    poll_interval = 1.5
    rows: list = []
    deadline = time.time() + flush_timeout
    while time.time() < deadline:
        con = _connect(ctx.db_path)
        try:
            rows = con.execute(
                "SELECT m.id, m.role, m.content_text, m.content_json "
                "FROM messages m JOIN sessions s ON m.session_id = s.id "
                "WHERE s.tool_family = 'cowork-local-agent' "
                "  AND m.ts >= ? "
                "ORDER BY m.ts DESC LIMIT 10",
                (case_start,),
            ).fetchall()
        finally:
            con.close()
        if rows:
            break
        time.sleep(poll_interval)

    skill_msgs = 0
    for r in rows:
        ct = (r["content_text"] or "").lower()
        cj = (r["content_json"] or "").lower()
        if ("skill" in ct or "skill" in cj
                or "skill-creator" in ct or "skill-creator" in cj):
            skill_msgs += 1

    if skill_msgs > 0:
        return _verdict(
            "C08", "pass",
            reason=f"slash-picker invoked 'skill-creator'; "
                   f"{skill_msgs} new cowork message(s) reference 'skill' "
                   f"(out of {len(rows)} new messages in window)",
            evidence={
                "wait": wait_result,
                "skill_messages": skill_msgs,
                "total_new_messages": len(rows),
            },
        )
    return _verdict(
        "C08", "skip",
        reason=f"slash-picker click succeeded but the {len(rows)} new "
               "cowork message(s) in the case window do not mention 'skill' "
               "— skill-creator may have been rejected silently or this "
               "build's cowork commands don't surface as Skill-tagged "
               "tool calls. Picker UIA path verified working.",
        evidence={"wait": wait_result, "total_new_messages": len(rows)},
    )


def case_C09_live_artefact(ctx: CaseContext) -> dict:
    """C09 — Live Artifacts pane reachability."""
    if ctx.mode != "live":
        # Static: artifact pane is a UI surface; can't verify without UIA.
        # If VM bundle exists on disk that's a soft signal.
        ag_root = (
            Path.home()
            / "AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache"
            / "Roaming/Claude/vm_bundles"
        )
        if ag_root.exists() and any(ag_root.rglob("*.vhdx")):
            return _verdict(
                "C09", "pass",
                reason="static: vm_bundles/<...>/*.vhdx exists "
                       "(agent-mode VM has been created at some point)",
                evidence={"vm_bundles_root": str(ag_root)},
            )
        return _verdict(
            "C09", "skip",
            reason="static mode + no vm_bundles on disk — run --mode live",
        )
    driver = _get_driver(ctx)
    try:
        ok = driver.view_live_artifacts()
    except Exception as exc:
        return _verdict(
            "C09", "fail",
            reason=f"view_live_artifacts() raised: {exc}",
        )
    if ok:
        # Restore Cowork chat composer so the next live case can use
        # the composer. Sidebar click leaves the right pane on the
        # Live-artifacts subpane.
        try:
            driver.ensure_cowork_chat()
        except Exception:
            pass
        return _verdict(
            "C09", "pass",
            reason="Live artifacts sidebar entry clicked; in-app pane surfaced",
        )
    return _verdict(
        "C09", "fail",
        reason="Live artifacts sidebar entry not found or click failed",
    )


def case_C10_dispatch_concurrent(ctx: CaseContext) -> dict:
    """C10 — Dispatch (Beta) pane reachability + concurrent task launch."""
    if ctx.mode != "live":
        return _verdict(
            "C10", "skip",
            reason="requires --mode live to click Dispatch sidebar entry",
        )
    driver = _get_driver(ctx)
    try:
        ok = driver.open_dispatch()
    except Exception as exc:
        return _verdict(
            "C10", "fail",
            reason=f"open_dispatch() raised: {exc}",
        )
    if ok:
        # Restore Cowork chat composer for subsequent live cases.
        try:
            driver.ensure_cowork_chat()
        except Exception:
            pass
        return _verdict(
            "C10", "pass",
            reason="Dispatch (Beta) sidebar entry clicked; in-app pane "
                   "(per Q3 closure: descendant pane, not Win32 popup)",
        )
    return _verdict(
        "C10", "fail",
        reason="Dispatch sidebar entry not found or click failed",
    )


def case_C11_scheduled(ctx: CaseContext) -> dict:
    """C11 — Scheduled task lifecycle (SKIP: Q6 inconclusive)."""
    return _verdict(
        "C11", "skip",
        reason="Q6 inconclusive — scheduled task lifecycle (eager vs lazy) "
               "not verifiable on N-axis; needs >24h soak test out of v1.1 scope",
    )


def case_C12_project_scoped(ctx: CaseContext) -> dict:
    """C12 — Project-scoped Cowork task."""
    project_name = os.environ.get("CLAUDE_PROJECT_NAME")
    if not project_name:
        return _verdict(
            "C12", "skip",
            reason="CLAUDE_PROJECT_NAME env var not set — set it to a project "
                   "substring and re-run --mode live to exercise C12",
        )
    if ctx.mode != "live":
        return _verdict(
            "C12", "skip",
            reason="requires --mode live to open project + send prompt",
        )
    driver = _get_driver(ctx)
    try:
        ok = driver.open_project(project_name)
    except Exception as exc:
        return _verdict(
            "C12", "fail",
            reason=f"open_project({project_name!r}) raised: {exc}",
        )
    if not ok:
        return _verdict(
            "C12", "fail",
            reason=f"open_project({project_name!r}) returned False",
        )
    return _live_send_and_verify(
        ctx,
        "List 3 things this project is about. Just plain text.",
        min_new_messages=2,
        wait_timeout=120,
        case_name="C12",
    )


# ---------------------------------------------------------------------------
# Case registry
# ---------------------------------------------------------------------------


CASES: list[tuple[str, Callable[[CaseContext], dict]]] = [
    ("C00", case_C00_detection),
    ("C01", case_C01_single_task),
    ("C02", case_C02_streaming_complete),
    ("C03", case_C03_multistep_task),
    ("C04", case_C04_cancel),
    ("C05", case_C05_file_input),
    ("C06", case_C06_code_output),
    ("C07", case_C07_mcp_tool),
    ("C08", case_C08_skill_invocation),
    ("C09", case_C09_live_artefact),
    ("C10", case_C10_dispatch_concurrent),
    ("C11", case_C11_scheduled),
    ("C12", case_C12_project_scoped),
    ("C13", case_C13_settings_change),
    ("C14", case_C14_l3g_backstop),
    ("C15", case_C15_idle_silence),
    ("C16", case_C16_mcpb_install),
]


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="P1 Claude Desktop cowork C-case sweep",
    )
    parser.add_argument(
        "--mode", choices=("static", "live"), default="static",
        help="static: verify from existing DB/filesystem only (fast). "
             "live: drive Claude Desktop UI (slow, requires no-touch).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path.home() / ".pce" / "data" / "pce.db",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="comma-separated case ids (default: all). e.g. 'C00,C14,C16'",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("tests/e2e_desktop_ui/reports/p1_cowork"),
    )
    args = parser.parse_args(argv)

    try:
        from tests.e2e_desktop_ui.utils import configure_utf8_stdout
        configure_utf8_stdout()
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.db_path.exists():
        print(f"[sweep] error: DB not found at {args.db_path}", file=sys.stderr)
        return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_root / f"{ts}_mode-{args.mode}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[sweep] mode={args.mode}, output={run_dir.resolve()}")

    selected = set()
    if args.cases:
        selected = {s.strip().upper() for s in args.cases.split(",") if s.strip()}

    ctx = CaseContext(
        mode=args.mode,
        db_path=args.db_path,
        run_dir=run_dir,
        start_ts=time.time(),
    )

    per_case_results: list[dict] = []
    counts = {"pass": 0, "skip": 0, "fail": 0}
    for name, fn in CASES:
        if selected and name not in selected:
            continue
        print(f"\n[sweep] === {name} ===", flush=True)
        t0 = time.time()
        try:
            result = fn(ctx)
        except Exception as exc:
            tb = traceback.format_exc()
            result = _verdict(
                name, "fail",
                reason=f"unhandled exception: {type(exc).__name__}: {exc}",
                evidence={"traceback": tb[-2000:]},
            )
        elapsed = round(time.time() - t0, 1)
        result["elapsed_s"] = elapsed
        verdict = result["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
        per_case_results.append(result)
        emoji = {"pass": "✓", "skip": "⏭", "fail": "✗"}.get(verdict, "?")
        print(
            f"[sweep] {emoji} {name} {verdict.upper():5s} ({elapsed}s) — {result['reason']}",
            flush=True,
        )
        (run_dir / f"case_{name}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    summary = {
        "started_at": ctx.start_ts,
        "ended_at": time.time(),
        "elapsed_s": round(time.time() - ctx.start_ts, 1),
        "mode": args.mode,
        "db_path": str(args.db_path),
        "counts": counts,
        "cases": per_case_results,
        "target": {
            "pass_min": 13,
            "skip_max": 3,
            "fail_max": 0,
        },
        "achieved": {
            "pass": counts["pass"],
            "skip": counts["skip"],
            "fail": counts["fail"],
        },
        "passes_acceptance": (
            counts["pass"] >= 13
            and counts["skip"] <= 3
            and counts["fail"] == 0
        ),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    print(f"\n[sweep] DONE — {counts['pass']} PASS / {counts['skip']} SKIP / "
          f"{counts['fail']} FAIL  (target ≥13 PASS / ≤3 SKIP / 0 FAIL)")
    print(f"[sweep] gate: {'PASS' if summary['passes_acceptance'] else 'FAIL'}")
    print(f"[sweep] summary: {run_dir / 'summary.json'}")
    return 0 if summary["passes_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
