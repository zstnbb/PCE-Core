# SPDX-License-Identifier: Apache-2.0
"""Mobile capture onboarding helper (P4.5).

Turning a phone into a first-class PCE capture source takes three
sub-tasks that are individually trivial but collectively annoying to
explain:

1. Tell the phone which proxy host + port to point at (PCE's mitmproxy
   listens on the workstation's LAN IP).
2. Install mitmproxy's root certificate so HTTPS interception works.
3. Trust that cert in the phone's system settings.

This module provides the first two as a single QR code + JSON payload
so the operator can scan it from their phone's camera and finish the
setup in under a minute.

Dependency posture
==================

- The core helpers (``get_lan_ip``, ``get_setup_info``) are **zero-dep**.
- The PNG rendering helper (``render_qr_png``) needs ``qrcode`` +
  ``pillow``. When either is missing we return a structured error
  envelope; the CLI prints an actionable hint; the HTTP endpoint
  returns a 503.
- The ASCII renderer (``render_qr_ascii``) needs only ``qrcode`` and
  falls back to "QR not available — open this URL on your phone
  instead" when it isn't installed.

All imports of ``qrcode`` / ``PIL`` are lazy so importing this module
on a bare install costs nothing.
"""

from __future__ import annotations

import io
import json
import logging
import socket
from typing import Any, Optional

from .config import PROXY_LISTEN_PORT

logger = logging.getLogger("pce.mobile_wizard")


# ---------------------------------------------------------------------------
# Feature probe — cheap + side-effect-free
# ---------------------------------------------------------------------------

def _qrcode_available() -> bool:
    try:
        import qrcode  # noqa: F401
        return True
    except Exception:
        return False


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# LAN address discovery
# ---------------------------------------------------------------------------

def get_lan_ip(*, probe_target: str = "8.8.8.8") -> str:
    """Best-effort detection of the host's outbound LAN IPv4 address.

    Strategy: open a UDP socket to a well-known external address (no
    packets are actually sent — ``connect`` on a UDP socket just selects
    a local source address via the OS routing table) and read back the
    bound local IP. Falls back to ``127.0.0.1`` if that fails, with a
    warning so the caller knows the QR code won't be reachable from
    another device.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((probe_target, 53))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and ip != "0.0.0.0":
            return ip
    except Exception:
        logger.debug("LAN IP probe failed; falling back to 127.0.0.1", exc_info=True)
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Setup payload
# ---------------------------------------------------------------------------

def get_setup_info(
    *,
    lan_ip: Optional[str] = None,
    port: Optional[int] = None,
) -> dict[str, Any]:
    """Return the JSON blob the mobile onboarding flow needs.

    Keys in the returned dict:

    - ``proxy_host``  – LAN IP the phone should configure as HTTP proxy
    - ``proxy_port``  – port mitmproxy is listening on
    - ``cert_url``    – URL the phone should open to download the CA
                        (served by mitmproxy's ``mitm.it`` endpoint when
                        the phone uses the proxy)
    - ``setup_url``   – a ``pce-proxy://`` deep link encoding the same
                        info; useful if we ever ship a companion app
    - ``instructions`` – short human-readable steps, also embedded in
                         the CLI output

    The payload is intentionally small (< 200 bytes in practice) so the
    QR code stays at error-correction level ``M`` — readable by a phone
    camera in one or two seconds.
    """
    ip = lan_ip or get_lan_ip()
    p = int(port or PROXY_LISTEN_PORT)

    # ``mitm.it`` is mitmproxy's built-in helper page that serves the
    # CA cert bundle for whatever platform the phone identifies as.
    cert_url = f"http://mitm.it"
    proxy_endpoint = f"{ip}:{p}"
    setup_url = f"pce-proxy://setup?host={ip}&port={p}"

    return {
        "proxy_host": ip,
        "proxy_port": p,
        "proxy_endpoint": proxy_endpoint,
        "cert_url": cert_url,
        "setup_url": setup_url,
        "instructions": [
            f"1. On your phone's Wi-Fi settings, set HTTP proxy to {ip}:{p} (manual).",
            f"2. Open a browser and visit {cert_url} to download the PCE / mitmproxy CA.",
            "3. Install and trust the certificate (iOS: Settings → General → VPN & Device Management → Install → Certificate Trust Settings; Android: Settings → Security → Install from storage).",
            "4. Disable the proxy again when you're done capturing.",
        ],
    }


# ---------------------------------------------------------------------------
# QR rendering
# ---------------------------------------------------------------------------

def _qr_payload(info: dict[str, Any]) -> str:
    """Serialise ``info`` into the string we embed inside the QR code.

    We use the ``setup_url`` (a short custom-scheme URL) rather than the
    full JSON blob so the QR stays compact. The scanning flow is:
    operator scans QR → phone OS opens the URL → if the PCE companion
    app is installed it deep-links into setup, otherwise the phone shows
    a prompt with the host:port that the operator copies into the
    Wi-Fi proxy dialog.
    """
    return info.get("setup_url") or info.get("proxy_endpoint") or ""


def render_qr_ascii(info: dict[str, Any]) -> str:
    """Return an ASCII-art rendering of the QR code.

    Falls back to a plain-text banner when ``qrcode`` isn't installed so
    the CLI always prints something useful.
    """
    payload = _qr_payload(info)
    if not payload:
        return "(no setup payload — proxy misconfigured?)"

    if not _qrcode_available():
        return (
            "(QR rendering disabled — `pip install qrcode[pil]` to enable)\n"
            f"URL: {payload}\n"
            f"Proxy: {info.get('proxy_endpoint')}\n"
            f"Cert:  {info.get('cert_url')}"
        )

    import qrcode  # type: ignore[import-not-found]

    qr = qrcode.QRCode(
        version=None,
        # Level M is the mitm.it default: 15% error correction, small size.
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    # Always append a plain-text footer so operators reading the QR in
    # a terminal can copy the URL even if they can't scan with a phone.
    footer = (
        f"URL:   {payload}\n"
        f"Proxy: {info.get('proxy_endpoint')}\n"
        f"Cert:  {info.get('cert_url')}"
    )
    return buf.getvalue() + "\n" + footer


def render_qr_png(info: dict[str, Any]) -> dict[str, Any]:
    """Render the QR as a PNG image. Returns either
    ``{"ok": True, "png_bytes": bytes, "content_type": "image/png"}`` or
    ``{"ok": False, "error": "..."}`` on missing deps.
    """
    if not _qrcode_available() or not _pillow_available():
        return {
            "ok": False,
            "error": "qrcode_or_pillow_not_installed",
            "hint": "pip install qrcode[pil]",
        }
    payload = _qr_payload(info)
    if not payload:
        return {"ok": False, "error": "empty_payload"}

    import qrcode  # type: ignore[import-not-found]

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=2,
        box_size=8,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return {
        "ok": True,
        "png_bytes": out.getvalue(),
        "content_type": "image/png",
    }


# ---------------------------------------------------------------------------
# Aggregate status (for HTTP endpoint + CLI)
# ---------------------------------------------------------------------------

def status() -> dict[str, Any]:
    """Full onboarding snapshot — info + capability probe."""
    info = get_setup_info()
    return {
        "ok": True,
        "info": info,
        "backends": {
            "qrcode": _qrcode_available(),
            "pillow": _pillow_available(),
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m pce_core.mobile_wizard",
        description=(
            "Print the QR code + instructions a phone needs to become "
            "a PCE capture source."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Print the raw setup payload as JSON (machine-readable).",
    )
    p.add_argument(
        "--png", metavar="PATH",
        help="Write a PNG QR code to the given path instead of ASCII art.",
    )
    p.add_argument(
        "--ip", metavar="IP",
        help="Override the LAN IP (e.g. when the auto-probe picks the wrong NIC).",
    )
    p.add_argument(
        "--port", type=int, metavar="PORT",
        help="Override the proxy port (default: PCE_PROXY_PORT or 8080).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    info = get_setup_info(lan_ip=args.ip, port=args.port)

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    if args.png:
        out = render_qr_png(info)
        if not out.get("ok"):
            print(
                f"[PCE mobile_wizard] PNG rendering failed: "
                f"{out.get('error')} ({out.get('hint', '')})"
            )
            return 2
        with open(args.png, "wb") as fp:
            fp.write(out["png_bytes"])
        print(f"[PCE mobile_wizard] wrote PNG QR → {args.png}")

    print("=" * 60)
    print(" PCE mobile capture setup")
    print("=" * 60)
    print(render_qr_ascii(info))
    print()
    for step in info["instructions"]:
        print(step)
    print()
    print(f"Deep-link URL: {info['setup_url']}")
    return 0


if __name__ == "__main__":  # pragma: no cover — wired via `python -m`
    raise SystemExit(main())


__all__ = [
    "get_lan_ip",
    "get_setup_info",
    "main",
    "render_qr_ascii",
    "render_qr_png",
    "status",
]
