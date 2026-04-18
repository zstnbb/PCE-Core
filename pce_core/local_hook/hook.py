# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Local Model Hook server.

A FastAPI reverse-proxy that:
1. Receives requests meant for a local model server
2. Forwards them to the real server
3. Captures both request and response to PCE
4. Returns the response to the caller transparently

This allows capturing interactions with Ollama, LM Studio, vLLM, etc.
without requiring the user to configure a system-wide proxy.
"""

import json
import logging
import time
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from ..config import DB_PATH, LOCAL_MODEL_PORTS
from ..db import init_db, insert_capture, new_pair_id, SOURCE_PROXY
from ..normalizer.pipeline import try_normalize_pair

logger = logging.getLogger("pce.local_hook")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TARGET_HOST = "127.0.0.1"
DEFAULT_TARGET_PORT = 11434  # Ollama
DEFAULT_LISTEN_PORT = 11435  # Hook listens one port above


def create_hook_app(
    target_host: str = DEFAULT_TARGET_HOST,
    target_port: int = DEFAULT_TARGET_PORT,
) -> FastAPI:
    """Create a FastAPI app that proxies to a local model server."""

    target_base = f"http://{target_host}:{target_port}"
    provider = _detect_provider(target_port)

    app = FastAPI(
        title=f"PCE Local Hook → {target_base}",
        description=f"Transparent capture proxy for {provider}",
    )

    @app.on_event("startup")
    async def _startup():
        init_db()
        logger.info(
            "PCE Local Hook started: listening → forwarding to %s (provider: %s)",
            target_base, provider,
        )

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def proxy_all(request: Request, path: str):
        """Forward all requests to the target server and capture them."""
        pair_id = new_pair_id()
        req_time = time.time()

        # Read request body
        body = await request.body()
        body_text = body.decode("utf-8", errors="replace") if body else ""

        # Extract model name from request body
        model_name = _extract_model(body_text)

        # Build target URL
        target_url = f"{target_base}/{path}"
        if request.url.query:
            target_url += f"?{request.url.query}"

        # Forward headers (strip hop-by-hop)
        fwd_headers = dict(request.headers)
        for h in ("host", "transfer-encoding", "connection"):
            fwd_headers.pop(h, None)

        # Capture request
        _capture_async(
            direction="request",
            pair_id=pair_id,
            host=f"{target_host}:{target_port}",
            path=f"/{path}",
            method=request.method,
            provider=provider,
            model_name=model_name,
            body_text=body_text,
        )

        # Forward to real server
        try:
            async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
                resp = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=fwd_headers,
                    content=body,
                )
        except httpx.ConnectError:
            logger.warning("Target server unreachable: %s", target_base)
            return Response(
                content=json.dumps({"error": f"PCE Hook: target {target_base} unreachable"}),
                status_code=502,
                media_type="application/json",
            )
        except Exception as e:
            logger.exception("Proxy error: %s", e)
            return Response(
                content=json.dumps({"error": f"PCE Hook proxy error: {str(e)}"}),
                status_code=502,
                media_type="application/json",
            )

        # Read response
        resp_body = resp.content
        resp_text = resp_body.decode("utf-8", errors="replace") if resp_body else ""
        latency_ms = (time.time() - req_time) * 1000

        # Extract model from response if not in request
        if not model_name:
            model_name = _extract_model(resp_text)

        # Capture response
        _capture_async(
            direction="response",
            pair_id=pair_id,
            host=f"{target_host}:{target_port}",
            path=f"/{path}",
            method=request.method,
            provider=provider,
            model_name=model_name,
            status_code=resp.status_code,
            latency_ms=round(latency_ms, 2),
            body_text=resp_text,
        )

        # Auto-normalize the completed pair into sessions + messages
        try:
            try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="local_hook")
        except Exception:
            logger.exception("Auto-normalization failed for pair %s – non-fatal", pair_id[:8])

        # Build response headers (strip hop-by-hop)
        resp_headers = dict(resp.headers)
        for h in ("transfer-encoding", "content-encoding", "content-length", "connection"):
            resp_headers.pop(h, None)

        return Response(
            content=resp_body,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type"),
        )

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_async(
    *,
    direction: str,
    pair_id: str,
    host: str,
    path: str,
    method: str,
    provider: str,
    model_name: Optional[str] = None,
    status_code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    body_text: str = "",
) -> None:
    """Insert capture record (fail-safe, non-blocking)."""
    try:
        body_format = "json" if body_text.strip().startswith(("{", "[")) else "text"
        insert_capture(
            direction=direction,
            pair_id=pair_id,
            host=host,
            path=path,
            method=method,
            provider=provider,
            model_name=model_name,
            status_code=status_code,
            latency_ms=latency_ms,
            headers_redacted_json="{}",
            body_text_or_json=body_text,
            body_format=body_format,
            source_id=SOURCE_PROXY,
            meta_json=json.dumps({"capture_source": "local_hook"}),
        )
    except Exception:
        logger.exception("Failed to capture %s – non-fatal", direction)


def _extract_model(body: str) -> Optional[str]:
    """Try to extract model name from JSON body."""
    if not body or not body.strip().startswith("{"):
        return None
    try:
        data = json.loads(body)
        return data.get("model")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def _detect_provider(port: int) -> str:
    """Guess the local model provider from the port number."""
    port_map = {
        11434: "ollama",
        1234: "lm-studio",
        8000: "vllm",
        8080: "localai",
        5000: "text-gen-webui",
        3000: "jan",
    }
    return port_map.get(port, "local-model")
