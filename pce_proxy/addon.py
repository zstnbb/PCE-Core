"""PCE Proxy – mitmproxy addon.

This addon sits inside the mitmproxy pipeline and:
1. Checks every request against the AI-domain allowlist.
2. For matching requests, records the request *before* forwarding.
3. After the response arrives, records the response.
4. All persistence is fail-safe – errors are logged, never raised.

Usage:
    mitmdump -s pce_proxy/addon.py -p 8080 --set stream_large_bodies=1m
"""

import json
import logging
import time
from typing import Optional

from mitmproxy import http

from .config import ALLOWED_HOSTS
from .db import init_db, insert_capture, new_pair_id
from .redact import redact_headers_json, safe_body_text

logger = logging.getLogger("pce.addon")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

# Ensure DB is ready when the addon is loaded
init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _provider_from_host(host: str) -> str:
    """Derive a short provider label from hostname."""
    if "openai" in host:
        return "openai"
    if "anthropic" in host:
        return "anthropic"
    if "googleapis" in host:
        return "google"
    return host


def _extract_model(body_bytes: bytes) -> Optional[str]:
    """Best-effort extraction of the model name from a JSON request body."""
    try:
        data = json.loads(body_bytes)
        return data.get("model")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# State kept per-flow to link request ↔ response
# ---------------------------------------------------------------------------
_flow_meta: dict[str, dict] = {}


class PCEAddon:
    """mitmproxy addon that captures AI traffic into local SQLite."""

    # --- request phase ----------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        if host not in ALLOWED_HOSTS:
            return

        pair_id = new_pair_id()
        _flow_meta[flow.id] = {
            "pair_id": pair_id,
            "request_time": time.time(),
        }

        try:
            headers_json = redact_headers_json(dict(flow.request.headers))
            body_raw = flow.request.get_content(raise_if_missing=False) or b""
            body_text, body_fmt = safe_body_text(body_raw)
            model = _extract_model(body_raw)

            insert_capture(
                direction="request",
                pair_id=pair_id,
                host=host,
                path=flow.request.path,
                method=flow.request.method,
                provider=_provider_from_host(host),
                model_name=model,
                headers_redacted_json=headers_json,
                body_text_or_json=body_text,
                body_format=body_fmt,
            )
            logger.info("captured request  %s %s %s", flow.request.method, host, flow.request.path)
        except Exception:
            logger.exception("request capture failed – letting request through")

    # --- response phase ---------------------------------------------------

    def response(self, flow: http.HTTPFlow) -> None:
        meta = _flow_meta.pop(flow.id, None)
        if meta is None:
            return  # not a tracked flow

        host = flow.request.pretty_host
        pair_id = meta["pair_id"]
        latency = (time.time() - meta["request_time"]) * 1000  # ms

        try:
            headers_json = redact_headers_json(dict(flow.response.headers))
            body_raw = flow.response.get_content(raise_if_missing=False) or b""
            body_text, body_fmt = safe_body_text(body_raw)

            insert_capture(
                direction="response",
                pair_id=pair_id,
                host=host,
                path=flow.request.path,
                method=flow.request.method,
                provider=_provider_from_host(host),
                status_code=flow.response.status_code,
                latency_ms=round(latency, 2),
                headers_redacted_json=headers_json,
                body_text_or_json=body_text,
                body_format=body_fmt,
            )
            logger.info(
                "captured response %s %s %s -> %d (%.0f ms)",
                flow.request.method, host, flow.request.path,
                flow.response.status_code, latency,
            )
        except Exception:
            logger.exception("response capture failed – letting response through")


# mitmproxy picks up addon instances via the `addons` module-level list.
addons = [PCEAddon()]
