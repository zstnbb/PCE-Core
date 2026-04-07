"""PCE Core – FastAPI server providing Ingest & Query APIs.

Start with:
    python -m pce_core.server
    # or
    uvicorn pce_core.server:app --host 127.0.0.1 --port 9800
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import ALLOWED_HOSTS, CAPTURE_MODE, DB_PATH, INGEST_HOST, INGEST_PORT, PROXY_LISTEN_HOST, PROXY_LISTEN_PORT
from .db import (
    SOURCE_BROWSER_EXT,
    SOURCE_MCP,
    SOURCE_PROXY,
    add_custom_domain,
    get_custom_domains,
    get_stats,
    init_db,
    insert_capture,
    list_custom_domains,
    new_pair_id,
    query_by_pair,
    query_captures,
    query_messages,
    query_recent,
    query_sessions,
    refresh_custom_domains,
    remove_custom_domain,
)
from .models import (
    CaptureIn,
    CaptureOut,
    CaptureRecord,
    HealthOut,
    MessageRecord,
    SessionRecord,
    StatsOut,
)
from .normalizer.pipeline import normalize_conversation, try_normalize_pair

logger = logging.getLogger("pce.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

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
    logger.info("Initialising database at %s", DB_PATH)
    init_db()
    logger.info("PCE Core server v%s ready on %s:%s", __version__, INGEST_HOST, INGEST_PORT)
    yield


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

@app.get("/api/v1/health", response_model=HealthOut)
def health():
    stats = get_stats()
    return HealthOut(
        status="ok",
        version=__version__,
        db_path=str(DB_PATH),
        total_captures=stats["total_captures"],
    )


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
        source_id=source_id,
    )

    if capture_id is None:
        raise HTTPException(status_code=500, detail="Failed to insert capture")

    logger.info(
        "ingested %s %s from %s (%s)",
        payload.direction, payload.host, payload.source_type, capture_id[:8],
    )

    # Auto-normalize when a response completes a pair
    if payload.direction == "response":
        try:
            try_normalize_pair(pair_id, source_id=source_id, created_via=payload.source_type)
        except Exception:
            logger.exception("Auto-normalization failed for pair %s – non-fatal", pair_id[:8])

    # Normalize conversation captures (e.g. from browser extension DOM extraction)
    elif payload.direction == "conversation":
        try:
            from .db import query_by_pair
            rows = query_by_pair(pair_id)
            if rows:
                normalize_conversation(
                    rows[0], source_id=source_id, created_via=payload.source_type,
                )
        except Exception:
            logger.exception("Conversation normalization failed for %s – non-fatal", pair_id[:8])

    # Normalize network-intercepted captures (browser extension fetch/XHR/WS interception)
    elif payload.direction == "network_intercept":
        try:
            from .db import query_by_pair
            rows = query_by_pair(pair_id)
            if rows:
                normalize_conversation(
                    rows[0], source_id=source_id, created_via="browser_extension_network",
                )
        except Exception:
            logger.exception("Network intercept normalization failed for %s – non-fatal", pair_id[:8])

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
# Query: sessions
# ---------------------------------------------------------------------------

@app.get("/api/v1/sessions", response_model=list[SessionRecord])
def list_sessions(
    last: int = Query(20, ge=1, le=500),
    provider: Optional[str] = None,
):
    return query_sessions(last=last, provider=provider)


@app.get("/api/v1/sessions/{session_id}/messages", response_model=list[MessageRecord])
def get_session_messages(session_id: str):
    msgs = query_messages(session_id)
    if not msgs:
        raise HTTPException(status_code=404, detail="Session not found or empty")
    return msgs


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
    app.mount("/dashboard", StaticFiles(directory=str(_DASHBOARD_DIR)), name="dashboard")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_root():
        return FileResponse(str(_DASHBOARD_DIR / "index.html"))


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
