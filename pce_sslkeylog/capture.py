# SPDX-License-Identifier: Apache-2.0
"""Glue: tshark NDJSON lines → pair request/response → raw_captures rows.

This module owns the pairing state machine. tshark emits request and
response events out of order; we hold pending requests indexed by
``pair_key`` (tcp_stream + http2 stream_id) and emit ``insert_capture``
when the matching response arrives (or when a per-event TTL elapses).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .parser import (
    EkRecord,
    TsharkEvent,
    build_capture_from_pair,
    event_from_record,
    parse_ek_line,
)

logger = logging.getLogger("pce.sslkeylog.capture")


@dataclass
class CaptureStats:
    """Counters surfaced to ``/api/v1/health`` for monitoring."""
    lines_total: int = 0
    lines_parsed: int = 0
    events_total: int = 0
    pairs_emitted: int = 0
    orphans_emitted: int = 0
    insert_errors: int = 0


class PairingCaptureSink:
    """Stateful sink that turns tshark NDJSON lines into raw_captures.

    Invariants:
    - Holds at most ``max_pending`` pending requests in memory.
    - Each pending request has a TTL; expired ones are flushed as
      "orphan request" rows (matches pce_proxy/pipeline's
      ``sweep_orphan_request_rows`` semantics).
    - Thread-safe: ``handle_line()`` is callable from the
      ``TsharkRunner`` background thread.
    """

    def __init__(
        self,
        *,
        insert_capture_fn=None,
        new_pair_id_fn=None,
        try_normalize_pair_fn=None,
        source_id: str = "sslkeylog-default",
        host_allowlist: Optional[frozenset[str]] = None,
        max_pending: int = 1000,
        pending_ttl_s: float = 30.0,
    ) -> None:
        # Late binding so tests can pass mocks without importing pce_core
        if insert_capture_fn is None:
            from pce_core.db import insert_capture as _insert
            insert_capture_fn = _insert
        if new_pair_id_fn is None:
            from pce_core.db import new_pair_id as _newp
            new_pair_id_fn = _newp
        if try_normalize_pair_fn is None:
            try:
                from pce_core.normalizer.pipeline import (
                    try_normalize_pair as _norm,
                )
                try_normalize_pair_fn = _norm
            except Exception:
                try_normalize_pair_fn = None

        self._insert_capture = insert_capture_fn
        self._new_pair_id = new_pair_id_fn
        self._try_normalize_pair = try_normalize_pair_fn
        self.source_id = source_id
        self.host_allowlist = host_allowlist
        self.max_pending = max_pending
        self.pending_ttl_s = pending_ttl_s

        self._pending: dict[str, tuple[TsharkEvent, float, str]] = {}
        self._lock = threading.RLock()
        self.stats = CaptureStats()

    def handle_line(self, line: str) -> None:
        """Called from TsharkRunner background thread per NDJSON line.

        Never raises; logs warnings + bumps stats.insert_errors on
        failure."""
        self.stats.lines_total += 1
        rec = parse_ek_line(line)
        if rec is None:
            return
        self.stats.lines_parsed += 1
        ev = event_from_record(rec)
        if ev is None:
            return
        self.stats.events_total += 1

        # Host allowlist (post-filter; -f only narrows BPF coarsely)
        if self.host_allowlist and ev.host and not _host_allowed(ev.host, self.host_allowlist):
            return

        # GC expired pendings before insert
        self._sweep_expired_pending()

        try:
            if ev.direction == "request":
                self._handle_request(ev)
            elif ev.direction == "response":
                self._handle_response(ev)
        except Exception:
            self.stats.insert_errors += 1
            logger.exception("handle_line: insert failed (non-fatal)")

    # --- internal -----------------------------------------------------------

    def _handle_request(self, ev: TsharkEvent) -> None:
        pair_id = self._new_pair_id()
        with self._lock:
            # Hold request side until response arrives or TTL expires
            self._pending[ev.pair_key] = (ev, time.time(), pair_id)
            # Bound memory: drop oldest if over max_pending
            while len(self._pending) > self.max_pending:
                oldest_key = min(self._pending, key=lambda k: self._pending[k][1])
                old_ev, _, old_pid = self._pending.pop(oldest_key)
                self._emit_orphan(old_ev, old_pid)

    def _handle_response(self, ev: TsharkEvent) -> None:
        with self._lock:
            pending = self._pending.pop(ev.pair_key, None)
        if pending is None:
            # Orphan response (we never saw its request — TLS could've
            # started before tshark attached, OR the corresponding
            # request was filtered out by host_allowlist).
            #
            # Drop the orphan when a host_allowlist is in effect:
            # without a matching pending request we have no way to
            # know whether the response belongs to an allowlisted host
            # (HTTP/2 responses don't carry :authority — only :status).
            # The cost of dropping is acceptable; orphan responses
            # without context are low-value.
            if self.host_allowlist:
                return
            pair_id = self._new_pair_id()
            self._emit_pair(None, ev, pair_id)
            return
        req_ev, _, pair_id = pending
        self._emit_pair(req_ev, ev, pair_id)

    def _emit_pair(
        self, req: Optional[TsharkEvent], resp: Optional[TsharkEvent], pair_id: str,
    ) -> None:
        kwargs_list = build_capture_from_pair(req, resp, pair_id=pair_id, source_id=self.source_id)
        for kwargs in kwargs_list:
            self._insert_capture(**kwargs)
        self.stats.pairs_emitted += 1
        if self._try_normalize_pair is not None:
            try:
                self._try_normalize_pair(pair_id, source_id=self.source_id,
                                          created_via="sslkeylog")
            except Exception:
                logger.exception(
                    "auto-normalize failed for pair %s (non-fatal)",
                    pair_id[:8],
                )

    def _emit_orphan(self, ev: TsharkEvent, pair_id: str) -> None:
        """Emit a request-only row when no response arrived within TTL."""
        kwargs_list = build_capture_from_pair(ev, None, pair_id=pair_id, source_id=self.source_id)
        for kwargs in kwargs_list:
            self._insert_capture(**kwargs)
        self.stats.orphans_emitted += 1

    def _sweep_expired_pending(self) -> None:
        """Drop pendings older than TTL, emitting them as orphans."""
        now = time.time()
        to_drop: list[str] = []
        with self._lock:
            for key, (_, ts, _) in self._pending.items():
                if now - ts > self.pending_ttl_s:
                    to_drop.append(key)
            expired = [(k, self._pending.pop(k)) for k in to_drop]
        for _, (ev, _, pid) in expired:
            self._emit_orphan(ev, pid)


def _host_allowed(host: str, allowlist: frozenset[str]) -> bool:
    """Match a host against the allowlist, with suffix support.

    e.g. "claude.ai" in allowlist matches "www.claude.ai".

    Also strips ``:port`` suffix that appears on HTTP/1 ``CONNECT`` requests
    (e.g. ``api.anthropic.com:443``) and on HTTP/2 ``:authority`` when an
    intermediate proxy includes the port — without this normalization
    those captures get dropped by an otherwise-correct allowlist.
    """
    h = host.lower().rstrip(".")
    # Strip ``:port`` if present (but be careful: IPv6 literals contain
    # colons inside ``[...]`` brackets, so only strip the trailing one
    # after the last ``]`` or after a dot-name).
    if h.startswith("["):
        # IPv6 form ``[::1]:443`` — strip just the trailing :port
        idx = h.rfind("]:")
        if idx != -1:
            h = h[: idx + 1]
    else:
        idx = h.rfind(":")
        if idx != -1 and h[idx + 1:].isdigit():
            h = h[:idx]
    if h in allowlist:
        return True
    return any(h.endswith("." + allowed) for allowed in allowlist)
