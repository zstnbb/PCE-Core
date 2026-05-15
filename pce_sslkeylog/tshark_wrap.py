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

# Drive letters to scan for portable/custom Wireshark installs on Windows.
# We check both ``\Wireshark\tshark.exe`` (installer default capitalization)
# and ``\wireshark\tshark.exe`` (portable / lowercase) under each drive root.
_WIN_PORTABLE_DRIVES: tuple[str, ...] = ("C", "D", "E", "F", "G", "H")
_WIN_PORTABLE_SUBDIRS: tuple[str, ...] = (
    r"Wireshark\tshark.exe",
    r"wireshark\tshark.exe",
    r"Tools\Wireshark\tshark.exe",
    r"PortableApps\WiresharkPortable\App\Wireshark\tshark.exe",
)


def find_tshark() -> Optional[Path]:
    """Locate tshark on this host. Returns absolute Path or None.

    Resolution order:
    1. ``PCE_TSHARK_PATH`` env var (operator override).
    2. ``PATH`` (i.e. ``shutil.which``).
    3. Standard install paths (Program Files on Windows, /usr/bin on POSIX).
    4. Portable / non-default Windows locations (``F:\\wireshark\\…``, etc.).
    """
    # 1. Explicit override
    override = os.environ.get("PCE_TSHARK_PATH")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        logger.warning("PCE_TSHARK_PATH=%s but file not found; ignoring", override)
    # 2. PATH
    found = shutil.which("tshark")
    if found:
        return Path(found)
    # 3. Standard install dirs
    for candidate in _TSHARK_CANDIDATE_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    # 4. Portable installs (Windows only)
    if sys.platform == "win32":
        for drive in _WIN_PORTABLE_DRIVES:
            for sub in _WIN_PORTABLE_SUBDIRS:
                p = Path(f"{drive}:\\{sub}")
                if p.is_file():
                    return p
    return None


def _run_text(argv: list[str], timeout: float = 10.0) -> Optional[str]:
    """Run a subprocess and decode stdout as UTF-8 (replace errors).

    Crucial on non-English Windows where the system code page defaults to
    something like CP936/GBK — tshark itself emits UTF-8 for non-ASCII
    interface aliases like ``以太网``, but Python's ``text=True`` mode
    decodes via the system default and explodes with UnicodeDecodeError.
    """
    try:
        out = subprocess.run(
            argv, capture_output=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("subprocess failed: %s: %s", argv[0], exc)
        return None
    raw = out.stdout or b""
    # Try UTF-8 first; if it fails, fall back to system default.
    for enc in ("utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def tshark_version(tshark: Path) -> Optional[str]:
    """Return tshark's version string, or None on failure."""
    out_text = _run_text([str(tshark), "--version"])
    if out_text is None:
        return None
    first_line = out_text.splitlines()
    return first_line[0].strip() if first_line else None


def list_tshark_interfaces(tshark: Path) -> list[str]:
    """Return the interface aliases that ``tshark -D`` lists, e.g.
    ``["WLAN", "Loopback Pseudo-Interface 1", "以太网", ...]``.

    Used by :func:`detect_capture_interfaces` for auto-mode selection.
    Returns an empty list on failure (callers should fall back to "any").
    """
    out_text = _run_text([str(tshark), "-D"])
    if out_text is None:
        return []
    # Each line looks like "  3. \Device\NPF_{GUID} (WLAN)" — extract the
    # paren'd alias.
    aliases: list[str] = []
    for raw in out_text.splitlines():
        if "(" in raw and raw.rstrip().endswith(")"):
            alias = raw.rsplit("(", 1)[-1].rstrip(")").strip()
            if alias:
                aliases.append(alias)
    return aliases


def _windows_default_route_iface() -> Optional[str]:
    """Use PowerShell ``Get-NetRoute`` to find the alias of the interface
    carrying the IPv4 default route. Returns None on failure."""
    if sys.platform != "win32":
        return None
    out_text = _run_text(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
         "Sort-Object RouteMetric | "
         "Select-Object -First 1 -ExpandProperty InterfaceAlias"],
    )
    if out_text is None:
        return None
    alias = out_text.strip()
    return alias or None


def detect_capture_interfaces(tshark: Path) -> list[str]:
    """Auto-select a reasonable interface set for the current host.

    Windows:
      - Includes ``Adapter for loopback traffic capture`` (catches every
        Chromium app that honors the system proxy at 127.0.0.1:7890)
      - Plus the default-route interface (WLAN / 以太网 / Ethernet)
        which catches apps that bypass the system proxy (Cursor, codex
        CLI, gemini CLI, MCP clients, Node undici, etc.)
    POSIX:
      - Just ``any`` (Linux's pseudo-interface for all-interface capture).
    """
    if sys.platform != "win32":
        return ["any"]
    aliases = list_tshark_interfaces(tshark)
    chosen: list[str] = []
    # Loopback first
    for a in aliases:
        if "loopback" in a.lower():
            chosen.append(a)
            break
    # Then the default-route interface
    default_alias = _windows_default_route_iface()
    if default_alias and default_alias not in chosen:
        chosen.append(default_alias)
    # Fallback if both detections failed
    if not chosen:
        # Take everything except virtual/management adapters
        for a in aliases:
            la = a.lower()
            if any(skip in la for skip in (
                "vethernet", "etwdump", "本地连接",
            )):
                continue
            chosen.append(a)
    return chosen


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
    interfaces
        List of network interface aliases to capture from. On Windows,
        passing multiple gives tshark ``-i WLAN -i Loopback`` which
        merges traffic from both. Use :func:`detect_capture_interfaces`
        for the default Windows pair (loopback + default-route iface).
        On POSIX, ``["any"]`` is the usual choice.
    allowed_hosts
        DNS host names to keep. Used both in capture filter ``-f`` and
        post-filter in Python. Use ``pce_core.config.ALLOWED_HOSTS``
        when integrating.
    """

    tshark_path: Path
    keylog_file: Path
    interfaces: list[str] = field(default_factory=lambda: ["any"])
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    # Backward-compat shim: code (and tests) written against the
    # single-iface API can still pass ``interface=...`` as a kw arg.
    def __init__(
        self,
        tshark_path: Path,
        keylog_file: Path,
        interfaces: Optional[list[str]] = None,
        allowed_hosts: Optional[frozenset[str]] = None,
        interface: Optional[str] = None,
    ) -> None:
        self.tshark_path = tshark_path
        self.keylog_file = keylog_file
        if interfaces is not None:
            self.interfaces = list(interfaces)
        elif interface is not None:
            self.interfaces = [interface]
        else:
            self.interfaces = ["any"]
        self.allowed_hosts = allowed_hosts or frozenset()

    @property
    def interface(self) -> str:
        """Backward-compat: first interface (for older log strings)."""
        return self.interfaces[0] if self.interfaces else "any"

    def build_argv(self) -> list[str]:
        """Render the tshark command line for this config."""
        argv: list[str] = [str(self.tshark_path)]
        # Interface(s) — tshark accepts multiple ``-i`` flags and merges
        # captures from all listed interfaces into one output stream.
        for iface in self.interfaces:
            argv.extend(["-i", iface])
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
