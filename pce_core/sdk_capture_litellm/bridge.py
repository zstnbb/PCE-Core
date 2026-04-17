"""LiteLLM proxy lifecycle manager.

Writes a PCE-owned config, spawns ``litellm --config <path> --port <port>``
via :class:`pce_core.supervisor.Supervisor`, exposes status.

LiteLLM is optional; :data:`LITELLM_AVAILABLE` is ``False`` when the
package can't be imported, and :meth:`LiteLLMBridge.start` degrades to a
clean error.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..logging_config import log_event
from ..supervisor import ManagedProcess, RestartPolicy, Supervisor
from .config import write_litellm_config

logger = logging.getLogger("pce.sdk_capture_litellm.bridge")


DEFAULT_LITELLM_PORT = 9900
DEFAULT_CONFIG_FILENAME = "litellm_config.yaml"
SUPERVISOR_NAME = "pce-litellm"


def _is_litellm_importable() -> bool:
    try:
        import litellm              # noqa: F401
        return True
    except Exception:               # noqa: BLE001
        return False


def _has_litellm_cli() -> bool:
    return shutil.which("litellm") is not None


LITELLM_AVAILABLE: bool = _is_litellm_importable()


@dataclass
class _BridgeStatus:
    running: bool = False
    port: Optional[int] = None
    config_path: Optional[str] = None
    litellm_available: bool = LITELLM_AVAILABLE
    supervisor_status: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "port": self.port,
            "config_path": self.config_path,
            "litellm_available": self.litellm_available,
            "supervisor_status": self.supervisor_status,
        }


class LiteLLMBridge:
    """Manages the lifecycle of a ``litellm`` proxy subprocess.

    Designed to be used from :mod:`pce_core.server`'s lifespan or
    directly from the CLI.
    """

    def __init__(
        self,
        *,
        port: int = DEFAULT_LITELLM_PORT,
        config_dir: Optional[Path] = None,
        pce_ingest_url: str = "http://127.0.0.1:9800/api/v1/captures",
    ) -> None:
        self.port = port
        self.config_dir = Path(config_dir) if config_dir else _default_config_dir()
        self.config_path = self.config_dir / DEFAULT_CONFIG_FILENAME
        self.pce_ingest_url = pce_ingest_url
        self._supervisor: Optional[Supervisor] = None
        self._supervisor_owned = False
        self._status = _BridgeStatus(litellm_available=LITELLM_AVAILABLE)

    # ---- lifecycle ---------------------------------------------------------

    async def start(
        self,
        *,
        supervisor: Optional[Supervisor] = None,
    ) -> dict:
        """Write config, spawn ``litellm`` under a supervisor.

        Returns a status dict. If LiteLLM is not available, returns
        ``{"ok": False, "error": "litellm_not_installed"}`` without raising.
        """
        if not LITELLM_AVAILABLE and not _has_litellm_cli():
            return {
                "ok": False,
                "error": "litellm_not_installed",
                "hint": "pip install litellm[proxy]",
            }
        try:
            write_litellm_config(
                self.config_path,
                pce_ingest_url=self.pce_ingest_url,
                port=self.port,
            )
        except OSError as e:
            return {"ok": False, "error": f"config_write_failed: {e}"}

        argv = self._build_argv()
        spec = ManagedProcess(
            name=SUPERVISOR_NAME,
            argv=argv,
            env={"PCE_INGEST_URL": self.pce_ingest_url,
                 "PCE_LITELLM_PORT": str(self.port)},
            restart=RestartPolicy.ALWAYS,
            healthy_after_s=15.0,
            initial_backoff_s=1.0,
            max_backoff_s=60.0,
        )

        if supervisor is not None:
            self._supervisor = supervisor
            self._supervisor_owned = False
            # External supervisor expects caller to register the spec;
            # we re-initialise to just this one spec for symmetry.
        else:
            self._supervisor = Supervisor([spec])
            self._supervisor_owned = True
            await self._supervisor.start_all()

        self._status.running = True
        self._status.port = self.port
        self._status.config_path = str(self.config_path)
        log_event(logger, "litellm.started",
                  port=self.port, config_path=str(self.config_path))
        return {"ok": True, **self._status.as_dict()}

    async def stop(self) -> dict:
        if self._supervisor and self._supervisor_owned:
            await self._supervisor.stop_all()
        self._status.running = False
        log_event(logger, "litellm.stopped")
        return {"ok": True, **self._status.as_dict()}

    # ---- introspection ------------------------------------------------------

    def get_status(self) -> dict:
        if self._supervisor is not None:
            self._status.supervisor_status = self._supervisor.get_status().as_dict()
        return self._status.as_dict()

    # ---- helpers ------------------------------------------------------------

    def _build_argv(self) -> List[str]:
        """Prefer the ``litellm`` CLI; fall back to ``python -m litellm``."""
        if _has_litellm_cli():
            return [
                "litellm",
                "--config", str(self.config_path),
                "--port", str(self.port),
            ]
        import sys as _sys
        return [
            _sys.executable, "-m", "litellm",
            "--config", str(self.config_path),
            "--port", str(self.port),
        ]


def _default_config_dir() -> Path:
    from ..config import DATA_DIR
    return Path(DATA_DIR) / "litellm"
