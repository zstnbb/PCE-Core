"""PCE Core – FastAPI server providing Ingest & Query APIs.

Start with:
    python -m pce_core.server
    # or
    uvicorn pce_core.server:app --host 127.0.0.1 --port 9800
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import DB_PATH, INGEST_HOST, INGEST_PORT
from .db import (
    SOURCE_BROWSER_EXT,
    SOURCE_MCP,
    SOURCE_PROXY,
    get_stats,
    init_db,
    insert_capture,
    new_pair_id,
    query_by_pair,
    query_captures,
    query_messages,
    query_recent,
    query_sessions,
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
