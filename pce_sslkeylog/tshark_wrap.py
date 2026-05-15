# SPDX-License-Identifier: Apache-2.0
"""tshark subprocess management for the A2 SSLKEYLOGFILE capture daemon.

This module owns the live capture loop:

1. Locate tshark.exe on the system.
2. Spawn it with proper flags (display filter, output format, keylog).
3. Stream its NDJSON stdout line-by-line, hand each line to the parser.
4. Pair request + response events into ``raw_captures`` rows.
5. Restart tshark on crash with exponential backoff.

The actual TLS decryption is done inside tshark via the keylog file —
we just receive the decrypted HTTP semantics on stdout.

Boundary contract (mirrors pce_proxy/__init__.py):
- ALL DB writes / pipeline normalization happen via ``pce_core.db``
  and ``pce_core.normalizer.pipeline`` — no per-leg parsing duplication.
- ``TsharkRunner`` does NOT swallow stdout: stdout MUST be drained on
  another thread, otherwise tshark blocks on its own pipe.
- Any DB / config failure is downgraded to a logged warning, never a
  crash — keeping a long-running daemon alive is more important than
  a perfect capture rate.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger("pce.sslkeylog.tshark")


_TSHARK_CANDIDATE_PATHS: tuple[str, ...] = (
    # Standard install path on Windows
    r"C:\Program Files\Wireshark\tshark.exe",
    r"C:\Program Files (x86)\Wireshark\tshark.exe",
    # POSIX
    "/usr/bin/tshark",
    "/usr/local/bin/tshark",
    "/opt/homebrew/bin/tshark",
)


def find_tshark() -> Optional[Path]:
    """Locate tshark on this host. Returns absolute Path or None."""
    # First try PATH lookup
    found = shutil.which("tshark")
    if found:
        return Path(found)
    # Then the well-known install dirs
    for candidate in _TSHARK_CANDIDATE_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    return None


def tshark_version(tshark: Path) -> Optional[str]:
    """Return tshark's version string, or None on failure."""
    try:
        out = subprocess.run(
            [str(tshark), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        first_line = (out.stdout or "").splitlines()
        if first_line:
            return first_line[0].strip()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("tshark --version failed: %s", exc)
    return None


@dataclass
class TsharkConfig:
    """Static config for one TsharkRunner.

    Attributes
    ----------
    tshark_path
        Absolute path to tshark.exe. Use ``find_tshark()`` to locate.
    keylog_file
        Path to the SSLKEYLOGFILE that Chromium / Electron apps write
        TLS session keys to. tshark uses this for TLS decryption via
        ``-o tls.keylog_file:<path>``.
    interface
        Network interface name (or ``any`` on Linux, or a device GUID
        on Windows from ``tshark -D``). Default ``any``.
    allowed_hosts
        DNS host names to keep. Used both in capture filter ``-f`` and
        post-filter in Python. Use ``pce_core.config.ALLOWED_HOSTS``
        when integrating.
    """

    tshark_path: Path
    keylog_file: Path
    interface: str = "any"
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    def build_argv(self) -> list[str]:
        """Render the tshark command line for this config."""
        argv: list[str] = [str(self.tshark_path)]
        # Interface
        argv.extend(["-i", self.interface])
        # Display filter: only TLS-wrapped HTTP/2 or HTTP/1, drop ACK-only frames
        # tshark display filter -Y; capture filter (-f) is BPF and can't see
        # HTTP layer, so we only use -f for coarse host narrowing.
        if self.allowed_hosts:
            # BPF host filter (any of the allowed hosts; resolved at start)
            host_clause = " or ".join(f"host {h}" for h in sorted(self.allowed_hosts))
            argv.extend(["-f", host_clause])
        # TLS keylog file
        argv.extend(["-o", f"tls.keylog_file:{self.keylog_file}"])
        # Use -Y for display filter to keep only http and http2
        argv.extend(["-Y", "http or http2"])
        # Elastic-style NDJSON output for streamable parsing
        argv.extend(["-T", "ek"])
        # Force packet capture not to buffer (line-buffered stdout helps
        # us see events live; -l = line-buffered on POSIX, on Windows
        # tshark already line-buffers when stdout is a pipe.)
        argv.append("-l")
        return argv


class TsharkRunner:
    """Owns a tshark subprocess + the stdout drain thread.

    Lifecycle:
        runner = TsharkRunner(config, on_line=callback)
        runner.start()
        ...                # subprocess running; callbacks fired on bg thread
        runner.stop()      # graceful SIGINT, waits for drain, then SIGKILL

    The ``on_line`` callback receives one line (without trailing newline)
    of tshark output. It is invoked from a worker thread, so the
    callback MUST be thread-safe (typically pushes to a queue).
    """

    def __init__(
        self,
        config: TsharkConfig,
        *,
        on_line: Callable[[str], None],
        restart_max: int = 5,
        restart_backoff_s: float = 2.0,
    ) -> None:
        self.config = config
        self._on_line = on_line
        self._restart_max = restart_max
        self._restart_backoff_s = restart_backoff_s
        self._proc: Optional[subprocess.Popen[str]] = None
        self._stop = threading.Event()
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._restart_count = 0
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def start(self) -> None:
        """Spawn tshark and start the drain threads. Idempotent: returns
        immediately if already running."""
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._spawn_unlocked()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop tshark + drain threads. Safe to call multiple times."""
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                # SIGINT (Ctrl-C) is the polite request to tshark on POSIX;
                # on Windows there's no direct equivalent so we use
                # CTRL_BREAK_EVENT then terminate().
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("tshark didn't exit on signal, killing")
                proc.kill()
                proc.wait(timeout=2.0)
        # Drain threads will exit when proc.stdout closes
        for t in (self._stdout_thread, self._stderr_thread):
            if t is not None:
                t.join(timeout=2.0)
        with self._lock:
            self._proc = None
            self._stdout_thread = None
            self._stderr_thread = None

    # --- internal -----------------------------------------------------------

    def _spawn_unlocked(self) -> None:
        argv = self.config.build_argv()
        logger.info("tshark spawn: %s", " ".join(argv))
        # On Windows we want CREATE_NEW_PROCESS_GROUP so we can send
        # CTRL_BREAK to it without killing our own process.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self._proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
            creationflags=creationflags,
            env={
                **os.environ,
                "SSLKEYLOGFILE": str(self.config.keylog_file),
            },
        )
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, name="pce-sslkeylog-stdout", daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="pce-sslkeylog-stderr", daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _drain_stdout(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                # Strip trailing newline; deliver to callback
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    self._on_line(line)
                except Exception:
                    logger.exception("on_line callback raised; continuing")
        except Exception:
            logger.exception("stdout drain failed (non-fatal)")
        # Check exit code; restart if necessary
        rc = proc.poll() if proc else None
        if not self._stop.is_set() and rc is not None and rc != 0:
            self._maybe_restart(rc)

    def _drain_stderr(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip("\r\n")
            if line:
                logger.debug("tshark stderr: %s", line)

    def _maybe_restart(self, rc: int) -> None:
        if self._restart_count >= self._restart_max:
            logger.error(
                "tshark exited rc=%d; max restarts (%d) reached, giving up",
                rc, self._restart_max,
            )
            return
        self._restart_count += 1
        backoff = self._restart_backoff_s * self._restart_count
        logger.warning(
            "tshark exited rc=%d; restart %d/%d in %.1fs",
            rc, self._restart_count, self._restart_max, backoff,
        )
        # Sleep with stop awareness so a stop() during backoff is honoured
        if self._stop.wait(backoff):
            return
        with self._lock:
            self._spawn_unlocked()
