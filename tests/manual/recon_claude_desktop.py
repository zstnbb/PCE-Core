# SPDX-License-Identifier: Apache-2.0
"""Phase 4.0 · Claude Desktop super-app surface reconnaissance.

Goal: enumerate **every** HTTP request/response and **every**
WebSocket frame Claude Desktop emits while the user clicks through
its 8 first-class surfaces (Chat / Cowork / Code / Projects /
Artifacts / Customize / Voice / Quick actions). The output is
analysed by :mod:`tests.manual.analyze_recon` to produce a scope
decision table — what we already capture, what we miss, what we
should defer.

This tool intentionally:

* **Does NOT post to PCE Core.** Recon traffic must not pollute
  ``raw_captures``. Output goes to ``tests/manual/recon_<ts>/`` JSONL.
* **Does NOT filter URL patterns.** The whole point is to discover
  what we don't know. Catch-all is the contract.
* **DOES hook WebSocket frames** in addition to HTTP responses
  (capture_bridge currently only listens to ``Network.responseReceived``).

Usage::

    # Make sure Claude Desktop is fully closed first.
    python -m tests.manual.recon_claude_desktop --duration 3600

    # Or attach to an already-running Claude Desktop with debug port:
    python -m tests.manual.recon_claude_desktop --no-launch \\
        --cdp-endpoint http://127.0.0.1:9222

While running, type marker commands at the prompt::

    > mark chat-vanilla       # tag upcoming traffic as 'chat-vanilla'
    > mark cowork-open
    > mark code-tab-click
    > note CSP redirected here
    > stop                    # graceful shutdown
    > help                    # show all commands

After the run, analyse with::

    python -m tests.manual.analyze_recon tests/manual/recon_<ts>/

See ``tests/manual/RECON-CHECKLIST.md`` for the recommended
click-through order.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("pce.recon.claude_desktop")

# Inline body preview cap. We never write full bodies inline — they
# would balloon the JSONL. Use ``--full-bodies`` to opt in to per-event
# files.
PREVIEW_BYTES = 4 * 1024


@dataclass
class ReconStats:
    started_at: float = 0.0
    http_response: int = 0
    http_failed: int = 0
    ws_open: int = 0
    ws_recv: int = 0
    ws_sent: int = 0
    ws_close: int = 0
    markers: int = 0
    last_event_ts: float = 0.0


@dataclass
class ReconConfig:
    duration_s: float
    output_dir: Path
    full_bodies: bool
    cdp_endpoint: Optional[str]
    launch: bool
    print_progress_every_s: float = 10.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _truncate(s: Any, *, max_bytes: int = PREVIEW_BYTES) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        try:
            s = json.dumps(s, ensure_ascii=False)
        except Exception:
            s = repr(s)
    if len(s) > max_bytes:
        return s[:max_bytes] + f"…<truncated {len(s)-max_bytes} bytes>"
    return s


def _safe_call(obj: Any, attr: str, default: Any = "") -> Any:
    """Safely fetch ``obj.attr``, calling it if it's callable."""
    val = getattr(obj, attr, default)
    if callable(val):
        try:
            return val()
        except Exception:
            return default
    return val


def _safe_headers(obj: Any) -> dict:
    h = _safe_call(obj, "headers", {})
    try:
        return {str(k): str(v) for k, v in dict(h).items()}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Recon writer (thread-safe JSONL appender)
# ---------------------------------------------------------------------------


class ReconWriter:
    def __init__(self, out_dir: Path, *, full_bodies: bool):
        self.out_dir = out_dir
        self.events_path = out_dir / "events.jsonl"
        self.markers_path = out_dir / "markers.jsonl"
        self.full_bodies_dir = out_dir / "bodies" if full_bodies else None
        self.full_bodies = full_bodies
        self._lock = threading.Lock()
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.full_bodies_dir is not None:
            self.full_bodies_dir.mkdir(exist_ok=True)
        self._events_fp = self.events_path.open("a", encoding="utf-8")
        self._markers_fp = self.markers_path.open("a", encoding="utf-8")

    def write_event(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            self._events_fp.write(line + "\n")
            self._events_fp.flush()

    def write_marker(self, marker: dict) -> None:
        # Markers go into BOTH files so analyzer can split events by
        # marker without a join.
        line = json.dumps(marker, ensure_ascii=False, default=str)
        with self._lock:
            self._markers_fp.write(line + "\n")
            self._markers_fp.flush()
            self._events_fp.write(line + "\n")
            self._events_fp.flush()

    def write_full_body(self, pair_id: str, kind: str, body: str) -> Optional[str]:
        """Save a full body to disk if ``--full-bodies`` is on. Returns relative path."""
        if self.full_bodies_dir is None or not body:
            return None
        rel = f"bodies/{pair_id}_{kind}.txt"
        p = self.out_dir / rel
        try:
            p.write_text(body, encoding="utf-8", errors="replace")
            return rel
        except Exception:
            return None

    def close(self) -> None:
        with self._lock:
            try:
                self._events_fp.close()
                self._markers_fp.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Playwright wiring (sync API, runs on a dedicated thread)
# ---------------------------------------------------------------------------


class ReconBridge:
    """Connects to CDP and forwards every event to a :class:`ReconWriter`."""

    def __init__(
        self,
        *,
        cdp_endpoint: str,
        writer: ReconWriter,
        stats: ReconStats,
        stop_event: threading.Event,
    ) -> None:
        self._cdp_endpoint = cdp_endpoint
        self._writer = writer
        self._stats = stats
        self._stop_event = stop_event
        self._stats_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._last_error: Optional[str] = None

    def start(self, *, timeout: float = 15.0) -> None:
        self._thread = threading.Thread(
            target=self._run, name="pce-recon-bridge", daemon=True
        )
        self._thread.start()
        if not self._started.wait(timeout=timeout):
            raise TimeoutError(
                f"Recon bridge did not connect to {self._cdp_endpoint} within {timeout}s"
            )
        if self._last_error:
            raise RuntimeError(f"Recon bridge failed: {self._last_error}")

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except Exception as exc:
            self._last_error = f"playwright_import_failed: {exc}"
            self._started.set()
            return

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(self._cdp_endpoint)
                with self._stats_lock:
                    self._stats.started_at = time.time()

                for ctx in browser.contexts:
                    self._wire_context(ctx)
                browser.on("context", lambda c: self._wire_context(c))

                self._started.set()

                while not self._stop_event.is_set():
                    self._stop_event.wait(timeout=0.5)

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Recon bridge connect_over_cdp failed")
            self._started.set()

    def _wire_context(self, context: Any) -> None:
        try:
            for page in context.pages:
                self._wire_page(page)
            context.on("page", self._wire_page)
        except Exception:
            logger.exception("Recon: context wiring failed")

    def _wire_page(self, page: Any) -> None:
        try:
            page.on("response", self._on_response)
            page.on("websocket", self._on_websocket)
        except Exception:
            logger.exception("Recon: page wiring failed")

    # ------------------------------------------------------------------

    def _on_response(self, response: Any) -> None:
        try:
            url = getattr(response, "url", "") or ""
            request = getattr(response, "request", None)
            method = _safe_call(request, "method", "GET") if request else "GET"
            status = int(_safe_call(response, "status", 0) or 0)
            req_headers = _safe_headers(request)
            res_headers = _safe_headers(response)

            content_type = (
                res_headers.get("content-type") or res_headers.get("Content-Type") or ""
            )

            req_body = ""
            if request is not None:
                pd = _safe_call(request, "post_data", "")
                req_body = pd if isinstance(pd, str) else ""

            # Body fetch may fail for binary / streaming. Be defensive.
            try:
                res_body = response.text()
            except Exception:
                res_body = "<binary or stream-failed>"

            pair_id = f"r{int(time.time()*1000)}-{id(response) & 0xffffff:06x}"

            event = {
                "ts": time.time(),
                "kind": "http_response",
                "pair_id": pair_id,
                "url": url,
                "method": method,
                "status": status,
                "content_type": content_type,
                "req_headers": req_headers,
                "res_headers": res_headers,
                "req_body_preview": _truncate(req_body),
                "res_body_preview": _truncate(res_body),
                "req_body_bytes": len(req_body) if isinstance(req_body, str) else 0,
                "res_body_bytes": len(res_body) if isinstance(res_body, str) else 0,
            }

            if self._writer.full_bodies:
                event["req_body_path"] = self._writer.write_full_body(
                    pair_id, "req", req_body
                )
                event["res_body_path"] = self._writer.write_full_body(
                    pair_id, "res", res_body
                )

            self._writer.write_event(event)
            with self._stats_lock:
                self._stats.http_response += 1
                self._stats.last_event_ts = event["ts"]
        except Exception:
            logger.exception("Recon: response handler failed")
            with self._stats_lock:
                self._stats.http_failed += 1

    def _on_websocket(self, ws: Any) -> None:
        try:
            url = getattr(ws, "url", "") or ""
            ws_id = f"ws{int(time.time()*1000)}-{id(ws) & 0xffffff:06x}"

            self._writer.write_event({
                "ts": time.time(),
                "kind": "ws_open",
                "ws_id": ws_id,
                "url": url,
            })
            with self._stats_lock:
                self._stats.ws_open += 1

            ws.on(
                "framereceived",
                lambda payload: self._on_ws_frame(ws_id, url, "ws_recv", payload),
            )
            ws.on(
                "framesent",
                lambda payload: self._on_ws_frame(ws_id, url, "ws_sent", payload),
            )
            ws.on(
                "close",
                lambda: self._writer.write_event({
                    "ts": time.time(),
                    "kind": "ws_close",
                    "ws_id": ws_id,
                    "url": url,
                }) or self._bump_stat("ws_close"),
            )
        except Exception:
            logger.exception("Recon: websocket wiring failed")

    def _on_ws_frame(self, ws_id: str, url: str, kind: str, payload: Any) -> None:
        try:
            if isinstance(payload, (bytes, bytearray)):
                preview = f"<binary {len(payload)} bytes>"
                bytes_n = len(payload)
            else:
                preview = _truncate(payload)
                bytes_n = len(payload) if isinstance(payload, str) else 0

            self._writer.write_event({
                "ts": time.time(),
                "kind": kind,
                "ws_id": ws_id,
                "url": url,
                "payload_preview": preview,
                "payload_bytes": bytes_n,
            })
            with self._stats_lock:
                if kind == "ws_recv":
                    self._stats.ws_recv += 1
                elif kind == "ws_sent":
                    self._stats.ws_sent += 1
                self._stats.last_event_ts = time.time()
        except Exception:
            logger.exception("Recon: ws frame handler failed")

    def _bump_stat(self, name: str) -> None:
        with self._stats_lock:
            setattr(self._stats, name, getattr(self._stats, name, 0) + 1)


# ---------------------------------------------------------------------------
# Stdin REPL (markers + control)
# ---------------------------------------------------------------------------


HELP_TEXT = """
Available commands:
  mark <label>          tag upcoming traffic with this label (e.g. 'chat-vanilla')
  note <text>           add a free-form note to the timeline
  stats                 print current event counts
  list-markers          list markers recorded so far
  stop                  graceful shutdown
  help                  show this message
"""


def _run_repl(
    *,
    writer: ReconWriter,
    stats: ReconStats,
    stop_event: threading.Event,
) -> None:
    print(HELP_TEXT)
    sys.stdout.flush()
    markers: list[dict] = []

    while not stop_event.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[recon] EOF / interrupt — initiating graceful stop")
            stop_event.set()
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd == "mark":
            if not rest:
                print("[recon] usage: mark <label>")
                continue
            marker = {
                "ts": time.time(),
                "kind": "marker",
                "label": rest.strip(),
            }
            writer.write_marker(marker)
            markers.append(marker)
            stats.markers += 1
            print(f"[recon] marker recorded: {rest!r}")
        elif cmd == "note":
            if not rest:
                print("[recon] usage: note <text>")
                continue
            writer.write_marker({
                "ts": time.time(),
                "kind": "note",
                "text": rest.strip(),
            })
            print("[recon] note recorded")
        elif cmd == "stats":
            print(json.dumps({
                "started_at": stats.started_at,
                "elapsed_s": round(time.time() - stats.started_at, 1),
                "http_response": stats.http_response,
                "http_failed": stats.http_failed,
                "ws_open": stats.ws_open,
                "ws_recv": stats.ws_recv,
                "ws_sent": stats.ws_sent,
                "ws_close": stats.ws_close,
                "markers": stats.markers,
            }, indent=2))
        elif cmd == "list-markers":
            if not markers:
                print("[recon] (no markers yet)")
            else:
                for m in markers:
                    print(f"  {time.strftime('%H:%M:%S', time.localtime(m['ts']))}  {m['label']}")
        elif cmd in ("stop", "exit", "quit"):
            print("[recon] graceful stop")
            stop_event.set()
            break
        elif cmd in ("help", "?"):
            print(HELP_TEXT)
        else:
            print(f"[recon] unknown command: {cmd!r}. Type 'help'.")


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _resolve_endpoint(cfg: ReconConfig) -> tuple[str, Optional[Any]]:
    """Either launch Claude Desktop or attach to an existing CDP endpoint.

    Returns ``(cdp_endpoint, launcher_handle_or_None)``. The handle is
    None if the user passed ``--no-launch``; otherwise it's a
    :class:`pce_app_launcher.claude_desktop.launcher.LauncherHandle`
    so we can terminate the spawned process on exit.
    """
    if not cfg.launch:
        if not cfg.cdp_endpoint:
            raise SystemExit(
                "error: --no-launch requires --cdp-endpoint http://127.0.0.1:9222"
            )
        return cfg.cdp_endpoint, None

    from pce_app_launcher.claude_desktop.detector import detect_claude_desktop
    from pce_app_launcher.claude_desktop.launcher import launch_claude_desktop

    install = detect_claude_desktop()
    if install is None:
        raise SystemExit(
            "error: Claude Desktop not detected. Install from "
            "https://claude.ai/download or pass --no-launch with --cdp-endpoint."
        )

    handle = launch_claude_desktop(install, auto_pick_port=True)
    print(
        f"[recon] Claude Desktop launched "
        f"(pid={handle.process.pid}; CDP {handle.cdp_endpoint})"
    )
    return handle.cdp_endpoint, handle


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Phase 4.0 Claude Desktop super-app recon"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3600.0,
        help="Maximum run duration in seconds (default 3600 = 60 min)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("tests/manual"),
        help="Where to write recon_<ts>/ artifacts",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Don't launch Claude Desktop; attach to an existing CDP endpoint instead",
    )
    parser.add_argument(
        "--cdp-endpoint",
        type=str,
        default=None,
        help="Existing CDP endpoint URL (only with --no-launch)",
    )
    parser.add_argument(
        "--full-bodies",
        action="store_true",
        help="Save full request/response bodies to per-event files (uses more disk)",
    )

    args = parser.parse_args(argv)

    ts = time.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_root / f"recon_{ts}"

    cfg = ReconConfig(
        duration_s=args.duration,
        output_dir=output_dir,
        full_bodies=args.full_bodies,
        cdp_endpoint=args.cdp_endpoint,
        launch=not args.no_launch,
    )

    cdp_endpoint, launcher_handle = _resolve_endpoint(cfg)

    writer = ReconWriter(output_dir, full_bodies=cfg.full_bodies)
    stats = ReconStats()
    stop_event = threading.Event()

    # Write meta.json upfront so analyzer can find it even on crash.
    meta = {
        "started_at": time.time(),
        "cdp_endpoint": cdp_endpoint,
        "launched_by_recon": cfg.launch,
        "duration_s": cfg.duration_s,
        "full_bodies": cfg.full_bodies,
        "argv": sys.argv,
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    bridge = ReconBridge(
        cdp_endpoint=cdp_endpoint,
        writer=writer,
        stats=stats,
        stop_event=stop_event,
    )
    bridge.start()
    print(f"[recon] writing to {output_dir}")
    print(f"[recon] catching ALL HTTP responses + WebSocket frames (no filter)")

    repl_thread = threading.Thread(
        target=_run_repl,
        args=(),
        kwargs={"writer": writer, "stats": stats, "stop_event": stop_event},
        name="pce-recon-repl",
        daemon=True,
    )
    repl_thread.start()

    deadline = time.time() + cfg.duration_s
    last_progress = 0.0
    try:
        while not stop_event.is_set():
            if time.time() >= deadline:
                print(f"[recon] duration {cfg.duration_s}s elapsed — stopping")
                stop_event.set()
                break
            time.sleep(1.0)

            now = time.time()
            if now - last_progress >= cfg.print_progress_every_s:
                last_progress = now
                print(
                    f"[recon] http={stats.http_response} "
                    f"ws_recv={stats.ws_recv} ws_sent={stats.ws_sent} "
                    f"markers={stats.markers} "
                    f"elapsed={int(now - stats.started_at)}s"
                )
    except KeyboardInterrupt:
        print("\n[recon] Ctrl+C — stopping")
        stop_event.set()
    finally:
        bridge.stop()
        writer.close()
        if launcher_handle is not None:
            launcher_handle.terminate()

        summary = {
            "ended_at": time.time(),
            "started_at": stats.started_at,
            "elapsed_s": round(time.time() - stats.started_at, 1) if stats.started_at else 0,
            "http_response": stats.http_response,
            "http_failed": stats.http_failed,
            "ws_open": stats.ws_open,
            "ws_recv": stats.ws_recv,
            "ws_sent": stats.ws_sent,
            "ws_close": stats.ws_close,
            "markers": stats.markers,
            "output_dir": str(output_dir),
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(f"[recon] final summary: {json.dumps(summary, indent=2)}")
        print(
            f"[recon] analyse with: python -m tests.manual.analyze_recon "
            f"{output_dir}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
