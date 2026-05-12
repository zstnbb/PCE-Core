# SPDX-License-Identifier: Apache-2.0
"""L3g reader for Cursor's local chat storage.

Cursor stores chat history in two SQLite key-value tables inside
``%APPDATA%/Cursor/User/globalStorage/state.vscdb``:

  ItemTable          ← settings, UI state, model preferences, composer index
  cursorDiskKV       ← THE chat content (1 row per session, N rows per message)

Inside ``cursorDiskKV``, two key namespaces matter:

  composerData:<UUID>            ← chat session metadata
       JSON {
         _v, composerId,
         name,                            ← chat tab title ("What is 2+2?")
         richText,                        ← Lexical editor JSON of current draft
         text,                            ← plain-text current draft
         fullConversationHeadersOnly[],   ← list of {bubbleId, type, grouping}
         conversationMap,                 ← {<bubbleId>: <messageType>}
         status, context, codeBlockData,
         modelConfig: {modelName, selectedModels[{modelId, ...}], ...},
         usageData, capabilities[], ...
       }

  bubbleId:<composerId>:<msgId>  ← individual message bubble
       JSON {
         _v, type, bubbleId,
         text,                            ← user/assistant message text
         richText,                        ← Lexical editor JSON of message
         context: {
           composers, selectedCommits, selectedPullRequests, selectedImages,
           folderSelections, fileSelections, terminalFiles, selections,
           terminalSelections, selectedDocs, externalLinks,
           cursorRules,                   ← active rules text
           ...
         },
         attachedCodeChunks[], codebaseContextChunks[],
         attachedFolders[], attachedFoldersNew[],
         images[], commits[], pullRequests[],
         assistantSuggestedDiffs[], gitDiffs[],
         interpreterResults[],
         approximateLintErrors, lints,
         ...
       }

We don't have a confirmed `bubble.type` enumeration, but based on observed
data and the fact that Cursor stores user + assistant turns as bubbles in
the same conversation:

  type=1 (or "user")        → user input bubble
  type=2 (or "assistant")   → assistant reply bubble

The exact int values may shift across Cursor versions; downstream
normalizer should treat `type` as opaque-with-heuristics (e.g. is there
`text` content but no `assistantSuggestedDiffs`?  Probably user.).

Usage::

    from scripts.harvest.l3g_cursor import read_cursor_sessions

    for session in read_cursor_sessions():
        print(session.composer_id, session.name, len(session.bubbles))
        for b in session.bubbles:
            print(" ", b.bubble_id, b.text[:60])

CLI::

    python scripts/harvest/l3g_cursor.py             # human-readable summary
    python scripts/harvest/l3g_cursor.py --json      # JSON dump of everything
    python scripts/harvest/l3g_cursor.py --since 1h  # only sessions touched in last hour
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator, Optional


logger = logging.getLogger("pce.harvest.l3g.cursor")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CursorBubble:
    """One message bubble in a Cursor chat session."""

    bubble_id: str
    composer_id: str
    bubble_type: Optional[int]      # opaque (1 = user observed, 2 = assistant observed)
    text: str                       # plain-text body
    rich_text: Optional[str]        # Lexical editor JSON (string)
    context: dict                   # attached files / chunks / folders / etc.
    raw: dict                       # full raw JSON for forensics


@dataclasses.dataclass
class CursorSession:
    """One chat session (composer) in Cursor."""

    composer_id: str
    name: Optional[str]             # chat tab title
    text_draft: Optional[str]       # current unsent input
    conversation_headers: list      # [{bubbleId, type, grouping}, ...]
    conversation_map: dict          # {bubbleId: messageType}
    model_config: dict              # {modelName, selectedModels, ...}
    bubbles: list[CursorBubble]
    raw: dict                       # full composer JSON for forensics


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_cursor_state_db() -> Path:
    """Where Cursor keeps its main state.vscdb on Windows."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Cursor" / "User" / "globalStorage" / "state.vscdb"


# ---------------------------------------------------------------------------
# Core reader
# ---------------------------------------------------------------------------


def _open_db_safely(state_db: Path) -> tuple[sqlite3.Connection, Path]:
    """Copy state.vscdb (+ WAL + SHM) to a temp dir and open read-only.

    SQLite's WAL mode means recently-written rows may live in the
    ``state.vscdb-wal`` sidecar, not yet checkpointed into the main file.
    To see them without taking a write lock on Cursor's live DB, we copy
    all three files to a temp dir first.
    """
    if not state_db.is_file():
        raise FileNotFoundError(f"Cursor state.vscdb not found at {state_db}")
    tmpdir = Path(tempfile.mkdtemp(prefix="pce_l3g_cursor_"))
    shutil.copy(state_db, tmpdir / "state.vscdb")
    for ext in (".vscdb-wal", ".vscdb-shm"):
        src = state_db.with_suffix(ext)
        if src.is_file():
            shutil.copy(src, tmpdir / src.name)
    conn = sqlite3.connect(f"file:{tmpdir / 'state.vscdb'}?mode=ro", uri=True)
    return conn, tmpdir


def _parse_bubble_row(key: str, value: str | bytes) -> Optional[CursorBubble]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        obj = json.loads(value)
    except Exception as exc:
        logger.debug("could not parse bubble %s as JSON: %s", key, exc)
        return None
    # key format: bubbleId:<composerId>:<bubbleId>
    parts = key.split(":")
    if len(parts) < 3:
        return None
    composer_id = parts[1]
    bubble_id = parts[2]
    return CursorBubble(
        bubble_id=bubble_id,
        composer_id=composer_id,
        bubble_type=obj.get("type") if isinstance(obj.get("type"), int) else None,
        text=obj.get("text", "") or "",
        rich_text=obj.get("richText"),
        context=obj.get("context", {}) or {},
        raw=obj,
    )


def _parse_composer_row(key: str, value: str | bytes) -> Optional[CursorSession]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        obj = json.loads(value)
    except Exception as exc:
        logger.debug("could not parse composer %s as JSON: %s", key, exc)
        return None
    # key format: composerData:<composerId>
    parts = key.split(":")
    composer_id = parts[1] if len(parts) >= 2 else obj.get("composerId", "")
    return CursorSession(
        composer_id=composer_id,
        name=obj.get("name"),
        text_draft=obj.get("text"),
        conversation_headers=obj.get("fullConversationHeadersOnly", []) or [],
        conversation_map=obj.get("conversationMap", {}) or {},
        model_config=obj.get("modelConfig", {}) or {},
        bubbles=[],  # filled in later
        raw=obj,
    )


def read_cursor_sessions(
    state_db: Optional[Path] = None,
    since_unix: Optional[float] = None,
) -> list[CursorSession]:
    """Read all Cursor chat sessions from local storage.

    Args:
        state_db: Path to ``state.vscdb``. Defaults to the Windows path.
        since_unix: If set, only returns sessions whose composer JSON
            mentions a ``lastUpdatedAt`` (or ``createdAt``) >= this epoch
            seconds. Composers without those fields are always included.

    Returns:
        list of :class:`CursorSession`, each with its ``bubbles`` populated.
    """
    state_db = state_db or default_cursor_state_db()
    conn, tmpdir = _open_db_safely(state_db)
    try:
        # 1. Load all composers
        sessions: dict[str, CursorSession] = {}
        cur = conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
        )
        for key, value in cur:
            s = _parse_composer_row(key, value)
            if s is None:
                continue
            sessions[s.composer_id] = s

        # 2. Load all bubbles, group by composer
        cur = conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
        )
        for key, value in cur:
            b = _parse_bubble_row(key, value)
            if b is None:
                continue
            s = sessions.get(b.composer_id)
            if s is None:
                # orphan bubble — create a synthetic session
                s = CursorSession(
                    composer_id=b.composer_id,
                    name=None,
                    text_draft=None,
                    conversation_headers=[],
                    conversation_map={},
                    model_config={},
                    bubbles=[],
                    raw={"_orphan": True},
                )
                sessions[b.composer_id] = s
            s.bubbles.append(b)

        # 3. Order bubbles per composer using fullConversationHeadersOnly
        for s in sessions.values():
            order = {
                h.get("bubbleId"): i
                for i, h in enumerate(s.conversation_headers)
                if isinstance(h, dict)
            }
            s.bubbles.sort(key=lambda b: order.get(b.bubble_id, 10**9))

        # 4. Optional time filter
        result = []
        for s in sessions.values():
            if since_unix is not None:
                last = (
                    s.raw.get("lastUpdatedAt") or s.raw.get("createdAt") or 0
                )
                # Cursor uses millisecond timestamps; convert
                if last and last > 10**12:
                    last = last / 1000.0
                if last and last < since_unix:
                    continue
            result.append(s)
        # newest first
        result.sort(
            key=lambda s: -(s.raw.get("lastUpdatedAt") or s.raw.get("createdAt") or 0)
        )
        return result
    finally:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Conversation extraction helpers (for downstream normalizer)
# ---------------------------------------------------------------------------


def session_to_messages(session: CursorSession) -> list[dict]:
    """Convert a CursorSession into a flat list of role-tagged messages.

    Heuristic role assignment based on observed Cursor data layout:
      - Bubbles with substantial ``assistantSuggestedDiffs`` /
        ``codeBlockData`` / ``gitDiffs`` / ``interpreterResults`` → assistant
      - Bubbles with only ``text``+``richText`` → user
      - Fallback: alternating starting at user

    Returns:
        list of {role, text, timestamp, attachments, raw_bubble_id}
        in conversation order.
    """
    out = []
    for i, b in enumerate(session.bubbles):
        # Determine role
        raw = b.raw
        has_assistant_artifacts = bool(
            raw.get("assistantSuggestedDiffs")
            or raw.get("codeBlockData")
            or raw.get("gitDiffs")
            or raw.get("interpreterResults")
            or raw.get("approximateLintErrors")
        )
        if b.bubble_type == 1:
            role = "user"
        elif b.bubble_type == 2:
            role = "assistant"
        elif has_assistant_artifacts:
            role = "assistant"
        elif i % 2 == 0:
            role = "user"
        else:
            role = "assistant"

        out.append(
            {
                "role": role,
                "text": b.text,
                "bubble_id": b.bubble_id,
                "bubble_type": b.bubble_type,
                "context": b.context,
                "attached_files": [
                    fs.get("uri", {}).get("fsPath", "")
                    for fs in b.context.get("fileSelections", []) or []
                    if isinstance(fs, dict)
                ],
                "attached_code_chunks": len(raw.get("attachedCodeChunks", []) or []),
                "attached_folders": [
                    f.get("relativeWorkspacePath", "")
                    for f in raw.get("attachedFolders", []) or []
                    if isinstance(f, dict)
                ],
                "images": len(raw.get("images", []) or []),
                "git_diffs": len(raw.get("gitDiffs", []) or []),
                "interpreter_results": len(raw.get("interpreterResults", []) or []),
                "rich_text": b.rich_text,
            }
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(s: str) -> float:
    """Parse '1h', '30m', '2d', or ISO-8601 → unix seconds."""
    s = s.strip()
    if not s:
        return 0.0
    if s[-1] in "smhd":
        n = float(s[:-1])
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[s[-1]]
        return time.time() - n * mult
    # Try ISO 8601
    from datetime import datetime
    return datetime.fromisoformat(s.rstrip("Z")).timestamp()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state-db", type=Path, default=None, help="Path to state.vscdb (default: Windows %%APPDATA%%/Cursor/User/globalStorage/state.vscdb)")
    p.add_argument("--since", type=str, default=None, help="Only include sessions updated since [Ns|Nm|Nh|Nd|ISO-8601].")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable summary.")
    p.add_argument("--include-bubbles", action="store_true", help="In JSON output, include full bubble raw payloads.")
    p.add_argument("--limit", type=int, default=20, help="Cap sessions shown (default 20).")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    since_unix = _parse_since(args.since) if args.since else None
    sessions = read_cursor_sessions(args.state_db, since_unix)
    sessions = sessions[: args.limit]

    if args.json:
        out = []
        for s in sessions:
            out.append({
                "composer_id": s.composer_id,
                "name": s.name,
                "model_config": s.model_config,
                "n_bubbles": len(s.bubbles),
                "messages": session_to_messages(s),
                **({"raw": s.raw} if args.include_bubbles else {}),
            })
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
        return 0

    print(f"Found {len(sessions)} Cursor session(s):")
    for s in sessions:
        model = s.model_config.get("modelName", "unknown")
        print(f"\n  composer {s.composer_id}")
        print(f"    name:    {s.name!r}")
        print(f"    model:   {model}")
        print(f"    bubbles: {len(s.bubbles)}")
        for i, m in enumerate(session_to_messages(s)):
            text = (m["text"] or "").replace("\n", " ")[:80]
            print(f"      [{i:>2}] {m['role']:<10} {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
