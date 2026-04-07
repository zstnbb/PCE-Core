"""PCE Local Hook – Multi-hook manager.

Manages multiple hook instances, one per discovered local model server.
Periodically rescans ports and starts/stops hooks as servers appear/disappear.
"""

import logging
import multiprocessing
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .scanner import DiscoveredServer, scan_known_ports

logger = logging.getLogger("pce.multi_hook")

RESCAN_INTERVAL_S = 60  # Rescan every 60 seconds
HOOK_PORT_OFFSET = 1000  # Hook port = target port + offset


@dataclass
class HookInstance:
    """Tracks a running hook process for one local model server."""
    target_port: int
    listen_port: int
    provider: str
    models: list[str] = field(default_factory=list)
    process: Optional[multiprocessing.Process] = field(default=None, repr=False)
    pid: Optional[int] = None
    started_at: float = 0.0


def _run_hook_process(target_port: int, listen_port: int):
    """Top-level process target for a single hook instance (must be picklable)."""
    import sys, os, io
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    import uvicorn
    from pce_core.local_hook.hook import create_hook_app
    app = create_hook_app(target_host="127.0.0.1", target_port=target_port)
    uvicorn.run(app, host="127.0.0.1", port=listen_port, log_level="info")


class MultiHookManager:
    """Manages multiple local model hook instances with auto-discovery."""

    def __init__(self, port_offset: int = HOOK_PORT_OFFSET):
        self.port_offset = port_offset
        self.hooks: dict[int, HookInstance] = {}  # keyed by target_port
        self._lock = threading.Lock()
        self._rescan_thread: Optional[threading.Thread] = None
        self._running = False

    # -- Public API --

    def start(self):
        """Start the multi-hook manager: initial scan + periodic rescan."""
        if self._running:
            return
        self._running = True
        logger.info("MultiHookManager starting (rescan every %ds)", RESCAN_INTERVAL_S)
        self._scan_and_update()
        self._rescan_thread = threading.Thread(target=self._rescan_loop, daemon=True)
        self._rescan_thread.start()

    def stop(self):
        """Stop all hooks and the rescan thread."""
        self._running = False
        with self._lock:
            for port, hook in list(self.hooks.items()):
                self._stop_hook(hook)
            self.hooks.clear()
        logger.info("MultiHookManager stopped")

    def get_status(self) -> list[dict]:
        """Return status of all managed hooks."""
        with self._lock:
            result = []
            for port, hook in sorted(self.hooks.items()):
                alive = hook.process is not None and hook.process.is_alive()
                result.append({
                    "target_port": hook.target_port,
                    "listen_port": hook.listen_port,
                    "provider": hook.provider,
                    "models": hook.models,
                    "status": "running" if alive else "stopped",
                    "pid": hook.pid,
                })
            return result

    def get_active_count(self) -> int:
        """Return number of currently active hooks."""
        with self._lock:
            return sum(1 for h in self.hooks.values() if h.process and h.process.is_alive())

    # -- Internal --

    def _rescan_loop(self):
        """Background thread: periodically rescan and update hooks."""
        while self._running:
            time.sleep(RESCAN_INTERVAL_S)
            if not self._running:
                break
            try:
                self._scan_and_update()
            except Exception:
                logger.exception("Rescan failed (non-fatal)")

    def _scan_and_update(self):
        """Scan ports and start/stop hooks as needed."""
        discovered = scan_known_ports()
        discovered_ports = {s.port for s in discovered}
        discovered_map = {s.port: s for s in discovered}

        with self._lock:
            # Stop hooks for servers that disappeared
            for port in list(self.hooks.keys()):
                if port not in discovered_ports:
                    logger.info("Server on port %d gone, stopping hook", port)
                    self._stop_hook(self.hooks[port])
                    del self.hooks[port]

            # Start hooks for newly discovered servers
            for server in discovered:
                if server.port not in self.hooks:
                    self._start_hook(server)
                else:
                    # Update model list
                    self.hooks[server.port].models = server.models

    def _start_hook(self, server: DiscoveredServer):
        """Start a hook instance for a discovered server."""
        listen_port = server.port + self.port_offset

        hook = HookInstance(
            target_port=server.port,
            listen_port=listen_port,
            provider=server.provider,
            models=server.models,
            started_at=time.time(),
        )

        proc = multiprocessing.Process(
            target=_run_hook_process,
            args=(server.port, listen_port),
            daemon=True,
        )
        proc.start()

        hook.process = proc
        hook.pid = proc.pid
        self.hooks[server.port] = hook

        logger.info(
            "Started hook for %s on :%d → :%d (pid=%s, models=%s)",
            server.provider, server.port, listen_port, proc.pid, server.models,
        )

    def _stop_hook(self, hook: HookInstance):
        """Stop a hook instance."""
        if hook.process and hook.process.is_alive():
            hook.process.terminate()
            hook.process.join(timeout=5)
            if hook.process.is_alive():
                hook.process.kill()
            logger.info("Stopped hook for :%d (pid=%s)", hook.target_port, hook.pid)
        hook.process = None
        hook.pid = None
