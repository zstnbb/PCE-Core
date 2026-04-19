# SPDX-License-Identifier: Apache-2.0
"""PCE Core – First-run wizard aggregator (P5.A-5, UCS §3.3).

The wizard UI in ``pce_core/dashboard/onboarding.js`` previously polled
five separate endpoints (``/api/v1/cert``, ``/proxy``, ``/electron/apps``,
``/bypass/apps``, ``/health/pinning``) on every step render. That's
chatty, racy under slow I/O, and forces the front-end to re-derive the
"is the user ready to send a test message?" judgement.

This module collapses those signals into a single read-only snapshot
that ``GET /api/v1/onboarding/status`` serves in one round trip. It is
deliberately a pure orchestrator — no state is written — so the wizard
state machine (``app_state.py``) stays the single source of truth for
"which step is the user on".

Signals collapsed:

- **CA trust** — platform + number of PCE-managed certs in the trust
  store + default CA path.
- **System proxy** — whether the OS is currently pointed at our
  mitmproxy listener, with host / port if so.
- **Electron apps** — KNOWN_APPS ∩ detected-on-disk, annotated with
  their bypass flag (P5.A-7).
- **Pinning warnings** — hosts that have refused our MITM cert in the
  last hour (P5.A-6) — usually the trigger for the bypass step.
- **Capture health** — total raw_captures rows + count received in
  the last 5 min, which the wizard's "send a test prompt" verification
  step uses to show green.

``verify_capture_since(ts_ns)`` is factored out so the wizard can poll
it cheaply in a tight loop without paying for the full status payload.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import DB_PATH

logger = logging.getLogger("pce.onboarding")


_RECENT_CAPTURE_WINDOW_SECONDS: float = 300.0  # 5 min
_PINNING_WINDOW_HOURS_DEFAULT: float = 1.0
_PINNING_MIN_FAILURES_DEFAULT: int = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_captures(conn: sqlite3.Connection) -> tuple[int, int, Optional[float]]:
    """Return (total, recent_count, latest_ts_epoch).

    ``recent_count`` is the number of captures whose ``created_at`` falls
    within the last :data:`_RECENT_CAPTURE_WINDOW_SECONDS`. A non-zero
    ``recent_count`` is what drives the wizard's "looks good, go send a
    test prompt" green dot.
    """
    row = conn.execute("SELECT COUNT(*) AS c FROM raw_captures").fetchone()
    total = row["c"] if row else 0
    cutoff = time.time() - _RECENT_CAPTURE_WINDOW_SECONDS
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_captures WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    recent = row["c"] if row else 0
    row = conn.execute(
        "SELECT MAX(created_at) AS t FROM raw_captures",
    ).fetchone()
    latest = row["t"] if row and row["t"] else None
    return total, recent, latest


def _count_pinning_warnings(
    conn: sqlite3.Connection,
    *,
    window_hours: float = _PINNING_WINDOW_HOURS_DEFAULT,
    min_failures: int = _PINNING_MIN_FAILURES_DEFAULT,
) -> int:
    """Count hosts flagged as suspected-pinning in the recent window.

    We don't replicate the full pinning-report math here — the wizard
    just needs a boolean-ish "should I warn the user about pinned apps"
    signal. ``GET /api/v1/health/pinning`` remains the detailed view.
    """
    cutoff = time.time() - window_hours * 3600.0
    row = conn.execute(
        """
        SELECT COUNT(*) AS hosts
          FROM (
            SELECT host, COUNT(*) AS failures
              FROM tls_failures
             WHERE created_at >= ?
             GROUP BY host
            HAVING COUNT(*) >= ?
          )
        """,
        (cutoff, min_failures),
    ).fetchone()
    return int(row["hosts"]) if row else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_status(
    *,
    db_path: Optional[Path] = None,
    capture_window_seconds: float = _RECENT_CAPTURE_WINDOW_SECONDS,
    pinning_window_hours: float = _PINNING_WINDOW_HOURS_DEFAULT,
    pinning_min_failures: int = _PINNING_MIN_FAILURES_DEFAULT,
) -> dict:
    """Build the aggregate onboarding snapshot.

    All sub-queries are wrapped in try/except so a failure in one
    dimension (e.g. an unreadable trust store) degrades to a partial
    payload instead of a 500. The caller can tell what's missing by
    looking at the per-section ``error`` field.
    """
    # Imported lazily so tests that monkey-patch any of these modules
    # get the fresh versions after pytest fixtures reload config.
    from . import app_bypass as _app_bypass
    from . import cert_wizard as _cert_wizard
    from . import electron_proxy as _electron
    from . import proxy_toggle as _proxy_toggle
    from .db import get_connection

    result: dict = {}

    # ── Trust store ------------------------------------------------------
    try:
        platform = _cert_wizard.detect_platform()
        certs = _cert_wizard.list_ca()
        result["ca"] = {
            "platform": platform.value,
            "default_path": str(_cert_wizard.default_ca_path()),
            "installed_count": len(certs),
            "installed": [
                {"subject": c.subject, "thumbprint_sha1": c.thumbprint_sha1}
                for c in certs
            ],
            "ok": len(certs) > 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding: cert probe failed: %s", exc)
        result["ca"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ── System proxy toggle ---------------------------------------------
    try:
        proxy_state = _proxy_toggle.get_proxy_state()
        result["proxy"] = {
            "enabled": bool(proxy_state.enabled),
            "host": proxy_state.host,
            "port": proxy_state.port,
            "platform": proxy_state.platform.value,
            "bypass": list(proxy_state.bypass or []),
            "ok": bool(proxy_state.enabled),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding: proxy probe failed: %s", exc)
        result["proxy"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ── Electron apps + bypass flags ------------------------------------
    try:
        bypass_state = _app_bypass.load_bypass()
        bypassed_set = set(bypass_state["bypassed"])
        installed = _electron.detect_installed_apps()
        detected_names = {app["name"] for app in installed}

        apps_payload = [
            {
                "name": info["name"],
                "display_name": info["display_name"],
                "path": info["path"],
                "ai_domains": list(info.get("ai_domains") or []),
                "bypassed": info["name"] in bypassed_set,
            }
            for info in installed
        ]
        # Apps known to PCE but not on disk — still surfaced so the UI can
        # show "we support ChatGPT Desktop, but it isn't installed here".
        missing_payload = [
            {
                "name": app.name,
                "display_name": app.display_name,
                "ai_domains": list(app.ai_domains),
            }
            for app in _electron.KNOWN_APPS
            if app.name not in detected_names
        ]
        result["apps"] = {
            "detected": apps_payload,
            "detected_count": len(apps_payload),
            "missing": missing_payload,
            "bypassed_count": sum(1 for a in apps_payload if a["bypassed"]),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding: app probe failed: %s", exc)
        result["apps"] = {"error": f"{type(exc).__name__}: {exc}"}

    # ── Captures + pinning (DB) -----------------------------------------
    try:
        conn = get_connection(db_path)
        conn.row_factory = sqlite3.Row
        try:
            total, recent, latest = _count_captures(conn)
            pinning_hosts = _count_pinning_warnings(
                conn,
                window_hours=pinning_window_hours,
                min_failures=pinning_min_failures,
            )
        finally:
            conn.close()

        result["captures"] = {
            "total": int(total),
            "recent_count": int(recent),
            "recent_window_seconds": float(capture_window_seconds),
            "latest_ts_epoch": latest,
            "has_recent": recent > 0,
        }
        result["pinning"] = {
            "suspected_hosts_last_hour": pinning_hosts,
            "window_hours": float(pinning_window_hours),
            "min_failures_threshold": int(pinning_min_failures),
            "has_warnings": pinning_hosts > 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding: db probe failed: %s", exc)
        result["captures"] = {"error": f"{type(exc).__name__}: {exc}"}
        result["pinning"] = {"error": f"{type(exc).__name__}: {exc}"}

    # ── Overall readiness ------------------------------------------------
    ca_ok = result.get("ca", {}).get("ok", False)
    proxy_ok = result.get("proxy", {}).get("ok", False)
    result["ready"] = bool(ca_ok and proxy_ok)
    result["generated_at_epoch"] = time.time()

    return result


def verify_capture_since(
    since_epoch: float,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Did PCE record any capture after ``since_epoch``?

    Used by the wizard's "send a test ChatGPT message" step: the UI
    grabs the current server epoch, asks the user to send a prompt,
    then polls this endpoint until ``captured`` flips true. A small
    window tolerance is applied to absorb clock-skew between the UI's
    ``Date.now()`` and ``time.time()`` on the server (they may differ
    slightly if the UI reads ``generated_at_epoch`` off ``/status``).
    """
    if since_epoch < 0:
        since_epoch = 0
    try:
        conn = get_connection_for_verify(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c, MAX(created_at) AS latest "
                "FROM raw_captures WHERE created_at > ?",
                (since_epoch,),
            ).fetchone()
            count = int(row[0]) if row else 0
            latest = row[1] if row and row[1] else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding.verify: db probe failed: %s", exc)
        return {
            "captured": False,
            "count": 0,
            "since_epoch": since_epoch,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "captured": count > 0,
        "count": count,
        "since_epoch": since_epoch,
        "latest_ts_epoch": latest,
    }


def get_connection_for_verify(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Thin wrapper so tests can monkey-patch the connector cheaply."""
    from .db import get_connection

    return get_connection(db_path)
