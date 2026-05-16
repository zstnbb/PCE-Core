# SPDX-License-Identifier: Apache-2.0
"""NSS Key Log Format parser (W2-T1 / ADR-018 Phase 5).

Reads ``SSLKEYLOGFILE`` (the file Chromium writes pre-master secrets to)
and joins per-flow ClientRandom → secret bundle. Used by the mitmproxy
addon as a corroboration channel: when L1 MITM successfully decrypted
a TLS session, we cross-check that the same ClientRandom appears in
the keylog → produces a forensic-grade evidence claim that ``raw_captures``
body is genuine and not tampered with mid-flight.

Format reference:
https://firefox-source-docs.mozilla.org/security/nss/legacy/key_log_format/index.html

::

    <Label> <ClientRandom (hex)> <Secret (hex)>

Labels we care about (TLS 1.3, 5 per session):
    CLIENT_HANDSHAKE_TRAFFIC_SECRET
    SERVER_HANDSHAKE_TRAFFIC_SECRET
    CLIENT_TRAFFIC_SECRET_0
    SERVER_TRAFFIC_SECRET_0
    EXPORTER_SECRET

Privacy: secrets stay **in-memory only**. The mitmproxy callback
emits a ``keylog_evidence`` dict that gets serialised into
``raw_captures.meta_json.keylog`` — but only ``completeness`` and
``session_count``, NEVER the secret bytes (W2-§4.4).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("pce.proxy.keylog")


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

#: Full TLS 1.3 label set (5).
TLS13_LABELS: frozenset[str] = frozenset({
    "CLIENT_HANDSHAKE_TRAFFIC_SECRET",
    "SERVER_HANDSHAKE_TRAFFIC_SECRET",
    "CLIENT_TRAFFIC_SECRET_0",
    "SERVER_TRAFFIC_SECRET_0",
    "EXPORTER_SECRET",
})

#: Labels we accept (we don't reject TLS 1.2-only labels but they don't
#: count toward "full").
ALL_KNOWN_LABELS: frozenset[str] = TLS13_LABELS | frozenset({
    "CLIENT_RANDOM",                    # TLS 1.2
    "CLIENT_EARLY_TRAFFIC_SECRET",      # TLS 1.3 0-RTT
})

#: When deciding completeness for a session, one of these subsets implies "full".
_FULL_TLS13: frozenset[str] = TLS13_LABELS
_FULL_TLS12: frozenset[str] = frozenset({"CLIENT_RANDOM"})


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

@dataclass
class KeylogSession:
    """Per-ClientRandom secret bundle."""

    client_random_hex: str
    secrets: dict[str, str] = field(default_factory=dict)

    def add(self, label: str, secret_hex: str) -> None:
        self.secrets[label] = secret_hex

    def completeness(self) -> str:
        labels = frozenset(self.secrets.keys())
        if _FULL_TLS13 <= labels:
            return "full"
        if _FULL_TLS12 <= labels:
            return "tls12_full"
        if any(lbl.endswith("_HANDSHAKE_TRAFFIC_SECRET") for lbl in labels):
            return "partial_handshake_only"
        return "minimal"

    def evidence_meta(self) -> dict:
        """The metadata-only blob that gets written to raw_captures.meta_json."""
        return {
            "completeness": self.completeness(),
            "label_count": len(self.secrets),
            "labels": sorted(self.secrets.keys()),
        }


def parse_keylog_line(line: str) -> Optional[tuple[str, str, str]]:
    """Return ``(label, client_random_hex, secret_hex)`` or None if malformed."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) != 3:
        return None
    label, cr, secret = parts
    if label not in ALL_KNOWN_LABELS:
        return None
    if not _is_hex(cr) or not _is_hex(secret):
        return None
    if len(cr) != 64:  # ClientRandom is 32 bytes = 64 hex chars
        return None
    return label, cr.lower(), secret.lower()


def _is_hex(s: str) -> bool:
    if not s:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def parse_keylog_text(text: str) -> dict[str, KeylogSession]:
    """Parse a full keylog blob into ``{client_random_hex: KeylogSession}``."""
    out: dict[str, KeylogSession] = {}
    for line in text.splitlines():
        parsed = parse_keylog_line(line)
        if parsed is None:
            continue
        label, cr, secret = parsed
        session = out.setdefault(cr, KeylogSession(client_random_hex=cr))
        session.add(label, secret)
    return out


# ---------------------------------------------------------------------------
# Watcher: incremental tail of the keylog file
# ---------------------------------------------------------------------------

class KeylogWatcher:
    """Maintains an in-memory map of ``client_random → KeylogSession``.

    Behaviour:

    - Initial ``poll()`` reads the entire file.
    - Subsequent ``poll()`` calls read the appended tail since last
      offset (Chromium only ever appends to keylog).
    - Detects rotation / truncation via inode change or shrinking
      file size; in that case the map is reset and we re-read from 0.
    - LRU-caps the session map at ``max_sessions`` (default 1000) to
      bound memory; oldest entries fall out.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_sessions: int = 1000,
    ) -> None:
        self._path = Path(path)
        self._max_sessions = int(max_sessions)
        if self._max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self._sessions: "OrderedDict[str, KeylogSession]" = OrderedDict()
        self._offset = 0
        self._inode: Optional[int] = None
        self._lock = threading.Lock()

    def poll(self) -> int:
        """Read any new bytes; return the number of newly-parsed lines."""
        with self._lock:
            if not self._path.exists():
                # File rotated away or not yet created.
                self._reset()
                return 0
            stat = self._path.stat()
            if self._inode is None:
                self._inode = self._stat_inode(stat)
            elif (self._stat_inode(stat) != self._inode
                  or stat.st_size < self._offset):
                # Rotation / truncation.
                self._reset()
                self._inode = self._stat_inode(stat)
            try:
                with self._path.open("rb") as fh:
                    fh.seek(self._offset)
                    new_bytes = fh.read()
                    self._offset = fh.tell()
            except OSError:
                return 0
            if not new_bytes:
                return 0
            text = new_bytes.decode("utf-8", errors="replace")
            new_lines = 0
            for line in text.splitlines():
                parsed = parse_keylog_line(line)
                if parsed is None:
                    continue
                label, cr, secret = parsed
                session = self._sessions.get(cr)
                if session is None:
                    session = KeylogSession(client_random_hex=cr)
                    self._sessions[cr] = session
                session.add(label, secret)
                self._sessions.move_to_end(cr)
                new_lines += 1
                self._enforce_cap()
            return new_lines

    def lookup(self, client_random_hex: str) -> Optional[KeylogSession]:
        """O(1) lookup. Returns None if no entry."""
        cr = (client_random_hex or "").lower()
        with self._lock:
            return self._sessions.get(cr)

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def reset(self) -> None:
        with self._lock:
            self._reset()

    # -------------------------------------------------------------- internal

    def _reset(self) -> None:
        self._sessions.clear()
        self._offset = 0
        self._inode = None

    def _enforce_cap(self) -> None:
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    @staticmethod
    def _stat_inode(stat) -> int:
        # Windows st_ino is best-effort; on NTFS volumes it works reliably,
        # otherwise we fall back to st_dev so the rotation heuristic still
        # picks up file replacement.
        return int(stat.st_ino) if stat.st_ino else int(stat.st_dev)


# ---------------------------------------------------------------------------
# Mitmproxy flow callback (placeholder — wired up by addon.py in W2-T3)
# ---------------------------------------------------------------------------

def emit_evidence_from_flow(
    watcher: KeylogWatcher,
    *,
    client_random_hex: Optional[str],
) -> Optional[dict]:
    """Look up a flow's ClientRandom and return ``meta_json.keylog`` blob.

    Returns ``None`` when:
      - ``client_random_hex`` is missing (e.g. mitmproxy didn't surface it)
      - the watcher has no entry for that random (race / keylog disabled)

    The returned dict is **metadata only** — secrets are intentionally
    excluded so the value is safe to serialise into raw_captures.
    """
    if not client_random_hex:
        return None
    session = watcher.lookup(client_random_hex)
    if session is None:
        return None
    return session.evidence_meta()
