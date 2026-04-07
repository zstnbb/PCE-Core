"""PCE Service Manager – start/stop/monitor all PCE services.

Manages three services:
1. Core API Server (FastAPI on :9800) – always runs
2. Network Proxy (mitmproxy on :8080) – optional
3. Local Model Hook (reverse-proxy) – optional

NOTE: All process targets MUST be top-level functions (not closures/lambdas)
so that they can be pickled by multiprocessing on Windows / PyInstaller.
"""

import logging
import multiprocessing
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("pce.app")


# ═══════════════════════════════════════════════════════════════════════════
# Top-level process targets (must be picklable for Windows multiprocessing)
# ═══════════════════════════════════════════════════════════════════════════

def _patch_stdio():
    """Ensure sys.stdout/stderr are never None.

    PyInstaller with console=False sets them to None, which crashes
    uvicorn's logging formatter (calls sys.stderr.isatty()).
    """
    import io, os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _run_core_process():
    """Top-level target for the Core API server subprocess."""
    _patch_stdio()
    import uvicorn
    from pce_core.server import app
    from pce_core.config import INGEST_HOST, INGEST_PORT
    uvicorn.run(app, host=INGEST_HOST, port=INGEST_PORT, log_level="info")


def _run_proxy_process():
    """Top-level target for the mitmproxy subprocess."""
    from mitmproxy.tools.main import mitmdump
    from pce_core.config import PROXY_LISTEN_HOST, PROXY_LISTEN_PORT

    addon_path = str(Path(__file__).resolve().parent.parent / "run_proxy.py")
    if not Path(addon_path).exists():
        addon_path = str(
            Path(__file__).resolve().parent.parent / "pce_proxy" / "addon.py"
        )

    args = [
        "--listen-host", PROXY_LISTEN_HOST,
        "--listen-port", str(PROXY_LISTEN_PORT),
        "--set", "flow_detail=0",
        "-s", addon_path,
    ]
    mitmdump(args)


def _run_local_hook_process(target_port: int = 11434, listen_port: int = 11435):
    """Top-level target for the Local Model Hook subprocess."""
    _patch_stdio()
    import uvicorn
    from pce_core.local_hook.hook import create_hook_app
    app = create_hook_app(target_host="127.0.0.1", target_port=target_port)
    uvicorn.run(app, host="127.0.0.1", port=listen_port, log_level="info")


def _run_multi_hook_process():
    """Top-level target for the Multi-Hook manager subprocess."""
    _patch_stdio()
    from pce_core.local_hook.multi_hook import MultiHookManager
    manager = MultiHookManager()
    manager.start()
    # Block forever (rescan thread runs in background)
    import threading
    threading.Event().wait()


def _run_clipboard_monitor_process():
    """Top-level target for the clipboard monitor subprocess."""
    _patch_stdio()
    from pce_core.clipboard_monitor import ClipboardMonitor
    monitor = ClipboardMonitor()
    monitor.start()
    import threading
    threading.Event().wait()


# ═══════════════════════════════════════════════════════════════════════════
# Service status types
# ═══════════════════════════════════════════════════════════════════════════

class ServiceStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class ServiceInfo:
    name: str
    status: ServiceStatus = ServiceStatus.STOPPED
    port: int = 0
    pid: Optional[int] = None
    error: Optional[str] = None
    process: Optional[multiprocessing.Process] = field(default=None, repr=False)


# ═══════════════════════════════════════════════════════════════════════════
# Service Manager
# ═══════════════════════════════════════════════════════════════════════════

class ServiceManager:
    """Manages all PCE background services."""

    def __init__(self):
        self.services: dict[str, ServiceInfo] = {
            "core": ServiceInfo(name="Core API Server", port=9800),
            "proxy": ServiceInfo(name="Network Proxy", port=8080),
            "local_hook": ServiceInfo(name="Local Model Hook", port=11435),
            "multi_hook": ServiceInfo(name="Multi-Port Model Hook", port=0),
            "clipboard": ServiceInfo(name="Clipboard Monitor", port=0),
        }
        self._lock = threading.Lock()
        self._callbacks: list[Callable] = []

    def on_change(self, callback: Callable):
        """Register a callback for service state changes."""
        self._callbacks.append(callback)

    def _notify(self):
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    # -- Status API (called from dashboard / tray) --

    def get_status(self) -> dict:
        with self._lock:
            return {
                name: {
                    "name": svc.name,
                    "status": svc.status.value,
                    "port": svc.port,
                    "pid": svc.pid,
                    "error": svc.error,
                }
                for name, svc in self.services.items()
            }

    # -- Core API Server --

    def start_core(self):
        self._start_service("core", _run_core_process)

    # -- Network Proxy (mitmproxy) --

    def proxy_available(self) -> bool:
        """Check if mitmproxy is installed."""
        try:
            import mitmproxy  # noqa: F401
            return True
        except ImportError:
            return False

    def start_proxy(self):
        if not self.proxy_available():
            with self._lock:
                svc = self.services["proxy"]
                svc.status = ServiceStatus.ERROR
                svc.error = "mitmproxy not installed (pip install mitmproxy)"
            self._notify()
            return
        self._start_service("proxy", _run_proxy_process)

    # -- Local Model Hook --

    def start_local_hook(self, target_port: int = 11434, listen_port: int = 11435):
        svc = self.services["local_hook"]
        svc.port = listen_port
        self._start_service(
            "local_hook",
            _run_local_hook_process,
            args=(target_port, listen_port),
        )

    # -- Multi-Port Model Hook (auto-discovery) --

    def start_multi_hook(self):
        """Start multi-hook manager with auto-discovery of local model servers."""
        self._start_service("multi_hook", _run_multi_hook_process)

    # -- Clipboard Monitor --

    def start_clipboard(self):
        """Start the clipboard monitor (experimental)."""
        self._start_service("clipboard", _run_clipboard_monitor_process)

    # -- Generic start/stop --

    def _start_service(self, key: str, target: Callable, args: tuple = ()):
        with self._lock:
            svc = self.services[key]
            if svc.status == ServiceStatus.RUNNING and svc.process and svc.process.is_alive():
                return  # already running

            svc.status = ServiceStatus.STARTING
            svc.error = None

        proc = multiprocessing.Process(target=target, args=args, daemon=True)
        proc.start()

        with self._lock:
            svc.process = proc
            svc.pid = proc.pid
            svc.status = ServiceStatus.RUNNING

        self._notify()
        logger.info("Started %s (pid=%s, port=%s)", svc.name, proc.pid, svc.port)

    def stop_service(self, key: str):
        with self._lock:
            svc = self.services.get(key)
            if not svc or not svc.process:
                return

            if svc.process.is_alive():
                svc.process.terminate()
                svc.process.join(timeout=5)
                if svc.process.is_alive():
                    svc.process.kill()

            svc.status = ServiceStatus.STOPPED
            svc.pid = None
            svc.process = None
            svc.error = None

        self._notify()
        logger.info("Stopped %s", svc.name)

    def stop_all(self):
        for key in list(self.services.keys()):
            self.stop_service(key)

    def restart_service(self, key: str):
        self.stop_service(key)
        time.sleep(0.5)
        if key == "core":
            self.start_core()
        elif key == "proxy":
            self.start_proxy()
        elif key == "local_hook":
            self.start_local_hook()
        elif key == "multi_hook":
            self.start_multi_hook()
        elif key == "clipboard":
            self.start_clipboard()

    def is_running(self, key: str) -> bool:
        with self._lock:
            svc = self.services.get(key)
            return (
                svc is not None
                and svc.status == ServiceStatus.RUNNING
                and svc.process is not None
                and svc.process.is_alive()
            )
