# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.leveldb_reader – safe read of Chromium LevelDB.

This module exposes a **read-only, lock-safe** interface to the LevelDB
directories that Chromium-based apps use for ``Local Storage`` and
``IndexedDB``. The classical concern: Chromium holds an exclusive LOCK
file while the app runs, and naive LevelDB readers that use that lock
refuse to open the directory.

We side-step the LOCK in two ways, both read-only:

1. **Safe-copy mode** (always available): copy the ``.ldb`` + ``.log`` +
   ``CURRENT`` + ``MANIFEST-*`` files (NOT the LOCK) to a temp directory
   under ``PCE_DATA_DIR/l3g_tmp/``, then open the copy. Chromium's
   file LOCK does not prevent file-level read copies on Windows
   because Chrome uses ``FILE_SHARE_READ`` in its CreateFile flags.

2. **Binary reader** (optional, pluggable): when the ``plyvel-ci`` or
   ``plyvel`` wheel is importable, we use it to iterate the copied
   database. Without it, ``iter_records`` raises ``LevelDbUnavailable``
   — callers in the watcher's ``scan``/``watch`` loops catch that and
   downgrade to JSON-only sources.

The reader is **intentionally optional** for v0: ADR-018 Phase 3
targets agent-sessions + skills-catalogue capture first, because those
are 100% JSON-based and need zero binary deps. A follow-up commit
(Phase 3.1) ships a pure-Python LDB block reader here so L3g is never
gated by a native dependency.

Call sites:

- ``pce_persistence_watcher.capture.ChromiumStateObserver.flush_local_storage()``
  — when the user sets ``--enable-leveldb`` and a reader backend is
  available.
- ``tests/e2e_l3g/test_leveldb_reader.py`` — uses in-memory LDB fixture
  to validate copy + iterate semantics without needing Claude Desktop.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("pce.persistence_watcher.leveldb_reader")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LevelDbUnavailable(RuntimeError):
    """Raised when no LevelDB reader backend is importable in this env.

    The watcher's main loop catches this and continues with the
    JSON-only sources (agent_sessions + skills).
    """


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

# Name of the available backend for diagnostics. One of:
#   "plyvel"      — the canonical LevelDB binding
#   "plyvel-ci"   — community fork with Windows wheels
#   None          — no backend available; iter_records raises
_BACKEND: Optional[str] = None


def _resolve_backend() -> Optional[str]:
    """Try to import a usable LevelDB binding; return its short name.

    Import is deferred so importing this module never fails.
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import plyvel  # type: ignore  # noqa: F401
        _BACKEND = "plyvel"
        return _BACKEND
    except ImportError:
        pass
    # ``plyvel-ci`` ships as ``plyvel`` too, so the import above already
    # catches it. Left here as a naming anchor for future alternative
    # pure-Python backends (``pure_leveldb``, etc.).
    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LevelDbRecord:
    """One (key, value) pair read from a copied LevelDB directory."""

    source_dir: Path        # ORIGINAL (uncopied) leveldb dir the record came from
    key_bytes: bytes
    value_bytes: bytes


# ---------------------------------------------------------------------------
# Safe-copy
# ---------------------------------------------------------------------------


_FILES_TO_SKIP = {"LOCK"}


def safe_copy(src: Path, dest_parent: Optional[Path] = None) -> Path:
    """Copy a LevelDB directory into a temp dir and return the copy path.

    The copy includes every file EXCEPT the ``LOCK`` file (which would
    otherwise collide with the running app's lock on Linux; Windows
    doesn't share-lock .ldb content but the LOCK file itself is
    redundant for a reader).

    Raises ``FileNotFoundError`` if ``src`` does not exist. Raises
    ``OSError`` on filesystem errors — callers should treat as soft
    failure.
    """
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"LevelDB source not found: {src}")

    if dest_parent is None:
        dest_parent = Path(tempfile.mkdtemp(prefix="pce_l3g_"))
    else:
        dest_parent.mkdir(parents=True, exist_ok=True)

    dest = dest_parent / src.name
    dest.mkdir(parents=True, exist_ok=True)

    # We iterate children directly (not shutil.copytree) so we can skip
    # LOCK and tolerate per-file transient errors without aborting the
    # whole copy.
    for child in src.iterdir():
        if child.name in _FILES_TO_SKIP:
            continue
        target = dest / child.name
        try:
            if child.is_file():
                # copy2 preserves mtime so downstream dedup stays stable.
                shutil.copy2(child, target)
            elif child.is_dir():
                # Chromium LevelDB dirs don't currently have nested dirs,
                # but future-proof: recursive copy without LOCK.
                shutil.copytree(child, target, ignore=shutil.ignore_patterns(*_FILES_TO_SKIP))
        except OSError as exc:
            logger.warning("skip file during LDB safe-copy %s: %s", child, exc)
            continue

    return dest


def cleanup_copy(dest: Path) -> None:
    """Remove a temp copy produced by ``safe_copy``. Idempotent."""
    if not dest.exists():
        return
    try:
        shutil.rmtree(dest)
    except OSError as exc:
        logger.debug("failed to clean %s: %s", dest, exc)


# ---------------------------------------------------------------------------
# Iteration (optional backend)
# ---------------------------------------------------------------------------


def iter_records(
    src: Path,
    *,
    max_records: Optional[int] = None,
    prefix: Optional[bytes] = None,
) -> Iterator[LevelDbRecord]:
    """Iterate (key, value) records out of a LevelDB dir.

    ``src`` is the ORIGINAL leveldb directory under the app's profile;
    this function performs the safe-copy internally and cleans up on
    iterator exhaustion. Callers should not copy themselves.

    ``max_records`` caps the yielded count (None = unlimited).
    ``prefix`` restricts iteration to keys starting with those bytes.

    Raises ``LevelDbUnavailable`` when no backend is importable.
    """
    backend = _resolve_backend()
    if backend is None:
        raise LevelDbUnavailable(
            "No LevelDB reader backend is importable. Install one of:\n"
            "  pip install plyvel-ci       # Windows-friendly wheels\n"
            "  pip install plyvel          # standard (requires libleveldb)\n"
            "Without a backend, L3g runs in JSON-only mode (agent_sessions "
            "+ skills only) — see ADR-018 §3.4."
        )

    copied: Optional[Path] = None
    db = None
    try:
        copied = safe_copy(src)
        import plyvel  # type: ignore
        db = plyvel.DB(str(copied), create_if_missing=False)
        it = db.iterator(prefix=prefix) if prefix is not None else db.iterator()
        count = 0
        for key, value in it:
            yield LevelDbRecord(source_dir=src, key_bytes=bytes(key), value_bytes=bytes(value))
            count += 1
            if max_records is not None and count >= max_records:
                break
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # pragma: no cover — defensive
                pass
        if copied is not None:
            cleanup_copy(copied)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def backend_info() -> dict[str, Optional[str]]:
    """Return ``{"backend": "plyvel"|None, "version": str|None}``.

    Used by ``python -m pce_persistence_watcher discover`` to report
    whether LevelDB reads will work, without actually opening anything.
    """
    b = _resolve_backend()
    version: Optional[str] = None
    if b is not None:
        try:
            import plyvel  # type: ignore
            version = getattr(plyvel, "__version__", None)
        except ImportError:
            b = None
    return {"backend": b, "version": version}


def is_available() -> bool:
    """True iff ``iter_records`` will succeed on a valid LevelDB dir."""
    return _resolve_backend() is not None


# ---------------------------------------------------------------------------
# IndexedDB .log strings extractor (ADR-018 §6 C4 supplementary, plyvel-free)
# ---------------------------------------------------------------------------
#
# When plyvel is unavailable (Windows, default), we cannot iterate
# Chromium IndexedDB key/value pairs via the LevelDB binding. But the
# .log file is just a binary stream with embedded printable ASCII runs
# carrying enough structural information (database names, object store
# names, UUIDs, JSON blobs) to surface a useful T3 metadata-only
# capture per ADR-018 §6 C4 supplementary findings.
#
# This section provides:
#   - iter_log_strings(path)         — pure ASCII run extractor
#   - scan_indexeddb_log(path)       — one-pass aggregate summary
#   - IndexedDbScanSummary            — typed result dataclass
#   - _redact_composer_draft(blob)   — TipTap text → "[redacted]"
#
# Capture observers consume IndexedDbScanSummary via
# ``ChromiumStateObserver.observe_indexeddb_summary``.

# Schema constants — keep aligned with ADR-018 §6 C4 supplementary.
# Adding a new known database name / object store: extend the relevant
# frozenset and add a regression test in test_indexeddb_strings.py.

_KNOWN_DB_NAMES: frozenset[str] = frozenset({
    "keyval-store",
    "claude-notifications",
    "ConversationsDatabase",
    "webrtc-cert-db",
})

_KNOWN_OBJECT_STORES: frozenset[str] = frozenset({
    "composer-drafts",
    "starredIds",
    "clientState",
    "mutations",
    "timestamp",
    "conversations",
    "certs",
})

# UUID-shape detection. Anchored variants would miss real Chromium
# keyval-store keys like ``id"$019e0fdf-..."`` or
# ``id"?new-assistant-message-uuid-<uuid>"`` where the UUID is embedded
# inside an IndexedDB-encoded prefix. The unanchored finder catches
# both bare UUIDs and embedded ones.
_UUID_FIND_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# UUID v7 carries the version nibble 0x7 in the third group's first
# hex digit (per RFC 9562). Time-ordered UUIDs in keyval-store keys.
_UUID_V7_FIND_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# Substring marker for composer-drafts JSON: TipTap doc model.
_COMPOSER_DRAFT_KEY = '"tipTapEditorState"'


@dataclass
class IndexedDbScanSummary:
    """One-pass aggregate of a Chromium IndexedDB ``.log`` file.

    Per ADR-018 §6 C4 supplementary findings (mapped 2026-05-10), the
    keyval-store ``.log`` file carries composer-drafts JSON,
    conversations_v2 keys, UUID v7 message refs, and a small set of
    other stores. This summary surfaces enough structural information
    to support dashboard "what's in this app's local state" views
    without binary deps.

    **Tier**: T3 metadata-only — does NOT decode UTF-16-LE conversation
    bodies (those need a real LevelDB binding + key-suffix decoder).
    Use this alongside any future per-record observer; the summary
    fingerprints stably across re-scans of an unchanged log file.
    """

    source_path: Path
    log_size_bytes: int
    scanned_at_epoch: float
    # Aggregate counts
    total_strings: int = 0
    uuid_count: int = 0
    uuid_v7_count: int = 0
    json_blob_count: int = 0
    composer_draft_count: int = 0
    # Schema discoveries
    db_name_hints: list[str] = field(default_factory=list)
    object_store_hints: list[str] = field(default_factory=list)
    # Capped + optionally redacted JSON examples
    json_blob_examples: list[dict[str, Any]] = field(default_factory=list)
    composer_drafts_redacted: bool = True


def iter_log_strings(
    log_path: Path,
    *,
    min_run: int = 6,
    max_run: int = 65536,
    max_records: int = 5000,
) -> Iterator[str]:
    """Yield printable-ASCII runs ≥``min_run`` chars from a binary log file.

    Streams the file in 64 KB chunks so memory usage is bounded
    regardless of log size. Each yielded run is decoded as Latin-1
    (lossless for the printable ASCII subset 0x20–0x7E) and truncated
    at ``max_run`` characters.

    Yields up to ``max_records`` runs total, then stops cleanly. The
    cap protects pathological logs (e.g. a malformed file that looks
    like all-printable) from blowing up callers.

    Returns silently if ``log_path`` is missing or not a regular file.
    """
    if not log_path.exists() or not log_path.is_file():
        return

    record_count = 0
    pending = bytearray()
    chunk_size = 65536

    with log_path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                # Flush pending tail
                if len(pending) >= min_run:
                    text = bytes(pending[:max_run]).decode("latin1", errors="replace")
                    yield text
                break

            for byte in chunk:
                if 0x20 <= byte < 0x7F:
                    pending.append(byte)
                else:
                    if len(pending) >= min_run:
                        text = bytes(pending[:max_run]).decode("latin1", errors="replace")
                        yield text
                        record_count += 1
                        if record_count >= max_records:
                            return
                    pending.clear()


def _redact_composer_draft(blob: Any) -> Any:
    """Recursively replace TipTap ``text`` content with ``"[redacted]"``.

    Operates on parsed JSON values: dicts, lists, scalars. Used by
    ``scan_indexeddb_log`` when ``redact_drafts=True`` (the default,
    per ADR-018 §6 C4 sensitivity warning). Idempotent.
    """
    if isinstance(blob, dict):
        return {
            k: ("[redacted]" if k == "text" and isinstance(v, str) else _redact_composer_draft(v))
            for k, v in blob.items()
        }
    if isinstance(blob, list):
        return [_redact_composer_draft(x) for x in blob]
    return blob


def scan_indexeddb_log(
    log_path: Path,
    *,
    redact_drafts: bool = True,
    max_examples: int = 10,
) -> IndexedDbScanSummary:
    """One-pass scan of a Chromium IndexedDB ``.log`` file → summary.

    Per ADR-018 §6 C4 supplementary, this surfaces:

    - Database / object store names (against ``_KNOWN_DB_NAMES`` /
      ``_KNOWN_OBJECT_STORES``)
    - UUID v7 conversation IDs (time-ordered per RFC 9562)
    - JSON blob examples (composer-drafts, starredIds, etc.)
    - Composer-draft count (``tipTapEditorState`` marker)

    ``redact_drafts=True`` (default per the ADR sensitivity warning):
    composer-draft text fields are replaced with ``"[redacted]"`` in
    ``json_blob_examples``. Set to False only with explicit user
    consent — composer drafts are unsent user content.

    ``max_examples`` caps how many JSON blobs land in the summary's
    ``json_blob_examples`` list (default 10) so the summary capture
    stays small enough for the DB envelope.
    """
    log_size = log_path.stat().st_size if log_path.exists() else 0
    summary = IndexedDbScanSummary(
        source_path=log_path,
        log_size_bytes=log_size,
        scanned_at_epoch=time.time(),
        composer_drafts_redacted=redact_drafts,
    )

    db_names_seen: set[str] = set()
    stores_seen: set[str] = set()
    # Track DISTINCT UUIDs seen across the whole log. Real keyval-store
    # often embeds the same UUID in multiple keys (e.g. id"$<uuid>" and
    # mutations/<uuid>), so per-string counting would over-count.
    uuids_seen: set[str] = set()
    uuids_v7_seen: set[str] = set()

    for s in iter_log_strings(log_path):
        summary.total_strings += 1

        # Schema indicators — cheap exact-match check.
        if s in _KNOWN_DB_NAMES:
            db_names_seen.add(s)
            continue
        if s in _KNOWN_OBJECT_STORES:
            stores_seen.add(s)
            continue

        # UUID detection — unanchored findall catches bare UUIDs as well
        # as ones embedded in IndexedDB-encoded key prefixes like
        # id"$<uuid>" / id"?new-assistant-message-uuid-<uuid>".
        for u in _UUID_FIND_RE.findall(s):
            uuids_seen.add(u)
            if _UUID_V7_FIND_RE.match(u):
                uuids_v7_seen.add(u)
        # Don't continue — a single string may carry both a UUID and a
        # JSON blob (e.g. mutations/<uuid> entries are both a key and
        # a serialised value).

        # JSON blob detection — only attempt on runs starting with '{'.
        # The Latin-1 decode means '{' is byte 0x7B regardless of
        # surrounding multi-byte sequences.
        stripped = s.strip()
        if stripped.startswith("{"):
            try:
                blob = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(blob, dict):
                continue
            summary.json_blob_count += 1

            # Composer-draft check: presence of TipTap marker in the
            # raw string (cheaper than walking the parsed dict).
            is_draft = _COMPOSER_DRAFT_KEY in stripped
            if is_draft:
                summary.composer_draft_count += 1
                if redact_drafts:
                    blob = _redact_composer_draft(blob)

            if len(summary.json_blob_examples) < max_examples:
                summary.json_blob_examples.append(blob)

    # Inference: object store names are stored as UTF-16-LE in Chromium
    # IndexedDB keys, which the iter_log_strings printable-ASCII filter
    # cannot recover directly. We backfill known stores via content
    # signatures: a TipTap-shaped JSON implies composer-drafts presence.
    if summary.composer_draft_count > 0:
        stores_seen.add("composer-drafts")

    summary.uuid_count = len(uuids_seen)
    summary.uuid_v7_count = len(uuids_v7_seen)
    summary.db_name_hints = sorted(db_names_seen)
    summary.object_store_hints = sorted(stores_seen)
    return summary


def find_log_files(leveldb_dir: Path) -> list[Path]:
    """Return all ``.log`` files in a Chromium LevelDB directory.

    Chromium's IndexedDB maintains exactly one active log file at a
    time (named ``<seq>.log`` where seq is monotonically increasing),
    plus optionally an ``LOG`` text file (server log, not data). This
    helper filters to the data ``.log`` files only.

    Used by ``__main__._scan_install`` to enumerate scan targets when
    falling back to the strings extractor.
    """
    if not leveldb_dir.exists() or not leveldb_dir.is_dir():
        return []
    out: list[Path] = []
    for child in leveldb_dir.iterdir():
        if not child.is_file():
            continue
        # Match Chromium's <seq>.log naming, exclude plain LOG / LOG.old.
        if re.match(r"^\d+\.log$", child.name):
            out.append(child)
    return sorted(out)
