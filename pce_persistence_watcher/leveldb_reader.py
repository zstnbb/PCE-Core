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

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

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
