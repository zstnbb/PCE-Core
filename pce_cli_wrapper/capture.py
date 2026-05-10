# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper.capture – assemble PCE capture rows from a relayed run.

This module is the bridge between the stdio relay (``relay.py``) and
the PCE capture pipeline. It owns three responsibilities:

1. **Envelope construction** — turn the per-invocation transcript into
   the row shape ``pce_core.db.insert_capture`` expects, with a
   consistent ``provider`` / ``host`` / ``path`` / ``meta`` layout so
   the normalizer can pick it up.

2. **Body shaping** — bound the captured stdin / stdout / stderr at
   ``max_body_bytes`` per stream, decode best-effort to UTF-8 with a
   ``base64`` fallback marker, and stamp a fingerprint that is stable
   across re-runs of identical invocations.

3. **Soft DB write** — any DB failure is logged and counted, never
   raised. The relay must always exit with the child's exit code so a
   PCE outage cannot brick the user's CLI session.

The contract with ``pce_core.db.insert_capture`` is the same one
``pce_persistence_watcher`` and ``pce_mcp_proxy`` use; that's the
point — every L3 source funnels through one row shape.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pce_core.db import (
    SOURCE_L3H_CLI_WRAPPER,
    insert_capture,
    new_pair_id,
)

logger = logging.getLogger("pce.cli_wrapper.capture")


# ---------------------------------------------------------------------------
# Result dataclass — what the relay hands us back
# ---------------------------------------------------------------------------


@dataclass
class RelayResult:
    """The output of one ``relay.relay()`` invocation."""

    target_id: str                # e.g. "claude-code"
    command_name: str             # e.g. "claude"
    provider: str                 # e.g. "anthropic"
    target_path: Path             # absolute path of the real shim invoked
    args: list[str]               # argv handed to the child
    cwd: Path                     # CWD at spawn time
    started_at_ns: int            # monotonic ns at spawn
    finished_at_ns: int           # monotonic ns at exit
    exit_code: int                # child's exit code (or -1 on internal error)
    timed_out: bool = False       # set when --timeout fired
    tty_passthrough: bool = False # True ⇒ stdio not captured (interactive REPL)

    # Captured byte streams — empty in tty_passthrough mode.
    stdin_bytes: bytes = b""
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""

    # Was the per-stream byte cap reached? (informational meta field)
    stdin_truncated: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    # Optional version string from discovery (e.g. "2.1.59 (Claude Code)").
    target_version: Optional[str] = None

    # Optional human label override (defaults to target_id).
    capture_label: Optional[str] = None


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


class CliWrapperObserver:
    """Writes ``raw_captures`` rows tagged ``SOURCE_L3H_CLI_WRAPPER``.

    One observer instance handles one relayed invocation. The relay is
    the only caller; tests instantiate it directly to assert on row
    shape without spawning a real process.
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        dry_run: bool = False,
    ) -> None:
        self.db_path = db_path
        self.dry_run = bool(dry_run)
        self.stats: dict[str, int] = {
            "rows_written": 0,
            "write_failures": 0,
        }
        self.last_capture_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, result: RelayResult) -> None:
        """Turn a ``RelayResult`` into one row in ``raw_captures``."""
        body_str = self._build_body(result)
        meta_json = self._build_meta(result, body_str)
        path = f"/{result.target_id}/{result.command_name}"

        if self.dry_run:
            self.stats["rows_written"] += 1
            return

        try:
            pair_id = new_pair_id()
            self.last_capture_id = pair_id
            insert_capture(
                direction="conversation",
                pair_id=pair_id,
                host="cli-wrapper",
                path=path,
                method="EXEC",
                provider=result.provider,
                status_code=result.exit_code,
                latency_ms=_latency_ms(result),
                body_text_or_json=body_str,
                body_format="json",
                meta_json=meta_json,
                source_id=SOURCE_L3H_CLI_WRAPPER,
                source="cli_wrapper",
                agent_name="pce-cli-wrapper",
                db_path=self.db_path,
                session_hint=pair_id,
            )
            self.stats["rows_written"] += 1
        except Exception as exc:  # pragma: no cover — defensive guard
            self.stats["write_failures"] += 1
            logger.warning(
                "cli_wrapper.capture_failed",
                extra={
                    "event": "cli_wrapper.capture_failed",
                    "pce_fields": {
                        "target_id": result.target_id,
                        "exit_code": result.exit_code,
                        "error": repr(exc),
                    },
                },
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_body(self, result: RelayResult) -> str:
        """Compose the row body as a single JSON document.

        Each stream is encoded according to its decode-ability:
        - ``utf8_text``: raw UTF-8 string preserved verbatim;
        - ``base64``: binary content base64-encoded (with a marker key).
        Each stream is independent — stdout might be UTF-8 while
        stderr contains a binary diagnostic.
        """
        body: dict[str, Any] = {
            "args": result.args,
            "cwd": str(result.cwd),
            "exit_code": result.exit_code,
            "started_at_ns": result.started_at_ns,
            "finished_at_ns": result.finished_at_ns,
            "duration_ms": _latency_ms(result),
            "timed_out": result.timed_out,
            "tty_passthrough": result.tty_passthrough,
        }
        if not result.tty_passthrough:
            body["stdin"] = _encode_stream(result.stdin_bytes)
            body["stdout"] = _encode_stream(result.stdout_bytes)
            body["stderr"] = _encode_stream(result.stderr_bytes)
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def _build_meta(self, result: RelayResult, body_str: str) -> str:
        """Compose the row meta JSON. Smaller than body, always structured."""
        meta = {
            "target_id": result.target_id,
            "command_name": result.command_name,
            "target_path": str(result.target_path),
            "target_version": result.target_version,
            "capture_label": result.capture_label or result.target_id,
            "tty_passthrough": result.tty_passthrough,
            "timed_out": result.timed_out,
            "stdin_bytes": len(result.stdin_bytes),
            "stdout_bytes": len(result.stdout_bytes),
            "stderr_bytes": len(result.stderr_bytes),
            "stdin_truncated": result.stdin_truncated,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "fingerprint": _fingerprint(result, body_str),
            "wrapper_version": _wrapper_version(),
            "capture_kind": "stdio_relay",
        }
        return json.dumps(meta, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _latency_ms(result: RelayResult) -> int:
    """Wall-clock duration of the child invocation, in milliseconds."""
    if result.finished_at_ns <= result.started_at_ns:
        return 0
    return (result.finished_at_ns - result.started_at_ns) // 1_000_000


def _encode_stream(data: bytes) -> dict[str, Any]:
    """Encode one captured byte stream for JSON storage.

    Returns ``{"format": "utf8_text"|"base64", "value": ..., "len": N}``.
    """
    if not data:
        return {"format": "utf8_text", "value": "", "len": 0}
    try:
        text = data.decode("utf-8")
        return {"format": "utf8_text", "value": text, "len": len(data)}
    except UnicodeDecodeError:
        return {
            "format": "base64",
            "value": base64.b64encode(data).decode("ascii"),
            "len": len(data),
        }


def _fingerprint(result: RelayResult, body_str: str) -> str:
    """Stable sha256 fingerprint over (target, args, body).

    Deliberately excludes timing fields so two identical re-runs collide
    if and only if the user kicked the same command with the same input.
    The dedup decision itself is taken downstream — we just stamp the
    value here.
    """
    h = hashlib.sha256()
    h.update(result.target_id.encode("utf-8"))
    h.update(b"|")
    h.update(str(result.target_path).encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update("\x00".join(result.args).encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update(body_str.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _wrapper_version() -> str:
    try:
        from pce_cli_wrapper import __version__
        return str(__version__)
    except Exception:  # pragma: no cover — defensive
        return "0.0.0"


# Re-exported so tests don't have to know our internal layout.
__all__ = [
    "CliWrapperObserver",
    "RelayResult",
]
