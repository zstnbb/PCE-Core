# SPDX-License-Identifier: Apache-2.0
"""Capture Supervisor — dedup contract (ADR-021 §3.1).

Sliding-window LRU on ``(pair_id, fingerprint)``. The intent is to let
multiple capture legs all hit the same ``POST /api/v1/captures`` flow
without producing duplicate ``raw_captures`` rows when they're recording
the **same** request:

- First leg to ``claim()`` → ``is_primary=True`` → caller inserts a
  fresh ``raw_captures`` row.
- Subsequent legs hitting the same ``(pair_id, fingerprint)`` within the
  window → ``is_primary=False`` + ``primary_source`` reveals which leg
  owns the primary row → caller appends its own source to the primary's
  ``deduped_by`` JSON array (CaptureEvent v2 column, migration 0006).

False-positive avoidance (ADR-021 §3.1.1 + Wave 3 risk W3-R1):

- ``fingerprint`` is computed by :func:`compute_fingerprint` and includes
  a 5-minute timestamp bucket so the **same** request issued 5 minutes
  apart does NOT collide as a duplicate.
- The window cap (``max_entries=10000``) bounds memory; the window time
  (default 30s) bounds the de-dup horizon. Any caller that wants to
  inspect everything raw can pass ``--no-dedup`` upstream and skip
  ``claim()`` entirely.

Thread-safety: ``claim()`` is guarded by a single ``threading.Lock``;
the critical section is just a dict probe, so contention is tolerable
even at the modest QPS of a single-user PCE instance.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a :meth:`CaptureDedup.claim` call."""

    is_primary: bool
    primary_source: Optional[str] = None
    primary_capture_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Fingerprint helper
# ---------------------------------------------------------------------------

def compute_fingerprint(
    method: str,
    host: str,
    path: str,
    body: Optional[bytes] = None,
    *,
    ts_bucket_5min: Optional[int] = None,
    now: Optional[float] = None,
) -> str:
    """SHA-256 over canonicalised request fields.

    The 5-minute bucket means an *identical* user request issued 5
    minutes apart cannot dedupe against itself — eliminating the most
    common false-positive class (chat regenerate / refresh button mash).
    """
    if ts_bucket_5min is None:
        clock = now if now is not None else time.time()
        ts_bucket_5min = int(clock // 300)
    body_canon = (body or b"")[:1024]
    head = f"{method.upper()}|{host.lower()}|{path}|{ts_bucket_5min}|".encode("utf-8")
    return hashlib.sha256(head + body_canon).hexdigest()


# ---------------------------------------------------------------------------
# Sliding-window LRU
# ---------------------------------------------------------------------------

class CaptureDedup:
    """Sliding-window LRU over ``(pair_id, fingerprint)``.

    Parameters
    ----------
    window_s
        How long an entry remains de-duplicable. After this many seconds
        the same key starts a fresh primary.
    max_entries
        Hard cap on the LRU; oldest entry evicted past this bound.
    """

    def __init__(
        self,
        window_s: float = 30.0,
        max_entries: int = 10000,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._window_s = float(window_s)
        self._max_entries = int(max_entries)
        # OrderedDict preserves insertion order so we can pop the oldest
        # entry in O(1) when the cap is hit.
        self._entries: "OrderedDict[tuple[str, str], _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        # Lightweight metrics — exposed to /api/v1/supervisor/status for
        # transparency. Counters reset on supervisor restart, which is fine.
        self._claims_total = 0
        self._claims_primary = 0
        self._claims_duplicate = 0

    # ------------------------------------------------------------------ API

    def claim(
        self,
        pair_id: str,
        fingerprint: str,
        source: str,
        *,
        capture_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ClaimResult:
        """Claim a (pair_id, fingerprint) for ``source``.

        Returns
        -------
        ClaimResult
            ``is_primary=True`` → caller is the first leg → insert a row.
            ``is_primary=False`` → ``primary_source`` is the leg that
            already owns the primary row; caller should append itself
            to that row's ``deduped_by`` JSON array (db column added in
            migration 0006).
        """
        key = (pair_id, fingerprint)
        clock = now if now is not None else time.time()
        with self._lock:
            self._claims_total += 1
            self._evict_expired(clock)
            entry = self._entries.get(key)
            if entry is not None and (clock - entry.first_seen) <= self._window_s:
                # Hit. Move to MRU end and append source list.
                self._entries.move_to_end(key)
                if source not in entry.sources:
                    entry.sources.append(source)
                self._claims_duplicate += 1
                return ClaimResult(
                    is_primary=False,
                    primary_source=entry.primary_source,
                    primary_capture_id=entry.primary_capture_id,
                )
            # Miss → fresh primary.
            new_entry = _Entry(
                primary_source=source,
                primary_capture_id=capture_id,
                first_seen=clock,
                sources=[source],
            )
            self._entries[key] = new_entry
            self._entries.move_to_end(key)
            self._enforce_cap()
            self._claims_primary += 1
            return ClaimResult(
                is_primary=True,
                primary_source=source,
                primary_capture_id=capture_id,
            )

    def metrics(self) -> dict[str, int | float]:
        """Lightweight snapshot for /supervisor/status."""
        with self._lock:
            total = self._claims_total
            dup = self._claims_duplicate
            hit_ratio = (dup / total) if total else 0.0
            return {
                "claims_total": total,
                "claims_primary": self._claims_primary,
                "claims_duplicate": dup,
                "hit_ratio": round(hit_ratio, 4),
                "entries_live": len(self._entries),
                "window_s": self._window_s,
                "max_entries": self._max_entries,
            }

    def reset(self) -> None:
        """Drop all state — handy in tests."""
        with self._lock:
            self._entries.clear()
            self._claims_total = 0
            self._claims_primary = 0
            self._claims_duplicate = 0

    # ------------------------------------------------------------------ internals

    def _evict_expired(self, now: float) -> None:
        """Walk from the LRU side and pop anything older than the window."""
        cutoff = now - self._window_s
        while self._entries:
            oldest_key = next(iter(self._entries))
            oldest = self._entries[oldest_key]
            if oldest.first_seen < cutoff:
                self._entries.popitem(last=False)
            else:
                break

    def _enforce_cap(self) -> None:
        """Hard cap: pop the LRU until len ≤ max_entries."""
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


# ---------------------------------------------------------------------------
# Internal entry record
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    primary_source: str
    primary_capture_id: Optional[str]
    first_seen: float
    sources: list[str]
