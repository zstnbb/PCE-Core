# SPDX-License-Identifier: Apache-2.0
"""pce_mcp_proxy – JSON-RPC 2.0 frame observer.

Sole responsibility: turn a stream of byte-lines (one per JSON-RPC
frame) plus a direction tag (``"request"`` / ``"response"``) into
``raw_captures`` rows with ``source_id = SOURCE_MCP_PROXY``.

Frame taxonomy this observer handles:

- **Request** (``direction=request`` and ``msg.id`` present and
  ``msg.method`` present): host is asking upstream to do something
  (``initialize`` / ``tools/list`` / ``tools/call`` / ``resources/read``
  / …). Stashed by id, written as ``raw_captures.direction='request'``.

- **Notification** (``direction=request`` and ``msg.method`` present
  and ``msg.id`` absent): one-way signal, no response expected
  (``notifications/initialized`` / ``notifications/cancelled`` / …).
  Written as ``direction='request'`` with no pair tracking.

- **Response** (``direction=response`` and ``msg.id`` present): paired
  with the stashed request by ``id``. Latency computed at observation
  time. Errors (``msg.error`` present) get ``status_code=500``;
  successful responses (``msg.result`` present) get ``status_code=200``.

- **Server-initiated request** (``direction=response`` and
  ``msg.method`` present): upstream asks the host to do something
  (sampling, roots/list). Currently observed as direction='response'
  (because that's the wire direction: upstream → host) with the method
  recorded; pair tracking is intentionally one-shot for v1 (we do not
  wait for the host's reply since it would arrive on the request
  channel under a different id mapping that's hard to disambiguate).

Robustness rules:

- Any non-JSON line is silently dropped (stderr noise leaking into
  stdout, partial frames at EOF, etc.).
- Any DB write failure is caught and never raised; capture is best-
  effort, the relay is the source of truth.
- All observations are serialised behind a single threading lock so
  the pending-request map stays consistent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from pce_core.db import (
    SOURCE_MCP_PROXY,
    insert_capture,
    new_pair_id,
)
from pce_core.normalizer.pipeline import try_normalize_pair

logger = logging.getLogger("pce.mcp_proxy.capture")


class JsonRpcObserver:
    """Threadsafe observer that writes JSON-RPC frames to PCE.

    A single instance is shared between the relay's two observation
    pipes (host→upstream and upstream→host). All public methods are
    safe to call from multiple threads.
    """

    def __init__(
        self,
        *,
        upstream_name: str,
        db_path: Optional[Path] = None,
    ) -> None:
        self.upstream_name = upstream_name
        self.db_path = db_path
        self._lock = threading.Lock()
        # id (as str) -> (pair_id, request_ts, request_method)
        self._pending: dict[str, tuple[str, float, str]] = {}
        self._stats: dict[str, int] = {
            "frames_observed": 0,
            "frames_dropped_non_json": 0,
            "requests": 0,
            "notifications": 0,
            "responses": 0,
            "responses_paired": 0,
            "responses_orphan": 0,
            "errors": 0,
            "server_initiated": 0,
            "capture_failures": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(self, direction: str, line: bytes) -> None:
        """Process one wire-line in the given direction.

        ``line`` should be the raw bytes of a single JSON-RPC frame
        without the trailing newline. Empty lines are ignored.
        """
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return

        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            with self._lock:
                self._stats["frames_dropped_non_json"] += 1
            return

        if not isinstance(msg, dict):
            with self._lock:
                self._stats["frames_dropped_non_json"] += 1
            return

        with self._lock:
            self._stats["frames_observed"] += 1
            self._classify_and_write(direction, msg, text)

    @property
    def stats(self) -> dict[str, int]:
        """Snapshot of observation counters (threadsafe copy)."""
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal classification + write
    # ------------------------------------------------------------------

    def _classify_and_write(
        self,
        direction: str,
        msg: dict[str, Any],
        raw_text: str,
    ) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")
        # JSON-RPC 2.0 ids may be int / str / null. Normalise to str
        # for use as a dict key in the pending map.
        id_key = str(msg_id) if msg_id is not None else None

        if direction == "request" and method is not None:
            if id_key is None:
                # Notification (one-shot, no response expected)
                self._stats["notifications"] += 1
                self._write(
                    pair_id=new_pair_id(),
                    direction="request",
                    method=method,
                    raw_text=raw_text,
                    msg=msg,
                    status=None,
                    latency=None,
                    kind="notification",
                )
                return
            # Real request: stash for later pairing with response
            self._stats["requests"] += 1
            pair_id = new_pair_id()
            self._pending[id_key] = (pair_id, time.time(), method)
            self._write(
                pair_id=pair_id,
                direction="request",
                method=method,
                raw_text=raw_text,
                msg=msg,
                status=None,
                latency=None,
                kind="request",
            )
            return

        if direction == "response" and id_key is not None and method is None:
            # Real response (no method field per JSON-RPC 2.0)
            self._stats["responses"] += 1
            pending = self._pending.pop(id_key, None)
            if pending:
                pair_id, request_ts, request_method = pending
                latency_ms = (time.time() - request_ts) * 1000.0
                self._stats["responses_paired"] += 1
                pair_complete = True
            else:
                pair_id = new_pair_id()
                latency_ms = None
                request_method = ""
                self._stats["responses_orphan"] += 1
                pair_complete = False
            is_error = "error" in msg
            if is_error:
                self._stats["errors"] += 1
            self._write(
                pair_id=pair_id,
                direction="response",
                method=request_method,
                raw_text=raw_text,
                msg=msg,
                status=500 if is_error else 200,
                latency=latency_ms,
                kind="response_error" if is_error else "response",
                is_error=is_error,
            )
            # Trigger Tier 1 normalisation for paired responses. We mirror
            # the pattern in ``pce_mcp/server.py``: as soon as the pair is
            # complete, ``try_normalize_pair`` produces sessions+messages.
            # Failures here MUST NOT propagate — the wire forwarding is
            # the binding contract.
            if pair_complete:
                self._try_normalize(pair_id)
            return

        if direction == "response" and method is not None:
            # Server-initiated request from upstream (sampling, etc.)
            self._stats["server_initiated"] += 1
            self._write(
                pair_id=new_pair_id(),
                direction="response",
                method=method,
                raw_text=raw_text,
                msg=msg,
                status=None,
                latency=None,
                kind="server_initiated",
            )
            return

        # Anything else (rare: malformed envelope, unexpected direction)
        # is observed as an opaque frame so the row still exists for
        # diagnostic value.
        self._write(
            pair_id=new_pair_id(),
            direction=direction if direction in ("request", "response") else "request",
            method=method or "",
            raw_text=raw_text,
            msg=msg,
            status=None,
            latency=None,
            kind="unclassified",
        )

    def _write(
        self,
        *,
        pair_id: str,
        direction: str,
        method: str,
        raw_text: str,
        msg: dict[str, Any],
        status: Optional[int],
        latency: Optional[float],
        kind: str,
        is_error: bool = False,
    ) -> None:
        meta: dict[str, Any] = {
            "upstream": self.upstream_name,
            "kind": kind,
            "jsonrpc_method": method or None,
            "jsonrpc_id": msg.get("id"),
        }
        if is_error:
            err = msg.get("error")
            if isinstance(err, dict):
                meta["jsonrpc_error_code"] = err.get("code")
                meta["jsonrpc_error_message"] = err.get("message")

        try:
            insert_capture(
                direction=direction,
                pair_id=pair_id,
                host="stdio",
                path=method or "",
                method="JSONRPC",
                provider=f"mcp:{self.upstream_name}",
                status_code=status,
                latency_ms=latency,
                body_text_or_json=raw_text,
                body_format="json",
                meta_json=json.dumps(meta, ensure_ascii=False),
                source_id=SOURCE_MCP_PROXY,
                source="mcp_proxy",
                agent_name="pce-mcp-proxy",
                db_path=self.db_path,
            )
        except Exception as exc:  # pragma: no cover — defensive guard
            self._stats["capture_failures"] += 1
            logger.warning(
                "mcp_proxy.capture_failed",
                extra={
                    "event": "mcp_proxy.capture_failed",
                    "pce_fields": {
                        "kind": kind,
                        "method": method,
                        "error": repr(exc),
                    },
                },
            )

    def _try_normalize(self, pair_id: str) -> None:
        """Best-effort Tier 1 normalisation. Errors are swallowed."""
        try:
            try_normalize_pair(
                pair_id,
                source_id=SOURCE_MCP_PROXY,
                created_via="mcp_proxy",
                db_path=self.db_path,
            )
        except Exception as exc:  # pragma: no cover — defensive guard
            logger.debug(
                "mcp_proxy.normalize_failed",
                extra={
                    "event": "mcp_proxy.normalize_failed",
                    "pce_fields": {"error": repr(exc), "pair_id": pair_id},
                },
            )
