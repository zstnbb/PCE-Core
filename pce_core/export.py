# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Data export utilities.

Two output formats, both streaming:

1. **OTLP JSONL** (``format=otlp``) – one OpenInference-shaped span per
   line, suitable for replay into any OTel-compatible backend (Phoenix,
   Langfuse, Jaeger …). Each span represents one PCE capture pair.

2. **PCE JSON envelope** (``format=json``) – session-centric document
   including messages and (optionally) the originating ``raw_captures``
   rows. Intended for "take my data somewhere else" and for import back
   into another PCE install.

Both paths are iterator-based so ``server.py`` can wrap them in
``StreamingResponse`` without loading the whole DB into memory.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

from .db import (
    get_connection,
    query_by_pair,
    query_messages_by_pair,
    query_messages,
)
from .logging_config import log_event
from .normalizer.openinference_mapper import (
    OI_MAPPER_VERSION,
    pair_to_oi_span,
    session_to_oi_attributes,
)

logger = logging.getLogger("pce.export")

EXPORT_SCHEMA_VERSION = 1  # envelope format version — bumps on breaking change


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def iter_oi_spans(
    *,
    since: Optional[float] = None,
    until: Optional[float] = None,
    session_ids: Optional[Iterable[str]] = None,
    db_path: Optional[Path] = None,
) -> Iterator[dict[str, Any]]:
    """Yield one OpenInference span dict per capture pair in range.

    Ordering: ascending by pair's first message timestamp.
    Pairs with no messages are skipped.
    """
    pair_session_pairs = _list_pairs_in_range(
        since=since, until=until, session_ids=session_ids, db_path=db_path,
    )
    if not pair_session_pairs:
        return

    for pair_id, session_id in pair_session_pairs:
        session = _fetch_session_dict(session_id, db_path=db_path)
        if session is None:
            continue
        messages = query_messages_by_pair(pair_id, db_path=db_path)
        if not messages:
            continue
        raw_rows = query_by_pair(pair_id, db_path=db_path) or []
        raw_req = next((r for r in raw_rows if r.get("direction") == "request"), None)
        raw_resp = next((r for r in raw_rows if r.get("direction") == "response"), None)
        try:
            yield pair_to_oi_span(
                pair_id=pair_id,
                session=session,
                messages=messages,
                raw_request=raw_req,
                raw_response=raw_resp,
            )
        except Exception as exc:  # pragma: no cover – defensive
            logger.warning("export span failed for pair=%s: %s", pair_id[:8], exc)
            continue


def iter_session_envelopes(
    *,
    since: Optional[float] = None,
    until: Optional[float] = None,
    session_ids: Optional[Iterable[str]] = None,
    include_raw: bool = False,
    db_path: Optional[Path] = None,
) -> Iterator[dict[str, Any]]:
    """Yield one session envelope at a time.

    Each envelope:

        {
            "schema":   "pce.session.envelope/v1",
            "session":  {... full sessions row ...},
            "openinference_attributes": {...},
            "messages": [ {... message row ...}, ... ],
            "raw_captures": [...]   # only if include_raw=True
        }
    """
    sessions = _list_sessions_in_range(
        since=since, until=until, session_ids=session_ids, db_path=db_path,
    )
    for sess in sessions:
        envelope: dict[str, Any] = {
            "schema": f"pce.session.envelope/v{EXPORT_SCHEMA_VERSION}",
            "session": sess,
            "openinference_attributes": session_to_oi_attributes(sess),
            "messages": query_messages(sess["id"], db_path=db_path),
        }
        if include_raw:
            envelope["raw_captures"] = _raw_captures_for_session(
                sess["id"], db_path=db_path,
            )
        yield envelope


def stream_jsonl(
    records: Iterable[Mapping[str, Any]],
) -> Iterator[bytes]:
    """Encode any iterable of dict records as UTF-8 NDJSON bytes."""
    for rec in records:
        try:
            yield (json.dumps(rec, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
        except Exception as exc:
            logger.debug("jsonl skip on encode error: %s", exc)


def stream_json_envelope(
    *,
    since: Optional[float] = None,
    until: Optional[float] = None,
    session_ids: Optional[Iterable[str]] = None,
    include_raw: bool = False,
    db_path: Optional[Path] = None,
) -> Iterator[bytes]:
    """Emit a standards-free JSON document describing the requested data.

    Output shape (streamed as UTF-8 bytes):

        {
          "schema": "pce.export/v1",
          "generated_at": 1766001234.56,
          "filters": {...},
          "sessions": [
              {envelope}, ...
          ]
        }

    Emitted as incremental bytes so large exports don't balloon memory.
    """
    filters = {
        "since": since,
        "until": until,
        "session_ids": list(session_ids) if session_ids is not None else None,
        "include_raw": include_raw,
        "oi_mapper_version": OI_MAPPER_VERSION,
    }
    yield (
        '{"schema":"pce.export/v1",'
        f'"generated_at":{time.time():.3f},'
        f'"filters":{json.dumps(filters, ensure_ascii=False)},'
        '"sessions":['
    ).encode("utf-8")
    first = True
    for env in iter_session_envelopes(
        since=since, until=until, session_ids=session_ids,
        include_raw=include_raw, db_path=db_path,
    ):
        prefix = b"" if first else b","
        first = False
        yield prefix + json.dumps(env, ensure_ascii=False).encode("utf-8")
    yield b"]}"


def export_summary(
    *,
    since: Optional[float] = None,
    until: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Return counts (sessions / messages / pairs) for the given range.

    Used as a cheap preflight so callers can decide whether to stream.
    """
    conn = get_connection(db_path)
    try:
        clauses, params = _build_ts_clauses("started_at", since, until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sess_count = conn.execute(
            f"SELECT COUNT(*) FROM sessions{where}", params,
        ).fetchone()[0]

        msg_clauses, msg_params = _build_ts_clauses("ts", since, until)
        msg_where = (" WHERE " + " AND ".join(msg_clauses)) if msg_clauses else ""
        msg_count = conn.execute(
            f"SELECT COUNT(*) FROM messages{msg_where}", msg_params,
        ).fetchone()[0]
        pair_count = conn.execute(
            f"SELECT COUNT(DISTINCT capture_pair_id) FROM messages{msg_where} "
            f"{'AND' if msg_clauses else 'WHERE'} capture_pair_id IS NOT NULL",
            msg_params,
        ).fetchone()[0]
        return {
            "sessions": int(sess_count),
            "messages": int(msg_count),
            "pairs": int(pair_count),
            "oi_mapper_version": OI_MAPPER_VERSION,
            "since": since,
            "until": until,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _list_sessions_in_range(
    *,
    since: Optional[float],
    until: Optional[float],
    session_ids: Optional[Iterable[str]],
    db_path: Optional[Path],
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        import sqlite3
        conn.row_factory = sqlite3.Row
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("started_at <= ?")
            params.append(until)
        if session_ids:
            ids = list(session_ids)
            if ids:
                clauses.append("id IN (" + ",".join(["?"] * len(ids)) + ")")
                params.extend(ids)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM sessions{where} ORDER BY started_at ASC", params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _list_pairs_in_range(
    *,
    since: Optional[float],
    until: Optional[float],
    session_ids: Optional[Iterable[str]],
    db_path: Optional[Path],
) -> list[tuple[str, str]]:
    """Return [(pair_id, session_id)] ordered by earliest ts per pair."""
    conn = get_connection(db_path)
    try:
        clauses: list[str] = ["capture_pair_id IS NOT NULL"]
        params: list[Any] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        if session_ids:
            ids = list(session_ids)
            if ids:
                clauses.append("session_id IN (" + ",".join(["?"] * len(ids)) + ")")
                params.extend(ids)
        where = " WHERE " + " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT capture_pair_id, session_id, MIN(ts) as mts FROM messages"
            f"{where} GROUP BY capture_pair_id ORDER BY mts ASC",
            params,
        ).fetchall()
        return [(r[0], r[1]) for r in rows if r[0]]
    finally:
        conn.close()


def _fetch_session_dict(
    session_id: str, *, db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        import sqlite3
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? LIMIT 1", (session_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _raw_captures_for_session(
    session_id: str, *, db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        import sqlite3
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rc.* FROM raw_captures rc "
            "JOIN messages m ON m.capture_pair_id = rc.pair_id "
            "WHERE m.session_id = ? "
            "GROUP BY rc.id "
            "ORDER BY rc.created_at ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _build_ts_clauses(
    column: str, since: Optional[float], until: Optional[float],
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{column} <= ?")
        params.append(until)
    return clauses, params


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "iter_oi_spans",
    "iter_session_envelopes",
    "stream_jsonl",
    "stream_json_envelope",
    "export_summary",
]
