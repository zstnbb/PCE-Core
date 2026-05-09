# SPDX-License-Identifier: Apache-2.0
"""Bridge: a running Claude Desktop CDP endpoint → ``pce_core`` ingest.

Splits cleanly into three concerns:

1. **build_capture_event(...)** — pure helper that turns one
   Playwright ``Response`` into a JSON-serialisable payload accepted
   by ``POST /api/v1/captures`` with ``source_type="desktop_electron"``.
   No Playwright dep — just duck-typing — so it's exhaustively unit-testable.

2. **post_to_pce_core(...)** — fire-and-forget HTTP POST with sensible
   timeout + soft-fail (errors → log only, never crash the bridge).

3. **CaptureBridge** — orchestrates Playwright ``connect_over_cdp`` +
   page hooking. Lazily imports ``playwright`` so test environments
   without it can still exercise the pure helpers.

Behaviour:

- The bridge listens on every existing context/page when it attaches,
  then auto-hooks every newly-opened context/page so multi-window
  Claude Desktop sessions still get captured.
- We send a **request** row and a **response** row with a shared
  ``pair_id`` for each matched response, mirroring the shape PCE Core
  expects for normalisation.
- A regex allowlist filters traffic to Anthropic API endpoints; non-
  matching responses are silently dropped (no noisy logs).
- On capture failure the bridge logs at WARNING and keeps running.
  The product guarantee is "never break Claude Desktop because of PCE".
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("pce.app_launcher.claude_desktop.capture_bridge")


# Anthropic-only allowlist by default. Users can pass their own
# patterns to capture other Electron AI apps that share the same
# launcher (Cursor / Windsurf — P5.B.3).
DEFAULT_URL_PATTERNS: tuple[str, ...] = (
    r"^https://api\.anthropic\.com/v1/.*",
    r"^https://claude\.ai/api/.*",
)

DEFAULT_PCE_CORE_URL = "http://127.0.0.1:9800"
DEFAULT_HTTP_TIMEOUT = 3.0  # seconds — never block Claude UI
MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB / row safety cap


# ---------------------------------------------------------------------------
# Pure helpers (no Playwright dep)
# ---------------------------------------------------------------------------


def url_matches(url: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(url or "") for p in patterns)


def host_path_from_url(url: str) -> tuple[str, str]:
    """Best-effort split of an absolute URL into ``(host, path)``."""
    if not url:
        return "", ""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return host, path or "/"


def provider_from_host(host: str) -> str:
    h = (host or "").lower()
    if "anthropic.com" in h or "claude.ai" in h:
        return "anthropic"
    if "openai.com" in h or "chatgpt.com" in h:
        return "openai"
    if "google" in h:
        return "google"
    return "unknown"


def truncate(body: str, *, max_bytes: int = MAX_BODY_BYTES) -> str:
    if not body:
        return ""
    if len(body) > max_bytes:
        return body[:max_bytes]
    return body


def build_capture_event(
    *,
    direction: str,
    url: str,
    method: str,
    status_code: Optional[int],
    headers: Mapping[str, str],
    body: str,
    pair_id: str,
    app_name: str = "claude-desktop",
    extra_meta: Optional[dict] = None,
) -> dict:
    """Return a ``CaptureIn``-compatible dict for POST /api/v1/captures.

    Keep this function side-effect-free — the tests assert exact field
    layouts so any implicit transformation breaks downstream contracts.
    """
    if direction not in ("request", "response"):
        raise ValueError(f"direction must be 'request' or 'response', got {direction!r}")
    host, path = host_path_from_url(url)
    headers_safe = {k: str(v) for k, v in dict(headers or {}).items()}
    meta = {
        "launcher.app_name": app_name,
        "launcher.url": url,
    }
    if extra_meta:
        meta.update(extra_meta)
    payload: dict[str, Any] = {
        "source_type": "desktop_electron",
        "source_name": "pce-app-launcher",
        "direction": direction,
        "pair_id": pair_id,
        "provider": provider_from_host(host),
        "host": host,
        "path": path,
        "method": method or ("GET" if direction == "request" else ""),
        "headers_json": json.dumps(headers_safe, ensure_ascii=False, sort_keys=True),
        "body_json": truncate(body or ""),
        "body_format": "json" if _looks_like_json(body) else "text",
        "meta": meta,
    }
    if direction == "response" and status_code is not None:
        payload["status_code"] = int(status_code)
    return payload


def _looks_like_json(body: str) -> bool:
    s = (body or "").lstrip()
    return bool(s) and s[0] in ("{", "[")


# ---------------------------------------------------------------------------
# HTTP POST helper
# ---------------------------------------------------------------------------


def post_to_pce_core(
    payload: dict,
    *,
    pce_core_url: str = DEFAULT_PCE_CORE_URL,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    path: str = "/api/v1/captures",
) -> Optional[dict]:
    """POST a capture payload to pce_core. Returns the JSON response, or None on failure.

    Soft-fails on every error path — the bridge must keep running even
    if pce_core is unreachable.
    """
    url = pce_core_url.rstrip("/") + path
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "pce-app-launcher/0.1.0",
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return None
    except (URLError, OSError, ValueError) as exc:
        logger.warning(
            "pce_core POST failed: %s (url=%s)",
            exc, url,
        )
        return None
    except Exception:
        logger.exception("pce_core POST failed unexpectedly")
        return None


# ---------------------------------------------------------------------------
# CaptureBridge — Playwright orchestrator
# ---------------------------------------------------------------------------


@dataclass
class BridgeStats:
    matched: int = 0
    written: int = 0
    failed: int = 0
    started_at: Optional[float] = None
    last_url: Optional[str] = None


class CaptureBridge:
    """Lifecycle wrapper around ``connect_over_cdp`` + page hooking.

    Not thread-safe; create one per CDP endpoint and call ``start()``
    once. ``stop()`` is idempotent. ``snapshot()`` is safe to call from
    any thread.
    """

    def __init__(
        self,
        cdp_endpoint: str,
        *,
        url_patterns: Optional[list[str]] = None,
        pce_core_url: str = DEFAULT_PCE_CORE_URL,
        app_name: str = "claude-desktop",
        on_capture: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._cdp_endpoint = cdp_endpoint
        self._patterns = [
            re.compile(p) for p in (url_patterns or DEFAULT_URL_PATTERNS)
        ]
        self._pce_core_url = pce_core_url
        self._app_name = app_name
        self._on_capture = on_capture

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._stats_lock = threading.Lock()
        self._stats = BridgeStats()
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self, *, timeout: float = 15.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._started_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever,
            name="pce-capture-bridge",
            daemon=True,
        )
        self._thread.start()
        if not self._started_event.wait(timeout=timeout):
            raise TimeoutError(
                f"CaptureBridge did not connect to {self._cdp_endpoint} within {timeout}s"
            )

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def snapshot(self) -> dict:
        with self._stats_lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "matched": self._stats.matched,
                "written": self._stats.written,
                "failed": self._stats.failed,
                "started_at": self._stats.started_at,
                "last_url": self._stats.last_url,
                "last_error": self._last_error,
                "cdp_endpoint": self._cdp_endpoint,
            }

    # ------------------------------------------------------------------
    # Internal — runs on the bridge's own thread
    # ------------------------------------------------------------------

    def _run_forever(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except Exception as exc:
            self._last_error = f"playwright_import_failed: {exc}"
            self._started_event.set()
            return

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(self._cdp_endpoint)
                with self._stats_lock:
                    self._stats.started_at = time.time()

                # Hook every existing page in every context.
                for ctx in browser.contexts:
                    self._wire_context(ctx)
                # Hook future contexts/pages.
                browser.on("context", lambda c: self._wire_context(c))

                self._started_event.set()

                # Park until stop() is called.
                while not self._stop_event.is_set():
                    self._stop_event.wait(timeout=0.5)

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("CaptureBridge connect_over_cdp failed")
            self._started_event.set()

    def _wire_context(self, context: Any) -> None:
        try:
            for page in context.pages:
                self._wire_page(page)
            context.on("page", self._wire_page)
        except Exception:
            logger.exception("CaptureBridge: context wiring failed")

    def _wire_page(self, page: Any) -> None:
        try:
            page.on("response", self._on_response)
        except Exception:
            logger.exception("CaptureBridge: page wiring failed")

    def _on_response(self, response: Any) -> None:
        try:
            url = getattr(response, "url", "") or ""
            if not url_matches(url, self._patterns):
                return

            with self._stats_lock:
                self._stats.matched += 1
                self._stats.last_url = url

            request = getattr(response, "request", None)
            method = getattr(request, "method", "GET") if request else "GET"
            req_headers = self._safe_headers(request)
            res_headers = self._safe_headers(response)
            req_body = self._safe_request_body(request)
            res_body = self._safe_response_body(response)
            status_code = int(getattr(response, "status", 0) or 0)

            pair_id = uuid.uuid4().hex

            req_payload = build_capture_event(
                direction="request",
                url=url,
                method=method,
                status_code=None,
                headers=req_headers,
                body=req_body,
                pair_id=pair_id,
                app_name=self._app_name,
            )
            res_payload = build_capture_event(
                direction="response",
                url=url,
                method=method,
                status_code=status_code,
                headers=res_headers,
                body=res_body,
                pair_id=pair_id,
                app_name=self._app_name,
            )

            ok = (
                post_to_pce_core(req_payload, pce_core_url=self._pce_core_url) is not None
                and post_to_pce_core(res_payload, pce_core_url=self._pce_core_url) is not None
            )
            with self._stats_lock:
                if ok:
                    self._stats.written += 1
                else:
                    self._stats.failed += 1

            if ok and self._on_capture is not None:
                try:
                    self._on_capture({
                        "pair_id": pair_id, "url": url,
                        "host": req_payload["host"],
                        "status": status_code,
                    })
                except Exception:
                    logger.debug("on_capture callback raised", exc_info=True)
        except Exception:
            with self._stats_lock:
                self._stats.failed += 1
            logger.exception("CaptureBridge response handler failed")

    # ------------------------------------------------------------------
    # Defensive accessors mirroring pce_core/cdp/driver.py shape.
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_headers(obj: Any) -> Mapping[str, str]:
        if obj is None:
            return {}
        h = getattr(obj, "headers", None)
        if h is None:
            return {}
        if callable(h):
            try:
                h = h()
            except Exception:
                return {}
        try:
            return {str(k): str(v) for k, v in dict(h).items()}
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
        return truncate(str(post_data or ""))

    @staticmethod
    def _safe_response_body(response: Any) -> str:
        if response is None:
            return ""
        try:
            text = response.text()
        except Exception:
            return ""
        return truncate(text or "")
