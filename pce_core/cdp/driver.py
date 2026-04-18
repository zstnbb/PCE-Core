# SPDX-License-Identifier: Apache-2.0
"""Playwright-based CDP driver that writes matching responses to ``raw_captures``.

The driver runs Playwright's **sync API in a background thread** so the
FastAPI server (async) can ``start()`` / ``stop()`` it without blocking
its event loop. Playwright holds its own greenlet loop internally;
wrapping it in a dedicated OS thread is the officially recommended
pattern for long-running scripts.

Capture semantics
-----------------

For every network response whose URL matches one of ``url_patterns``
we write **two** rows to ``raw_captures``:

- ``direction="request"``   holds the request headers + body
- ``direction="response"``  holds the response headers + body

Both rows share the same ``pair_id`` (a UUID minted on response) so the
existing normalizer can reconcile them just like mitmproxy-sourced
pairs. Source id is ``SOURCE_CDP`` so downstream consumers can tell
this traffic apart from extension / proxy captures.

We deliberately reuse :func:`pce_core.db.insert_capture` rather than
inventing a new table; the CDP source is a *transport*, not a new data
type. The normalizer does all the rest of the work.

Headers are redacted with the same :func:`pce_core.redact.scrub_headers`
helper the proxy uses, so authorization tokens never hit disk.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import uuid
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

from ..db import SOURCE_CDP, insert_capture
from ..redact import redact_headers

logger = logging.getLogger("pce.cdp.driver")

# Default URL patterns matched against the response URL. These cover the
# main consumer LLM apps; users can override via ``url_patterns``.
DEFAULT_URL_PATTERNS: tuple[str, ...] = (
    r"^https://chatgpt\.com/backend-api/.*",
    r"^https://chat\.openai\.com/backend-api/.*",
    r"^https://api\.openai\.com/v1/.*",
    r"^https://claude\.ai/api/.*",
    r"^https://api\.anthropic\.com/v1/.*",
    r"^https://gemini\.google\.com/.*",
    r"^https://generativelanguage\.googleapis\.com/.*",
    r"^https://api\.mistral\.ai/v1/.*",
    r"^https://api\.groq\.com/openai/v1/.*",
    r"^https://api\.perplexity\.ai/.*",
    r"^https://www\.perplexity\.ai/api/.*",
)


# Tight bounds so we never bring the UI thread down with a 500 MB video
# response.
_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB per row


class CDPDriver:
    """Owns a Playwright-driven Chromium and a capture callback.

    This is *not* thread-safe; callers should serialise start/stop at
    the level of :mod:`pce_core.cdp` (which it does via a module-level
    lock). ``snapshot()`` is safe to call from any thread.
    """

    def __init__(
        self,
        *,
        start_url: str = "about:blank",
        url_patterns: Optional[list[str]] = None,
        headless: bool = False,
        on_capture: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._start_url = start_url
        self._headless = headless
        self._patterns = [
            re.compile(p) for p in (url_patterns or DEFAULT_URL_PATTERNS)
        ]
        self._on_capture = on_capture
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._started_evt = threading.Event()
        self._shutdown_q: "queue.Queue[object]" = queue.Queue()

        # Telemetry that ``snapshot()`` returns — guarded by ``_state_lock``.
        self._state_lock = threading.Lock()
        self._running = False
        self._captures_written = 0
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def snapshot(self) -> dict:
        with self._state_lock:
            return {
                "running": self._running,
                "captures_written": self._captures_written,
                "last_error": self._last_error,
                "start_url": self._start_url,
            }

    def start(self, *, timeout_s: float = 30.0) -> None:
        """Launch Playwright in a background thread. Blocks until the
        browser is ready or ``timeout_s`` elapses."""
        if self.is_running():
            return
        self._stop_evt.clear()
        self._started_evt.clear()
        self._thread = threading.Thread(
            target=self._run_forever, name="pce-cdp-driver", daemon=True,
        )
        self._thread.start()

        if not self._started_evt.wait(timeout=timeout_s):
            # Thread didn't signal readiness — surface the last error.
            self._stop_evt.set()
            with self._state_lock:
                reason = self._last_error or "cdp_start_timeout"
            raise TimeoutError(reason)

    def stop(self, *, timeout_s: float = 10.0) -> None:
        if not self.is_running() and self._thread is None:
            return
        self._stop_evt.set()
        # Wake the run loop out of any Playwright-side blocking call.
        try:
            self._shutdown_q.put_nowait(object())
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        with self._state_lock:
            self._running = False

    # ------------------------------------------------------------------
    # Internal run loop (executes on the background thread)
    # ------------------------------------------------------------------

    def _run_forever(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except Exception as exc:
            with self._state_lock:
                self._last_error = f"playwright_import_failed: {exc}"
            self._started_evt.set()
            return

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self._headless)
                context = browser.new_context()
                page = context.new_page()

                # Hook the response listener *before* navigation so we
                # don't miss the first round-trip.
                page.on("response", self._handle_response)

                page.goto(self._start_url, wait_until="domcontentloaded")

                with self._state_lock:
                    self._running = True
                    self._last_error = None
                self._started_evt.set()

                # Park here until .stop() sets the event. Using
                # ``queue.get(timeout=…)`` keeps us responsive to
                # shutdown while still letting Playwright's internal
                # event pump run.
                while not self._stop_evt.is_set():
                    try:
                        self._shutdown_q.get(timeout=0.25)
                        break
                    except queue.Empty:
                        continue

                # Graceful teardown.
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass
        except Exception as exc:
            with self._state_lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("cdp driver crashed")
            # Ensure start() unblocks even on import-time failure.
            self._started_evt.set()
        finally:
            with self._state_lock:
                self._running = False

    # ------------------------------------------------------------------
    # Response handler
    # ------------------------------------------------------------------

    def _handle_response(self, response: Any) -> None:
        """Playwright calls this for **every** response. We decide here
        whether to persist it.
        """
        try:
            url = getattr(response, "url", "") or ""
            if not self._url_matches(url):
                return
            request = getattr(response, "request", None)
            method = getattr(request, "method", "GET") if request else "GET"
            req_headers = self._safe_headers(request)
            res_headers = self._safe_headers(response)
            req_body = self._safe_request_body(request)
            res_body = self._safe_response_body(response)
            status_code = int(getattr(response, "status", 0) or 0)

            parsed = urlparse(url)
            host = parsed.netloc
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            provider = _provider_from_host(host)
            pair_id = uuid.uuid4().hex
            timing = getattr(response, "timing", None) if hasattr(response, "timing") else None
            latency_ms = _latency_from_timing(timing)

            meta = {
                "cdp.start_url": self._start_url,
                "cdp.url": url,
            }
            meta_json = json.dumps(meta, ensure_ascii=False)

            req_id = insert_capture(
                direction="request",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                headers_redacted_json=_dumps_headers(req_headers),
                body_text_or_json=req_body,
                body_format="json" if _looks_like_json(req_body) else "text",
                source_id=SOURCE_CDP,
                meta_json=meta_json,
            )
            res_id = insert_capture(
                direction="response",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                status_code=status_code,
                latency_ms=latency_ms,
                headers_redacted_json=_dumps_headers(res_headers),
                body_text_or_json=res_body,
                body_format="json" if _looks_like_json(res_body) else "text",
                source_id=SOURCE_CDP,
                meta_json=meta_json,
            )
            if req_id and res_id:
                with self._state_lock:
                    self._captures_written += 1
                if self._on_capture is not None:
                    try:
                        self._on_capture({
                            "pair_id": pair_id, "url": url,
                            "host": host, "status": status_code,
                        })
                    except Exception:
                        logger.debug("on_capture callback raised", exc_info=True)
        except Exception:
            # Never crash the browser thread over a capture error.
            logger.exception("cdp response handler failed")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _url_matches(self, url: str) -> bool:
        return any(p.search(url) for p in self._patterns)

    @staticmethod
    def _safe_headers(obj: Any) -> Mapping[str, str]:
        if obj is None:
            return {}
        hdrs = getattr(obj, "headers", None)
        if hdrs is None:
            return {}
        if callable(hdrs):
            try:
                hdrs = hdrs()
            except Exception:
                return {}
        try:
            return {str(k): str(v) for k, v in dict(hdrs).items()}
        except Exception:
            return {}

    @staticmethod
    def _safe_request_body(request: Any) -> str:
        if request is None:
            return ""
        post_data = getattr(request, "post_data", "") or ""
        if callable(post_data):
            try:
                post_data = post_data() or ""
            except Exception:
                return ""
        s = str(post_data or "")
        if len(s) > _MAX_BODY_BYTES:
            return s[:_MAX_BODY_BYTES]
        return s

    @staticmethod
    def _safe_response_body(response: Any) -> str:
        if response is None:
            return ""
        try:
            text = response.text()
        except Exception:
            return ""
        if text is None:
            return ""
        if len(text) > _MAX_BODY_BYTES:
            return text[:_MAX_BODY_BYTES]
        return text


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_HOST_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("openai.com", "openai"),
    ("chatgpt.com", "openai"),
    ("anthropic.com", "anthropic"),
    ("claude.ai", "anthropic"),
    ("gemini.google.com", "google"),
    ("generativelanguage.googleapis.com", "google"),
    ("mistral.ai", "mistral"),
    ("groq.com", "groq"),
    ("perplexity.ai", "perplexity"),
)


def _provider_from_host(host: str) -> str:
    h = (host or "").lower()
    for needle, provider in _HOST_PROVIDER_HINTS:
        if needle in h:
            return provider
    return "unknown"


def _dumps_headers(headers: Mapping[str, str]) -> str:
    try:
        redacted = redact_headers(dict(headers))
    except Exception:
        redacted = {}
    return json.dumps(redacted, ensure_ascii=False, sort_keys=True)


def _looks_like_json(body: str) -> bool:
    s = (body or "").lstrip()
    return bool(s) and s[0] in ("{", "[")


def _latency_from_timing(timing: Any) -> Optional[float]:
    """Playwright's ``Response.timing`` is a dict with millisecond offsets.

    We compute a rough RTT from ``responseEnd - startTime`` when both
    are present. Returns ``None`` when the values are missing — the
    normalizer treats ``None`` as "unknown", not "zero".
    """
    if not isinstance(timing, Mapping):
        return None
    start = timing.get("startTime") or timing.get("requestStart")
    end = timing.get("responseEnd")
    try:
        if start is not None and end is not None:
            return max(0.0, float(end) - float(start))
    except (TypeError, ValueError):
        pass
    return None


__all__ = [
    "CDPDriver",
    "DEFAULT_URL_PATTERNS",
    "SOURCE_CDP",
]
