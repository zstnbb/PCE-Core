# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.capture – turn parsed records into PCE rows.

This module is the bridge between the read-only source parsers
(``agent_sessions.py`` + ``leveldb_reader.py``) and the PCE capture
pipeline. It owns three responsibilities:

1. **Dedup**: track which source files / records have already been
   emitted so a ``scan`` or ``watch`` re-run does not flood the DB.
   State is persisted to a JSON sidecar under ``<pce-data>/
   persistence_watcher_state.json``.

2. **Envelope construction**: format each parsed record as the payload
   ``pce_core.db.insert_capture`` expects, with consistent ``provider``
   / ``host`` / ``path`` / ``meta`` shape so the normalizer can pick
   it up.

3. **Write safety**: any DB failure is caught and logged. The watcher
   always returns — capture is best-effort.

The contract with ``pce_mcp_proxy.capture.JsonRpcObserver`` is
deliberate: both observers write through the same ``insert_capture``
API with their own ``source_id`` constant, so dashboard + stats /
query endpoints treat L3g rows uniformly with all other capture rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pce_core.db import (
    SOURCE_L3G_LOCAL_PERSISTENCE,
    insert_capture,
    new_pair_id,
)

from .agent_sessions import AgentSessionRecord
from .leveldb_reader import IndexedDbScanSummary, LevelDbRecord

logger = logging.getLogger("pce.persistence_watcher.capture")


# ---------------------------------------------------------------------------
# Dedup state
# ---------------------------------------------------------------------------


_STATE_SCHEMA_VERSION = 1


@dataclass
class _DedupState:
    """In-memory mirror of the JSON sidecar state file."""

    schema_version: int = _STATE_SCHEMA_VERSION
    # key = fingerprint (source_path + kind + content_hash)
    # value = {"emitted_at": float_epoch, "app_id": str, "kind": str}
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)


def _load_state(path: Path) -> _DedupState:
    if not path.exists():
        return _DedupState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cannot load dedup state %s (%s); starting fresh", path, exc)
        return _DedupState()
    if not isinstance(raw, dict):
        return _DedupState()
    version = int(raw.get("schema_version", _STATE_SCHEMA_VERSION))
    entries = raw.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    return _DedupState(schema_version=version, entries=entries)


def _save_state(state: _DedupState, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"schema_version": state.schema_version, "entries": state.entries},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        logger.warning("cannot write dedup state %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


class ChromiumStateObserver:
    """Writes ``raw_captures`` rows tagged ``SOURCE_L3G_LOCAL_PERSISTENCE``.

    One observer instance is used for the lifetime of a ``scan`` /
    ``watch`` run and is threadsafe (the public methods guard state
    mutations with a single lock).
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_version: Optional[str],
        app_channel: str,
        db_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
        dry_run: bool = False,
        include_bodies: bool = True,
    ) -> None:
        self.app_id = app_id
        self.app_version = app_version
        self.app_channel = app_channel
        self.db_path = db_path
        self.state_path = state_path
        self.dry_run = bool(dry_run)
        self.include_bodies = bool(include_bodies)
        self._lock = threading.Lock()
        self._state = _load_state(state_path) if state_path else _DedupState()
        # Per-kind counters mirror the AgentSessionRecord.kind values
        # ("session" / "skills_catalogue" / "local_config") plus the
        # non-AgentSessionRecord paths ("leveldb" / "indexeddb_summary").
        # Keep keys SINGULAR-MATCHING-rec.kind so observe_agent_session's
        # generic ``stats[rec.kind] += 1`` lands in the right counter.
        self.stats: dict[str, int] = {
            "records_seen": 0,
            "records_emitted": 0,
            "records_deduped": 0,
            "capture_failures": 0,
            "session": 0,
            "skills_catalogue": 0,
            "leveldb": 0,
            "local_config": 0,
            "indexeddb_summary": 0,
            # P5.B.5.3 (2026-05-11) — Cowork agent-mode JSONL transcript
            # per-line ingestion. Counts ALL transcript lines seen,
            # including ai-title / queue-operation / etc. metadata
            # lines. The normaliser routes user / assistant lines into
            # sessions+messages; metadata lines stay in raw_captures.
            "transcript_line": 0,
            # P5.B.7 (2026-05-11) — Code-tab session pointer JSONs.
            "code_tab_session_pointer": 0,
            # P5.B.7.P2 (2026-05-12) — user-home state surfaces from
            # ``claude_user_state.iter_claude_user_state_records``:
            # ``user_state_snapshot`` covers ~/.claude.json,
            # settings*.json, and todos/*.json (point-in-time JSON);
            # ``user_state_line`` covers history.jsonl (per-line).
            "user_state_snapshot": 0,
            "user_state_line": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe_agent_session(self, rec: AgentSessionRecord) -> None:
        """Ingest one parsed ``agent_sessions`` record (any kind).

        Routes by ``rec.kind``:

        - ``"session"`` / ``"skills_catalogue"``: host=local-agent-mode,
          path=/<app_id>/agent-session/<uuid>, session_hint=<uuid>.
        - ``"local_config"``: host=local-config,
          path=/<app_id>/local-config/<surface>, session_hint=None.
          (ADR-018 §6 C4 supplementary surfaces — preferences,
          cowork_owner, git_worktrees, device_id.)
        - ``"transcript_line"``: host=local-agent-mode,
          path=/<app_id>/agent-transcript/<session_id>/<line_key>,
          session_hint=<session_id>, fingerprint uses line_uuid OR
          line_index so a growing append-only JSONL only re-emits the
          new lines. After insert, triggers
          ``pipeline.normalize_conversation`` to produce sessions +
          messages immediately (P5.B.5.3, 2026-05-11).
        - ``"code_tab_session_pointer"``: host=local-agent-mode,
          path=/<app_id>/code-tab-session-pointer/<uuid>,
          session_hint=<cliSessionId>. Metadata-only; no normalize
          trigger (P5.B.7, 2026-05-11).
        - ``"user_state_snapshot"`` / ``"user_state_line"``:
          host=local-config, path=/<app_id>/user-state/<surface>[/<key>],
          session_hint=<session_id-or-None>. Both surfaces are
          config-level state from ``~/.claude/`` + ``~/.claude.json``
          and do NOT trigger normalize — they live only in
          raw_captures (P5.B.7.P2, 2026-05-12).
        """
        with self._lock:
            self.stats["records_seen"] += 1
            self.stats[rec.kind] = self.stats.get(rec.kind, 0) + 1

            body_str = self._body_for(rec.body_json)

            # Fingerprint seed depends on kind:
            # - Line-oriented kinds (``transcript_line``,
            #   ``user_state_line``) use line_uuid (preferred) or
            #   line_index for per-line dedup so an appended JSONL
            #   only re-emits the NEW lines (otherwise content-hash
            #   dedup would re-emit every existing line on each
            #   watcher pass).
            # - Everything else uses the canonical body-string hash.
            if rec.kind in ("transcript_line", "user_state_line"):
                seed = (
                    rec.line_uuid
                    or f"idx={rec.line_index}|sz={rec.size_bytes}"
                )
            else:
                seed = body_str
            fp = self._fingerprint(rec.source_path, rec.kind, seed)
            if self._already_emitted(fp):
                self.stats["records_deduped"] += 1
                return

            meta: dict[str, Any] = {
                "app_id": self.app_id,
                "app_channel": self.app_channel,
                "app_version": self.app_version,
                "source_kind": rec.kind,
                "source_path": str(rec.source_path),
                "source_mtime_ns": rec.mtime_ns,
                "source_size_bytes": rec.size_bytes,
                "last_updated_ms": rec.last_updated_ms,
                "fingerprint": fp,
            }
            if rec.surface is not None:
                meta["surface"] = rec.surface
            if rec.kind in ("transcript_line", "user_state_line"):
                meta["line_uuid"] = rec.line_uuid
                meta["line_index"] = rec.line_index
                meta["line_type"] = (
                    rec.body_json.get("type")
                    if isinstance(rec.body_json, dict)
                    else None
                )
            # P5.B.7.P2 (2026-05-12): subagent transcripts inject
            # ``is_subagent`` / ``agent_id`` / ``parent_session_id``
            # onto the body. Surface them in meta as well so dashboard
            # filters don't need to peek inside body_json.
            if rec.kind == "transcript_line" and isinstance(rec.body_json, dict):
                if rec.body_json.get("is_subagent") is True:
                    meta["is_subagent"] = True
                    aid = rec.body_json.get("agent_id")
                    pid = rec.body_json.get("parent_session_id")
                    if isinstance(aid, str):
                        meta["agent_id"] = aid
                    if isinstance(pid, str):
                        meta["parent_session_id"] = pid

            triggered_normalize = False
            if rec.kind == "local_config":
                host = "local-config"
                path = f"/{self.app_id}/local-config/{rec.surface or 'unknown'}"
                session_hint: Optional[str] = None
            elif rec.kind == "transcript_line":
                host = "local-agent-mode"
                line_key = rec.line_uuid or f"idx-{rec.line_index}"
                path = (
                    f"/{self.app_id}/agent-transcript/"
                    f"{rec.session_id or 'unknown'}/{line_key}"
                )
                session_hint = rec.session_id
                triggered_normalize = True
            elif rec.kind == "user_state_snapshot":
                # P5.B.7.P2 (2026-05-12) — user-home JSON snapshots.
                # The ``todos`` surface emits one record per file so
                # the path includes the filename to keep records
                # distinct in raw_captures; other surfaces are
                # singleton snapshots whose surface name suffices.
                host = "local-config"
                if rec.surface == "user_state_todos":
                    key_suffix = f"/{rec.source_path.name}"
                else:
                    key_suffix = ""
                path = (
                    f"/{self.app_id}/user-state/"
                    f"{rec.surface or 'unknown'}{key_suffix}"
                )
                # Todos carry a derived session_id (parsed from the
                # filename ``<sessId>-agent-<agentId>.json``); other
                # surfaces have no native session affinity.
                session_hint = rec.session_id
            elif rec.kind == "user_state_line":
                # P5.B.7.P2 (2026-05-12) — ``history.jsonl`` per-line.
                # Lines have no per-line uuid in the wild; line_index
                # is the dedup key.
                host = "local-config"
                line_key = rec.line_uuid or f"idx-{rec.line_index}"
                path = (
                    f"/{self.app_id}/user-state/"
                    f"{rec.surface or 'history'}/{line_key}"
                )
                # ``history.jsonl`` lines carry a ``sessionId`` field
                # tying the prompt to the Code-tab session that ran
                # it; pipe it into session_hint so the dashboard can
                # join history entries to captured sessions.
                session_hint = rec.session_id
            elif rec.kind == "code_tab_session_pointer":
                # P5.B.7 (2026-05-11) — Code-tab session metadata
                # pointer (~1 KB JSON at claude-code-sessions/<user>/
                # <org>/local_<sess>.json). Carries title / model /
                # permissionMode / enabledMcpTools /
                # sessionPermissionUpdates. Pointer is metadata-only —
                # the transcript JSONL is the message ledger and
                # triggers the normaliser; this branch just stores the
                # pointer for downstream session enrichment via
                # cliSessionId join.
                host = "local-agent-mode"
                cli_sess = rec.body_json.get("cliSessionId") if isinstance(rec.body_json, dict) else None
                path = (
                    f"/{self.app_id}/code-tab-session-pointer/"
                    f"{rec.session_id or 'unknown'}"
                )
                session_hint = (
                    cli_sess if isinstance(cli_sess, str) else rec.session_id
                )
            else:
                host = "local-agent-mode"
                path = f"/{self.app_id}/agent-session/{rec.session_id or 'unknown'}"
                session_hint = rec.session_id

            ok = self._write(
                direction="conversation",
                host=host,
                path=path,
                provider="anthropic",
                body_str=body_str,
                meta=meta,
                session_hint=session_hint,
                trigger_normalize=triggered_normalize,
            )
            if ok:
                # dry_run must be fully side-effect-free: count but do
                # NOT mutate the dedup state (otherwise a dry-run pass
                # poisons the real-run that follows it).
                if not self.dry_run:
                    self._mark_emitted(fp, kind=rec.kind)
                self.stats["records_emitted"] += 1

    def observe_leveldb(
        self,
        rec: LevelDbRecord,
        *,
        storage_kind: str,        # "local_storage" | "indexeddb"
        origin: Optional[str] = None,
    ) -> None:
        """Ingest one raw LevelDB (key, value) record.

        v0 stores the record as-is with a hex-encoded key in meta; a
        later Chromium-storage decoder (``chromium_storage.py``,
        planned for Phase 3.1) will decompose values into typed JSON
        structures. Until then the row is T3-metadata-only quality.
        """
        with self._lock:
            self.stats["records_seen"] += 1
            self.stats["leveldb"] += 1

            # LevelDB bodies can be arbitrary bytes; we hex-encode when
            # they don't decode as UTF-8 JSON, otherwise keep as text.
            body_str, body_repr = self._leveldb_body(rec.value_bytes)
            fp = self._fingerprint(
                rec.source_dir,
                f"leveldb:{storage_kind}",
                rec.key_bytes.hex() + ":" + hashlib.sha256(rec.value_bytes).hexdigest(),
            )
            if self._already_emitted(fp):
                self.stats["records_deduped"] += 1
                return

            meta: dict[str, Any] = {
                "app_id": self.app_id,
                "app_channel": self.app_channel,
                "app_version": self.app_version,
                "source_kind": f"leveldb:{storage_kind}",
                "source_dir": str(rec.source_dir),
                "key_hex": rec.key_bytes.hex(),
                "key_ascii_preview": _ascii_preview(rec.key_bytes),
                "value_repr": body_repr,
                "origin": origin,
                "fingerprint": fp,
            }

            ok = self._write(
                direction="conversation",
                host=f"chromium-{storage_kind}",
                path=f"/{self.app_id}/{storage_kind}",
                provider="anthropic" if self.app_id == "claude-desktop" else "openai",
                body_str=body_str,
                meta=meta,
                session_hint=None,
            )
            if ok:
                # See observe_agent_session() for dry-run rationale.
                if not self.dry_run:
                    self._mark_emitted(fp, kind=f"leveldb:{storage_kind}")
                self.stats["records_emitted"] += 1

    def observe_indexeddb_summary(
        self,
        summary: IndexedDbScanSummary,
        *,
        origin: Optional[str] = None,
    ) -> None:
        """Ingest one IndexedDB ``.log`` scan summary as a T3 metadata-only capture.

        Per ADR-018 §6 C4 supplementary "v1 envelope-level capture":
        emits ONE row per scanned ``.log`` file, body is the summary
        JSON (db_name_hints, object_store_hints, counts, capped+
        redacted JSON examples).

        Routes:
          host = ``chromium-indexeddb``
          path = ``/<app_id>/indexeddb-summary/<origin or "default">``
          provider = ``anthropic`` | ``openai`` based on app_id

        Fingerprint is content-stable: re-scanning an unchanged log
        file produces an identical fingerprint and does not re-emit.
        """
        with self._lock:
            self.stats["records_seen"] += 1
            self.stats["indexeddb_summary"] = self.stats.get("indexeddb_summary", 0) + 1

            body = {
                "log_size_bytes": summary.log_size_bytes,
                "total_strings": summary.total_strings,
                "uuid_count": summary.uuid_count,
                "uuid_v7_count": summary.uuid_v7_count,
                "json_blob_count": summary.json_blob_count,
                "composer_draft_count": summary.composer_draft_count,
                "composer_drafts_redacted": summary.composer_drafts_redacted,
                "db_name_hints": summary.db_name_hints,
                "object_store_hints": summary.object_store_hints,
                "json_blob_examples": summary.json_blob_examples,
            }
            body_str = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )

            # Fingerprint seed: structural counts + db/store names.
            # We deliberately exclude json_blob_examples + size so a
            # log file that simply grows by one record doesn't blow
            # away dedup; we re-emit when schema indicators or counts
            # actually change in a meaningful way.
            seed = (
                f"strings={summary.total_strings}"
                f"|uuid={summary.uuid_count}"
                f"|jsonblobs={summary.json_blob_count}"
                f"|drafts={summary.composer_draft_count}"
                f"|dbs={','.join(summary.db_name_hints)}"
                f"|stores={','.join(summary.object_store_hints)}"
            )
            fp = self._fingerprint(summary.source_path, "indexeddb_summary", seed)
            if self._already_emitted(fp):
                self.stats["records_deduped"] += 1
                return

            meta: dict[str, Any] = {
                "app_id": self.app_id,
                "app_channel": self.app_channel,
                "app_version": self.app_version,
                "source_kind": "indexeddb_summary",
                "source_path": str(summary.source_path),
                "log_size_bytes": summary.log_size_bytes,
                "scanned_at_epoch": summary.scanned_at_epoch,
                "origin": origin,
                "fingerprint": fp,
            }

            ok = self._write(
                direction="conversation",
                host="chromium-indexeddb",
                path=f"/{self.app_id}/indexeddb-summary/{origin or 'default'}",
                provider="anthropic" if self.app_id == "claude-desktop" else "openai",
                body_str=body_str,
                meta=meta,
                session_hint=None,
            )
            if ok:
                # See observe_agent_session() for dry-run rationale.
                if not self.dry_run:
                    self._mark_emitted(fp, kind="indexeddb_summary")
                self.stats["records_emitted"] += 1

    def flush_state(self) -> None:
        """Persist the dedup state to disk. Call once at end of run.

        No-op in dry-run mode — dry-run must not alter any on-disk
        state so repeated dry-runs are deterministic previews of
        "what a real run would do from the current dedup baseline".
        """
        with self._lock:
            if self.dry_run:
                return
            if self.state_path is not None:
                _save_state(self._state, self.state_path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _body_for(self, body_json: dict) -> str:
        """Serialise a parsed JSON dict back to a canonical string for hashing.

        Uses ``sort_keys=True`` so semantically-identical manifests
        always produce the same fingerprint regardless of how the app
        ordered the fields when writing.
        """
        try:
            return json.dumps(
                body_json,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("body serialise failed: %s", exc)
            return json.dumps({"_pce_error": "non_serialisable", "str": str(body_json)[:200]})

    def _leveldb_body(self, value: bytes) -> tuple[str, str]:
        """Return (body_str, body_repr_tag).

        body_repr_tag is one of ``"utf8_json"`` | ``"utf8_text"`` |
        ``"base64"``, so downstream normalizers know how to parse.
        """
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            import base64
            return base64.b64encode(value).decode("ascii"), "base64"

        stripped = text.strip()
        if stripped and stripped[0] in "{[":
            try:
                json.loads(stripped)
                return text, "utf8_json"
            except json.JSONDecodeError:
                pass
        return text, "utf8_text"

    def _fingerprint(self, source_path: Path, kind: str, content_hash_seed: str) -> str:
        h = hashlib.sha256()
        h.update(str(source_path).encode("utf-8", errors="replace"))
        h.update(b"|")
        h.update(kind.encode("utf-8"))
        h.update(b"|")
        h.update(content_hash_seed.encode("utf-8", errors="replace"))
        return h.hexdigest()

    def _already_emitted(self, fp: str) -> bool:
        return fp in self._state.entries

    def _mark_emitted(self, fp: str, *, kind: str) -> None:
        self._state.entries[fp] = {
            "emitted_at": time.time(),
            "app_id": self.app_id,
            "kind": kind,
        }

    def _write(
        self,
        *,
        direction: str,
        host: str,
        path: str,
        provider: str,
        body_str: str,
        meta: dict[str, Any],
        session_hint: Optional[str],
        trigger_normalize: bool = False,
    ) -> bool:
        """Insert a raw_captures row.

        When ``trigger_normalize`` is True, also invoke
        ``pipeline.normalize_conversation`` on the freshly-written row
        so the L3g capture lands in sessions+messages immediately. Used
        by transcript_line records where each line is independently
        parseable (P5.B.5.3, 2026-05-11).
        """
        if self.dry_run:
            return True
        payload_body = body_str if self.include_bodies else ""
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        try:
            pair_id = new_pair_id()
            insert_capture(
                direction=direction,
                pair_id=pair_id,
                host=host,
                path=path,
                method="GET",
                provider=provider,
                status_code=None,
                latency_ms=None,
                body_text_or_json=payload_body,
                body_format="json" if meta.get("source_kind", "").startswith("leveldb") is False else "json",
                meta_json=meta_json,
                source_id=SOURCE_L3G_LOCAL_PERSISTENCE,
                source="local_persistence",
                agent_name="pce-persistence-watcher",
                db_path=self.db_path,
                session_hint=session_hint,
            )
            if trigger_normalize:
                self._normalize_just_inserted(pair_id)
            return True
        except Exception as exc:  # pragma: no cover — defensive guard
            self.stats["capture_failures"] += 1
            logger.warning(
                "persistence_watcher.capture_failed",
                extra={
                    "event": "persistence_watcher.capture_failed",
                    "pce_fields": {
                        "host": host,
                        "path": path,
                        "error": repr(exc),
                    },
                },
            )
            return False

    def _normalize_just_inserted(self, pair_id: str) -> None:
        """Trigger ``normalize_conversation`` on a single L3g pair.

        Imports are lazy to avoid a watcher → normaliser → DB import
        cycle at module load (the normaliser pulls in heavy SQL helpers
        only needed once we actually have a capture to process).

        Failures are recorded into ``pipeline_errors`` and counted in
        ``stats["capture_failures"]`` but never raised — the watcher
        must keep running even if one line fails to normalise.
        """
        try:
            from pce_core.db import query_by_pair, record_pipeline_error
            from pce_core.normalizer.pipeline import normalize_conversation
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("normaliser import failed (skipping): %s", exc)
            return
        try:
            rows = query_by_pair(pair_id, db_path=self.db_path)
            if not rows:
                return
            normalize_conversation(
                rows[0],
                source_id=SOURCE_L3G_LOCAL_PERSISTENCE,
                created_via="l3g_transcript_line",
                db_path=self.db_path,
            )
        except Exception as exc:
            self.stats["capture_failures"] += 1
            try:
                record_pipeline_error(
                    "normalize",
                    f"normalize_conversation(l3g): {type(exc).__name__}: {exc}",
                    source_id=SOURCE_L3G_LOCAL_PERSISTENCE,
                    pair_id=pair_id,
                    details={"axis": "L3g", "kind": "transcript_line"},
                )
            except Exception:
                pass
            logger.debug(
                "transcript_line normalise failed pair=%s: %s",
                pair_id[:8], exc,
            )


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _ascii_preview(b: bytes, limit: int = 64) -> str:
    """Return a truncated ASCII-safe preview of byte content for meta."""
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        s = b.decode("latin-1", errors="replace")
    s = s.replace("\x00", ".")
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s
