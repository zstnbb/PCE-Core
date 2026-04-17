"""LiteLLM → PCE callback.

LiteLLM loads callbacks by dotted path from the config file. We expose
*both* a function-style handler (``pce_callback_handler``) for
``success_callback``/``failure_callback`` lists and a class-style
``CustomLogger`` subclass (``PCECallback``) for newer LiteLLM versions.

The callback is deliberately independent of the rest of ``pce_core``:

- No DB access — we always POST to the Ingest API so data survives
  independent of the in-process DB session.
- No imports from ``pce_core.db`` / ``pce_core.server`` so this file can
  be loaded cleanly inside the LiteLLM subprocess without triggering
  SQLite init twice.
- Every exception is caught and logged; we never break the user's SDK
  call because our sidecar failed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional
from urllib import request as urllib_request

logger = logging.getLogger("pce.sdk_capture_litellm.callback")

# The PCE callback can be pointed at any Ingest URL via this env var.
# When run under LiteLLM, the config generator puts the correct value here.
PCE_INGEST_URL_ENV = "PCE_INGEST_URL"
DEFAULT_PCE_INGEST_URL = "http://127.0.0.1:9800/api/v1/captures"


# ---------------------------------------------------------------------------
# Function-style handler (works with litellm success_callback=[...])
# ---------------------------------------------------------------------------

def pce_callback_handler(
    kwargs: dict,
    response_obj: Any,
    start_time: Any,
    end_time: Any,
) -> None:
    """LiteLLM-compatible success/failure callback.

    Forwards the completion to PCE's Ingest API in a background thread so
    LiteLLM's request path is never blocked.
    """
    try:
        payload = _build_ingest_payload(kwargs, response_obj, start_time, end_time)
    except Exception as e:      # noqa: BLE001
        logger.warning("pce.callback.build_failed error=%s", e)
        return
    _fire_and_forget_post(payload)


# ---------------------------------------------------------------------------
# Class-style callback (works with litellm.callbacks=[PCECallback()])
# ---------------------------------------------------------------------------

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore
    _HAS_LITELLM_BASE = True
except Exception:                   # noqa: BLE001 — litellm optional
    CustomLogger = object           # type: ignore[misc,assignment]
    _HAS_LITELLM_BASE = False


class PCECallback(CustomLogger):        # type: ignore[misc]
    """LiteLLM ``CustomLogger`` that forwards completions to PCE Ingest."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        pce_callback_handler(kwargs, response_obj, start_time, end_time)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        pce_callback_handler(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        pce_callback_handler(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        pce_callback_handler(kwargs, response_obj, start_time, end_time)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_ingest_payload(
    kwargs: dict,
    response_obj: Any,
    start_time: Any,
    end_time: Any,
) -> dict:
    """Flatten LiteLLM's callback arguments into a PCE CaptureIn payload."""
    model = (
        (kwargs or {}).get("model")
        or (kwargs or {}).get("litellm_params", {}).get("model")
        or "unknown"
    )
    provider = _provider_from_model(model)
    messages = (kwargs or {}).get("messages") or []
    # Extract response as dict for JSON serialisation.
    response_dict = _response_to_dict(response_obj)
    latency_ms = _compute_latency_ms(start_time, end_time)
    status_code = _status_code_from_response(response_obj)

    request_body_json = json.dumps(
        {"model": model, "messages": messages,
         **{k: v for k, v in (kwargs or {}).items()
            if k in ("temperature", "max_tokens", "top_p",
                     "stream", "tools", "tool_choice")}},
        ensure_ascii=False, default=str,
    )
    response_body_json = json.dumps(response_dict, ensure_ascii=False, default=str)

    body_json = json.dumps({
        "url": f"https://sdk-litellm.local/v1/chat/completions",
        "is_streaming": bool((kwargs or {}).get("stream")),
        "request_body": request_body_json,
        "response_body": response_body_json,
    }, ensure_ascii=False)

    return {
        "source_type": "local_hook",
        "source_name": "sdk-litellm",
        "direction": "network_intercept",
        "provider": provider,
        "host": "sdk-litellm.local",
        "path": "/v1/chat/completions",
        "method": "POST",
        "model_name": model,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "headers_json": "{}",
        "body_json": body_json,
        "body_format": "json",
        "session_hint": None,
        "meta": {
            "capture_source": "litellm_proxy",
            "model": model,
            "provider": provider,
            "is_streaming": bool((kwargs or {}).get("stream")),
        },
    }


def _provider_from_model(model: str) -> str:
    """Infer ``openai`` / ``anthropic`` / ``gemini`` / … from a LiteLLM model id."""
    if not model:
        return "unknown"
    m = model.lower()
    # Google's SDK accepts "models/gemini-…" — detect before slash-splitting.
    if m.startswith("models/gemini"):
        return "gemini"
    if "/" in m:
        return m.split("/", 1)[0]
    if m.startswith(("gpt-", "o1-", "o3-", "text-embedding")):
        return "openai"
    if m.startswith("claude-"):
        return "anthropic"
    if m.startswith("gemini-"):
        return "gemini"
    if m.startswith("command-"):
        return "cohere"
    if m.startswith(("mistral-", "open-mistral", "open-mixtral")):
        return "mistral"
    return "unknown"


def _response_to_dict(response_obj: Any) -> dict:
    """Serialise LiteLLM's response to a JSON-compatible dict."""
    if response_obj is None:
        return {}
    if isinstance(response_obj, dict):
        return response_obj
    # LiteLLM ModelResponse has .model_dump() (pydantic v2) or .dict()
    for method in ("model_dump", "dict", "to_dict"):
        fn = getattr(response_obj, method, None)
        if callable(fn):
            try:
                d = fn()
                if isinstance(d, dict):
                    return d
            except Exception:       # noqa: BLE001
                continue
    if hasattr(response_obj, "__dict__"):
        try:
            return {k: v for k, v in vars(response_obj).items()
                    if not k.startswith("_")}
        except Exception:           # noqa: BLE001
            pass
    return {"repr": str(response_obj)}


def _compute_latency_ms(start_time: Any, end_time: Any) -> int:
    """Best-effort latency compute from datetime / float / None."""
    try:
        if isinstance(start_time, datetime) and isinstance(end_time, datetime):
            return int((end_time - start_time).total_seconds() * 1000)
        if isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
            return int((float(end_time) - float(start_time)) * 1000)
    except Exception:               # noqa: BLE001
        pass
    return 0


def _status_code_from_response(response_obj: Any) -> int:
    """Detect LiteLLM's failure markers; default to 200 otherwise."""
    if response_obj is None:
        return 0
    # LiteLLM surfaces exceptions as response objects too in failure callbacks.
    if isinstance(response_obj, Exception):
        status = getattr(response_obj, "status_code", None)
        if isinstance(status, int):
            return status
        return 500
    # Some versions attach an HTTP status code.
    status = getattr(response_obj, "status_code", None)
    if isinstance(status, int):
        return status
    return 200


# ---------------------------------------------------------------------------
# Non-blocking POST to PCE Ingest
# ---------------------------------------------------------------------------

def _get_ingest_url() -> str:
    return os.environ.get(PCE_INGEST_URL_ENV, DEFAULT_PCE_INGEST_URL)


def _fire_and_forget_post(payload: dict) -> None:
    """POST in a background thread. Never raises."""
    def _worker() -> None:
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib_request.Request(
                _get_ingest_url(),
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=5) as resp:
                rc = resp.status
            if rc >= 300:
                logger.warning("pce.callback.ingest_rejected status=%s", rc)
        except Exception as e:      # noqa: BLE001
            logger.warning("pce.callback.ingest_failed error=%s", e)

    t = threading.Thread(
        target=_worker, name="pce-litellm-callback", daemon=True,
    )
    t.start()
