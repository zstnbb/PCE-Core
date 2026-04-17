"""Unit tests for ``pce_core.proxy_toggle``.

We never touch real OS proxy state. The Windows path uses an injected
``WinRegAdapter`` stub; macOS/Linux paths use an injected runner.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pytest

from pce_core.proxy_toggle import (
    DEFAULT_BYPASS,
    Platform,
    ProxyManager,
    ProxyState,
    detect_platform,
    disable_system_proxy,
    enable_system_proxy,
    get_proxy_state,
)
from pce_core.proxy_toggle.manager import (
    WinRegAdapter,
    _parse_host_port,
    _parse_macos_getproxy,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeWinReg(WinRegAdapter):
    """In-memory stand-in for winreg. Tracks writes; never touches HKCU."""

    def __init__(self, initial: dict | None = None) -> None:
        self.values = initial or {
            "ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None,
        }
        self.refresh_calls = 0
        self.fail_write = False

    def read(self) -> dict:
        return dict(self.values)

    def write(self, *, enable, server, override) -> None:
        if self.fail_write:
            raise PermissionError("simulated ACL denial")
        self.values["ProxyEnable"] = 1 if enable else 0
        if server is not None:
            self.values["ProxyServer"] = server
        if override is not None:
            self.values["ProxyOverride"] = override
        if not enable:
            # Disable intentionally does not touch ProxyServer / Override,
            # matching Windows' convention (mirror real adapter semantics).
            pass

    def refresh_wininet(self) -> None:
        self.refresh_calls += 1


def _recorded_runner() -> Tuple[List[List[str]], object]:
    calls: List[List[str]] = []

    def runner(argv: Sequence[str]) -> Tuple[int, str, str]:
        calls.append(list(argv))
        # Simulate "listallnetworkservices" output for macOS helper calls
        if argv and argv[0] == "networksetup" and any(
            "listallnetworkservices" in a for a in argv
        ):
            return 0, ("An asterisk (*) denotes that a network service is disabled.\n"
                       "Wi-Fi\n"
                       "Ethernet\n"
                       "*Bluetooth PAN\n"), ""
        # Simulate gsettings "get" for state queries
        if argv and argv[0] == "gsettings" and "get" in argv:
            if "mode" in argv:
                return 0, "'none'\n", ""
            return 0, "''\n", ""
        # Simulate networksetup -getwebproxy output
        if argv and argv[0] == "networksetup" and "-getwebproxy" in argv:
            return 0, "Enabled: No\nServer: \nPort: 0\n", ""
        return 0, "", ""

    return calls, runner


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def test_detect_platform_returns_known_value():
    assert detect_platform() in {
        Platform.WINDOWS, Platform.MACOS, Platform.LINUX, Platform.UNKNOWN,
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class TestSmallHelpers:
    def test_parse_host_port_simple(self):
        assert _parse_host_port("127.0.0.1:8080") == ("127.0.0.1", 8080)

    def test_parse_host_port_ie_style(self):
        assert _parse_host_port("http=127.0.0.1:8080;https=127.0.0.1:8080") == (
            "127.0.0.1", 8080,
        )

    def test_parse_host_port_empty(self):
        assert _parse_host_port("") == (None, None)

    def test_parse_macos_getproxy(self):
        info = _parse_macos_getproxy("Enabled: Yes\nServer: 127.0.0.1\nPort: 8080\n")
        assert info == {"enabled": True, "host": "127.0.0.1", "port": 8080}


# ---------------------------------------------------------------------------
# Windows path
# ---------------------------------------------------------------------------

class TestWindowsEnable:
    def test_dry_run_never_writes(self):
        reg = FakeWinReg()
        mgr = ProxyManager(platform=Platform.WINDOWS, winreg_adapter=reg)
        res = mgr.enable("127.0.0.1", 8080, dry_run=True)
        assert res.dry_run is True
        assert res.ok is False
        # Dry run never touches the registry
        assert reg.values["ProxyEnable"] == 0
        assert reg.refresh_calls == 0

    def test_real_enable_sets_registry_and_refreshes_wininet(self):
        reg = FakeWinReg()
        mgr = ProxyManager(platform=Platform.WINDOWS, winreg_adapter=reg)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is True
        assert reg.values["ProxyEnable"] == 1
        assert reg.values["ProxyServer"] == "127.0.0.1:8080"
        assert "127.0.0.1" in reg.values["ProxyOverride"]
        assert reg.refresh_calls == 1

    def test_permission_error_surfaces_as_needs_elevation(self):
        reg = FakeWinReg()
        reg.fail_write = True
        mgr = ProxyManager(platform=Platform.WINDOWS, winreg_adapter=reg)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is False
        assert res.needs_elevation is True

    def test_invalid_port_rejected(self):
        mgr = ProxyManager(platform=Platform.WINDOWS,
                           winreg_adapter=FakeWinReg())
        res = mgr.enable("127.0.0.1", 0)
        assert res.ok is False
        assert "invalid" in res.message.lower()


class TestWindowsDisable:
    def test_real_disable_clears_enabled_flag(self):
        reg = FakeWinReg({"ProxyEnable": 1,
                          "ProxyServer": "127.0.0.1:8080",
                          "ProxyOverride": "localhost"})
        mgr = ProxyManager(platform=Platform.WINDOWS, winreg_adapter=reg)
        res = mgr.disable()
        assert res.ok is True
        assert reg.values["ProxyEnable"] == 0


class TestWindowsState:
    def test_reports_server_and_bypass(self):
        reg = FakeWinReg({
            "ProxyEnable": 1,
            "ProxyServer": "127.0.0.1:8080",
            "ProxyOverride": "localhost;127.0.0.1;<local>",
        })
        mgr = ProxyManager(platform=Platform.WINDOWS, winreg_adapter=reg)
        state = mgr.get_state()
        assert state.enabled is True
        assert state.host == "127.0.0.1"
        assert state.port == 8080
        assert "localhost" in state.bypass


# ---------------------------------------------------------------------------
# macOS path
# ---------------------------------------------------------------------------

class TestMacos:
    def test_enable_dispatches_five_commands_per_active_service(self):
        calls, runner = _recorded_runner()
        mgr = ProxyManager(platform=Platform.MACOS, runner=runner)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is True
        # 1 for listallnetworkservices + 5 per enabled service (Wi-Fi, Ethernet)
        # + state queries at the end
        setwebproxy_calls = [c for c in calls if "-setwebproxy" in c]
        assert len(setwebproxy_calls) == 2
        set_state_on = [c for c in calls if "-setwebproxystate" in c and "on" in c]
        assert len(set_state_on) == 2   # Wi-Fi, Ethernet

    def test_dry_run_does_not_shell_out(self):
        calls, runner = _recorded_runner()
        mgr = ProxyManager(platform=Platform.MACOS, runner=runner)
        res = mgr.enable("127.0.0.1", 8080, dry_run=True)
        assert res.dry_run is True
        # dry_run *still* needs to list services; but it shouldn't call any
        # setwebproxy commands.
        assert not any("-setwebproxy" in c for c in calls)

    def test_runner_failure_returns_clean_error(self):
        def runner(argv):
            if any("listallnetworkservices" in a for a in argv):
                return 0, "An asterisk (*)\nWi-Fi\n", ""
            return 1, "", "permission denied"
        mgr = ProxyManager(platform=Platform.MACOS, runner=runner)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is False
        assert "networksetup failed" in res.message


# ---------------------------------------------------------------------------
# Linux path
# ---------------------------------------------------------------------------

class TestLinux:
    def test_enable_drives_gsettings(self):
        calls, runner = _recorded_runner()
        mgr = ProxyManager(platform=Platform.LINUX, runner=runner)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is True
        modes = [c for c in calls
                 if c[:3] == ["gsettings", "set", "org.gnome.system.proxy"]
                 and "mode" in c]
        assert modes and "'manual'" in modes[0]
        # Host and port should be set for both http and https
        set_host = [c for c in calls if c[:2] == ["gsettings", "set"]
                    and "host" in c]
        assert len(set_host) == 2

    def test_disable_sets_mode_none(self):
        calls, runner = _recorded_runner()
        mgr = ProxyManager(platform=Platform.LINUX, runner=runner)
        res = mgr.disable()
        assert res.ok is True
        assert any("'none'" in c for c in calls)

    def test_gsettings_missing_hints_env_vars(self):
        def runner(argv):
            return 127, "", "gsettings: command not found"
        mgr = ProxyManager(platform=Platform.LINUX, runner=runner)
        res = mgr.enable("127.0.0.1", 8080)
        assert res.ok is False
        assert "HTTP_PROXY" in (res.extra.get("hint", ""))


# ---------------------------------------------------------------------------
# Default bypass list
# ---------------------------------------------------------------------------

def test_default_bypass_includes_loopback_and_private_ranges():
    assert "127.0.0.1" in DEFAULT_BYPASS
    assert "localhost" in DEFAULT_BYPASS
    assert any(b.startswith("192.168") for b in DEFAULT_BYPASS)


# ---------------------------------------------------------------------------
# Public functional API sanity
# ---------------------------------------------------------------------------

def test_public_api_thin_wrappers_forward_args():
    reg = FakeWinReg()
    res = enable_system_proxy(
        "127.0.0.1", 9999,
        platform=Platform.WINDOWS, winreg_adapter=reg,
    )
    assert res.ok is True
    assert reg.values["ProxyEnable"] == 1

    res2 = disable_system_proxy(
        platform=Platform.WINDOWS, winreg_adapter=reg,
    )
    assert res2.ok is True

    state = get_proxy_state(platform=Platform.WINDOWS, winreg_adapter=reg)
    assert isinstance(state, ProxyState)
