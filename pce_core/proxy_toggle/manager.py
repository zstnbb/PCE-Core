"""Cross-platform system-proxy switcher.

Windows:
    Touches ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings``
    directly via ``winreg`` (no elevation needed for per-user changes) then
    refreshes WinINET via an ``InternetSetOption`` broadcast.

macOS:
    Drives ``networksetup(8)`` for every *active* network service.

Linux (GNOME):
    Drives ``gsettings(1)`` under ``org.gnome.system.proxy``.
    Best-effort for other DEs — env-var advice only.

All three backends are pluggable — tests inject a fake runner / registry
so nothing touches real OS state.
"""

from __future__ import annotations

import logging
import platform as _platform
import shlex
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from ..logging_config import log_event
from .models import DEFAULT_BYPASS, Platform, ProxyOpResult, ProxyState

logger = logging.getLogger("pce.proxy_toggle")


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform() -> Platform:
    sysname = _platform.system().lower()
    if sysname == "windows":
        return Platform.WINDOWS
    if sysname == "darwin":
        return Platform.MACOS
    if sysname == "linux":
        return Platform.LINUX
    return Platform.UNKNOWN


# ---------------------------------------------------------------------------
# Subprocess runner — same shape as cert_wizard.Runner
# ---------------------------------------------------------------------------

Runner = Callable[[Sequence[str]], Tuple[int, str, str]]


def _default_runner(argv: Sequence[str]) -> Tuple[int, str, str]:
    import subprocess
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"command not found: {e}"
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", f"timed out: {e}"
    except Exception as e:          # noqa: BLE001
        return 1, "", f"runner error: {e}"


# ---------------------------------------------------------------------------
# Windows registry adapter — small interface so tests can stub it out.
# ---------------------------------------------------------------------------

class WinRegAdapter:
    """Minimal ``winreg`` facade. Injectable for tests."""

    INTERNET_SETTINGS_KEY = (
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    )

    def read(self) -> dict:
        """Read ProxyEnable / ProxyServer / ProxyOverride under HKCU."""
        import winreg      # type: ignore[import-not-found]
        out: dict = {}
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, self.INTERNET_SETTINGS_KEY,
            0, winreg.KEY_READ,
        ) as key:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                try:
                    val, _type = winreg.QueryValueEx(key, name)
                    out[name] = val
                except FileNotFoundError:
                    out[name] = None
        return out

    def write(
        self,
        *,
        enable: bool,
        server: Optional[str],
        override: Optional[str],
    ) -> None:
        import winreg      # type: ignore[import-not-found]
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, self.INTERNET_SETTINGS_KEY,
            0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key, "ProxyEnable", 0, winreg.REG_DWORD,
                1 if enable else 0,
            )
            if server is not None:
                winreg.SetValueEx(
                    key, "ProxyServer", 0, winreg.REG_SZ, server,
                )
            if override is not None:
                winreg.SetValueEx(
                    key, "ProxyOverride", 0, winreg.REG_SZ, override,
                )

    def refresh_wininet(self) -> None:
        """Broadcast INTERNET_OPTION_SETTINGS_CHANGED so WinINET picks up
        the new values without a reboot. Best-effort — failures are logged
        and swallowed since the registry value itself is already updated."""
        try:
            import ctypes      # type: ignore[import-not-found]
            wininet = ctypes.WinDLL("wininet")  # type: ignore[attr-defined]
            INTERNET_OPTION_SETTINGS_CHANGED = 39
            INTERNET_OPTION_REFRESH = 37
            wininet.InternetSetOptionW(
                None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0,
            )
            wininet.InternetSetOptionW(
                None, INTERNET_OPTION_REFRESH, None, 0,
            )
        except Exception as e:      # noqa: BLE001
            log_event(logger, "proxy.wininet_refresh_failed",
                      level=logging.WARNING, error=str(e))


# ---------------------------------------------------------------------------
# Proxy manager
# ---------------------------------------------------------------------------

@dataclass
class ProxyManager:
    platform: Platform
    runner: Runner = _default_runner
    winreg_adapter: Optional[WinRegAdapter] = None

    # ---- enable ------------------------------------------------------------

    def enable(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        bypass: Optional[Sequence[str]] = None,
        *, dry_run: bool = False,
    ) -> ProxyOpResult:
        if not host or not isinstance(port, int) or port <= 0 or port >= 65536:
            return ProxyOpResult(
                ok=False, message=f"invalid host/port: {host}:{port}",
                platform=self.platform,
            )
        bypass_list = list(bypass) if bypass is not None else list(DEFAULT_BYPASS)
        if self.platform == Platform.WINDOWS:
            return self._enable_windows(host, port, bypass_list, dry_run)
        if self.platform == Platform.MACOS:
            return self._enable_macos(host, port, bypass_list, dry_run)
        if self.platform == Platform.LINUX:
            return self._enable_linux(host, port, bypass_list, dry_run)
        return ProxyOpResult(
            ok=False, message=f"unsupported platform: {self.platform.value}",
            platform=self.platform,
        )

    def _enable_windows(
        self, host: str, port: int, bypass_list: List[str], dry_run: bool,
    ) -> ProxyOpResult:
        adapter = self.winreg_adapter or WinRegAdapter()
        server = f"{host}:{port}"
        override = ";".join(bypass_list + ["<local>"])
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message=(
                    f"dry_run: would set HKCU\\{adapter.INTERNET_SETTINGS_KEY} "
                    f"ProxyEnable=1, ProxyServer={server!r}, "
                    f"ProxyOverride={override!r}"
                ),
                platform=self.platform,
                dry_run=True,
                extra={"server": server, "override": override},
            )
        try:
            adapter.write(enable=True, server=server, override=override)
            adapter.refresh_wininet()
        except PermissionError as e:
            return ProxyOpResult(
                ok=False,
                message=f"registry write denied: {e}",
                platform=self.platform,
                needs_elevation=True,
            )
        except Exception as e:      # noqa: BLE001
            return ProxyOpResult(
                ok=False, message=f"winreg write failed: {e}",
                platform=self.platform,
            )
        log_event(logger, "proxy.enabled", platform="windows",
                  host=host, port=port)
        state = self._state_windows()
        return ProxyOpResult(
            ok=True,
            message=f"system proxy set to {server}",
            platform=self.platform, state_after=state,
        )

    def _enable_macos(
        self, host: str, port: int, bypass_list: List[str], dry_run: bool,
    ) -> ProxyOpResult:
        services = self._macos_active_services()
        if not services:
            return ProxyOpResult(
                ok=False,
                message="no active network services reported by networksetup",
                platform=self.platform,
            )
        port_s = str(port)
        per_service_cmds: List[List[str]] = []
        for svc in services:
            per_service_cmds.extend([
                ["networksetup", "-setwebproxy", svc, host, port_s],
                ["networksetup", "-setsecurewebproxy", svc, host, port_s],
                ["networksetup", "-setproxybypassdomains", svc, *bypass_list],
                ["networksetup", "-setwebproxystate", svc, "on"],
                ["networksetup", "-setsecurewebproxystate", svc, "on"],
            ])
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message="dry_run: would configure networksetup proxy for "
                        f"{len(services)} service(s)",
                platform=self.platform,
                dry_run=True,
                needs_elevation=True,
                elevated_cmd=_flatten_to_shell(per_service_cmds),
                extra={"services": services, "commands": per_service_cmds},
            )
        stdout, stderr = "", ""
        for cmd in per_service_cmds:
            rc, out, err = self.runner(cmd)
            stdout += out
            stderr += err
            if rc != 0:
                needs_elev = "must be run as root" in err.lower() or "permission" in err.lower()
                return ProxyOpResult(
                    ok=False,
                    message=f"networksetup failed: {' '.join(cmd)!r} -> rc={rc}",
                    platform=self.platform,
                    needs_elevation=needs_elev,
                    elevated_cmd=_flatten_to_shell(per_service_cmds)
                    if needs_elev else None,
                    stdout=stdout, stderr=stderr,
                )
        log_event(logger, "proxy.enabled", platform="macos",
                  host=host, port=port, services=services)
        return ProxyOpResult(
            ok=True,
            message=f"networksetup configured for {len(services)} service(s)",
            platform=self.platform,
            state_after=self._state_macos(services),
            stdout=stdout, stderr=stderr,
        )

    def _enable_linux(
        self, host: str, port: int, bypass_list: List[str], dry_run: bool,
    ) -> ProxyOpResult:
        ignore_value = "[" + ", ".join(f"'{b}'" for b in bypass_list) + "]"
        cmds: List[List[str]] = [
            ["gsettings", "set", "org.gnome.system.proxy", "mode", "'manual'"],
            ["gsettings", "set", "org.gnome.system.proxy.http", "host", f"'{host}'"],
            ["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)],
            ["gsettings", "set", "org.gnome.system.proxy.https", "host", f"'{host}'"],
            ["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)],
            ["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts", ignore_value],
        ]
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message="dry_run: would drive gsettings org.gnome.system.proxy (manual)",
                platform=self.platform,
                dry_run=True,
                extra={"commands": cmds, "hint": (
                    "For non-GNOME DEs set HTTP_PROXY / HTTPS_PROXY / "
                    "NO_PROXY env vars in your shell rc instead."
                )},
            )
        stdout, stderr = "", ""
        for cmd in cmds:
            rc, out, err = self.runner(cmd)
            stdout += out; stderr += err
            if rc != 0:
                return ProxyOpResult(
                    ok=False,
                    message=f"gsettings failed: {' '.join(cmd)!r} -> rc={rc}",
                    platform=self.platform,
                    stdout=stdout, stderr=stderr,
                    extra={"hint": (
                        "If you're not on GNOME, set HTTP_PROXY / HTTPS_PROXY "
                        "env vars manually."
                    )},
                )
        log_event(logger, "proxy.enabled", platform="linux",
                  host=host, port=port)
        return ProxyOpResult(
            ok=True,
            message="gsettings proxy set to manual",
            platform=self.platform,
            state_after=self._state_linux(),
            stdout=stdout, stderr=stderr,
        )

    # ---- disable -----------------------------------------------------------

    def disable(self, *, dry_run: bool = False) -> ProxyOpResult:
        if self.platform == Platform.WINDOWS:
            return self._disable_windows(dry_run)
        if self.platform == Platform.MACOS:
            return self._disable_macos(dry_run)
        if self.platform == Platform.LINUX:
            return self._disable_linux(dry_run)
        return ProxyOpResult(
            ok=False, message=f"unsupported platform: {self.platform.value}",
            platform=self.platform,
        )

    def _disable_windows(self, dry_run: bool) -> ProxyOpResult:
        adapter = self.winreg_adapter or WinRegAdapter()
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message="dry_run: would set HKCU ProxyEnable=0",
                platform=self.platform, dry_run=True,
            )
        try:
            adapter.write(enable=False, server=None, override=None)
            adapter.refresh_wininet()
        except Exception as e:      # noqa: BLE001
            return ProxyOpResult(
                ok=False, message=f"winreg write failed: {e}",
                platform=self.platform,
            )
        log_event(logger, "proxy.disabled", platform="windows")
        return ProxyOpResult(
            ok=True, message="system proxy disabled",
            platform=self.platform,
            state_after=self._state_windows(),
        )

    def _disable_macos(self, dry_run: bool) -> ProxyOpResult:
        services = self._macos_active_services()
        cmds = []
        for svc in services:
            cmds.append(["networksetup", "-setwebproxystate", svc, "off"])
            cmds.append(["networksetup", "-setsecurewebproxystate", svc, "off"])
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message=f"dry_run: would disable proxy on {len(services)} service(s)",
                platform=self.platform, dry_run=True,
                extra={"commands": cmds},
            )
        stdout, stderr = "", ""
        for cmd in cmds:
            rc, out, err = self.runner(cmd)
            stdout += out; stderr += err
            if rc != 0:
                return ProxyOpResult(
                    ok=False,
                    message=f"networksetup failed: {' '.join(cmd)!r} -> rc={rc}",
                    platform=self.platform,
                    stdout=stdout, stderr=stderr,
                )
        log_event(logger, "proxy.disabled", platform="macos", services=services)
        return ProxyOpResult(
            ok=True, message=f"disabled proxy on {len(services)} service(s)",
            platform=self.platform,
            state_after=self._state_macos(services),
            stdout=stdout, stderr=stderr,
        )

    def _disable_linux(self, dry_run: bool) -> ProxyOpResult:
        cmd = ["gsettings", "set", "org.gnome.system.proxy", "mode", "'none'"]
        if dry_run:
            return ProxyOpResult(
                ok=False,
                message="dry_run: would set org.gnome.system.proxy.mode=none",
                platform=self.platform, dry_run=True,
                extra={"command": cmd},
            )
        rc, out, err = self.runner(cmd)
        if rc != 0:
            return ProxyOpResult(
                ok=False, message=f"gsettings failed: rc={rc}",
                platform=self.platform,
                stdout=out, stderr=err,
            )
        log_event(logger, "proxy.disabled", platform="linux")
        return ProxyOpResult(
            ok=True, message="gsettings proxy set to none",
            platform=self.platform,
            state_after=self._state_linux(),
            stdout=out, stderr=err,
        )

    # ---- state -------------------------------------------------------------

    def get_state(self) -> ProxyState:
        if self.platform == Platform.WINDOWS:
            return self._state_windows()
        if self.platform == Platform.MACOS:
            return self._state_macos(self._macos_active_services())
        if self.platform == Platform.LINUX:
            return self._state_linux()
        return ProxyState(platform=self.platform)

    def _state_windows(self) -> ProxyState:
        adapter = self.winreg_adapter or WinRegAdapter()
        try:
            raw = adapter.read()
        except Exception as e:      # noqa: BLE001
            return ProxyState(platform=self.platform, raw={"error": str(e)})
        enabled = bool(raw.get("ProxyEnable"))
        server = raw.get("ProxyServer") or ""
        host, port = _parse_host_port(server)
        override = raw.get("ProxyOverride") or ""
        bypass = [b for b in override.split(";") if b]
        return ProxyState(
            platform=self.platform,
            enabled=enabled, host=host, port=port,
            bypass=bypass, raw=raw,
        )

    def _state_macos(self, services: Optional[Sequence[str]] = None) -> ProxyState:
        svcs = list(services) if services is not None else self._macos_active_services()
        raw: dict = {"services": {}}
        enabled_any = False
        host_seen: Optional[str] = None
        port_seen: Optional[int] = None
        for svc in svcs:
            rc, out, _err = self.runner(["networksetup", "-getwebproxy", svc])
            if rc == 0:
                info = _parse_macos_getproxy(out)
                raw["services"][svc] = info
                if info.get("enabled"):
                    enabled_any = True
                    host_seen = host_seen or info.get("host")
                    port_seen = port_seen or info.get("port")
        return ProxyState(
            platform=self.platform,
            enabled=enabled_any, host=host_seen, port=port_seen,
            bypass=[], raw=raw,
        )

    def _state_linux(self) -> ProxyState:
        raw: dict = {}
        rc, mode, _err = self.runner(
            ["gsettings", "get", "org.gnome.system.proxy", "mode"],
        )
        raw["mode"] = mode.strip().strip("'") if rc == 0 else None
        enabled = raw["mode"] == "manual"
        host = port = None
        if enabled:
            rc, h, _err = self.runner(
                ["gsettings", "get", "org.gnome.system.proxy.http", "host"],
            )
            if rc == 0:
                host = h.strip().strip("'") or None
            rc, p, _err = self.runner(
                ["gsettings", "get", "org.gnome.system.proxy.http", "port"],
            )
            if rc == 0:
                try:
                    port = int(p.strip())
                except ValueError:
                    port = None
        return ProxyState(
            platform=self.platform,
            enabled=enabled, host=host, port=port, bypass=[], raw=raw,
        )

    # ---- helpers -----------------------------------------------------------

    def _macos_active_services(self) -> List[str]:
        """Enumerate non-asterisk-prefixed (= enabled) network services."""
        rc, out, _err = self.runner(["networksetup", "-listallnetworkservices"])
        if rc != 0:
            return []
        services: List[str] = []
        for line in out.splitlines()[1:]:    # first line is the "An asterisk" note
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            services.append(line)
        return services


# ---------------------------------------------------------------------------
# Small parsing helpers (module-level for testability)
# ---------------------------------------------------------------------------

def _parse_host_port(server: str) -> Tuple[Optional[str], Optional[int]]:
    """Parse ``host:port`` (or ``http=host:port;https=host:port``) → (host, port)."""
    if not server:
        return None, None
    candidate = server
    if "=" in server:
        # IE-style "http=127.0.0.1:8080;https=127.0.0.1:8080"
        for part in server.split(";"):
            if part.startswith("http="):
                candidate = part.split("=", 1)[1]
                break
            if part.startswith("https="):
                candidate = part.split("=", 1)[1]
    if ":" not in candidate:
        return candidate, None
    host, _, port_s = candidate.rpartition(":")
    try:
        return host, int(port_s)
    except ValueError:
        return host, None


def _parse_macos_getproxy(output: str) -> dict:
    """Parse networksetup -getwebproxy output."""
    info: dict = {"enabled": False, "host": None, "port": None}
    for line in output.splitlines():
        k, _, v = line.partition(":")
        k = k.strip().lower()
        v = v.strip()
        if k == "enabled":
            info["enabled"] = v.lower() == "yes"
        elif k == "server":
            info["host"] = v or None
        elif k == "port":
            try:
                info["port"] = int(v)
            except ValueError:
                info["port"] = None
    return info


def _flatten_to_shell(cmds: Sequence[Sequence[str]]) -> List[str]:
    """Collapse a list of argv lists to a ``sh -c 'a && b && …'`` form.

    Used only for the ``elevated_cmd`` hint returned to callers — we never
    invoke it ourselves.
    """
    joined = " && ".join(shlex.join(list(cmd)) for cmd in cmds)
    return ["sh", "-c", joined]


# ---------------------------------------------------------------------------
# Public functional API
# ---------------------------------------------------------------------------

def enable_system_proxy(
    host: str = "127.0.0.1",
    port: int = 8080,
    bypass: Optional[Sequence[str]] = None,
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    winreg_adapter: Optional[WinRegAdapter] = None,
    dry_run: bool = False,
) -> ProxyOpResult:
    mgr = ProxyManager(
        platform=platform or detect_platform(),
        runner=runner or _default_runner,
        winreg_adapter=winreg_adapter,
    )
    return mgr.enable(host, port, bypass, dry_run=dry_run)


def disable_system_proxy(
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    winreg_adapter: Optional[WinRegAdapter] = None,
    dry_run: bool = False,
) -> ProxyOpResult:
    mgr = ProxyManager(
        platform=platform or detect_platform(),
        runner=runner or _default_runner,
        winreg_adapter=winreg_adapter,
    )
    return mgr.disable(dry_run=dry_run)


def get_proxy_state(
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    winreg_adapter: Optional[WinRegAdapter] = None,
) -> ProxyState:
    mgr = ProxyManager(
        platform=platform or detect_platform(),
        runner=runner or _default_runner,
        winreg_adapter=winreg_adapter,
    )
    return mgr.get_state()
