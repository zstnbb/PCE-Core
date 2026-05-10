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
from .leveldb_reader import LevelDbRecord

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
        self.stats: dict[str, int] = {
            "records_seen": 0,
            "records_emitted": 0,
            "records_deduped": 0,
            "capture_failures": 0,
            "sessions": 0,
            "skills_catalogue": 0,
            "leveldb": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe_agent_session(self, rec: AgentSessionRecord) -> None:
        """Ingest one parsed ``agent_sessions`` record."""
        with self._lock:
            self.stats["records_seen"] += 1
            self.stats[rec.kind] = self.stats.get(rec.kind, 0) + 1

            body_str = self._body_for(rec.body_json)
            fp = self._fingerprint(rec.source_path, rec.kind, body_str)
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

            ok = self._write(
                direction="conversation",
                host="local-agent-mode",
                path=f"/{self.app_id}/agent-session/{rec.session_id or 'unknown'}",
                provider="anthropic",
                body_str=body_str,
                meta=meta,
                session_hint=rec.session_id,
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
    ) -> bool:
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
