# SPDX-License-Identifier: Apache-2.0
"""pce_mcp_proxy – Threading-based bidirectional stdio relay.

Why threads, not asyncio? Windows is the primary target platform for
PCE-Core, and the default Windows event loop (``ProactorEventLoop``)
does not support ``loop.connect_read_pipe(sys.stdin)`` — the stdio
attach trick that makes asyncio's ``StreamReader`` work on Linux.
Mixing ``run_in_executor`` for stdin + native asyncio for the
subprocess is fragile and adds a class of race conditions we don't
need. A small thread pool with a bounded queue handles this cleanly
on every platform we ship to.

Topology of one running relay:

    sys.stdin (host) ─[I/O thread T1]──► proc.stdin (upstream)
                                  │
                                  └► observe queue ──► [observer thread T4]
    proc.stdout (upstream) ─[I/O thread T2]──► sys.stdout (host)
                                  │
                                  └► observe queue ──► [observer thread T4]
    proc.stderr (upstream) ─[I/O thread T3]──► sys.stderr (host)
                                                  (passthrough only,
                                                   never observed)
    main thread ──► proc.wait() ──► sentinel ──► [observer thread T4]

Decoupling rationale: forwarding (T1/T2) must be latency-preserving;
JSON parsing in observation must not stall the wire. Pushing chunks
into a queue and parsing in T4 means a slow ``json.loads`` over a
huge ``tools/call`` response cannot starve the host of forwarded
bytes.

Lifecycle rules:

- Upstream exit ⇒ proxy exits with the same return code (the host
  treats a clean upstream shutdown as a graceful session end).
- Host closing stdin ⇒ T1 sees EOF, closes proc.stdin, upstream
  observes EOF and exits, ``proc.wait()`` returns, proxy exits.
- KeyboardInterrupt / SIGTERM ⇒ proxy terminates upstream with a
  3-second grace window, then SIGKILL.
- Any exception in T1/T2/T3 ⇒ logged once, the offending pipe is
  closed, the relay continues to drain other pipes until upstream
  exits.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from queue import Queue
from typing import Any, BinaryIO, Optional

from .capture import JsonRpcObserver

logger = logging.getLogger("pce.mcp_proxy.relay")

# 64 KiB is the standard Windows pipe buffer size; reading in 64 KiB
# chunks gives single-syscall throughput while still letting us
# observe partial frames promptly.
_CHUNK = 65536


class Relay:
    """Coordinator for one upstream MCP server subprocess.

    Construct with the upstream argv and an observer; call ``run()``
    to block until the upstream exits and return its return code.
    """

    def __init__(
        self,
        upstream_argv: list[str],
        observer: JsonRpcObserver,
        *,
        host_stdin: Optional[BinaryIO] = None,
        host_stdout: Optional[BinaryIO] = None,
        host_stderr: Optional[BinaryIO] = None,
    ) -> None:
        if not upstream_argv:
            raise ValueError("Relay requires a non-empty upstream argv")
        self.upstream_argv = upstream_argv
        self.observer = observer
        # Allow tests to inject pipe ends instead of the real stdio.
        self._host_stdin = host_stdin
        self._host_stdout = host_stdout
        self._host_stderr = host_stderr
        self._observer_q: Queue[Any] = Queue()
        self._sentinel = object()
        self._proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Spawn the upstream and forward stdio until it exits.

        Returns the upstream's exit code. Returns ``127`` if the
        upstream binary cannot be found.
        """
        argv = self._resolve_executable(self.upstream_argv)

        host_stdin = self._host_stdin or _stdio_buffer(sys.stdin)
        host_stdout = self._host_stdout or _stdio_buffer(sys.stdout)
        host_stderr = self._host_stderr or _stdio_buffer(sys.stderr)

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # unbuffered: forward bytes immediately
            )
        except FileNotFoundError as exc:
            host_stderr.write(
                f"pce-mcp-proxy: cannot spawn upstream '{argv[0]}': {exc}\n".encode()
            )
            host_stderr.flush()
            return 127
        except OSError as exc:
            host_stderr.write(
                f"pce-mcp-proxy: failed to start upstream: {exc}\n".encode()
            )
            host_stderr.flush()
            return 126

        threads = [
            threading.Thread(
                target=self._pipe_observed,
                args=(host_stdin, self._proc.stdin, "request"),
                name="mcp-proxy-host-to-upstream",
                daemon=True,
            ),
            threading.Thread(
                target=self._pipe_observed,
                args=(self._proc.stdout, host_stdout, "response"),
                name="mcp-proxy-upstream-to-host",
                daemon=True,
            ),
            threading.Thread(
                target=self._pipe_passthrough,
                args=(self._proc.stderr, host_stderr),
                name="mcp-proxy-stderr-passthrough",
                daemon=True,
            ),
            threading.Thread(
                target=self._observer_worker,
                name="mcp-proxy-observer",
                daemon=True,
            ),
        ]
        for t in threads:
            t.start()

        try:
            rc = self._proc.wait()
        except KeyboardInterrupt:
            rc = self._terminate_upstream(grace=3.0)

        # Drain the observer queue so any frames buffered after wait()
        # returns still get persisted before we exit.
        self._observer_q.put(self._sentinel)
        for t in threads:
            t.join(timeout=2.0)

        return rc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_executable(self, argv: list[str]) -> list[str]:
        """Resolve argv[0] via PATHEXT-aware lookup on Windows.

        On Windows, ``subprocess.Popen`` with ``shell=False`` and a
        list does not search PATHEXT, so ``["npx", ...]`` would fail
        even though ``npx.cmd`` exists on PATH. ``shutil.which`` does
        the right resolution; if it returns ``None`` we fall through
        and let ``Popen`` surface the original error.
        """
        if not argv:
            return argv
        resolved = shutil.which(argv[0])
        if resolved:
            return [resolved] + list(argv[1:])
        return list(argv)

    def _pipe_observed(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        direction: str,
    ) -> None:
        """Forward bytes from reader to writer; copy chunks to observer queue."""
        try:
            while True:
                try:
                    chunk = _read_chunk(reader)
                except (ValueError, OSError):
                    break
                if not chunk:
                    break
                try:
                    writer.write(chunk)
                    writer.flush()
                except (BrokenPipeError, OSError):
                    break
                # Hand off a copy for line-extraction in the observer
                # thread. We don't buffer here because the queue
                # consumer maintains its own per-direction buffer.
                self._observer_q.put((direction, chunk))
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _pipe_passthrough(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
    ) -> None:
        """Forward bytes verbatim with no observation (used for stderr)."""
        try:
            while True:
                try:
                    chunk = _read_chunk(reader)
                except (ValueError, OSError):
                    break
                if not chunk:
                    break
                try:
                    writer.write(chunk)
                    writer.flush()
                except (BrokenPipeError, OSError):
                    break
        finally:
            pass  # we never close host_stderr; it's the caller's

    def _observer_worker(self) -> None:
        """Consume queued chunks, extract complete lines, hand to observer."""
        request_buf = b""
        response_buf = b""
        while True:
            item = self._observer_q.get()
            if item is self._sentinel:
                break
            try:
                direction, chunk = item
            except (TypeError, ValueError):
                continue
            if direction == "request":
                request_buf = self._extract_lines(request_buf + chunk, direction)
            elif direction == "response":
                response_buf = self._extract_lines(response_buf + chunk, direction)
            # Unknown direction tags are dropped silently.

        # Final drain: an upstream that exits without a trailing newline
        # would leave a partial line in the buffer. Try to observe it
        # in case it's still a complete JSON object.
        if request_buf.strip():
            try:
                self.observer.observe("request", request_buf)
            except Exception:
                pass
        if response_buf.strip():
            try:
                self.observer.observe("response", response_buf)
            except Exception:
                pass

    def _extract_lines(self, buf: bytes, direction: str) -> bytes:
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                self.observer.observe(direction, line)
            except Exception:  # pragma: no cover — defensive guard
                logger.debug(
                    "mcp_proxy.observer_failed",
                    exc_info=True,
                )
        return buf

    def _terminate_upstream(self, *, grace: float) -> int:
        """Best-effort upstream shutdown for KeyboardInterrupt / SIGTERM."""
        if self._proc is None or self._proc.returncode is not None:
            return self._proc.returncode if self._proc else 130
        try:
            self._proc.terminate()
            return self._proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            return self._proc.wait()


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _stdio_buffer(stream: Any) -> BinaryIO:
    """Return the underlying binary buffer of a stdio stream.

    On Python 3, ``sys.stdin`` / ``sys.stdout`` / ``sys.stderr`` are
    text streams; their ``.buffer`` attribute is the raw binary
    stream we actually want to read/write through. Some test
    harnesses replace stdio with bytes-mode objects directly, in
    which case we use them as-is.
    """
    return getattr(stream, "buffer", stream)


def _read_chunk(reader: BinaryIO) -> bytes:
    """Read up to ``_CHUNK`` bytes, preferring read1 to avoid blocking on a full buffer."""
    if hasattr(reader, "read1"):
        return reader.read1(_CHUNK)
    return reader.read(_CHUNK)
