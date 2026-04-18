# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Arize Phoenix integration (P3.5, TASK-005 §5.5).

Phoenix is an OpenTelemetry-compatible local UI for LLM traces. PCE can
stream its already-OpenInference-shaped spans (see
:mod:`pce_core.otel_exporter` and :mod:`pce_core.normalizer.openinference_mapper`)
straight into Phoenix without any schema work — they were designed for it.

This module is entirely optional:

- If ``arize-phoenix`` isn't installed, :data:`PHOENIX_AVAILABLE` is
  False and :func:`start` returns ``{"ok": False, "error": "phoenix_not_installed"}``.
- If Phoenix is running, starting PCE's OTLP exporter against
  ``http://127.0.0.1:<port>/v1/traces`` is idempotent.
- Stop / start is safe to call repeatedly — we don't leak subprocesses.

Public API::

    from pce_core.phoenix_integration import PhoenixManager, PHOENIX_AVAILABLE

    mgr = PhoenixManager()
    await mgr.start()          # spawns phoenix on port 6006 and wires OTLP
    mgr.get_status()            # {"running": True, "port": 6006, "otlp_wired": True, ...}
    await mgr.stop()            # stops phoenix + disables OTLP exporter
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import otel_exporter
from .logging_config import log_event

logger = logging.getLogger("pce.phoenix")


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def _is_phoenix_importable() -> bool:
    try:
        import phoenix  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def _has_phoenix_cli() -> bool:
    """Phoenix ships a ``phoenix-server`` console entry-point in recent versions."""
    return shutil.which("phoenix-server") is not None or shutil.which("phoenix") is not None


PHOENIX_AVAILABLE: bool = _is_phoenix_importable()

DEFAULT_PHOENIX_PORT = 6006
DEFAULT_PHOENIX_HOST = "127.0.0.1"
_STARTUP_DEADLINE_S = 30.0
_PING_INTERVAL_S = 0.25


# ---------------------------------------------------------------------------
# Status dataclass
# ---------------------------------------------------------------------------

@dataclass
class _PhoenixStatus:
    running: bool = False
    host: str = DEFAULT_PHOENIX_HOST
    port: int = DEFAULT_PHOENIX_PORT
    pid: Optional[int] = None
    otlp_wired: bool = False
    ui_url: Optional[str] = None
    otlp_endpoint: Optional[str] = None
    last_error: Optional[str] = None
    phoenix_available: bool = PHOENIX_AVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "otlp_wired": self.otlp_wired,
            "ui_url": self.ui_url,
            "otlp_endpoint": self.otlp_endpoint,
            "last_error": self.last_error,
            "phoenix_available": self.phoenix_available,
        }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PhoenixManager:
    """Lifecycle manager for the Arize Phoenix local server.

    Designed to be instantiated once by :mod:`pce_core.server`'s lifespan
    (on demand — not at startup) and shared through ``app.state``.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_PHOENIX_HOST,
        port: int = DEFAULT_PHOENIX_PORT,
        working_dir: Optional[Path] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.working_dir = (
            Path(working_dir) if working_dir is not None
            else _default_working_dir()
        )
        self._process: Optional[subprocess.Popen[bytes]] = None
        # Set when the caller attached to an already-running external Phoenix
        # (port was bound at start time but we didn't spawn it). We trust the
        # caller's lifecycle in that case rather than re-probing the socket
        # on every status check.
        self._attached_external: bool = False
        self._status = _PhoenixStatus(
            host=host, port=port,
            phoenix_available=PHOENIX_AVAILABLE,
        )

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> dict[str, Any]:
        """Spawn Phoenix and wire PCE's OTLP exporter at it.

        Idempotent: if Phoenix is already running (either our spawned
        subprocess or an external instance we previously attached to) we
        simply re-report state and refresh the OTLP wiring.
        """
        # Already-spawned subprocess still alive.
        if self._process is not None and self._process.poll() is None:
            self._refresh_otlp_wiring()
            return {"ok": True, **self.get_status()}

        # Previously attached to an external Phoenix — don't re-probe the
        # port (that's both redundant and fragile under listener backlog
        # pressure), just refresh OTLP wiring.
        if self._attached_external:
            self._refresh_otlp_wiring()
            return {
                "ok": True,
                "attached_to_existing": True,
                **self.get_status(),
            }

        if not PHOENIX_AVAILABLE and not _has_phoenix_cli():
            return {
                "ok": False,
                "error": "phoenix_not_installed",
                "hint": "pip install arize-phoenix",
                **self.get_status(),
            }

        # Fail early if another process already owns the port.
        if _is_port_in_use(self.host, self.port):
            # Possibly Phoenix is already running externally — still wire OTLP.
            self._status.running = True
            self._status.pid = None
            self._attached_external = True
            self._refresh_otlp_wiring()
            log_event(logger, "phoenix.already_running",
                      host=self.host, port=self.port)
            return {"ok": True, "attached_to_existing": True, **self.get_status()}

        argv = self._build_argv()
        env = dict(os.environ)
        env.setdefault("PHOENIX_HOST", self.host)
        env.setdefault("PHOENIX_PORT", str(self.port))
        env.setdefault("PHOENIX_WORKING_DIR", str(self.working_dir))
        self.working_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._process = subprocess.Popen(
                argv,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_platform_creationflags(),
            )
        except (OSError, ValueError) as exc:
            self._status.last_error = f"spawn_failed: {exc}"
            log_event(logger, "phoenix.spawn_failed",
                      level=logging.WARNING, error=str(exc), argv=argv)
            return {"ok": False, "error": self._status.last_error, **self.get_status()}

        # Poll the port until Phoenix answers, or we time out.
        ok = await self._wait_for_ready()
        if not ok:
            self._kill_process()
            return {"ok": False, "error": "startup_timeout", **self.get_status()}

        self._status.running = True
        self._status.pid = self._process.pid
        self._refresh_otlp_wiring()
        log_event(logger, "phoenix.started",
                  pid=self._process.pid, host=self.host, port=self.port,
                  otlp_wired=self._status.otlp_wired)
        return {"ok": True, **self.get_status()}

    async def stop(self) -> dict[str, Any]:
        """Stop the subprocess (if we spawned one) and disable the exporter."""
        self._kill_process()

        # Fully shut the OTLP exporter down so a later user who points it
        # at their own collector isn't surprised to still see PCE data
        # flowing to a non-running Phoenix.
        if self._status.otlp_wired:
            try:
                otel_exporter.shutdown_otlp_exporter()
            except Exception:
                logger.exception("otel shutdown failed (non-fatal)")
            self._status.otlp_wired = False

        self._status.running = False
        self._status.pid = None
        self._attached_external = False
        log_event(logger, "phoenix.stopped")
        return {"ok": True, **self.get_status()}

    # ---- introspection -----------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        # If we previously spawned Phoenix, double-check the subprocess is alive.
        if self._process is not None:
            if self._process.poll() is not None:
                self._status.running = False
                self._status.pid = None
                self._status.otlp_wired = False
                self._attached_external = False
        # When we attached to an external Phoenix we trust the caller's
        # lifecycle. Repeatedly connecting to the port on every status call
        # is fragile (listener backlog, flaky networks) and duplicative of
        # what /api/v1/capture-health already surfaces for every channel.
        # Explicit ``stop()`` is the supported way to flip back to "down".

        self._status.ui_url = f"http://{self.host}:{self.port}/"
        self._status.otlp_endpoint = otlp_endpoint_for(self.host, self.port)
        self._status.phoenix_available = PHOENIX_AVAILABLE
        return self._status.as_dict()

    def reprobe_external(self) -> bool:
        """Best-effort health probe for an externally-started Phoenix.

        Kept separate so the default :meth:`get_status` is side-effect free.
        """
        if not self._attached_external:
            return self._status.running
        alive = _is_port_in_use(self.host, self.port)
        if not alive:
            self._status.running = False
            self._status.otlp_wired = False
        return alive

    # ---- helpers -----------------------------------------------------------

    def _build_argv(self) -> list[str]:
        """Prefer the Phoenix CLI; fall back to ``python -m phoenix.server.main``."""
        # Newer Phoenix packages ship a ``phoenix-server`` entry-point that
        # accepts subcommands. We use ``serve`` which starts the HTTP UI
        # and OTLP receiver.
        cli = shutil.which("phoenix-server") or shutil.which("phoenix")
        base: list[str]
        if cli:
            base = [cli, "serve"]
        else:
            base = [sys.executable, "-m", "phoenix.server.main", "serve"]

        # Phoenix honours PHOENIX_HOST / PHOENIX_PORT env; newer versions
        # also accept --host / --port. We pass both to be compatible.
        base.extend(["--host", self.host, "--port", str(self.port)])
        return base

    async def _wait_for_ready(self) -> bool:
        """Block until the Phoenix server answers or we hit the deadline."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STARTUP_DEADLINE_S
        while loop.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                # Phoenix died early.
                self._status.last_error = "phoenix exited before becoming ready"
                return False
            if _is_port_in_use(self.host, self.port):
                return True
            await asyncio.sleep(_PING_INTERVAL_S)
        self._status.last_error = "timed out waiting for phoenix to bind the port"
        return False

    def _kill_process(self) -> None:
        proc = self._process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                logger.exception("phoenix terminate failed (non-fatal)")
        self._process = None

    def _refresh_otlp_wiring(self) -> None:
        """Point PCE's OTLP exporter at Phoenix. Idempotent / fail-open."""
        endpoint = otlp_endpoint_for(self.host, self.port)
        try:
            ok = otel_exporter.configure_otlp_exporter(
                endpoint=endpoint,
                protocol="http/protobuf",
                force=True,
            )
            self._status.otlp_wired = bool(ok)
            self._status.otlp_endpoint = endpoint
        except Exception as exc:
            logger.exception("otel configure failed (non-fatal)")
            self._status.otlp_wired = False
            self._status.last_error = f"otlp_wiring_failed: {exc}"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def otlp_endpoint_for(host: str, port: int) -> str:
    """Return the OTLP/HTTP traces endpoint for a Phoenix instance."""
    return f"http://{host}:{port}/v1/traces"


def _is_port_in_use(host: str, port: int) -> bool:
    """Best-effort ``localhost:port`` probe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.connect((host, int(port)))
            return True
        except (OSError, socket.timeout):
            return False


def _default_working_dir() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR) / "phoenix"


def _platform_creationflags() -> int:
    """On Windows, detach Phoenix from our console so Ctrl-C doesn't kill it twice."""
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        return 0x00000200
    return 0


__all__ = [
    "DEFAULT_PHOENIX_HOST",
    "DEFAULT_PHOENIX_PORT",
    "PHOENIX_AVAILABLE",
    "PhoenixManager",
    "otlp_endpoint_for",
]
