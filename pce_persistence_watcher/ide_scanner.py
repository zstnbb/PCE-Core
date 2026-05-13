# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.ide_scanner – L3g scanner for IDE chat history.

Scans local storage for:
- **P5 Copilot**: VS Code's ``emptyWindowChatSessions/*.jsonl`` (delta-replay)
- **P3 Cursor**: Cursor's ``state.vscdb`` :: ``cursorDiskKV`` table

Each scan pass:
1. Discovers session files/rows that have changed since last scan
2. Replays/parses them into session state
3. Feeds the full session JSON into the PCE pipeline via insert_capture
4. Triggers normalize_conversation to produce sessions + messages

Dedup: fingerprint = source_path + session_id + content_hash. A session
that hasn't changed since last scan is skipped. A session that grew
(new messages) is re-emitted in full (the normalizer is idempotent on
session_key — it upserts).

Usage::

    python -m pce_persistence_watcher.ide_scanner scan
    python -m pce_persistence_watcher.ide_scanner watch --poll-interval 10
"""

from __future__ import annotations

import hashlib
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pce.persistence_watcher.ide_scanner")


# ---------------------------------------------------------------------------
# Dedup state
# ---------------------------------------------------------------------------

_STATE_FILE = "ide_scanner_state.json"


@dataclass
class _ScanState:
    # key = fingerprint, value = {"emitted_at": float, "app": str}
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)


def _load_state(path: Path) -> _ScanState:
    if not path.exists():
        return _ScanState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _ScanState(entries=raw.get("entries", {}))
    except (OSError, json.JSONDecodeError):
        return _ScanState()


def _save_state(state: _ScanState, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"entries": state.entries}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        logger.warning("cannot save ide_scanner state: %s", exc)


def _fingerprint(source: str, session_id: str, content_hash: str) -> str:
    h = hashlib.sha256()
    h.update(source.encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update(session_id.encode("utf-8"))
    h.update(b"|")
    h.update(content_hash.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Copilot scanner
# ---------------------------------------------------------------------------


def _copilot_sessions_dir() -> Optional[Path]:
    import os
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    d = appdata / "Code" / "User" / "globalStorage" / "emptyWindowChatSessions"
    return d if d.is_dir() else None


def _scan_copilot(state: _ScanState, db_path: Optional[Path], dry_run: bool) -> dict[str, int]:
    stats = {"seen": 0, "emitted": 0, "deduped": 0, "errors": 0}
    sessions_dir = _copilot_sessions_dir()
    if sessions_dir is None:
        return stats

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.harvest.l3g_copilot import replay_session

    for jsonl_file in sessions_dir.glob("*.jsonl"):
        stats["seen"] += 1
        try:
            session_state = replay_session(jsonl_file)
        except Exception as exc:
            logger.debug("copilot replay failed %s: %s", jsonl_file.name, exc)
            stats["errors"] += 1
            continue

        if not session_state or not isinstance(session_state, dict):
            continue

        session_id = session_state.get("sessionId", "")
        requests = session_state.get("requests", [])
        if not session_id or not requests:
            continue

        body_str = json.dumps(session_state, ensure_ascii=False, separators=(",", ":"))
        content_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()[:16]
        fp = _fingerprint("copilot", session_id, content_hash)

        if fp in state.entries:
            stats["deduped"] += 1
            continue

        if not dry_run:
            ok = _emit_session(
                host="local-copilot-chat",
                path=f"/{session_id}",
                provider="github",
                session_id=session_id,
                body_str=body_str,
                source_path=str(jsonl_file),
                db_path=db_path,
            )
            if ok:
                state.entries[fp] = {"emitted_at": time.time(), "app": "copilot"}
                stats["emitted"] += 1
            else:
                stats["errors"] += 1
        else:
            stats["emitted"] += 1

    return stats


# ---------------------------------------------------------------------------
# Cursor scanner
# ---------------------------------------------------------------------------


def _cursor_db_path() -> Optional[Path]:
    import os
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    db = appdata / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return db if db.is_file() else None


def _scan_cursor(state: _ScanState, db_path: Optional[Path], dry_run: bool) -> dict[str, int]:
    stats = {"seen": 0, "emitted": 0, "deduped": 0, "errors": 0}
    cursor_db = _cursor_db_path()
    if cursor_db is None:
        return stats

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.harvest.l3g_cursor import read_cursor_sessions

    try:
        sessions = list(read_cursor_sessions(state_db=cursor_db))
    except Exception as exc:
        logger.warning("cursor read failed: %s", exc)
        stats["errors"] += 1
        return stats

    for session in sessions:
        stats["seen"] += 1
        if not session.bubbles:
            continue

        data = {
            "composer_id": session.composer_id,
            "name": session.name,
            "model_config": session.model_config,
            "conversation_headers": session.conversation_headers,
            "bubbles": [
                {
                    "bubble_id": b.bubble_id,
                    "composer_id": b.composer_id,
                    "bubble_type": b.bubble_type,
                    "text": b.text,
                    "context": {},
                }
                for b in session.bubbles
            ],
        }

        body_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        content_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()[:16]
        fp = _fingerprint("cursor", session.composer_id, content_hash)

        if fp in state.entries:
            stats["deduped"] += 1
            continue

        if not dry_run:
            ok = _emit_session(
                host="local-cursor-chat",
                path=f"/{session.composer_id}",
                provider="cursor",
                session_id=session.composer_id,
                body_str=body_str,
                source_path=str(cursor_db),
                db_path=db_path,
            )
            if ok:
                state.entries[fp] = {"emitted_at": time.time(), "app": "cursor"}
                stats["emitted"] += 1
            else:
                stats["errors"] += 1
        else:
            stats["emitted"] += 1

    return stats


# ---------------------------------------------------------------------------
# Emit helper
# ---------------------------------------------------------------------------


def _emit_session(
    *,
    host: str,
    path: str,
    provider: str,
    session_id: str,
    body_str: str,
    source_path: str,
    db_path: Optional[Path],
) -> bool:
    try:
        from pce_core.db import (
            SOURCE_L3G_LOCAL_PERSISTENCE,
            insert_capture,
            new_pair_id,
            query_by_pair,
        )
        from pce_core.normalizer.pipeline import normalize_conversation

        pair_id = new_pair_id()
        meta = json.dumps({
            "source_kind": "ide_chat_session",
            "source_path": source_path,
            "scanner": "ide_scanner",
        }, ensure_ascii=False, separators=(",", ":"))

        insert_capture(
            direction="conversation",
            pair_id=pair_id,
            host=host,
            path=path,
            method="GET",
            provider=provider,
            status_code=None,
            latency_ms=None,
            body_text_or_json=body_str,
            body_format="json",
            meta_json=meta,
            source_id=SOURCE_L3G_LOCAL_PERSISTENCE,
            source="ide_scanner",
            agent_name="pce-ide-scanner",
            db_path=db_path,
            session_hint=session_id,
        )

        rows = query_by_pair(pair_id, db_path=db_path)
        if rows:
            normalize_conversation(
                rows[0],
                source_id=SOURCE_L3G_LOCAL_PERSISTENCE,
                created_via="ide_scanner",
                db_path=db_path,
            )
        return True
    except Exception as exc:
        logger.warning("emit failed for %s %s: %s", host, path, exc)
        return False


# ---------------------------------------------------------------------------
# Codex CLI scanner
# ---------------------------------------------------------------------------


def _scan_codex(state: _ScanState, db_path: Optional[Path], dry_run: bool) -> dict[str, int]:
    stats = {"seen": 0, "emitted": 0, "deduped": 0, "errors": 0}

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.harvest.l3g_codex import read_codex_sessions
    import dataclasses

    sessions = read_codex_sessions()
    if not sessions:
        return stats

    for session in sessions:
        stats["seen"] += 1

        data = {
            "session_id": session.session_id,
            "model": session.model,
            "cwd": session.cwd,
            "cli_version": session.cli_version,
            "originator": session.originator,
            "git_repo": session.git_repo,
            "git_branch": session.git_branch,
            "turns": [
                {"role": t.role, "content_text": t.content_text, "timestamp": t.timestamp}
                for t in session.turns
            ],
        }

        body_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        content_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()[:16]
        fp = _fingerprint("codex", session.session_id, content_hash)

        if fp in state.entries:
            stats["deduped"] += 1
            continue

        if not dry_run:
            ok = _emit_session(
                host="local-codex-cli",
                path=f"/{session.session_id}",
                provider="openai",
                session_id=session.session_id,
                body_str=body_str,
                source_path=str(session.source_path or ""),
                db_path=db_path,
            )
            if ok:
                state.entries[fp] = {"emitted_at": time.time(), "app": "codex"}
                stats["emitted"] += 1
            else:
                stats["errors"] += 1
        else:
            stats["emitted"] += 1

    return stats


# ---------------------------------------------------------------------------
# Gemini CLI scanner
# ---------------------------------------------------------------------------


def _scan_gemini(state: _ScanState, db_path: Optional[Path], dry_run: bool) -> dict[str, int]:
    stats = {"seen": 0, "emitted": 0, "deduped": 0, "errors": 0}

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.harvest.l3g_gemini import discover_sessions, read_session

    session_files = discover_sessions()
    if not session_files:
        return stats

    for jsonl_path in session_files:
        stats["seen"] += 1

        session = read_session(jsonl_path)
        if session is None or not session.messages:
            continue

        data = {
            "session_id": session.session_id,
            "project_hash": session.project_hash,
            "start_time": session.start_time,
            "kind": session.kind,
            "model": session.model,
            "messages": [
                {
                    "id": m.id,
                    "timestamp": m.timestamp,
                    "type": "user" if m.role == "user" else "gemini",
                    "content": m.content,
                    "model": m.model,
                    "thoughts": m.thoughts if m.thoughts else None,
                    "tokens": m.tokens,
                }
                for m in session.messages
            ],
        }

        body_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        content_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()[:16]
        fp = _fingerprint("gemini", session.session_id, content_hash)

        if fp in state.entries:
            stats["deduped"] += 1
            continue

        if not dry_run:
            ok = _emit_session(
                host="local-gemini-cli",
                path=f"/{session.session_id}",
                provider="google",
                session_id=session.session_id,
                body_str=body_str,
                source_path=str(jsonl_path),
                db_path=db_path,
            )
            if ok:
                state.entries[fp] = {"emitted_at": time.time(), "app": "gemini"}
                stats["emitted"] += 1
            else:
                stats["errors"] += 1
        else:
            stats["emitted"] += 1

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SHUTDOWN = False


def _on_signal(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True


def _resolve_state_path(db_path: Optional[Path]) -> Path:
    if db_path:
        return db_path.parent / _STATE_FILE
    try:
        from pce_core.config import DATA_DIR
        return DATA_DIR / _STATE_FILE
    except Exception:
        return Path.home() / ".pce" / "data" / _STATE_FILE


def scan(db_path: Optional[Path] = None, dry_run: bool = False) -> dict:
    """Run one scan pass for both Copilot and Cursor. Returns stats."""
    from pce_core.db import init_db
    if not dry_run:
        init_db(db_path)

    state_path = _resolve_state_path(db_path)
    state = _load_state(state_path)

    copilot_stats = _scan_copilot(state, db_path, dry_run)
    cursor_stats = _scan_cursor(state, db_path, dry_run)
    codex_stats = _scan_codex(state, db_path, dry_run)
    gemini_stats = _scan_gemini(state, db_path, dry_run)

    if not dry_run:
        _save_state(state, state_path)

    return {"copilot": copilot_stats, "cursor": cursor_stats, "codex": codex_stats, "gemini": gemini_stats}


def watch(db_path: Optional[Path] = None, poll_interval: float = 10.0) -> None:
    """Continuous watch loop."""
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    sys.stderr.write(
        f"pce-ide-scanner: watch mode, polling every {poll_interval}s. Ctrl+C to stop.\n"
    )

    while not _SHUTDOWN:
        stats = scan(db_path=db_path)
        total_emitted = (
            stats["copilot"]["emitted"] + stats["cursor"]["emitted"]
            + stats["codex"]["emitted"]
        )
        if total_emitted > 0:
            sys.stderr.write(
                f"  emitted: copilot={stats['copilot']['emitted']} "
                f"cursor={stats['cursor']['emitted']}\n"
            )

        deadline = time.time() + poll_interval
        while not _SHUTDOWN and time.time() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.time())))

    sys.stderr.write("pce-ide-scanner: exiting.\n")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="pce-ide-scanner",
        description="Scan Copilot + Cursor local chat history into PCE",
    )
    parser.add_argument(
        "mode", choices=["scan", "watch"],
        help="'scan' for one-shot, 'watch' for continuous polling",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=10.0,
        help="Seconds between watch passes (default: 10)",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override PCE database path",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Discover and count without writing to DB",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.mode == "scan":
        stats = scan(db_path=args.db_path, dry_run=args.dry_run)
        for app, s in stats.items():
            sys.stderr.write(
                f"  [{app}] seen={s['seen']} emitted={s['emitted']} "
                f"deduped={s['deduped']} errors={s['errors']}\n"
            )
        return 0
    else:
        watch(db_path=args.db_path, poll_interval=args.poll_interval)
        return 0


if __name__ == "__main__":
    sys.exit(main())
