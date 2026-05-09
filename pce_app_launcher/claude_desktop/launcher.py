# SPDX-License-Identifier: Apache-2.0
"""Spawn Claude Desktop with ``--remote-debugging-port`` and poll until
the CDP endpoint is ready.

Pure stdlib + ``urllib`` — no Playwright dep at this layer (the
:mod:`pce_app_launcher.claude_desktop.capture_bridge` module pulls in
Playwright lazily when the user actually attaches).

Usage::

    from pce_app_launcher.claude_desktop import (
        detect_claude_desktop, launch_claude_desktop,
    )

    install = detect_claude_desktop()
    if install is None:
        raise RuntimeError("Claude Desktop not installed")
    handle = launch_claude_desktop(install)
    print(handle.cdp_endpoint)  # e.g. "http://127.0.0.1:9222"

    # ... attach a CaptureBridge later ...
    handle.terminate()  # kill on exit
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

from .detector import ClaudeDesktopInstall

logger = logging.getLogger("pce.app_launcher.claude_desktop.launcher")


DEFAULT_DEBUG_PORT = 9222
DEFAULT_READY_TIMEOUT = 30.0  # seconds
DEFAULT_POLL_INTERVAL = 0.5


@dataclass
class LauncherHandle:
    """Result of a :func:`launch_claude_desktop` call.

    Holds the live ``subprocess.Popen`` + the resolved CDP endpoint
    URL. Callers should call :meth:`terminate` (or use as context
    manager) when done so the spawned Claude Desktop child process is
    cleaned up.
    """

    process: subprocess.Popen
    cdp_endpoint: str
    debug_port: int
    install: ClaudeDesktopInstall

    def is_running(self) -> bool:
        return self.process.poll() is None

    def terminate(self, *, timeout: float = 5.0) -> None:
        if not self.is_running():
            return
        try:
            self.process.terminate()
        except Exception:
            return
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except Exception:
                pass

    def __enter__(self) -> "LauncherHandle":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.terminate()


# ---------------------------------------------------------------------------
# Public launcher entry point
# ---------------------------------------------------------------------------


def launch_claude_desktop(
    install: ClaudeDesktopInstall,
    *,
    debug_port: int = DEFAULT_DEBUG_PORT,
    auto_pick_port: bool = True,
    extra_args: Optional[list[str]] = None,
    extra_env: Optional[dict[str, str]] = None,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> LauncherHandle:
    """Spawn Claude Desktop with CDP debugging enabled and wait for it.

    Args:
        install: Output of :func:`pce_app_launcher.claude_desktop.detector.detect_claude_desktop`.
        debug_port: Preferred TCP port for the CDP endpoint. Defaults
            to 9222 (the ad-hoc Chromium convention).
        auto_pick_port: If ``True`` and ``debug_port`` is in use, pick
            another free ephemeral port automatically. Multi-instance
            Claude Desktop is the typical case (ADR-016 §6 open question
            on multi-window orchestration).
        extra_args: Additional command-line args appended after the
            ``--remote-debugging-port=<port>`` flag.
        extra_env: Extra environment variables (merged onto
            ``os.environ``).
        ready_timeout: Seconds to wait for the CDP endpoint to respond
            before giving up.
        poll_interval: Sleep between readiness probes.

    Returns:
        :class:`LauncherHandle` describing the running process + CDP
        endpoint URL.

    Raises:
        TimeoutError: when Claude Desktop launches but its CDP endpoint
            never becomes reachable within ``ready_timeout``.
        FileNotFoundError: when ``install.exe_path`` doesn't exist.
        RuntimeError: when the process exits during the readiness wait.
    """
    if not install.exe_path.is_file():
        raise FileNotFoundError(
            f"Claude Desktop executable missing at {install.exe_path}"
        )

    port = _resolve_port(debug_port, auto_pick_port=auto_pick_port)

    cmd: list[str] = [str(install.exe_path), f"--remote-debugging-port={port}"]
    if extra_args:
        cmd.extend(extra_args)

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)

    logger.info(
        "launching Claude Desktop",
        extra={"event": "launcher.spawn", "pce_fields": {
            "exe_path": str(install.exe_path),
            "debug_port": port,
            "extra_args": list(extra_args or []),
        }},
    )

    process = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(install.install_root) if install.install_root else None,
        # Windows: detach console so PCE's own console isn't shared.
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt"
        else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cdp_endpoint = f"http://127.0.0.1:{port}"

    deadline = time.time() + ready_timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        rc = process.poll()
        if rc is not None:
            raise RuntimeError(
                f"Claude Desktop exited prematurely (returncode={rc}) "
                f"before its CDP endpoint became ready"
            )
        if _probe_cdp_ready(cdp_endpoint):
            logger.info(
                "Claude Desktop CDP endpoint ready",
                extra={"event": "launcher.ready", "pce_fields": {
                    "cdp_endpoint": cdp_endpoint, "debug_port": port,
                }},
            )
            return LauncherHandle(
                process=process,
                cdp_endpoint=cdp_endpoint,
                debug_port=port,
                install=install,
            )
        time.sleep(poll_interval)

    # Timed out — clean up the orphaned process before raising.
    try:
        process.terminate()
    except Exception:
        pass
    raise TimeoutError(
        f"Claude Desktop did not expose CDP endpoint {cdp_endpoint} "
        f"within {ready_timeout}s. Last error: {last_error!r}"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _probe_cdp_ready(endpoint: str, *, timeout: float = 1.5) -> bool:
    """Return True iff GET ``/json/version`` returns valid JSON.

    Chromium exposes ``/json/version`` immediately after binding the
    debugging port; it's the canonical readiness signal documented in
    https://chromedevtools.github.io/devtools-protocol/.
    """
    url = endpoint.rstrip("/") + "/json/version"
    try:
        with closing(urlopen(url, timeout=timeout)) as resp:
            if resp.status != 200:
                return False
            payload = resp.read(8192)
            data = json.loads(payload.decode("utf-8", errors="replace"))
            return isinstance(data, dict) and "Browser" in data
    except (URLError, OSError, json.JSONDecodeError, UnicodeError):
        return False
    except Exception:
        return False


def _resolve_port(preferred: int, *, auto_pick_port: bool) -> int:
    """Return ``preferred`` if free; otherwise an ephemeral port (or raise)."""
    if _port_is_free(preferred):
        return preferred
    if not auto_pick_port:
        raise OSError(f"port {preferred} is already in use")
    # Ask the kernel for a free ephemeral port.
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_is_free(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
