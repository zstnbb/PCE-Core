# SPDX-License-Identifier: Apache-2.0
"""Warm up Cloudflare-blocked sites via a residential HTTP proxy.

Why this script exists
----------------------
The PCE managed Chrome profile usually exits via an IDC/datacenter ASN
(e.g. AS10111 Zeofast Network), which Cloudflare flags as high-risk for
sites with the strictest bot rules (Claude.ai, Perplexity.ai). Even a
fingerprint-perfect Selenium session is held at the JS challenge.

But ``~/.pce/chrome_profile`` PERSISTS cookies across launches. So if we
log in *once* via a clean residential IP, Cloudflare grants those
cookies a free pass on subsequent visits even from the original IDC IP.

This script:
  1. Reads HTTP-proxy credentials from ``PCE_HTTP_PROXY`` env var.
  2. Spins up a local CONNECT-proxy wrapper on 127.0.0.1:<auto-port>
     that injects ``Proxy-Authorization: Basic ...`` into every upstream
     request -- this is the workaround for Chrome's refusal to honor
     credentials embedded in ``--proxy-server`` URLs and the fact that
     Chrome 137+ disables MV2 webRequest auth helpers.
  3. Launches Chrome through the wrapper, with PCE extension + stealth.
  4. Verifies the exit IP via ipinfo.io.
  5. Opens Claude + Perplexity with stealth applied pre- AND post-
     navigation so Cloudflare samples the patched fingerprint.
  6. Babysits while the user logs in manually.

Usage (PowerShell)
------------------
::

    $env:PCE_HTTP_PROXY = "host:port:user:pass"
    python tests/e2e/_warmup_login_via_proxy.py

The credential string is consumed only at runtime; nothing is committed
to git. After login, unset the env var and discard it.
"""

from __future__ import annotations

import base64
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Make ``from tests.e2e.* import`` work regardless of how the script is
# invoked (mirrors pce_site_probe.py / _open_login_tabs.py).
if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent.parent))
    __package__ = "tests.e2e"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXTENSION_DIR = (
    PROJECT_ROOT / "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
)
MANAGED_PROFILE = Path.home() / ".pce" / "chrome_profile"

# Only the two sites that the IDC IP gets blocked on. Everything else
# already works through the regular ``_open_login_tabs.py`` flow, so we
# do NOT spend the residential proxy's metered bandwidth on them.
WARMUP_SITES: list[tuple[str, str]] = [
    ("claude",     "https://claude.ai/login"),
    ("perplexity", "https://www.perplexity.ai/"),
]


# --------------------------------------------------------------------------- #
# Local CONNECT-proxy wrapper that injects HTTP Basic auth for upstream.
# --------------------------------------------------------------------------- #


def _open_upstream(
    target_host: str,
    target_port: int,
    via_host: str | None,
    via_port: int | None,
    timeout: float = 15.0,
) -> socket.socket:
    """Open a TCP connection to ``target_host:target_port``, optionally
    chained through an upstream HTTP CONNECT proxy ``via_host:via_port``.

    The "via" hop is for environments where the residential proxy is
    only reachable via the user's existing local proxy (Clash, etc.) —
    typical in CN networks where direct TCP to overseas hosts is dropped
    by the GFW. The first hop is assumed to require NO auth (Clash's
    127.0.0.1:7890 is auth-free by default).

    Raises ``OSError`` / ``ConnectionError`` on failure so the caller
    can log and abort the per-request handler.
    """
    if via_host is None or via_port is None:
        return socket.create_connection((target_host, target_port), timeout=timeout)

    # Hop 1: connect to the local proxy (Clash etc.).
    s = socket.create_connection((via_host, via_port), timeout=timeout)
    s.settimeout(timeout)

    # Ask the local proxy to CONNECT to the residential proxy.
    connect_req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Proxy-Connection: Keep-Alive\r\n"
        f"\r\n"
    ).encode("ascii")
    s.sendall(connect_req)

    # Read until end of the response headers.
    resp = bytearray()
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            s.close()
            raise ConnectionError(
                f"first-hop proxy {via_host}:{via_port} closed during CONNECT"
            )
        resp.extend(chunk)
        if len(resp) > 65536:
            s.close()
            raise ConnectionError("first-hop CONNECT response too large")

    status_line = bytes(resp).split(b"\r\n", 1)[0].decode("latin-1", "replace")
    if " 200 " not in status_line and not status_line.endswith(" 200"):
        s.close()
        raise ConnectionError(
            f"first-hop CONNECT failed: {status_line!r} "
            f"(target was {target_host}:{target_port})"
        )

    s.settimeout(None)
    return s


class _AuthInjectingHandler(socketserver.BaseRequestHandler):
    """Read first HTTP request from Chrome, inject Proxy-Authorization,
    forward to upstream, then bridge bytes opaquely.

    Class attributes are populated by :func:`_start_proxy_wrapper` via a
    dynamically-built subclass (avoids globals / module-level state).
    """

    upstream_host: str = ""
    upstream_port: int = 0
    auth_b64: str = ""
    via_host: str | None = None
    via_port: int | None = None

    def handle(self) -> None:
        client = self.request
        client.settimeout(30.0)

        # Read up to the end of the request headers. The CONNECT method
        # used by Chrome for HTTPS targets is small (a few hundred
        # bytes), so a 64KB cap is plenty and prevents DoS.
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            try:
                chunk = client.recv(4096)
            except Exception:
                return
            if not chunk:
                return
            buf.extend(chunk)
            if len(buf) > 65536:
                return

        # Inject Proxy-Authorization right before the trailing \r\n\r\n
        # if Chrome did not already supply one (it never does because we
        # told it the proxy needs no auth).
        if b"Proxy-Authorization:" not in buf:
            idx = buf.find(b"\r\n\r\n")
            inject = f"\r\nProxy-Authorization: Basic {self.auth_b64}".encode()
            buf = buf[:idx] + inject + buf[idx:]

        try:
            upstream = _open_upstream(
                self.upstream_host,
                self.upstream_port,
                self.via_host,
                self.via_port,
                timeout=15.0,
            )
        except Exception as exc:
            sys.stderr.write(f"[proxy-wrapper] upstream connect failed: {exc}\n")
            return

        upstream.settimeout(None)
        client.settimeout(None)

        try:
            upstream.sendall(bytes(buf))
        except Exception:
            upstream.close()
            return

        # Peek the first chunk from upstream so we can surface auth/whitelist
        # failures (407 / 403) directly to the operator instead of letting
        # Chrome bury them in net-internals. We then forward the chunk and
        # bridge the rest. Best-effort: timeout/error => skip the peek.
        upstream.settimeout(8.0)
        try:
            first = upstream.recv(4096)
        except Exception:
            first = b""
        upstream.settimeout(None)

        if first.startswith(b"HTTP/"):
            status_line = first.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if " 200 " not in status_line and not status_line.endswith(" 200"):
                first_method = bytes(buf).split(b" ", 1)[0]
                sys.stderr.write(
                    f"[proxy-wrapper] upstream rejected {first_method.decode('ascii','replace')} "
                    f"-> {status_line!r}\n"
                )

        if first:
            try:
                client.sendall(first)
            except Exception:
                upstream.close()
                return

        # Bridge both directions until either side closes. CONNECT
        # tunnels are TLS-opaque after the upstream "200 OK", so we just
        # pump bytes; for plaintext HTTP the response body flows the
        # same way.
        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(8192)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        t = threading.Thread(target=pump, args=(client, upstream), daemon=True)
        t.start()
        pump(upstream, client)
        t.join(timeout=2.0)
        upstream.close()


class _ThreadingProxyServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _start_proxy_wrapper(
    upstream_host: str,
    upstream_port: int,
    user: str,
    password: str,
    via_host: str | None = None,
    via_port: int | None = None,
) -> tuple[str, _ThreadingProxyServer]:
    """Start a local CONNECT-proxy wrapper that injects Basic auth.

    If ``via_host:via_port`` is given, every upstream connection is
    chained through that local proxy first (e.g. Clash on
    ``127.0.0.1:7890``) -- this is required when the residential proxy
    isn't directly reachable due to GFW packet drops.

    Returns ``("127.0.0.1:LOCAL_PORT", server)``. The server runs on a
    daemon thread; caller may call ``server.shutdown()`` to stop it.
    """
    auth_b64 = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")

    handler_cls = type(
        "BoundAuthHandler",
        (_AuthInjectingHandler,),
        {
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "auth_b64": auth_b64,
            "via_host": via_host,
            "via_port": via_port,
        },
    )

    # Bind to an ephemeral port.
    server = _ThreadingProxyServer(("127.0.0.1", 0), handler_cls)
    local_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"127.0.0.1:{local_port}", server


# --------------------------------------------------------------------------- #
# Chrome launch + Selenium driver setup.
# --------------------------------------------------------------------------- #


def _parse_proxy(spec: str) -> tuple[str, int, str, str]:
    """Parse ``host:port:user:pass`` (max-split=3 so passwords with
    colons would be permitted; ip2up usernames contain hyphens not
    colons, so this is robust).
    """
    parts = spec.split(":", 3)
    if len(parts) != 4:
        raise ValueError(
            "Bad PCE_HTTP_PROXY format. Expected host:port:user:pass, "
            f"got {spec!r}"
        )
    host, port_s, user, password = parts
    return host, int(port_s), user, password


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_chrome() -> Path:
    candidates = [
        Path(os.environ.get("PCE_CHROME_BINARY", "")),
        Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for p in candidates:
        if p and p.is_file():
            return p
    raise FileNotFoundError(
        "Chrome not found. Set PCE_CHROME_BINARY env var to chrome.exe"
    )


def _parse_via(spec: str | None) -> tuple[str | None, int | None]:
    """Parse ``host:port`` for the optional first-hop tunnel."""
    if not spec:
        return None, None
    if ":" not in spec:
        raise ValueError(f"Bad PCE_HTTP_PROXY_VIA format. Expected host:port, got {spec!r}")
    host, port_s = spec.rsplit(":", 1)
    return host, int(port_s)


def main() -> int:
    spec = os.environ.get("PCE_HTTP_PROXY")
    if not spec:
        sys.stderr.write(
            "ERROR: set PCE_HTTP_PROXY first.\n"
            "  PowerShell:  $env:PCE_HTTP_PROXY = 'host:port:user:pass'\n"
            "Optional first-hop tunnel (Clash etc.):\n"
            "  $env:PCE_HTTP_PROXY_VIA = '127.0.0.1:7890'\n"
        )
        return 1

    try:
        ups_host, ups_port, ups_user, ups_pass = _parse_proxy(spec)
        via_host, via_port = _parse_via(os.environ.get("PCE_HTTP_PROXY_VIA"))
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    if not EXTENSION_DIR.is_dir():
        sys.stderr.write(
            f"ERROR: PCE extension build not found at {EXTENSION_DIR}.\n"
            f"Build it first: cd pce_browser_extension_wxt && pnpm build\n"
        )
        return 1

    chrome = _find_chrome()

    print(f"Upstream proxy : {ups_host}:{ups_port}  (user={ups_user[:24]}...)")
    if via_host:
        print(f"First-hop tunnel: {via_host}:{via_port}  (Clash etc.)")

    # Start the local auth-injecting wrapper.
    local_addr, proxy_server = _start_proxy_wrapper(
        ups_host, ups_port, ups_user, ups_pass,
        via_host=via_host, via_port=via_port,
    )
    print(f"Local auth-injecting proxy listening on {local_addr}")

    debug_port = _free_port()
    chrome_args = [
        str(chrome),
        f"--user-data-dir={MANAGED_PROFILE}",
        f"--remote-debugging-port={debug_port}",
        # Chrome strips embedded credentials from --proxy-server, so we
        # point it at our auth-free local wrapper. The wrapper is the
        # one that adds Proxy-Authorization on the upstream hop.
        f"--proxy-server=http://{local_addr}",
        "--enable-extensions",
        f"--load-extension={EXTENSION_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--remote-allow-origins=*",
        "--window-size=1280,900",
        "about:blank",
    ]

    print(f"Launching Chrome on debug port {debug_port} ...")
    chrome_proc = subprocess.Popen(
        chrome_args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    endpoint = f"http://127.0.0.1:{debug_port}/json/version"
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            urllib.request.urlopen(endpoint, timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        sys.stderr.write("ERROR: Chrome debugger did not start.\n")
        proxy_server.shutdown()
        return 1

    print(f"Chrome ready at 127.0.0.1:{debug_port}. Connecting Selenium ...")

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        from tests.e2e._stealth import apply_stealth, apply_stealth_all_tabs
        from tests.e2e.conftest import _get_chromedriver_path

        opts = Options()
        opts.debugger_address = f"127.0.0.1:{debug_port}"
        driver_path = _get_chromedriver_path()
        service = Service(driver_path) if driver_path else None
        driver = webdriver.Chrome(service=service, options=opts)

        # ---- Sanity check: confirm the residential exit IP --------------
        apply_stealth(driver, label="warmup:tab0:pre")
        print("\n--- Sanity check via ipinfo.io ---")
        try:
            driver.get("https://ipinfo.io/json")
            apply_stealth(driver, label="warmup:tab0:post")
            time.sleep(2.5)
            body_text = driver.find_element("tag name", "body").text.strip()
            print(body_text)
        except Exception as exc:
            print(f"  (ipinfo fetch failed: {exc})")

        # ---- Open Claude + Perplexity, applying stealth pre+post --------
        for name, url in WARMUP_SITES:
            driver.switch_to.new_window("tab")
            apply_stealth(driver, label=f"warmup:{name}:pre")
            try:
                driver.get(url)
            except Exception as exc:
                print(f"  WARN: navigation to {url} raised {exc!r}; "
                      "continuing -- the page may still be visible.")
            apply_stealth(driver, label=f"warmup:{name}:post")
            print(f"  Opened: {name:<12} {url}")

        applied = apply_stealth_all_tabs(driver, label="warmup:final")
        print(f"  Stealth registered on {applied}/"
              f"{len(driver.window_handles)} tabs")

        # Focus the Claude tab so the user lands there first.
        try:
            for h in driver.window_handles:
                driver.switch_to.window(h)
                if "claude" in (driver.current_url or "").lower():
                    break
        except Exception:
            pass

        bar = "=" * 60
        print(
            f"\n{bar}\n"
            "  Manually log in to Claude.ai and Perplexity.ai now.\n"
            f"  Cookies will be saved into:\n"
            f"    {MANAGED_PROFILE}\n"
            f"  Debug address: 127.0.0.1:{debug_port}\n"
            f"  Local proxy : {local_addr}  -> {ups_host}:{ups_port}\n"
            f"{bar}\n\n"
            "When done, close Chrome OR press Ctrl+C here.\n"
            "After cookies are saved you can switch back to your\n"
            "regular network -- Cloudflare will give the cookies a free\n"
            "pass for ~30-90 days.\n"
        )

        # Babysit. Exit cleanly when Chrome closes or user hits Ctrl+C.
        try:
            while True:
                if chrome_proc.poll() is not None:
                    print("Chrome exited; warm-up session ended.")
                    break
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nCtrl+C received. Chrome stays open if still running.")
    finally:
        try:
            proxy_server.shutdown()
        except Exception:
            pass
        try:
            proxy_server.server_close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
