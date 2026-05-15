# SPDX-License-Identifier: Apache-2.0
"""Trigger fresh TLS handshakes to AI hosts so a co-running tshark capture
on the system has something to decrypt.

Background: TLS sessions are typically long-lived (HTTP/2 keep-alives,
session resumption). If you start ``pce_sslkeylog run`` on a busy system,
tshark sees only mid-session encrypted records — without the original
ClientHello it can't extract the ``client_random`` it needs to look up
session keys in SSLKEYLOGFILE. So no decryption, no http2 frames, no
captures.

Running this script alongside ``pce_sslkeylog run`` opens fresh HTTPS
connections to a handful of AI provider endpoints. Each fresh connection
performs a brand-new TLS handshake that tshark CAN see end-to-end, plus
writes session keys to ``SSLKEYLOGFILE`` (we configure the SSLContext's
``keylog_filename`` so Python contributes to the same keylog file the
Chromium apps write to).

Usage::

    python scripts/sslkeylog_trigger.py             # default URLs
    python scripts/sslkeylog_trigger.py https://api.openai.com/v1/models
"""

from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URLS = [
    # GET on api.anthropic.com root returns 404 — we don't care about the
    # response, we just want a TLS handshake to a real AI provider.
    "https://api.anthropic.com/",
    "https://claude.ai/",
    "https://chatgpt.com/",
    "https://generativelanguage.googleapis.com/",
    # Web frontends that aren't APIs — useful for W2.1 V-PARTIAL→V-GREEN sweeps
    "https://gemini.google.com/",
    "https://grok.com/",
    "https://aistudio.google.com/",
    # IDE / CLI backends (typically NOT proxied via system proxy):
    "https://api2.cursor.sh/",
    "https://server.codeium.com/",
    "https://api.openai.com/",
]


def _build_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    keylog_path = os.environ.get("SSLKEYLOGFILE")
    if keylog_path:
        # ssl.SSLContext supports keylog_filename since Python 3.8 — Python
        # appends one line per (master_secret, label) tuple, identical to
        # Chromium's format. tshark reads the same file.
        try:
            ctx.keylog_filename = keylog_path
            print(f"[trigger] keylog -> {keylog_path}")
        except AttributeError:
            print("[trigger] WARN: Python ssl lacks keylog_filename support")
    else:
        print("[trigger] WARN: SSLKEYLOGFILE env var not set; not writing keys")
    return ctx


def main(argv: list[str]) -> int:
    # Parse a tiny inline arg: ``--no-proxy`` forces direct WAN
    # connections (skip system proxy / Clash). Useful for verifying
    # that the WLAN/ethernet leg of multi-iface tshark works.
    no_proxy = False
    args = list(argv[1:])
    if "--no-proxy" in args:
        no_proxy = True
        args.remove("--no-proxy")
    urls = args if args else DEFAULT_URLS
    ctx = _build_ctx()
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    proxy = None if no_proxy else (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    )
    if proxy is None and not no_proxy:
        # On Windows, system proxy is in registry; we don't read it here.
        # Default to Clash if it's listening.
        try:
            import socket
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(("127.0.0.1", 7890))
            s.close()
            proxy = "http://127.0.0.1:7890"
            print("[trigger] using detected Clash proxy: 127.0.0.1:7890")
        except OSError:
            pass
    if proxy:
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy, "https": proxy,
        }))
    elif no_proxy:
        # Explicitly install an empty ProxyHandler so urllib won't read
        # the registry / env on its own.
        handlers.append(urllib.request.ProxyHandler({}))
        print("[trigger] --no-proxy: bypassing system proxy (direct WAN)")
    opener = urllib.request.build_opener(*handlers)

    for url in urls:
        t0 = time.time()
        try:
            resp = opener.open(url, timeout=10)
            status = resp.status
            body_len = len(resp.read(256))  # read a bit so the session has app data
            resp.close()
            print(f"[trigger] {url} -> {status} ({body_len}b, "
                  f"{(time.time()-t0)*1000:.0f}ms)  [HTTP/1.1]")
        except urllib.error.HTTPError as e:
            print(f"[trigger] {url} -> HTTP {e.code} (expected for unauth GET)")
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            print(f"[trigger] {url} -> FAILED: {e}")
        time.sleep(0.5)  # give tshark time to pair the request/response

    # Also trigger HTTP/2 connections via httpx (if installed). Real Chrome
    # negotiates HTTP/2 to api.anthropic.com / claude.ai via ALPN, so an
    # end-to-end smoke test needs to prove the HTTP/2 path works too.
    try:
        import httpx as _httpx  # noqa: PLC0415
    except ImportError:
        print("[trigger] httpx not installed; skipping HTTP/2 trigger")
        return 0
    print()
    print(f"[trigger] === HTTP/2 trigger via httpx ({'no proxy' if no_proxy else 'via ' + (proxy or 'direct')}) ===")
    proxy_dict: dict[str, str] = {}
    if proxy and not no_proxy:
        proxy_dict = {"http://": proxy, "https://": proxy}
    try:
        with _httpx.Client(http2=True, verify=ctx, proxies=proxy_dict,
                            timeout=10.0) as client:
            for url in urls:
                t0 = time.time()
                try:
                    r = client.get(url)
                    h2_used = r.http_version
                    print(f"[trigger] {url} -> {r.status_code} ({len(r.content)}b, "
                          f"{(time.time()-t0)*1000:.0f}ms)  [{h2_used}]")
                except Exception as e:  # noqa: BLE001
                    print(f"[trigger] httpx {url} -> FAILED: {e}")
                time.sleep(0.5)
    except TypeError:
        # httpx 0.28+ renamed `proxies` to `proxy`
        client_kwargs = {"http2": True, "verify": ctx, "timeout": 10.0}
        if proxy:
            client_kwargs["proxy"] = proxy
        with _httpx.Client(**client_kwargs) as client:
            for url in urls:
                t0 = time.time()
                try:
                    r = client.get(url)
                    h2_used = r.http_version
                    print(f"[trigger] {url} -> {r.status_code} ({len(r.content)}b, "
                          f"{(time.time()-t0)*1000:.0f}ms)  [{h2_used}]")
                except Exception as e:  # noqa: BLE001
                    print(f"[trigger] httpx {url} -> FAILED: {e}")
                time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
