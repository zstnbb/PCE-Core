# SPDX-License-Identifier: Apache-2.0
"""Probe the local box for upstream proxies, TUN interfaces, foreign CAs.

The detector is a **best-effort sniffer**: every probe is bounded, every
exception is swallowed and converted to ``None`` / empty list, so a
single misbehaving network primitive cannot prevent the rest of the
report from being produced. Callers treat the result as a hint, never
as ground truth.
"""

from __future__ import annotations

import logging
import platform as _platform
import socket
import struct
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

logger = logging.getLogger("pce.network_env")


_CONNECT_TIMEOUT_S: float = 0.25
_HANDSHAKE_TIMEOUT_S: float = 0.40

_HTTP_CONNECT_TEST_HOST: str = "www.gstatic.com"
_HTTP_CONNECT_TEST_PORT: int = 443

_PCE_PROXY_PORT: int = 8080
_PCE_CORE_PORT: int = 9800
_PCE_HOOK_PORTS: tuple[int, ...] = (11434, 11435)

#: (port, vendor-guess, primary-protocol) — order is panel presentation order.
_KNOWN_PROXY_PORTS: tuple[tuple[int, str, Literal["http", "socks5"]], ...] = (
    (7890, "clash",     "http"),
    (7891, "clash",     "socks5"),
    (7897, "mihomo",    "http"),
    (10808, "v2ray",    "socks5"),
    (10809, "v2ray",    "http"),
    (10810, "v2ray",    "http"),
    (1080,  "ss/socks", "socks5"),
    (8118,  "privoxy",  "http"),
    (8123,  "polipo",   "http"),
    (6152,  "switchy",  "http"),
    (6153,  "switchy",  "socks5"),
    (8580,  "surge",    "http"),
    (8581,  "surge",    "socks5"),
    (1087,  "v2rayU",   "http"),
    (1086,  "v2rayU",   "socks5"),
)

_TUN_PREFIXES: tuple[str, ...] = (
    "utun", "wg", "tun", "tap", "ppp",
    "wintun", "tailscale", "zerotier",
    "nordlynx", "expressvpn", "surfshark", "openvpn",
)

_FOREIGN_CA_VENDORS: tuple[str, ...] = (
    "zscaler", "netskope", "forcepoint", "bluecoat",
    "fortinet", "palo alto", "trustpoint",
    "barracuda", "ironport", "websense",
    "kaspersky", "bitdefender", "eset",
)


ProbeStatus = Literal["alive", "no_response", "wrong_protocol", "tls_error"]
ProxyKind = Literal["http", "socks5"]
RecommendedAction = Literal["none", "chain_upstream", "warn_conflict"]


@dataclass(frozen=True)
class UpstreamCandidate:
    host: str
    port: int
    kind: ProxyKind
    probe_status: ProbeStatus
    likely_vendor: Optional[str]
    confidence: float

    @property
    def is_usable(self) -> bool:
        return self.probe_status == "alive" and self.confidence >= 0.7

    @property
    def display(self) -> str:
        v = self.likely_vendor or "unknown"
        return f"{v} ({self.kind} {self.host}:{self.port})"


@dataclass
class NetworkEnv:
    upstream_candidates: list[UpstreamCandidate] = field(default_factory=list)
    tun_interfaces: list[str] = field(default_factory=list)
    foreign_root_cas: list[str] = field(default_factory=list)
    recommended_action: RecommendedAction = "none"
    detect_at: float = 0.0

    @property
    def best_upstream(self) -> Optional[UpstreamCandidate]:
        usable = [u for u in self.upstream_candidates if u.is_usable]
        if not usable:
            return None
        return max(usable, key=lambda u: u.confidence)

    @property
    def has_tun(self) -> bool:
        return bool(self.tun_interfaces)

    @property
    def has_conflict(self) -> bool:
        return bool(self.foreign_root_cas)

    def summary(self) -> str:
        if self.recommended_action == "chain_upstream":
            b = self.best_upstream
            return f"upstream proxy: {b.display}" if b else "upstream detected"
        if self.recommended_action == "warn_conflict":
            return f"⚠ enterprise CA: {', '.join(self.foreign_root_cas[:2])}"
        if self.has_tun:
            return f"TUN VPN active ({', '.join(self.tun_interfaces[:2])})"
        return "no VPN-style proxy detected"


def detect(
    *,
    host: str = "127.0.0.1",
    extra_ports: Iterable[int] = (),
) -> NetworkEnv:
    """Run the full sweep and return a :class:`NetworkEnv`."""
    import time
    candidates = list(_probe_upstreams(host, extra_ports))
    tuns = _list_tun_interfaces()
    foreign = _list_foreign_root_cas()

    # Highest severity wins: a confirmed enterprise CA dominates a
    # potentially-helpful upstream chain because chaining INTO a TLS-
    # inspecting proxy would re-encrypt and break PCE's MITM.
    if foreign:
        action: RecommendedAction = "warn_conflict"
    elif any(c.is_usable for c in candidates):
        action = "chain_upstream"
    else:
        action = "none"

    return NetworkEnv(
        upstream_candidates=candidates,
        tun_interfaces=tuns,
        foreign_root_cas=foreign,
        recommended_action=action,
        detect_at=time.time(),
    )


def upstream_arg_for_mitmproxy(env: NetworkEnv) -> Optional[list[str]]:
    """Translate the detection result into mitmproxy CLI flags.

    Returns ``None`` if no chaining is recommended. Otherwise returns
    the extra argv slice — caller splices it into the ``mitmdump``
    command line.
    """
    if env.recommended_action != "chain_upstream":
        return None
    best = env.best_upstream
    if best is None:
        return None
    scheme = "socks5" if best.kind == "socks5" else "http"
    return ["--mode", f"upstream:{scheme}://{best.host}:{best.port}"]


# ---------------------------------------------------------------------------
# Probes — upstream proxies
# ---------------------------------------------------------------------------

def _probe_upstreams(
    host: str,
    extra_ports: Iterable[int],
) -> Iterable[UpstreamCandidate]:
    seen: set[int] = set()
    schedule: list[tuple[int, str, ProxyKind]] = []
    for port, vendor, kind in _KNOWN_PROXY_PORTS:
        if port in (_PCE_PROXY_PORT, _PCE_CORE_PORT, *_PCE_HOOK_PORTS):
            continue
        schedule.append((port, vendor, kind))
        seen.add(port)
    for port in extra_ports:
        if port in seen or port in (
            _PCE_PROXY_PORT, _PCE_CORE_PORT, *_PCE_HOOK_PORTS,
        ):
            continue
        schedule.append((int(port), "user", "http"))

    for port, vendor, kind in schedule:
        if not _tcp_open(host, port):
            continue
        candidate = _classify(host, port, vendor, kind)
        if candidate is not None:
            yield candidate


def _classify(
    host: str, port: int, vendor: str, primary_kind: ProxyKind,
) -> Optional[UpstreamCandidate]:
    order: tuple[ProxyKind, ...] = (
        primary_kind, "socks5" if primary_kind == "http" else "http",
    )
    for kind in order:
        if kind == "http":
            ok = _probe_http_connect(host, port)
        else:
            ok = _probe_socks5(host, port)
        if ok:
            return UpstreamCandidate(
                host=host, port=port, kind=kind,
                probe_status="alive",
                likely_vendor=vendor if vendor != "user" else None,
                confidence=0.95 if vendor != "user" else 0.75,
            )

    return UpstreamCandidate(
        host=host, port=port, kind=primary_kind,
        probe_status="wrong_protocol",
        likely_vendor=vendor if vendor != "user" else None,
        confidence=0.2,
    )


def _tcp_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(_CONNECT_TIMEOUT_S)
        try:
            s.connect((host, int(port)))
            return True
        except (OSError, socket.timeout):
            return False


def _probe_http_connect(host: str, port: int) -> bool:
    request = (
        f"CONNECT {_HTTP_CONNECT_TEST_HOST}:{_HTTP_CONNECT_TEST_PORT} "
        "HTTP/1.1\r\n"
        f"Host: {_HTTP_CONNECT_TEST_HOST}:{_HTTP_CONNECT_TEST_PORT}\r\n"
        "User-Agent: PCE-Detector/1\r\n"
        "Proxy-Connection: close\r\n\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection(
            (host, int(port)), timeout=_HANDSHAKE_TIMEOUT_S,
        ) as s:
            s.settimeout(_HANDSHAKE_TIMEOUT_S)
            s.sendall(request)
            buf = s.recv(64)
    except (OSError, socket.timeout):
        return False
    text = buf.decode("ascii", errors="replace")
    if not text.startswith("HTTP/"):
        return False
    parts = text.split(" ", 2)
    if len(parts) < 2:
        return False
    try:
        return 200 <= int(parts[1]) < 300
    except ValueError:
        return False


def _probe_socks5(host: str, port: int) -> bool:
    greeting = b"\x05\x01\x00"
    try:
        with socket.create_connection(
            (host, int(port)), timeout=_HANDSHAKE_TIMEOUT_S,
        ) as s:
            s.settimeout(_HANDSHAKE_TIMEOUT_S)
            s.sendall(greeting)
            buf = s.recv(2)
    except (OSError, socket.timeout):
        return False
    if len(buf) != 2:
        return False
    ver, method = struct.unpack("!BB", buf)
    return ver == 5 and method in (0, 2)


def _list_tun_interfaces() -> list[str]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        addrs = psutil.net_if_addrs()
    except Exception as exc:  # noqa: BLE001
        logger.debug("net_if_addrs failed: %r", exc)
        return []

    matches: list[str] = []
    for name in addrs:
        low = name.lower()
        if any(low.startswith(p) for p in _TUN_PREFIXES):
            matches.append(name)
            continue
        if "wireguard" in low or "openvpn" in low or "tailscale" in low:
            matches.append(name)
    return sorted(set(matches))


def _list_foreign_root_cas() -> list[str]:
    sysname = _platform.system().lower()
    try:
        if sysname == "windows":
            return _foreign_cas_windows()
        if sysname == "darwin":
            return _foreign_cas_macos()
        if sysname == "linux":
            return _foreign_cas_linux()
    except Exception as exc:  # noqa: BLE001
        logger.debug("foreign-CA probe failed: %r", exc)
    return []


def _foreign_cas_windows() -> list[str]:
    import ssl
    try:
        certs = ssl.enum_certificates("ROOT")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return []
    found: list[str] = []
    for cert_bytes, encoding, trust in certs:
        try:
            ascii_view = cert_bytes.decode("latin-1", errors="replace")
        except Exception:
            continue
        low = ascii_view.lower()
        for vendor in _FOREIGN_CA_VENDORS:
            if vendor in low and vendor not in (s.lower() for s in found):
                found.append(vendor)
    return found


def _foreign_cas_macos() -> list[str]:
    import subprocess
    try:
        proc = subprocess.run(
            ["security", "find-certificate", "-a", "-p",
             "/Library/Keychains/System.keychain"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    low = proc.stdout.lower()
    return [v for v in _FOREIGN_CA_VENDORS if v in low]


def _foreign_cas_linux() -> list[str]:
    from pathlib import Path
    candidates = [
        Path("/etc/ssl/certs/ca-certificates.crt"),
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
        Path("/etc/ca-certificates/extracted/ca-bundle.trust.crt"),
    ]
    found: list[str] = []
    for p in candidates:
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace").lower()
        except OSError:
            continue
        for v in _FOREIGN_CA_VENDORS:
            if v in text and v not in found:
                found.append(v)
    extra_dir = Path("/usr/local/share/ca-certificates")
    if extra_dir.is_dir():
        for child in extra_dir.iterdir():
            low = child.name.lower()
            for v in _FOREIGN_CA_VENDORS:
                if v in low and v not in found:
                    found.append(v)
    return found
