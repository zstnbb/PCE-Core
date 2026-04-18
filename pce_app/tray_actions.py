# SPDX-License-Identifier: Apache-2.0
"""Backend-agnostic tray actions.

The pystray tray and a future Tauri tray both reach into the Core API
through the same actions defined here, so adding a new menu entry only
requires touching this file + the respective frontend. No Python HTTP
round-trip when the action can be done in-process (e.g. diagnose, open
dashboard, complete onboarding reset), and a thin ``httpx``/``urllib``
call when we want to talk to the running ingest server.

All actions are **blocking-safe** (they will never hang the UI thread
longer than :data:`ACTION_TIMEOUT_S`) and return a dict that UIs can turn
into a toast notification or status bar message.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("pce.tray_actions")

# Keep every network call bounded so the tray never freezes the desktop.
ACTION_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """What every tray action returns.

    The UI decides how to render it: pystray tends to use ``message`` for a
    notification balloon; Tauri can render the full structured payload.
    """
    ok: bool
    title: str
    message: str = ""
    payload: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "title": self.title,
            "message": self.message,
            "payload": self.payload or {},
        }


# ---------------------------------------------------------------------------
# Core API helper
# ---------------------------------------------------------------------------

def _api_url(path: str, host: str = "127.0.0.1", port: int = 9800) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}:{port}{path}"


def _core_running(host: str = "127.0.0.1", port: int = 9800) -> bool:
    """Cheap ``connect()`` probe that avoids the urllib overhead."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect((host, int(port)))
            return True
        except (OSError, socket.timeout):
            return False


def _api_call(
    method: str,
    path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 9800,
    body: Optional[dict] = None,
    timeout: float = ACTION_TIMEOUT_S,
) -> tuple[int, Any]:
    """Minimal JSON HTTP client — keeps ``urllib`` instead of httpx so we
    don't add import-time latency to the tray."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        _api_url(path, host, port),
        data=data,
        method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        raise ConnectionError(str(exc))
    if not raw:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def open_dashboard(host: str = "127.0.0.1", port: int = 9800) -> ActionResult:
    """Open the dashboard in the user's default browser."""
    url = f"http://{host}:{port}/"
    try:
        webbrowser.open(url)
        return ActionResult(ok=True, title="Dashboard", message=f"Opened {url}")
    except Exception as exc:
        return ActionResult(
            ok=False, title="Dashboard",
            message=f"Could not open browser: {exc}",
        )


def open_onboarding(host: str = "127.0.0.1", port: int = 9800) -> ActionResult:
    """Launch the first-run setup wizard in the user's browser."""
    url = f"http://{host}:{port}/onboarding"
    try:
        webbrowser.open(url)
        return ActionResult(ok=True, title="Setup Wizard", message=f"Opened {url}")
    except Exception as exc:
        return ActionResult(
            ok=False, title="Setup Wizard",
            message=f"Could not open browser: {exc}",
        )


def collect_diagnostics(
    *,
    host: str = "127.0.0.1",
    port: int = 9800,
    output_dir: Optional[Path] = None,
    include_logs: bool = True,
    reveal_in_explorer: bool = True,
) -> ActionResult:
    """Run the diagnose collector and write the zip to ``output_dir``.

    We prefer the in-process collector so the action works even when the
    core server is down. If ``output_dir`` is None we default to the
    standard ``{DATA_DIR}/diagnose/`` folder.
    """
    try:
        from pce_core import diagnose
        path = diagnose.collect_diagnostics(
            output_path=(Path(output_dir) / _diag_filename()) if output_dir else None,
            include_logs=include_logs,
        )
    except Exception as exc:
        logger.exception("tray diagnose failed")
        return ActionResult(
            ok=False, title="Collect Diagnostics",
            message=f"Failed: {type(exc).__name__}: {exc}",
        )

    if reveal_in_explorer:
        try:
            _open_in_file_manager(path.parent)
        except Exception:
            logger.debug("reveal_in_explorer failed", exc_info=True)

    size_kb = path.stat().st_size / 1024.0
    return ActionResult(
        ok=True,
        title="Diagnostics saved",
        message=f"Wrote {path.name} ({size_kb:.1f} KB) to {path.parent}",
        payload={"path": str(path), "size_kb": round(size_kb, 1)},
    )


def phoenix_toggle(
    *,
    host: str = "127.0.0.1",
    port: int = 9800,
) -> ActionResult:
    """If Phoenix is running, stop it. Otherwise try to start it."""
    if not _core_running(host, port):
        return ActionResult(
            ok=False, title="Phoenix",
            message="Core server is not running.",
        )
    try:
        status_code, body = _api_call("GET", "/api/v1/phoenix", host=host, port=port)
    except ConnectionError as exc:
        return ActionResult(
            ok=False, title="Phoenix",
            message=f"Core server unreachable: {exc}",
        )
    if status_code != 200 or not isinstance(body, dict):
        return ActionResult(ok=False, title="Phoenix", message="Unexpected response.")

    running = bool(body.get("running"))
    endpoint = "/api/v1/phoenix/stop" if running else "/api/v1/phoenix/start"
    try:
        status_code, resp = _api_call(
            "POST", endpoint, host=host, port=port, body={},
        )
    except ConnectionError as exc:
        return ActionResult(ok=False, title="Phoenix", message=str(exc))
    if not isinstance(resp, dict):
        return ActionResult(ok=False, title="Phoenix", message="Unexpected response.")

    if resp.get("ok"):
        title = "Phoenix stopped" if running else "Phoenix started"
        msg = resp.get("ui_url") or ""
        if not running and resp.get("running"):
            msg = f"Running at {resp.get('ui_url', '')}"
        return ActionResult(ok=True, title=title, message=msg, payload=resp)
    return ActionResult(
        ok=False, title="Phoenix",
        message=resp.get("error", "failed"),
        payload=resp,
    )


def reset_onboarding(host: str = "127.0.0.1", port: int = 9800) -> ActionResult:
    """Clear the first-run flag so the wizard re-appears on next launch."""
    try:
        from pce_core import app_state
        app_state.reset_onboarding()
        return ActionResult(
            ok=True, title="Setup Wizard",
            message="Setup wizard will reopen on next launch.",
        )
    except Exception as exc:
        logger.exception("reset_onboarding failed")
        return ActionResult(
            ok=False, title="Setup Wizard",
            message=f"Failed: {type(exc).__name__}: {exc}",
        )


def check_for_updates(
    host: str = "127.0.0.1",
    port: int = 9800,
) -> ActionResult:
    """Ask the core server whether a newer PCE version is available."""
    if not _core_running(host, port):
        return ActionResult(
            ok=False, title="Check for updates",
            message="Core server is not running.",
        )
    try:
        status_code, body = _api_call(
            "GET", "/api/v1/updates/check", host=host, port=port,
        )
    except ConnectionError as exc:
        return ActionResult(
            ok=False, title="Check for updates",
            message=f"Could not reach update manifest: {exc}",
        )
    if status_code != 200 or not isinstance(body, dict):
        return ActionResult(
            ok=False, title="Check for updates",
            message="Unexpected update manifest response.",
        )
    if body.get("update_available"):
        latest = body.get("latest_version", "?")
        return ActionResult(
            ok=True,
            title="Update available",
            message=f"PCE {latest} is available.",
            payload=body,
        )
    return ActionResult(
        ok=True,
        title="Up to date",
        message=f"PCE {body.get('current_version', '?')} is the latest.",
        payload=body,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _diag_filename() -> str:
    return f"pce-diag-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.zip"


def _open_in_file_manager(path: Path) -> None:
    """Cross-platform "reveal folder" that tolerates missing DISPLAY."""
    path = Path(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)], close_fds=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", str(path)], close_fds=True)


__all__ = [
    "ActionResult",
    "check_for_updates",
    "collect_diagnostics",
    "open_dashboard",
    "open_onboarding",
    "phoenix_toggle",
    "reset_onboarding",
]
