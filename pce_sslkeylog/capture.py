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
    bodies_attached: int = 0          # h2 DATA frames attached to a response
    bodies_unmatched: int = 0         # h2 DATA frames with no matching pair


@dataclass
class _PendingPair:
    """Per-(tcp_stream, h2 stream id) state during one HTTP exchange.

    The pair is emitted (``_insert_capture`` called) when either:
      - HTTP/1: the response HEADERS arrive (fields surface immediately)
      - HTTP/2: the response HEADERS arrive AND a DATA frame with
        END_STREAM either arrives or never comes (TTL); body bytes are
        attached on the way out
    """
    request_ev: Optional[TsharkEvent] = None
    response_ev: Optional[TsharkEvent] = None
    response_body: bytes = b""
    request_body: bytes = b""
    ts: float = 0.0
    pair_id: str = ""
    is_http2: bool = False


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

        # ``_pending`` keys are ``pair_key`` strings (tcp_stream + h2 stream
        # id). Values are :class:`_PendingPair` dataclasses that grow over
        # the lifetime of the exchange: request HEADERS → response HEADERS
        # → DATA frames carrying body bytes. For HTTP/1 the value is
        # emitted as soon as the response HEADERS arrives (no body
        # waiting); for HTTP/2 we wait for the END_STREAM DATA frame so
        # we can attach the reassembled response body.
        self._pending: dict[str, _PendingPair] = {}
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
            elif ev.direction == "data":
                self._handle_data(ev)
        except Exception:
            self.stats.insert_errors += 1
            logger.exception("handle_line: insert failed (non-fatal)")

    # --- internal -----------------------------------------------------------

    def _handle_request(self, ev: TsharkEvent) -> None:
        pair_id = self._new_pair_id()
        now = time.time()
        with self._lock:
            self._pending[ev.pair_key] = _PendingPair(
                request_ev=ev,
                request_body=ev.body or b"",
                ts=now,
                pair_id=pair_id,
                is_http2=ev.is_http2,
            )
            # Bound memory: drop oldest if over max_pending
            while len(self._pending) > self.max_pending:
                oldest_key = min(self._pending, key=lambda k: self._pending[k].ts)
                old_entry = self._pending.pop(oldest_key)
                if old_entry.request_ev is not None:
                    self._emit_orphan(old_entry.request_ev, old_entry.pair_id)

    def _handle_response(self, ev: TsharkEvent) -> None:
        with self._lock:
            entry = self._pending.get(ev.pair_key)
            # Decide whether to emit now or defer pending the body. For
            # HTTP/1 we always have everything in the response HEADERS
            # already, so emit immediately. For HTTP/2 we defer unless
            # END_STREAM is set on the HEADERS frame (rare — usually
            # only for "no body" responses like 204/304/redirects).
            should_defer_for_body = ev.is_http2 and not ev.end_stream
            if entry is not None:
                entry.response_ev = ev
                entry.ts = time.time()
                if not should_defer_for_body:
                    self._pending.pop(ev.pair_key, None)
            else:
                # Orphan response branch — no pending request was stored.
                pass
        if entry is None:
            if self.host_allowlist:
                # Drop orphan responses when an allowlist is configured:
                # without the request we can't validate the host belongs.
                return
            pair_id = self._new_pair_id()
            self._emit_pair(None, ev, pair_id)
            return
        if not should_defer_for_body:
            self._emit_pair(entry.request_ev, ev, entry.pair_id,
                            response_body=entry.response_body,
                            request_body=entry.request_body)

    def _handle_data(self, ev: TsharkEvent) -> None:
        """Attach a reassembled HTTP/2 body to a pending (or just-emitted)
        pair. We can't tell from the DATA frame alone which direction
        (request vs response) the body belongs to, but in real-world AI
        traffic the response body is overwhelmingly larger and the more
        interesting one; if the request side had a body, it's typically
        already encoded in the HEADERS frame's HPACK headers fragment.
        Treat all attached data as response_body."""
        emit: Optional[_PendingPair] = None
        with self._lock:
            entry = self._pending.get(ev.pair_key)
            if entry is None:
                self.stats.bodies_unmatched += 1
                return
            if ev.body:
                # Last-write-wins: the END_STREAM frame's
                # ``body_reassembled_data`` field already contains the
                # full body, so overwriting earlier fragments is safe.
                entry.response_body = ev.body
                self.stats.bodies_attached += 1
            entry.ts = time.time()
            if ev.end_stream and entry.response_ev is not None:
                # Both sides + body are now in hand — flush.
                self._pending.pop(ev.pair_key, None)
                emit = entry
        if emit is not None:
            self._emit_pair(emit.request_ev, emit.response_ev, emit.pair_id,
                            response_body=emit.response_body,
                            request_body=emit.request_body)

    def _emit_pair(
        self, req: Optional[TsharkEvent], resp: Optional[TsharkEvent], pair_id: str,
        *, request_body: bytes = b"", response_body: bytes = b"",
    ) -> None:
        # Body override: if the caller accumulated body bytes via h2 DATA
        # frames, set them on the events before serialization. We
        # construct shallow copies to avoid mutating the original events
        # (they may be retained by callers for tests / debugging).
        if response_body and resp is not None:
            resp = _ev_with_body(resp, response_body)
        if request_body and req is not None and not req.body:
            req = _ev_with_body(req, request_body)
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
        """Drop pendings older than TTL.

        A pending entry might be expired for two reasons:
          1. Request HEADERS seen, response never arrived → emit orphan
             request row (same semantics as before).
          2. Both HEADERS arrived (HTTP/2) but DATA never carried an
             END_STREAM with reassembled body → emit the pair now with
             whatever body bytes accumulated so far (which may be b"").
             Don't lose the response just because the body never closed.
        """
        now = time.time()
        to_drop: list[str] = []
        with self._lock:
            for key, entry in self._pending.items():
                if now - entry.ts > self.pending_ttl_s:
                    to_drop.append(key)
            expired = [(k, self._pending.pop(k)) for k in to_drop]
        for _, entry in expired:
            if entry.response_ev is not None:
                # Pair was awaiting body; emit with whatever we have.
                self._emit_pair(
                    entry.request_ev, entry.response_ev, entry.pair_id,
                    response_body=entry.response_body,
                    request_body=entry.request_body,
                )
            elif entry.request_ev is not None:
                self._emit_orphan(entry.request_ev, entry.pair_id)


def _ev_with_body(ev: TsharkEvent, body: bytes) -> TsharkEvent:
    """Return a copy of ``ev`` with ``body`` overridden."""
    return TsharkEvent(
        direction=ev.direction,
        host=ev.host,
        path=ev.path,
        method=ev.method,
        status_code=ev.status_code,
        headers=ev.headers,
        body=body,
        stream_id=ev.stream_id,
        tcp_stream=ev.tcp_stream,
        timestamp=ev.timestamp,
        is_http2=ev.is_http2,
        end_stream=ev.end_stream,
    )


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
