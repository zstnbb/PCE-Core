# SPDX-License-Identifier: Apache-2.0
"""PCE Core – FastAPI server providing Ingest & Query APIs.

Start with:
    python -m pce_core.server
    # or
    uvicorn pce_core.server:app --host 127.0.0.1 --port 9800
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import config as _config
from .config import (
    ALLOWED_HOSTS, CAPTURE_MODE, DB_PATH,
    INGEST_HOST, INGEST_PORT,
    PROXY_LISTEN_HOST, PROXY_LISTEN_PORT,
    OTEL_EXPORTER_OTLP_ENDPOINT,
    RETENTION_DAYS, RETENTION_MAX_ROWS, RETENTION_INTERVAL_HOURS,
)
from .db import (
    SOURCE_BROWSER_EXT,
    SOURCE_MCP,
    SOURCE_PROXY,
    add_custom_domain,
    count_favorited_sessions,
    delete_snippet,
    get_custom_domains,
    get_capture_health,
    get_detailed_health,
    get_snippet,
    get_snippet_categories,
    get_source_activity,
    get_stats,
    init_db,
    insert_capture,
    insert_snippet,
    list_custom_domains,
    new_pair_id,
    prune_pipeline_errors,
    query_by_pair,
    query_captures,
    query_messages,
    query_recent,
    query_sessions,
    query_snippets,
    record_pipeline_error,
    refresh_custom_domains,
    remove_custom_domain,
    reset_all_data,
    set_session_favorite,
    update_snippet,
)
from .export import (
    EXPORT_SCHEMA_VERSION,
    export_summary,
    iter_oi_spans,
    stream_json_envelope,
    stream_jsonl,
)
from .import_data import import_document, import_jsonl
from .logging_config import configure_logging, log_event
from .migrations import EXPECTED_SCHEMA_VERSION, get_current_version, get_migration_history
from .otel_exporter import configure_otlp_exporter, is_enabled as _otel_is_enabled, shutdown_otlp_exporter
from .retention import run_retention_loop, sweep_once
# P2 — capture UX + process supervision
from . import cert_wizard as _cert_wizard
from . import proxy_toggle as _proxy_toggle
from .sdk_capture_litellm import LITELLM_AVAILABLE, LiteLLMBridge
from .supervisor import Supervisor
from .models import (
    CaptureIn,
    CaptureOut,
    CaptureRecord,
    HealthOut,
    MessageRecord,
    SessionRecord,
    SnippetIn,
    SnippetRecord,
    StatsOut,
)
from .normalizer.pipeline import normalize_conversation, try_normalize_pair

# Configure root logging before creating the server logger so subsequent
# loggers pick up the JSON formatter when PCE_LOG_JSON=1.
configure_logging()
logger = logging.getLogger("pce.server")

# ---------------------------------------------------------------------------
# Source type → default source_id mapping
# ---------------------------------------------------------------------------

_SOURCE_MAP = {
    "proxy": SOURCE_PROXY,
    "browser_extension": SOURCE_BROWSER_EXT,
    "mcp": SOURCE_MCP,
}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    log_event(logger, "server.starting", db_path=str(DB_PATH), version=__version__)
    try:
        init_db()
    except RuntimeError as exc:
        # Downgrade-protection: the DB is at a schema version we don't
        # recognise. Surface the error clearly and abort startup.
        log_event(
            logger, "server.start_failed",
            level=logging.CRITICAL,
            reason="schema_version_incompatible",
            error=str(exc),
        )
        raise
    # Trim stale pipeline_errors on startup so health counts reflect recent
    # behaviour and the table never grows unbounded across long uptimes.
    try:
        pruned = prune_pipeline_errors()
        if pruned:
            log_event(logger, "pipeline_errors.pruned", deleted=pruned)
    except Exception:
        logger.exception("prune_pipeline_errors on startup failed (non-fatal)")

    # P1: initialise the opt-in OTLP exporter (no-op when the endpoint env
    # var is unset). Always safe to call; logs its own ready / disabled event.
    try:
        configure_otlp_exporter()
    except Exception:
        logger.exception("configure_otlp_exporter failed (non-fatal)")

    # P1: spawn the retention loop. The loop itself is a no-op when both
    # PCE_RETENTION_DAYS and PCE_RETENTION_MAX_ROWS are 0.
    app.state.retention_stop = asyncio.Event()
    app.state.retention_task = asyncio.create_task(
        run_retention_loop(
            interval_hours=RETENTION_INTERVAL_HOURS,
            stop_event=app.state.retention_stop,
        ),
        name="pce.retention.loop",
    )

    # P2 — slots for lazily-started process managers; populated on demand via
    # /api/v1/supervisor and /api/v1/sdk/litellm. Holding the slot here
    # guarantees clean teardown even if the user never hits the stop endpoint.
    app.state.supervisor = None
    app.state.litellm_bridge = None
    # P3 — Phoenix is opt-in; slot starts empty, populated on first /start call.
    app.state.phoenix_manager = None

    # P3 — honour the user's persisted "phoenix_auto_start" preference.
    try:
        from . import app_state as _app_state
        from . import phoenix_integration as _phoenix
        prefs = _app_state.load_state().get("preferences", {}) or {}
        if prefs.get("phoenix_auto_start") and _phoenix.PHOENIX_AVAILABLE:
            mgr = _phoenix.PhoenixManager()
            app.state.phoenix_manager = mgr
            try:
                await mgr.start()
            except Exception:
                logger.exception("phoenix auto-start failed (non-fatal)")
    except Exception:
        logger.exception("phoenix auto-start probe failed (non-fatal)")

    log_event(
        logger, "server.ready",
        host=INGEST_HOST, port=INGEST_PORT, version=__version__,
        otel_enabled=_otel_is_enabled(),
        retention_days=RETENTION_DAYS,
        retention_max_rows=RETENTION_MAX_ROWS,
        litellm_available=LITELLM_AVAILABLE,
    )
    yield

    log_event(logger, "server.stopping")
    # Gracefully stop the retention loop so shutdown isn't stuck waiting
    # the whole interval for the sleep to finish.
    try:
        stop_evt: asyncio.Event = app.state.retention_stop
        stop_evt.set()
        task: asyncio.Task = app.state.retention_task
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    except Exception:
        pass

    # P2 — tear down the LiteLLM bridge & any managed supervisor before
    # the event loop closes, otherwise lingering subprocesses become zombies.
    bridge = getattr(app.state, "litellm_bridge", None)
    if bridge is not None:
        try:
            await bridge.stop()
        except Exception:
            logger.exception("litellm_bridge.stop failed (non-fatal)")
    sup = getattr(app.state, "supervisor", None)
    if sup is not None:
        try:
            await sup.stop_all()
        except Exception:
            logger.exception("supervisor.stop_all failed (non-fatal)")

    # P3 — stop Phoenix if we spawned it so the subprocess doesn't outlive us.
    phoenix_mgr = getattr(app.state, "phoenix_manager", None)
    if phoenix_mgr is not None:
        try:
            await phoenix_mgr.stop()
        except Exception:
            logger.exception("phoenix_manager.stop failed (non-fatal)")

    try:
        shutdown_otlp_exporter()
    except Exception:
        pass


app = FastAPI(
    title="PCE Core API",
    description="Unified ingest & query API for all PCE capture frontends.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/v1/health")
def health():
    """Detailed health snapshot — per-source, pipeline, storage.

    Response shape matches TASK-002 §5.1 and is consumed by the
    dashboard health page. The top-level ``status`` / ``version`` /
    ``db_path`` / ``total_captures`` fields are preserved for
    backward compatibility with older clients and the ``HealthOut``
    Pydantic model.
    """
    try:
        snapshot = get_detailed_health()
    except Exception:
        logger.exception("get_detailed_health failed – returning degraded payload")
        snapshot = {
            "status": "degraded",
            "timestamp": None,
            "schema_version": None,
            "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            "sources": {},
            "pipeline": {},
            "storage": {},
        }
    # Backward-compatible surface fields.
    snapshot.setdefault("status", "ok")
    snapshot["version"] = __version__
    snapshot["db_path"] = str(DB_PATH)
    snapshot["total_captures"] = snapshot.get("storage", {}).get("raw_captures_rows", 0)
    snapshot["ingest_host"] = INGEST_HOST
    snapshot["ingest_port"] = INGEST_PORT
    return snapshot


@app.get("/api/v1/health/migrations")
def migrations_status():
    """Return schema version state and applied migration history."""
    from .db import get_connection

    conn = get_connection()
    try:
        current = get_current_version(conn)
        history = get_migration_history(conn)
    finally:
        conn.close()
    return {
        "current_version": current,
        "expected_version": EXPECTED_SCHEMA_VERSION,
        "ok": current == EXPECTED_SCHEMA_VERSION,
        "history": history,
    }


# ---------------------------------------------------------------------------
# Onboarding (P3.2 — TASK-005 §5.2)
# ---------------------------------------------------------------------------

@app.get("/api/v1/onboarding/state")
def onboarding_state():
    """Return the persisted wizard state (used by the first-run UI)."""
    from . import app_state as _app_state

    state = _app_state.load_state()
    return {
        "needs_onboarding": _app_state.needs_onboarding(state=state),
        "current_version": _app_state.CURRENT_ONBOARDING_VERSION,
        "steps": _app_state.ONBOARDING_STEPS,
        "state": state,
    }


@app.post("/api/v1/onboarding/state")
def onboarding_state_patch(payload: dict):
    """Patch arbitrary top-level state fields (used for preferences / marking steps).

    Body (any of):
        {"preferences": {"retention_days": 14}}
        {"onboarding_steps": {"certificate": {"status": "done"}}}
    """
    from . import app_state as _app_state

    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")
    new_state = _app_state.update_state(payload)
    log_event(logger, "onboarding.state_patched", keys=sorted(payload.keys()))
    return {"ok": True, "state": new_state}


@app.post("/api/v1/onboarding/step")
def onboarding_step(payload: dict):
    """Mark a single onboarding step complete / skipped / failed.

    Body::

        {"step": "certificate", "status": "done"}
    """
    from . import app_state as _app_state

    step = (payload or {}).get("step")
    status = (payload or {}).get("status", "done")
    if not step:
        raise HTTPException(400, "step is required")
    try:
        new_state = _app_state.mark_step(step, status=status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    log_event(logger, "onboarding.step", step=step, status=status)
    return {"ok": True, "state": new_state}


@app.post("/api/v1/onboarding/complete")
def onboarding_complete():
    """Mark the wizard as fully completed at the current version."""
    from . import app_state as _app_state

    new_state = _app_state.complete_onboarding()
    log_event(logger, "onboarding.completed",
              version=_app_state.CURRENT_ONBOARDING_VERSION)
    return {"ok": True, "state": new_state}


@app.post("/api/v1/onboarding/reset")
def onboarding_reset():
    """Dev helper: clear wizard progress so the UI reappears on next launch."""
    from . import app_state as _app_state

    new_state = _app_state.reset_onboarding()
    log_event(logger, "onboarding.reset")
    return {"ok": True, "state": new_state}


# ---------------------------------------------------------------------------
# Updates (P3.6 — TASK-005 §5.6)
# ---------------------------------------------------------------------------

@app.get("/api/v1/updates/check")
def updates_check(
    force: bool = Query(False, description="Bypass the in-process cache."),
    manifest_url: Optional[str] = Query(None, description="Override PCE_UPDATE_MANIFEST_URL."),
):
    """Fetch the release manifest and report whether a newer PCE is available.

    Never raises: network failures are captured in the response's
    ``error`` field so the tray / dashboard can show a readable message.
    """
    from . import updates as _updates

    result = _updates.check_for_updates(
        manifest_url=manifest_url,
        force_refresh=bool(force),
    )
    log_event(
        logger, "updates.check.served",
        update_available=result.update_available,
        current=result.current_version, latest=result.latest_version,
        error=result.error,
    )
    return result.as_dict()


# ---------------------------------------------------------------------------
# Diagnose (P3.7 — TASK-005 §5.7)
# ---------------------------------------------------------------------------

@app.get("/api/v1/diagnose")
def diagnose_endpoint(include_logs: bool = Query(True, description="Include tail of recent log files.")):
    """Return a redacted diagnostics zip (OS / config / health / schema / logs).

    The bundle is built in memory and streamed back with a filename that
    embeds the PCE version and UTC timestamp so users can just save the
    download and attach it to a bug report.
    """
    import io as _io

    from . import diagnose as _diagnose

    data = _diagnose.collect_diagnostics_bytes(include_logs=bool(include_logs))
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    filename = f"pce-diag-{__version__}-{ts}.zip"
    log_event(
        logger, "diagnose.served",
        size_bytes=len(data), include_logs=bool(include_logs),
    )
    return StreamingResponse(
        _io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(data)),
            "X-PCE-Diagnose-Version": __version__,
        },
    )


# ---------------------------------------------------------------------------
# Export / Import (P1 — TASK-003 §5.3)
# ---------------------------------------------------------------------------

@app.get("/api/v1/export/summary")
def export_summary_endpoint(
    since: Optional[float] = Query(None, description="Unix epoch seconds lower bound (inclusive)."),
    until: Optional[float] = Query(None, description="Unix epoch seconds upper bound (inclusive)."),
):
    """Cheap preflight before calling the streaming export endpoints."""
    return export_summary(since=since, until=until)


@app.get("/api/v1/export")
def export_endpoint(
    format: str = Query("otlp", pattern="^(otlp|json)$", description="otlp (JSONL spans) or json (envelope)"),
    since: Optional[float] = Query(None),
    until: Optional[float] = Query(None),
    include_raw: bool = Query(False, description="Only honoured by format=json."),
):
    """Stream the normalised data in an interoperable format.

    - ``format=otlp`` → ``application/x-ndjson``: one OpenInference-shaped
      span per line, ready for replay into any OTel-compatible backend.
    - ``format=json`` → ``application/json``: a single streaming envelope
      containing full session dicts plus messages (and optionally the
      originating raw captures).
    """
    if format == "otlp":
        log_event(
            logger, "export.otlp_started",
            since=since, until=until,
        )
        body = stream_jsonl(iter_oi_spans(since=since, until=until))
        return StreamingResponse(
            body,
            media_type="application/x-ndjson",
            headers={"X-PCE-Export-Format": "otlp"},
        )

    log_event(
        logger, "export.json_started",
        since=since, until=until, include_raw=include_raw,
    )
    body = stream_json_envelope(
        since=since, until=until, include_raw=include_raw,
    )
    return StreamingResponse(
        body,
        media_type="application/json",
        headers={
            "X-PCE-Export-Format": "json",
            "X-PCE-Export-Schema-Version": str(EXPORT_SCHEMA_VERSION),
        },
    )


@app.post("/api/v1/import")
async def import_endpoint(request: Request):
    """Idempotent import of previously-exported PCE data.

    Accepts either:

    - ``Content-Type: application/x-ndjson`` — newline-delimited JSON
      records (OpenInference spans or PCE session envelopes).
    - ``Content-Type: application/json`` — a single envelope document as
      produced by ``GET /api/v1/export?format=json``.

    Returns a JSON summary describing what was created / matched / skipped.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    body_bytes = await request.body()
    if not body_bytes:
        raise HTTPException(status_code=400, detail="empty request body")

    if "ndjson" in content_type or "jsonl" in content_type:
        lines = body_bytes.splitlines()
        summary = import_jsonl(lines)
    else:
        try:
            doc = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")
        if isinstance(doc, dict) and "sessions" in doc:
            summary = import_document(doc)
        elif isinstance(doc, list):
            # Treat as a JSON array of records
            summary = import_jsonl(iter(json.dumps(r) for r in doc))
        else:
            raise HTTPException(
                status_code=400,
                detail="payload must be NDJSON, an envelope object, or an array of records",
            )

    log_event(
        logger, "import.received",
        sessions_created=summary["sessions_created"],
        messages_created=summary["messages_created"],
        errors=len(summary["errors"]),
    )
    return summary


# ---------------------------------------------------------------------------
# Retention (P1 — TASK-003 §5.4)
# ---------------------------------------------------------------------------

@app.get("/api/v1/retention")
def retention_status():
    """Report the current retention policy and last sweep output."""
    return {
        "days": RETENTION_DAYS,
        "max_raw_rows": RETENTION_MAX_ROWS,
        "interval_hours": RETENTION_INTERVAL_HOURS,
        "otel_enabled": _otel_is_enabled(),
        "otel_endpoint": OTEL_EXPORTER_OTLP_ENDPOINT or None,
    }


@app.post("/api/v1/retention/sweep")
def retention_sweep_endpoint(
    days: Optional[int] = Query(None, ge=0, description="Override PCE_RETENTION_DAYS for this sweep."),
    max_raw_rows: Optional[int] = Query(None, ge=0, description="Override PCE_RETENTION_MAX_ROWS for this sweep."),
):
    """Run a retention sweep right now. Returns the summary."""
    summary = sweep_once(days=days, max_raw_rows=max_raw_rows)
    return summary


# ---------------------------------------------------------------------------
# Cert wizard (P2 — TASK-004 §5.2)
# ---------------------------------------------------------------------------

@app.get("/api/v1/cert")
def cert_list():
    """List PCE-owned CA certificates currently installed in the host trust store."""
    platform = _cert_wizard.detect_platform()
    certs = _cert_wizard.list_ca()
    return {
        "platform": platform.value,
        "default_ca_path": str(_cert_wizard.default_ca_path()),
        "installed": [
            {
                "subject": c.subject,
                "thumbprint_sha1": c.thumbprint_sha1,
                "store_id": c.store_id,
                "source_path": c.source_path,
                "not_after_iso": c.not_after_iso,
            }
            for c in certs
        ],
    }


@app.post("/api/v1/cert/install")
def cert_install(payload: Optional[dict] = None):
    """Install the mitmproxy CA into the host trust store.

    Body (optional)::

        {"cert_path": "/abs/path/to/ca.pem", "dry_run": false}
    """
    payload = payload or {}
    cert_path = payload.get("cert_path")
    dry_run = bool(payload.get("dry_run", False))
    res = _cert_wizard.install_ca(
        Path(cert_path) if cert_path else None,
        dry_run=dry_run,
    )
    log_event(
        logger, "cert.install.result",
        ok=res.ok, needs_elevation=res.needs_elevation,
        platform=res.platform.value, dry_run=res.dry_run,
    )
    return res.as_dict()


@app.post("/api/v1/cert/uninstall")
def cert_uninstall(payload: dict):
    """Uninstall a PCE-managed CA by SHA-1 thumbprint.

    Body::

        {"thumbprint_sha1": "…40 hex…", "dry_run": false}
    """
    thumb = (payload or {}).get("thumbprint_sha1") or ""
    dry_run = bool((payload or {}).get("dry_run", False))
    if not thumb:
        raise HTTPException(400, "thumbprint_sha1 is required")
    res = _cert_wizard.uninstall_ca(thumb, dry_run=dry_run)
    log_event(
        logger, "cert.uninstall.result",
        ok=res.ok, needs_elevation=res.needs_elevation,
        platform=res.platform.value, dry_run=res.dry_run,
    )
    return res.as_dict()


@app.post("/api/v1/cert/export")
def cert_export(payload: dict):
    """Copy the mitmproxy CA to a user-chosen path.

    Body::

        {"dest": "C:/Users/me/Desktop/pce-ca.pem"}
    """
    dest = (payload or {}).get("dest") or ""
    if not dest:
        raise HTTPException(400, "dest is required")
    res = _cert_wizard.export_ca(Path(dest))
    log_event(logger, "cert.export.result",
              ok=res.ok, dest=dest, platform=res.platform.value)
    return res.as_dict()


@app.post("/api/v1/cert/regenerate")
def cert_regenerate(payload: Optional[dict] = None):
    """Clear the mitmproxy CA files so the next mitmdump run regenerates them."""
    dry_run = bool((payload or {}).get("dry_run", False))
    res = _cert_wizard.regenerate_ca(dry_run=dry_run)
    log_event(logger, "cert.regenerate.result",
              ok=res.ok, dry_run=res.dry_run, platform=res.platform.value)
    return res.as_dict()


# ---------------------------------------------------------------------------
# System proxy toggle (P2 — TASK-004 §5.3)
# ---------------------------------------------------------------------------

@app.get("/api/v1/proxy")
def proxy_state():
    """Report the host's current system proxy state."""
    state = _proxy_toggle.get_proxy_state()
    return state.as_dict()


@app.post("/api/v1/proxy/enable")
def proxy_enable(payload: Optional[dict] = None):
    """Point the OS at PCE's mitmproxy listener.

    Body (optional)::

        {"host": "127.0.0.1", "port": 8080,
         "bypass": ["127.0.0.1", "localhost", ...],
         "dry_run": false}
    """
    payload = payload or {}
    host = payload.get("host") or PROXY_LISTEN_HOST
    # Use explicit None-check so that explicit ``port=0`` flows through to
    # the validator (which rejects it) instead of being silently replaced.
    port_raw = payload.get("port")
    port = int(port_raw) if port_raw is not None else PROXY_LISTEN_PORT
    bypass = payload.get("bypass")
    dry_run = bool(payload.get("dry_run", False))
    res = _proxy_toggle.enable_system_proxy(
        host=host, port=port, bypass=bypass, dry_run=dry_run,
    )
    log_event(
        logger, "proxy.enable.result",
        ok=res.ok, needs_elevation=res.needs_elevation,
        host=host, port=port, dry_run=res.dry_run,
    )
    return res.as_dict()


@app.post("/api/v1/proxy/disable")
def proxy_disable(payload: Optional[dict] = None):
    """Clear the OS system proxy."""
    dry_run = bool((payload or {}).get("dry_run", False))
    res = _proxy_toggle.disable_system_proxy(dry_run=dry_run)
    log_event(logger, "proxy.disable.result",
              ok=res.ok, dry_run=res.dry_run)
    return res.as_dict()


# ---------------------------------------------------------------------------
# Supervisor (P2 — TASK-004 §5.5)
# ---------------------------------------------------------------------------

@app.get("/api/v1/supervisor")
def supervisor_status():
    """Report the status of every process the server is supervising."""
    sup: Optional[Supervisor] = getattr(app.state, "supervisor", None)
    if sup is None:
        return {"running": False, "started_at": None, "processes": []}
    return sup.get_status().as_dict()


@app.post("/api/v1/supervisor/{name}/restart")
async def supervisor_restart(name: str):
    """Force-restart one managed process (stop + auto-respawn)."""
    sup: Optional[Supervisor] = getattr(app.state, "supervisor", None)
    if sup is None:
        raise HTTPException(409, "no supervisor is running in this server")
    ok = await sup.restart(name)
    if not ok:
        raise HTTPException(404, f"no managed process named {name!r}")
    log_event(logger, "supervisor.restart_requested", name=name)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------------
# LiteLLM SDK bridge (P2 — TASK-004 §5.4)
# ---------------------------------------------------------------------------

@app.get("/api/v1/sdk/litellm")
def sdk_litellm_status():
    """Report whether the LiteLLM bridge is running."""
    bridge: Optional[LiteLLMBridge] = getattr(app.state, "litellm_bridge", None)
    if bridge is None:
        return {
            "running": False, "port": None, "config_path": None,
            "litellm_available": LITELLM_AVAILABLE,
        }
    return bridge.get_status()


@app.post("/api/v1/sdk/litellm/start")
async def sdk_litellm_start(payload: Optional[dict] = None):
    """Spawn the PCE-configured LiteLLM proxy subprocess.

    Body (optional)::

        {"port": 9900}
    """
    payload = payload or {}
    port_raw = payload.get("port")
    port = int(port_raw) if port_raw is not None else 9900
    bridge: Optional[LiteLLMBridge] = getattr(app.state, "litellm_bridge", None)
    if bridge is not None and bridge.get_status().get("running"):
        return {"ok": False, "error": "already_running", **bridge.get_status()}
    ingest_url = f"http://{INGEST_HOST}:{INGEST_PORT}/api/v1/captures"
    bridge = LiteLLMBridge(port=port, pce_ingest_url=ingest_url)
    result = await bridge.start()
    if result.get("ok"):
        app.state.litellm_bridge = bridge
    log_event(logger, "litellm.start.result",
              ok=bool(result.get("ok")), port=port,
              litellm_available=LITELLM_AVAILABLE)
    return result


@app.post("/api/v1/sdk/litellm/stop")
async def sdk_litellm_stop():
    """Stop the LiteLLM proxy subprocess (if running)."""
    bridge: Optional[LiteLLMBridge] = getattr(app.state, "litellm_bridge", None)
    if bridge is None:
        return {"ok": True, "running": False, "message": "bridge not started"}
    result = await bridge.stop()
    app.state.litellm_bridge = None
    log_event(logger, "litellm.stop.result", ok=True)
    return result


# ---------------------------------------------------------------------------
# Phoenix integration (P3.5 — TASK-005 §5.5)
# ---------------------------------------------------------------------------

def _get_or_create_phoenix_manager(payload: Optional[dict] = None):
    """Lazily attach a :class:`PhoenixManager` to ``app.state``."""
    from .phoenix_integration import PhoenixManager, DEFAULT_PHOENIX_HOST, DEFAULT_PHOENIX_PORT

    mgr = getattr(app.state, "phoenix_manager", None)
    payload = payload or {}
    host = payload.get("host") or DEFAULT_PHOENIX_HOST
    port_raw = payload.get("port")
    port = int(port_raw) if port_raw is not None else DEFAULT_PHOENIX_PORT
    if mgr is None or mgr.host != host or mgr.port != port:
        mgr = PhoenixManager(host=host, port=port)
        app.state.phoenix_manager = mgr
    return mgr


@app.get("/api/v1/phoenix")
def phoenix_status():
    """Report whether Phoenix is available / running and how OTLP is wired."""
    from .phoenix_integration import PHOENIX_AVAILABLE, DEFAULT_PHOENIX_HOST, DEFAULT_PHOENIX_PORT

    mgr = getattr(app.state, "phoenix_manager", None)
    if mgr is None:
        return {
            "running": False,
            "phoenix_available": PHOENIX_AVAILABLE,
            "host": DEFAULT_PHOENIX_HOST,
            "port": DEFAULT_PHOENIX_PORT,
            "otlp_wired": False,
            "ui_url": f"http://{DEFAULT_PHOENIX_HOST}:{DEFAULT_PHOENIX_PORT}/",
            "otlp_endpoint": f"http://{DEFAULT_PHOENIX_HOST}:{DEFAULT_PHOENIX_PORT}/v1/traces",
            "pid": None,
            "last_error": None,
        }
    return mgr.get_status()


@app.post("/api/v1/phoenix/start")
async def phoenix_start(payload: Optional[dict] = None):
    """Spawn Phoenix (if installed) and auto-wire PCE's OTLP exporter at it.

    Body (optional)::

        {"host": "127.0.0.1", "port": 6006}
    """
    mgr = _get_or_create_phoenix_manager(payload)
    result = await mgr.start()
    log_event(
        logger, "phoenix.start.result",
        ok=bool(result.get("ok")),
        running=bool(result.get("running")),
        port=mgr.port,
        otlp_wired=bool(result.get("otlp_wired")),
    )
    return result


@app.post("/api/v1/phoenix/stop")
async def phoenix_stop():
    """Stop the Phoenix subprocess (if we spawned one) and unwire OTLP."""
    mgr = getattr(app.state, "phoenix_manager", None)
    if mgr is None:
        return {"ok": True, "running": False, "message": "phoenix not started"}
    result = await mgr.stop()
    log_event(logger, "phoenix.stop.result", ok=True)
    return result


# ---------------------------------------------------------------------------
# Dev: Reset baseline
# ---------------------------------------------------------------------------

@app.post("/api/v1/dev/reset")
def dev_reset():
    """DEV ONLY: Delete all captures, sessions, and messages to reset to baseline."""
    result = reset_all_data()
    logger.warning("DEV RESET triggered via API: %s", result)
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@app.post("/api/v1/captures", response_model=CaptureOut, status_code=201)
def ingest_capture(payload: CaptureIn):
    """Accept a capture from any frontend (proxy, browser ext, MCP, etc.)."""
    source_id = _SOURCE_MAP.get(payload.source_type, SOURCE_PROXY)
    pair_id = payload.pair_id or new_pair_id()

    meta_json = json.dumps(payload.meta, ensure_ascii=False) if payload.meta else None

    capture_id = insert_capture(
        direction=payload.direction,
        pair_id=pair_id,
        host=payload.host or "",
        path=payload.path or "",
        method=payload.method or "",
        provider=payload.provider,
        model_name=payload.model_name,
        status_code=payload.status_code,
        latency_ms=payload.latency_ms,
        headers_redacted_json=payload.headers_json,
        body_text_or_json=payload.body_json,
        body_format=payload.body_format,
        error=payload.error,
        session_hint=payload.session_hint,
        meta_json=meta_json,
        schema_version=payload.schema_version,
        source_id=source_id,
    )

    if capture_id is None:
        # Record so the health API can surface a non-zero failures_last_24h
        # for this source instead of silently swallowing the failure.
        record_pipeline_error(
            "ingest",
            "insert_capture returned None",
            source_id=source_id,
            pair_id=pair_id,
            details={
                "source_type": payload.source_type,
                "direction": payload.direction,
                "host": payload.host,
                "provider": payload.provider,
            },
        )
        log_event(
            logger, "capture.ingest_failed", level=logging.ERROR,
            source_type=payload.source_type, direction=payload.direction,
            host=payload.host, provider=payload.provider,
        )
        raise HTTPException(status_code=500, detail="Failed to insert capture")

    log_event(
        logger, "capture.ingested",
        capture_id=capture_id[:8], pair_id=pair_id[:8],
        source_type=payload.source_type, direction=payload.direction,
        host=payload.host, provider=payload.provider,
        model_name=payload.model_name,
    )

    # Auto-normalize when a response completes a pair
    if payload.direction == "response":
        try:
            try_normalize_pair(pair_id, source_id=source_id, created_via=payload.source_type)
        except Exception as exc:
            logger.exception("Auto-normalization failed for pair %s – non-fatal", pair_id[:8])
            record_pipeline_error(
                "normalize", f"try_normalize_pair: {type(exc).__name__}: {exc}",
                source_id=source_id, pair_id=pair_id,
                details={"direction": "response", "host": payload.host},
            )

    # Normalize conversation captures (e.g. from browser extension DOM extraction)
    elif payload.direction == "conversation":
        try:
            from .db import query_by_pair
            rows = query_by_pair(pair_id)
            if rows:
                normalize_conversation(
                    rows[0], source_id=source_id, created_via=payload.source_type,
                )
        except Exception as exc:
            logger.exception("Conversation normalization failed for %s – non-fatal", pair_id[:8])
            record_pipeline_error(
                "normalize", f"normalize_conversation: {type(exc).__name__}: {exc}",
                source_id=source_id, pair_id=pair_id,
                details={"direction": "conversation", "host": payload.host},
            )

    # Normalize network-intercepted captures (browser extension fetch/XHR/WS interception)
    elif payload.direction == "network_intercept":
        try:
            from .db import query_by_pair
            rows = query_by_pair(pair_id)
            if rows:
                normalize_conversation(
                    rows[0], source_id=source_id, created_via="browser_extension_network",
                )
        except Exception as exc:
            logger.exception("Network intercept normalization failed for %s – non-fatal", pair_id[:8])
            record_pipeline_error(
                "normalize", f"normalize_conversation(net): {type(exc).__name__}: {exc}",
                source_id=source_id, pair_id=pair_id,
                details={"direction": "network_intercept", "host": payload.host},
            )

    return CaptureOut(id=capture_id, pair_id=pair_id, source_id=source_id)


# ---------------------------------------------------------------------------
# Query: raw captures
# ---------------------------------------------------------------------------

@app.get("/api/v1/captures", response_model=list[CaptureRecord])
def list_captures(
    last: int = Query(20, ge=1, le=500),
    provider: Optional[str] = None,
    source_type: Optional[str] = None,
    host: Optional[str] = None,
):
    """List recent captures with optional filters."""
    rows = query_captures(last=last, provider=provider, source_type=source_type, host=host)
    return rows


@app.get("/api/v1/captures/pair/{pair_id}", response_model=list[CaptureRecord])
def get_pair(pair_id: str):
    """Get a request/response pair by pair_id."""
    rows = query_by_pair(pair_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Pair not found")
    return rows


# ---------------------------------------------------------------------------
# Query: stats
# ---------------------------------------------------------------------------

@app.get("/api/v1/stats", response_model=StatsOut)
def stats():
    return get_stats()


# ---------------------------------------------------------------------------
# Capture health (self-awareness)
# ---------------------------------------------------------------------------

@app.get("/api/v1/capture-health")
def capture_health():
    """Return per-channel capture health with time-windowed activity.

    Used by the dashboard to display a live health panel showing which
    capture channels are active, stale, or silent.
    """
    import time as _time

    health = get_capture_health()
    now = health["timestamp"]

    def _channel_status(source_id: str, label: str) -> dict:
        """Build a health summary for one logical capture channel."""
        src = health["sources"].get(source_id, {})
        last = src.get("last_seen")
        c5m = src.get("count_5m", 0)
        c1h = src.get("count_1h", 0)
        c24h = src.get("count_24h", 0)
        total = src.get("total", 0)

        if c5m > 0:
            status = "active"
        elif c1h > 0:
            status = "idle"
        elif c24h > 0:
            status = "stale"
        elif total > 0:
            status = "inactive"
        else:
            status = "never"

        ago = round(now - last, 1) if last else None
        return {
            "id": source_id,
            "label": label,
            "status": status,
            "last_seen": last,
            "last_seen_ago_s": ago,
            "count_5m": c5m,
            "count_1h": c1h,
            "count_24h": c24h,
            "total": total,
        }

    # ── Map direction-based channels (browser ext has two sub-channels) ─
    ext_src = health["sources"].get(SOURCE_BROWSER_EXT, {})
    directions = health.get("directions", {})

    # L1 (DOM) = direction=conversation from browser_ext
    # L3 (Network) = direction=network_intercept from browser_ext
    # We approximate by using direction counts as sub-channel indicators
    dom_dir = directions.get("conversation", {})
    net_dir = directions.get("network_intercept", {})

    channels = [
        _channel_status(SOURCE_BROWSER_EXT, "Browser Extension"),
        _channel_status(SOURCE_PROXY, "HTTPS Proxy"),
        _channel_status(SOURCE_MCP, "MCP Server"),
    ]

    # Add sub-channel detail for browser extension
    channels[0]["sub_channels"] = {
        "dom_extraction": {
            "label": "DOM Extraction",
            "count_5m": dom_dir.get("count_5m", 0),
            "count_1h": dom_dir.get("count_1h", 0),
            "count_24h": dom_dir.get("count_24h", 0),
            "last_seen": dom_dir.get("last_seen"),
            "status": "active" if dom_dir.get("count_5m", 0) > 0 else (
                "idle" if dom_dir.get("count_1h", 0) > 0 else (
                    "stale" if dom_dir.get("count_24h", 0) > 0 else "inactive")),
        },
        "network_intercept": {
            "label": "Network Interception",
            "count_5m": net_dir.get("count_5m", 0),
            "count_1h": net_dir.get("count_1h", 0),
            "count_24h": net_dir.get("count_24h", 0),
            "last_seen": net_dir.get("last_seen"),
            "status": "active" if net_dir.get("count_5m", 0) > 0 else (
                "idle" if net_dir.get("count_1h", 0) > 0 else (
                    "stale" if net_dir.get("count_24h", 0) > 0 else "inactive")),
        },
    }

    # Local hook: aggregate all local-hook-* sources
    local_sources = {k: v for k, v in health["sources"].items() if k.startswith("local-hook")}
    local_total = sum(v.get("total", 0) for v in local_sources.values())
    local_5m = sum(v.get("count_5m", 0) for v in local_sources.values())
    local_1h = sum(v.get("count_1h", 0) for v in local_sources.values())
    local_24h = sum(v.get("count_24h", 0) for v in local_sources.values())
    local_last = max((v.get("last_seen", 0) for v in local_sources.values()), default=None)
    if local_last and local_last <= 0:
        local_last = None

    local_status = "active" if local_5m > 0 else (
        "idle" if local_1h > 0 else (
            "stale" if local_24h > 0 else (
                "inactive" if local_total > 0 else "never")))

    channels.append({
        "id": "local_hook",
        "label": "Local Model Hook",
        "status": local_status,
        "last_seen": local_last,
        "last_seen_ago_s": round(now - local_last, 1) if local_last else None,
        "count_5m": local_5m,
        "count_1h": local_1h,
        "count_24h": local_24h,
        "total": local_total,
    })

    # Clipboard
    clip_sources = {k: v for k, v in health["sources"].items()
                    if "clipboard" in k or v.get("source_type") == "clipboard"}
    clip_total = sum(v.get("total", 0) for v in clip_sources.values())
    clip_5m = sum(v.get("count_5m", 0) for v in clip_sources.values())
    clip_1h = sum(v.get("count_1h", 0) for v in clip_sources.values())
    clip_24h = sum(v.get("count_24h", 0) for v in clip_sources.values())
    clip_last = max((v.get("last_seen", 0) for v in clip_sources.values()), default=None)
    if clip_last and clip_last <= 0:
        clip_last = None

    clip_status = "active" if clip_5m > 0 else (
        "idle" if clip_1h > 0 else (
            "stale" if clip_24h > 0 else (
                "inactive" if clip_total > 0 else "never")))

    channels.append({
        "id": "clipboard",
        "label": "Clipboard Monitor",
        "status": clip_status,
        "last_seen": clip_last,
        "last_seen_ago_s": round(now - clip_last, 1) if clip_last else None,
        "count_5m": clip_5m,
        "count_1h": clip_1h,
        "count_24h": clip_24h,
        "total": clip_total,
    })

    # ── Overall health score ───────────────────────────────────────────
    active_count = sum(1 for c in channels if c["status"] == "active")
    configured_count = sum(1 for c in channels if c["status"] != "never")

    return {
        "timestamp": now,
        "channels": channels,
        "recent_providers": health["recent_providers"],
        "normalization": health["normalization"],
        "summary": {
            "active_channels": active_count,
            "configured_channels": configured_count,
            "total_channels": len(channels),
        },
    }


# ---------------------------------------------------------------------------
# Query: sessions
# ---------------------------------------------------------------------------

@app.get("/api/v1/sessions")
def list_sessions(
    last: int = Query(20, ge=1, le=500),
    provider: Optional[str] = None,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    min_messages: Optional[int] = None,
    q: Optional[str] = None,
    favorited: Optional[bool] = None,
):
    return query_sessions(
        last=last, provider=provider, language=language,
        topic=topic, since=since, until=until,
        min_messages=min_messages, title_search=q,
        favorited_only=bool(favorited) if favorited is not None else False,
    )


@app.put("/api/v1/sessions/{session_id}/favorite")
def toggle_favorite(session_id: str, favorited: bool = True):
    """Set or clear the favorite flag on a session."""
    ok = set_session_favorite(session_id, favorited=favorited)
    if not ok:
        raise HTTPException(500, "Failed to update favorite")
    return {"ok": True, "session_id": session_id, "favorited": favorited}


@app.get("/api/v1/sessions/{session_id}/messages", response_model=list[MessageRecord])
def get_session_messages(session_id: str):
    msgs = query_messages(session_id)
    if not msgs:
        raise HTTPException(status_code=404, detail="Session not found or empty")
    return msgs


# ---------------------------------------------------------------------------
# Search (FTS + semantic + hybrid — P4.1)
# ---------------------------------------------------------------------------

@app.get("/api/v1/search")
def search(
    q: str = Query(..., min_length=1, description="Search query"),
    provider: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    mode: str = Query(
        "fts",
        pattern="^(fts|semantic|hybrid)$",
        description=(
            "fts = FTS5 / LIKE (default, always works). "
            "semantic = vector KNN (requires an embeddings backend). "
            "hybrid = RRF(fts, semantic). "
            "When a semantic backend is unavailable the server silently "
            "falls back to fts so the UI is never empty."
        ),
    ),
):
    """Unified message search.

    Delegates to :mod:`pce_core.semantic_search` which knows how to
    combine FTS and embedding-based ranking.
    """
    from .semantic_search import search as _search
    return _search(q, mode=mode, provider=provider, limit=limit)


@app.get("/api/v1/embeddings/status")
def embeddings_status_endpoint():
    """Report the configured embedding backend + coverage stats."""
    from .semantic_search import embeddings_status
    return embeddings_status()


@app.post("/api/v1/embeddings/backfill")
def embeddings_backfill_endpoint(
    limit: int = Query(500, ge=1, le=5000,
                       description="Maximum messages to embed in this call."),
):
    """Embed up to ``limit`` messages that don't yet have a vector.

    Returns immediately once the batch finishes. Safe to call repeatedly
    until ``remaining == 0``.
    """
    from .semantic_search import backfill_embeddings
    report = backfill_embeddings(limit=limit)
    log_event(
        logger, "embeddings.backfill",
        backend=report.get("backend"), model=report.get("model"),
        backfilled=report.get("backfilled"), remaining=report.get("remaining"),
        ok=report.get("ok"),
    )
    return report


# ---------------------------------------------------------------------------
# Analytics (DuckDB — P4.2)
# ---------------------------------------------------------------------------

@app.get("/api/v1/analytics/status")
def analytics_status_endpoint():
    """Report whether the DuckDB analytics backend is installed."""
    from . import analytics
    return analytics.status()


@app.get("/api/v1/analytics/timeseries")
def analytics_timeseries_endpoint(
    by: str = Query("day", pattern="^(hour|day|week|month)$"),
    days: int = Query(30, ge=1, le=365),
    provider: Optional[str] = Query(None),
):
    """Captures / sessions / messages per time bucket."""
    from . import analytics
    result = analytics.timeseries(by=by, days=days, provider=provider)
    if not result.get("ok") and result.get("error") == "duckdb_not_installed":
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/api/v1/analytics/top_models")
def analytics_top_models_endpoint(
    limit: int = Query(10, ge=1, le=1000),
    days: int = Query(30, ge=1, le=365),
):
    from . import analytics
    result = analytics.top_models(limit=limit, days=days)
    if not result.get("ok") and result.get("error") == "duckdb_not_installed":
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/api/v1/analytics/top_hosts")
def analytics_top_hosts_endpoint(
    limit: int = Query(10, ge=1, le=1000),
    days: int = Query(30, ge=1, le=365),
):
    from . import analytics
    result = analytics.top_hosts(limit=limit, days=days)
    if not result.get("ok") and result.get("error") == "duckdb_not_installed":
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/api/v1/analytics/token_usage")
def analytics_token_usage_endpoint(
    by: str = Query("day", pattern="^(hour|day|week|month)$"),
    days: int = Query(30, ge=1, le=365),
):
    from . import analytics
    result = analytics.token_usage(by=by, days=days)
    if not result.get("ok") and result.get("error") == "duckdb_not_installed":
        raise HTTPException(status_code=503, detail=result)
    return result


# ---------------------------------------------------------------------------
# CDP embedded-browser capture (P4.4)
# ---------------------------------------------------------------------------

@app.get("/api/v1/cdp/status")
def cdp_status_endpoint():
    """Report Playwright availability + current CDP driver state."""
    from . import cdp
    return cdp.status()


@app.post("/api/v1/cdp/start")
def cdp_start_endpoint(
    start_url: str = Query("https://chat.openai.com"),
    headless: bool = Query(False),
):
    """Launch the singleton :class:`CDPDriver` with default patterns.

    Returns 503 when Playwright isn't installed so the UI can surface an
    actionable message. Safe to call repeatedly; a running driver is a
    no-op (the response just reflects the current state).
    """
    from . import cdp
    result = cdp.start_default(start_url=start_url, headless=headless)
    if not result.get("ok"):
        code = 503 if result.get("error") == "playwright_not_installed" else 500
        raise HTTPException(status_code=code, detail=result)
    return result


@app.post("/api/v1/cdp/stop")
def cdp_stop_endpoint():
    """Stop the singleton CDP driver."""
    from . import cdp
    return cdp.stop_default()


# ---------------------------------------------------------------------------
# Mobile onboarding (P4.5)
# ---------------------------------------------------------------------------

@app.get("/api/v1/mobile_wizard/info")
def mobile_wizard_info_endpoint(
    ip: Optional[str] = Query(None, description="Override auto-detected LAN IP."),
    port: Optional[int] = Query(None, ge=1, le=65535),
):
    """Return the JSON payload a phone needs to configure HTTP proxy +
    download the mitmproxy CA cert.
    """
    from . import mobile_wizard
    return mobile_wizard.get_setup_info(lan_ip=ip, port=port)


@app.get("/api/v1/mobile_wizard/qr.png")
def mobile_wizard_qr_png_endpoint(
    ip: Optional[str] = Query(None),
    port: Optional[int] = Query(None, ge=1, le=65535),
):
    """Render the setup QR as a PNG. 503 when ``qrcode``/``pillow`` missing."""
    from . import mobile_wizard
    info = mobile_wizard.get_setup_info(lan_ip=ip, port=port)
    out = mobile_wizard.render_qr_png(info)
    if not out.get("ok"):
        raise HTTPException(status_code=503, detail=out)
    return Response(
        content=out["png_bytes"],
        media_type=out["content_type"],
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/mobile_wizard/qr.txt", response_class=PlainTextResponse)
def mobile_wizard_qr_ascii_endpoint(
    ip: Optional[str] = Query(None),
    port: Optional[int] = Query(None, ge=1, le=65535),
):
    """ASCII-art QR suitable for a terminal. Never fails — renders a
    plain-text banner when ``qrcode`` isn't installed.
    """
    from . import mobile_wizard
    info = mobile_wizard.get_setup_info(lan_ip=ip, port=port)
    return mobile_wizard.render_qr_ascii(info)


@app.get("/api/v1/analytics/export.parquet")
def analytics_export_parquet_endpoint(
    table: str = Query(..., pattern="^(raw_captures|sessions|messages|message_embeddings)$"),
    days: Optional[int] = Query(None, ge=1, le=365),
):
    """Dump a whitelisted table into Parquet and stream it back.

    The file is materialised under ``tempfile.gettempdir()`` so the
    response can ``FileResponse`` it without holding the whole dataset
    in memory. Client is expected to save the bytes locally.
    """
    from fastapi.responses import FileResponse
    from . import analytics
    result = analytics.export_parquet(table=table, days=days)
    if not result.get("ok"):
        code = 503 if result.get("error") == "duckdb_not_installed" else 500
        raise HTTPException(status_code=code, detail=result)

    path = Path(result["path"])
    stem = f"pce-{table}-{int(time.time())}.parquet"
    log_event(
        logger, "analytics.export.served",
        table=table, rows=result.get("rows"), size_bytes=result.get("size_bytes"),
    )
    return FileResponse(
        path=str(path),
        filename=stem,
        media_type="application/vnd.apache.parquet",
    )


# ---------------------------------------------------------------------------
# Data Export
# ---------------------------------------------------------------------------

@app.get("/api/v1/export/session/{session_id}")
def export_session(
    session_id: str,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
):
    """Export a single session as Markdown or JSON."""
    from .exporter import export_session_markdown, export_session_json

    if format == "markdown":
        md = export_session_markdown(session_id)
        if not md:
            raise HTTPException(status_code=404, detail="Session not found or empty")
        return Response(content=md, media_type="text/markdown; charset=utf-8")
    else:
        data = export_session_json(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found or empty")
        return data


@app.get("/api/v1/export/sessions")
def export_sessions_bulk(
    format: str = Query("jsonl", pattern="^(jsonl)$"),
    provider: Optional[str] = None,
    since: Optional[float] = None,
):
    """Batch export sessions as JSONL (streaming)."""
    from .exporter import export_sessions_jsonl

    def generate():
        yield from export_sessions_jsonl(provider=provider, since=since)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Snippets API (text selection capture)
# ---------------------------------------------------------------------------

@app.post("/api/v1/snippets", status_code=201)
def create_snippet(payload: SnippetIn):
    """Save a text snippet collected from a covered site."""
    snippet_id = insert_snippet(
        content_text=payload.content_text,
        source_url=payload.source_url,
        source_domain=payload.source_domain,
        provider=payload.provider,
        category=payload.category,
        note=payload.note,
    )
    if not snippet_id:
        raise HTTPException(500, "Failed to save snippet")
    logger.info("snippet saved: %s (%s, %s)", snippet_id[:8], payload.category, payload.source_domain or "unknown")
    return {"ok": True, "id": snippet_id}


@app.get("/api/v1/snippets", response_model=list[SnippetRecord])
def list_snippets(
    last: int = Query(50, ge=1, le=500),
    category: Optional[str] = None,
    domain: Optional[str] = None,
    favorited: Optional[bool] = None,
    q: Optional[str] = None,
):
    """List snippets with optional filters."""
    return query_snippets(
        last=last, category=category, domain=domain,
        favorited_only=bool(favorited) if favorited is not None else False,
        q=q,
    )


@app.get("/api/v1/snippets/categories")
def list_snippet_categories():
    """Return all snippet categories with counts."""
    return get_snippet_categories()


@app.get("/api/v1/snippets/{snippet_id}", response_model=SnippetRecord)
def get_snippet_by_id(snippet_id: str):
    """Get a single snippet."""
    s = get_snippet(snippet_id)
    if not s:
        raise HTTPException(404, "Snippet not found")
    return s


@app.put("/api/v1/snippets/{snippet_id}")
def update_snippet_endpoint(snippet_id: str, payload: dict):
    """Update snippet category, note, or favorited status."""
    ok = update_snippet(
        snippet_id,
        category=payload.get("category"),
        note=payload.get("note"),
        favorited=payload.get("favorited"),
    )
    if not ok:
        raise HTTPException(500, "Failed to update snippet")
    return {"ok": True, "id": snippet_id}


@app.delete("/api/v1/snippets/{snippet_id}")
def delete_snippet_endpoint(snippet_id: str):
    """Delete a snippet."""
    ok = delete_snippet(snippet_id)
    if not ok:
        raise HTTPException(500, "Failed to delete snippet")
    return {"ok": True, "id": snippet_id}


# ---------------------------------------------------------------------------
# Domain management API (dynamic allowlist)
# ---------------------------------------------------------------------------

@app.get("/api/v1/domains")
def list_domains(include_inactive: bool = False):
    """List all custom domains and the static allowlist."""
    custom = list_custom_domains(include_inactive=include_inactive)
    return {
        "capture_mode": CAPTURE_MODE.value,
        "static_domains": sorted(ALLOWED_HOSTS),
        "custom_domains": custom,
    }


@app.post("/api/v1/domains", status_code=201)
def add_domain(payload: dict):
    """Add a domain to the custom allowlist.

    Body: {"domain": "example.com", "source": "user"|"browser_extension"|"smart_heuristic"}
    """
    domain = payload.get("domain", "").strip().lower()
    if not domain:
        raise HTTPException(400, "domain is required")
    source = payload.get("source", "user")
    confidence = payload.get("confidence")
    reason = payload.get("reason")
    ok = add_custom_domain(domain, source=source, confidence=confidence, reason=reason)
    if not ok:
        raise HTTPException(500, "Failed to add domain")
    return {"ok": True, "domain": domain}


@app.delete("/api/v1/domains/{domain}")
def delete_domain(domain: str):
    """Deactivate a custom domain."""
    ok = remove_custom_domain(domain)
    if not ok:
        raise HTTPException(500, "Failed to remove domain")
    return {"ok": True, "domain": domain}


@app.post("/api/v1/domains/refresh")
def refresh_domains():
    """Force-refresh the custom domains cache."""
    domains = refresh_custom_domains()
    return {"ok": True, "count": len(domains)}


# ---------------------------------------------------------------------------
# Service Management API (used by dashboard to control pce_app services)
# ---------------------------------------------------------------------------

# The service manager is injected by pce_app when running as desktop app.
# When running standalone (python -m pce_core), these endpoints return minimal info.
_service_manager = None


def set_service_manager(manager):
    """Called by pce_app to inject the service manager."""
    global _service_manager
    _service_manager = manager


@app.get("/api/v1/services")
def get_services():
    if _service_manager is None:
        return {
            "mode": "standalone",
            "services": {
                "core": {"name": "Core API Server", "status": "running", "port": INGEST_PORT},
            },
        }
    return {"mode": "desktop", "services": _service_manager.get_status()}


@app.post("/api/v1/services/{key}/start")
def start_service(key: str):
    if _service_manager is None:
        raise HTTPException(400, "Not running in desktop mode")
    if key == "core":
        _service_manager.start_core()
    elif key == "proxy":
        _service_manager.start_proxy()
    elif key == "local_hook":
        _service_manager.start_local_hook()
    elif key == "multi_hook":
        _service_manager.start_multi_hook()
    elif key == "clipboard":
        _service_manager.start_clipboard()
    else:
        raise HTTPException(404, f"Unknown service: {key}")
    return {"ok": True, "services": _service_manager.get_status()}


@app.post("/api/v1/services/{key}/stop")
def stop_service(key: str):
    if _service_manager is None:
        raise HTTPException(400, "Not running in desktop mode")
    if key == "core":
        raise HTTPException(400, "Cannot stop core server from within itself")
    _service_manager.stop_service(key)
    return {"ok": True, "services": _service_manager.get_status()}


# ---------------------------------------------------------------------------
# Capabilities overview (dashboard capture channels status)
# ---------------------------------------------------------------------------

@app.get("/api/v1/capabilities")
def get_capabilities():
    """Return comprehensive status for all capture channels."""
    import time as _time

    activity = get_source_activity()
    services = {}
    mode = "standalone"
    if _service_manager is not None:
        mode = "desktop"
        services = _service_manager.get_status()

    now = _time.time()

    def _source_info(source_id: str):
        a = activity.get(source_id, {})
        count = a.get("capture_count", 0)
        last = a.get("last_seen")
        # Consider "active" if data received in last 24 hours
        active = last is not None and (now - last) < 86400
        return {"capture_count": count, "last_seen": last, "active": active}

    def _svc_status(key: str):
        svc = services.get(key)
        if svc is None:
            return "not_configured"
        return svc.get("status", "stopped")

    proxy_info = _source_info(SOURCE_PROXY)
    ext_info = _source_info(SOURCE_BROWSER_EXT)
    mcp_info = _source_info(SOURCE_MCP)

    # Local hook sources use source_id pattern "local-hook-*"
    local_hook_count = sum(
        v.get("capture_count", 0) for k, v in activity.items()
        if k.startswith("local-hook")
    )
    local_hook_last = max(
        (v.get("last_seen", 0) for k, v in activity.items() if k.startswith("local-hook")),
        default=None,
    )
    local_active = local_hook_last is not None and local_hook_last > 0 and (now - local_hook_last) < 86400

    # Clipboard sources
    clip_count = sum(
        v.get("capture_count", 0) for k, v in activity.items()
        if "clipboard" in k or v.get("source_type") == "clipboard"
    )
    clip_last = max(
        (v.get("last_seen", 0) for k, v in activity.items()
         if "clipboard" in k or v.get("source_type") == "clipboard"),
        default=None,
    )
    clip_active = clip_last is not None and clip_last > 0 and (now - clip_last) < 86400

    capabilities = [
        {
            "id": "core",
            "name": "Core API Server",
            "icon": "server",
            "description": "FastAPI server providing Ingest & Query APIs, serving this dashboard.",
            "status": "connected",
            "service_status": _svc_status("core"),
            "capture_count": None,
            "last_seen": None,
            "port": INGEST_PORT,
            "setup": f"Running on http://{INGEST_HOST}:{INGEST_PORT}",
        },
        {
            "id": "proxy",
            "name": "Network Proxy",
            "icon": "shield",
            "description": "mitmproxy-based HTTPS proxy that captures all AI API traffic transparently.",
            "status": "connected" if (proxy_info["active"] or _svc_status("proxy") == "running") else "disconnected",
            "service_status": _svc_status("proxy"),
            "capture_count": proxy_info["capture_count"],
            "last_seen": proxy_info["last_seen"],
            "port": PROXY_LISTEN_PORT,
            "setup": f"Start with `python -m pce_app` or configure system proxy to http://{PROXY_LISTEN_HOST}:{PROXY_LISTEN_PORT}",
            "setup_detail": {
                "auto": f"Use PAC file: http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
                "manual": f"Set HTTP/HTTPS proxy to {PROXY_LISTEN_HOST}:{PROXY_LISTEN_PORT}",
            },
        },
        {
            "id": "browser_extension",
            "name": "Browser Extension",
            "icon": "globe",
            "description": "Chrome extension with site-specific extractors for ChatGPT, Claude, Gemini, etc.",
            "status": "connected" if ext_info["active"] else "disconnected",
            "service_status": "connected" if ext_info["active"] else "not_installed",
            "capture_count": ext_info["capture_count"],
            "last_seen": ext_info["last_seen"],
            "port": None,
            "setup": "Build with `pnpm --dir pce_browser_extension_wxt build`, then load the `pce_browser_extension_wxt/.output/chrome-mv3/` directory in Chrome → chrome://extensions",
            "setup_detail": {
                "step0": "Run `pnpm install && pnpm build` in `pce_browser_extension_wxt/`",
                "step1": "Open Chrome → chrome://extensions",
                "step2": "Enable Developer mode",
                "step3": "Click 'Load unpacked' → select `pce_browser_extension_wxt/.output/chrome-mv3/`",
            },
        },
        {
            "id": "local_hook",
            "name": "Local Model Hook",
            "icon": "cpu",
            "description": "Reverse proxy for local AI servers (Ollama, LM Studio, vLLM, etc.).",
            "status": "connected" if (local_active or _svc_status("local_hook") == "running" or _svc_status("multi_hook") == "running") else "disconnected",
            "service_status": _svc_status("local_hook"),
            "multi_hook_status": _svc_status("multi_hook"),
            "capture_count": local_hook_count,
            "last_seen": local_hook_last if local_hook_last and local_hook_last > 0 else None,
            "port": 11434,
            "setup": "Start with `python -m pce_app` → Local Hook auto-proxies Ollama on port 11434",
            "setup_detail": {
                "single": "Proxies a single local model (default: Ollama on 11434)",
                "multi": "Multi-hook auto-discovers servers on ports: 11434, 1234, 8000, 8080, 5000, 3000",
            },
        },
        {
            "id": "clipboard",
            "name": "Clipboard Monitor",
            "icon": "clipboard",
            "description": "Watches system clipboard for AI conversation text patterns (experimental).",
            "status": "connected" if (clip_active or _svc_status("clipboard") == "running") else "disconnected",
            "service_status": _svc_status("clipboard"),
            "capture_count": clip_count,
            "last_seen": clip_last if clip_last and clip_last > 0 else None,
            "port": None,
            "setup": "Start with `python -m pce_app` → enable clipboard monitor from Services",
        },
        {
            "id": "mcp",
            "name": "MCP Server",
            "icon": "link",
            "description": "Model Context Protocol server for IDE integrations (Cursor, VS Code, etc.).",
            "status": "connected" if mcp_info["active"] else "disconnected",
            "service_status": "connected" if mcp_info["active"] else "not_configured",
            "capture_count": mcp_info["capture_count"],
            "last_seen": mcp_info["last_seen"],
            "port": None,
            "setup": "Add to IDE MCP config: `python -m pce_mcp`",
        },
        {
            "id": "pac",
            "name": "System Proxy (PAC)",
            "icon": "settings",
            "description": "Auto-configuration file that routes only AI traffic through the PCE proxy.",
            "status": "info",
            "service_status": "available",
            "capture_count": None,
            "last_seen": None,
            "port": None,
            "setup": f"PAC URL: http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
            "setup_detail": {
                "windows": f"Settings → Network → Proxy → Use setup script → http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
                "macos": f"System Preferences → Network → Proxies → Auto Proxy → http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
            },
        },
    ]

    return {
        "mode": mode,
        "capture_mode": CAPTURE_MODE.value,
        "capabilities": capabilities,
        "services": services,
    }


# ---------------------------------------------------------------------------
# PAC file (Proxy Auto-Configuration)
# ---------------------------------------------------------------------------

from .pac_generator import generate_pac


@app.get("/proxy.pac", include_in_schema=False)
def serve_pac():
    """Serve a dynamically generated PAC file for system proxy configuration."""
    from fastapi.responses import Response
    pac_content = generate_pac()
    return Response(
        content=pac_content,
        media_type="application/x-ns-proxy-autoconfig",
        headers={"Content-Disposition": "inline; filename=proxy.pac"},
    )


@app.get("/api/v1/pac")
def get_pac_info():
    """Return PAC file URL and current proxy configuration."""
    return {
        "pac_url": f"http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
        "proxy_host": ALLOWED_HOSTS and PROXY_LISTEN_HOST or "127.0.0.1",
        "proxy_port": PROXY_LISTEN_PORT,
        "instructions": {
            "windows": f"Settings → Network → Proxy → Use setup script → http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
            "macos": f"System Preferences → Network → Proxies → Automatic Proxy Configuration → http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac",
            "linux": f"Set auto_proxy=http://{INGEST_HOST}:{INGEST_PORT}/proxy.pac in network settings or export http_proxy=http://127.0.0.1:{PROXY_LISTEN_PORT}",
        },
    }


# ---------------------------------------------------------------------------
# Dashboard – static files
# ---------------------------------------------------------------------------

import sys as _sys
if getattr(_sys, "frozen", False):
    _DASHBOARD_DIR = Path(_sys._MEIPASS) / "pce_core" / "dashboard"
else:
    _DASHBOARD_DIR = Path(__file__).parent / "dashboard"

if _DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_root():
        # Auto-redirect first-time users to the wizard. Existing users land
        # on the dashboard as usual.
        try:
            from . import app_state as _app_state
            if _app_state.needs_onboarding():
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url="/onboarding", status_code=303)
        except Exception:
            logger.debug("onboarding probe failed (non-fatal)", exc_info=True)
        return FileResponse(str(_DASHBOARD_DIR / "index.html"))

    @app.get("/onboarding", response_class=HTMLResponse, include_in_schema=False)
    def onboarding_root():
        """Serve the first-run setup wizard."""
        return FileResponse(str(_DASHBOARD_DIR / "onboarding.html"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import uvicorn
    uvicorn.run(
        "pce_core.server:app",
        host=INGEST_HOST,
        port=INGEST_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
