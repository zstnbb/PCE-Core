"""PCE Core – Python SDK Monkey-Patch for Zero-Config API Capture.

Usage:
    import pce_core.sdk_capture  # That's it! All OpenAI/Anthropic calls are now captured.

Or selectively:
    from pce_core.sdk_capture import patch_openai, patch_anthropic
    patch_openai()

This module monkey-patches the OpenAI and Anthropic Python SDKs to
transparently capture all chat completion requests and responses,
sending them to the PCE Ingest API without any code changes.

Supports:
- openai.ChatCompletion.create / openai.chat.completions.create (sync + async)
- anthropic.messages.create (sync + async)
- Streaming responses (accumulated then sent)
"""

import json
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("pce.sdk_capture")

PCE_INGEST_URL = os.environ.get("PCE_INGEST_URL", "http://127.0.0.1:9800/api/v1/captures")
_patched = {"openai": False, "anthropic": False}


# ---------------------------------------------------------------------------
# Ingest sender (fire-and-forget, non-blocking)
# ---------------------------------------------------------------------------

def _send_capture(
    provider: str,
    model: str,
    request_body: dict,
    response_body: dict,
    latency_ms: int,
    is_streaming: bool = False,
):
    """Send a capture to the PCE Ingest API in a background thread."""
    def _post():
        try:
            import urllib.request
            payload = {
                "source_type": "local_hook",
                "source_name": f"sdk-{provider}",
                "direction": "network_intercept",
                "provider": provider,
                "host": f"sdk.{provider}",
                "path": "/chat/completions",
                "method": "POST",
                "model_name": model,
                "status_code": 200,
                "latency_ms": latency_ms,
                "headers_json": "{}",
                "body_json": json.dumps({
                    "request_body": json.dumps(request_body, default=str, ensure_ascii=False),
                    "response_body": json.dumps(response_body, default=str, ensure_ascii=False),
                    "url": f"https://api.{provider}.com/v1/chat/completions",
                    "is_streaming": is_streaming,
                }, ensure_ascii=False),
                "body_format": "json",
                "session_hint": None,
                "meta": {
                    "capture_source": "sdk_monkey_patch",
                    "sdk": provider,
                    "model": model,
                    "is_streaming": is_streaming,
                },
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                PCE_INGEST_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            logger.debug("SDK capture sent: %s %s", provider, model)
        except Exception as e:
            logger.debug("SDK capture send failed (non-fatal): %s", e)

    t = threading.Thread(target=_post, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# OpenAI SDK patch
# ---------------------------------------------------------------------------

def patch_openai():
    """Monkey-patch the OpenAI Python SDK to capture all chat completions."""
    if _patched["openai"]:
        return

    try:
        import openai
    except ImportError:
        logger.debug("openai package not installed, skipping patch")
        return

    _patched["openai"] = True

    # --- New SDK (openai >= 1.0) ---
    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions

        _orig_create = Completions.create

        def _patched_create(self, *args, **kwargs):
            start = time.monotonic()
            request_snapshot = _snapshot_kwargs(kwargs)
            is_stream = kwargs.get("stream", False)

            result = _orig_create(self, *args, **kwargs)

            if is_stream:
                return _wrap_openai_stream(result, request_snapshot, start)

            # Non-streaming: capture immediately
            latency = int((time.monotonic() - start) * 1000)
            try:
                resp_dict = result.model_dump() if hasattr(result, "model_dump") else {"raw": str(result)}
                _send_capture(
                    provider="openai",
                    model=kwargs.get("model", "unknown"),
                    request_body=request_snapshot,
                    response_body=resp_dict,
                    latency_ms=latency,
                )
            except Exception:
                pass
            return result

        Completions.create = _patched_create

        # Async version
        _orig_async_create = AsyncCompletions.create

        async def _patched_async_create(self, *args, **kwargs):
            start = time.monotonic()
            request_snapshot = _snapshot_kwargs(kwargs)
            is_stream = kwargs.get("stream", False)

            result = await _orig_async_create(self, *args, **kwargs)

            if is_stream:
                return _wrap_openai_async_stream(result, request_snapshot, start)

            latency = int((time.monotonic() - start) * 1000)
            try:
                resp_dict = result.model_dump() if hasattr(result, "model_dump") else {"raw": str(result)}
                _send_capture(
                    provider="openai",
                    model=kwargs.get("model", "unknown"),
                    request_body=request_snapshot,
                    response_body=resp_dict,
                    latency_ms=latency,
                )
            except Exception:
                pass
            return result

        AsyncCompletions.create = _patched_async_create
        logger.info("Patched OpenAI SDK (v1+) chat completions")

    except (ImportError, AttributeError):
        logger.debug("OpenAI SDK v1+ not found, trying legacy")

    # --- Legacy SDK (openai < 1.0) ---
    try:
        if hasattr(openai, "ChatCompletion"):
            _orig_legacy = openai.ChatCompletion.create

            @staticmethod
            def _patched_legacy(*args, **kwargs):
                start = time.monotonic()
                request_snapshot = _snapshot_kwargs(kwargs)
                result = _orig_legacy(*args, **kwargs)
                latency = int((time.monotonic() - start) * 1000)
                try:
                    resp_dict = dict(result) if hasattr(result, "__iter__") else {"raw": str(result)}
                    _send_capture(
                        provider="openai",
                        model=kwargs.get("model", "unknown"),
                        request_body=request_snapshot,
                        response_body=resp_dict,
                        latency_ms=latency,
                    )
                except Exception:
                    pass
                return result

            openai.ChatCompletion.create = _patched_legacy
            logger.info("Patched OpenAI SDK (legacy) ChatCompletion.create")
    except (ImportError, AttributeError):
        pass


def _wrap_openai_stream(stream, request_snapshot, start):
    """Wrap a sync OpenAI streaming response to accumulate chunks for capture."""
    chunks = []
    content_parts = []
    model = "unknown"

    def _gen():
        nonlocal model
        try:
            for chunk in stream:
                chunks.append(chunk)
                try:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        content_parts.append(delta.content)
                    if chunk.model:
                        model = chunk.model
                except Exception:
                    pass
                yield chunk
        finally:
            latency = int((time.monotonic() - start) * 1000)
            _send_capture(
                provider="openai",
                model=model,
                request_body=request_snapshot,
                response_body={
                    "choices": [{"message": {"role": "assistant", "content": "".join(content_parts)}}],
                    "model": model,
                },
                latency_ms=latency,
                is_streaming=True,
            )

    return _gen()


async def _wrap_openai_async_stream(stream, request_snapshot, start):
    """Wrap an async OpenAI streaming response."""
    content_parts = []
    model = "unknown"

    async def _gen():
        nonlocal model
        try:
            async for chunk in stream:
                try:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        content_parts.append(delta.content)
                    if chunk.model:
                        model = chunk.model
                except Exception:
                    pass
                yield chunk
        finally:
            latency = int((time.monotonic() - start) * 1000)
            _send_capture(
                provider="openai",
                model=model,
                request_body=request_snapshot,
                response_body={
                    "choices": [{"message": {"role": "assistant", "content": "".join(content_parts)}}],
                    "model": model,
                },
                latency_ms=latency,
                is_streaming=True,
            )

    return _gen()


# ---------------------------------------------------------------------------
# Anthropic SDK patch
# ---------------------------------------------------------------------------

def patch_anthropic():
    """Monkey-patch the Anthropic Python SDK to capture all message creates."""
    if _patched["anthropic"]:
        return

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, skipping patch")
        return

    _patched["anthropic"] = True

    try:
        from anthropic.resources.messages import Messages, AsyncMessages

        _orig_create = Messages.create

        def _patched_create(self, *args, **kwargs):
            start = time.monotonic()
            request_snapshot = _snapshot_kwargs(kwargs)
            is_stream = kwargs.get("stream", False)

            result = _orig_create(self, *args, **kwargs)

            if is_stream:
                return _wrap_anthropic_stream(result, request_snapshot, start, kwargs.get("model", "unknown"))

            latency = int((time.monotonic() - start) * 1000)
            try:
                resp_dict = result.model_dump() if hasattr(result, "model_dump") else {"raw": str(result)}
                _send_capture(
                    provider="anthropic",
                    model=kwargs.get("model", "unknown"),
                    request_body=request_snapshot,
                    response_body=resp_dict,
                    latency_ms=latency,
                )
            except Exception:
                pass
            return result

        Messages.create = _patched_create

        # Async version
        _orig_async_create = AsyncMessages.create

        async def _patched_async_create(self, *args, **kwargs):
            start = time.monotonic()
            request_snapshot = _snapshot_kwargs(kwargs)
            is_stream = kwargs.get("stream", False)

            result = await _orig_async_create(self, *args, **kwargs)

            if is_stream:
                return _wrap_anthropic_async_stream(result, request_snapshot, start, kwargs.get("model", "unknown"))

            latency = int((time.monotonic() - start) * 1000)
            try:
                resp_dict = result.model_dump() if hasattr(result, "model_dump") else {"raw": str(result)}
                _send_capture(
                    provider="anthropic",
                    model=kwargs.get("model", "unknown"),
                    request_body=request_snapshot,
                    response_body=resp_dict,
                    latency_ms=latency,
                )
            except Exception:
                pass
            return result

        AsyncMessages.create = _patched_async_create
        logger.info("Patched Anthropic SDK messages.create")

    except (ImportError, AttributeError) as e:
        logger.debug("Anthropic SDK patch failed: %s", e)


def _wrap_anthropic_stream(stream, request_snapshot, start, model):
    """Wrap a sync Anthropic streaming response."""
    content_parts = []

    def _gen():
        try:
            for event in stream:
                try:
                    if hasattr(event, "type"):
                        if event.type == "content_block_delta" and hasattr(event, "delta"):
                            if hasattr(event.delta, "text"):
                                content_parts.append(event.delta.text)
                except Exception:
                    pass
                yield event
        finally:
            latency = int((time.monotonic() - start) * 1000)
            _send_capture(
                provider="anthropic",
                model=model,
                request_body=request_snapshot,
                response_body={
                    "content": [{"type": "text", "text": "".join(content_parts)}],
                    "role": "assistant",
                    "model": model,
                },
                latency_ms=latency,
                is_streaming=True,
            )

    return _gen()


async def _wrap_anthropic_async_stream(stream, request_snapshot, start, model):
    """Wrap an async Anthropic streaming response."""
    content_parts = []

    async def _gen():
        try:
            async for event in stream:
                try:
                    if hasattr(event, "type"):
                        if event.type == "content_block_delta" and hasattr(event, "delta"):
                            if hasattr(event.delta, "text"):
                                content_parts.append(event.delta.text)
                except Exception:
                    pass
                yield event
        finally:
            latency = int((time.monotonic() - start) * 1000)
            _send_capture(
                provider="anthropic",
                model=model,
                request_body=request_snapshot,
                response_body={
                    "content": [{"type": "text", "text": "".join(content_parts)}],
                    "role": "assistant",
                    "model": model,
                },
                latency_ms=latency,
                is_streaming=True,
            )

    return _gen()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot_kwargs(kwargs: dict) -> dict:
    """Create a JSON-safe snapshot of SDK call kwargs."""
    snapshot = {}
    for k, v in kwargs.items():
        if k in ("self",):
            continue
        try:
            json.dumps(v)
            snapshot[k] = v
        except (TypeError, ValueError):
            if hasattr(v, "model_dump"):
                snapshot[k] = v.model_dump()
            else:
                snapshot[k] = str(v)
    return snapshot


def patch_all():
    """Patch all supported SDKs."""
    patch_openai()
    patch_anthropic()


# Auto-patch on import
patch_all()
