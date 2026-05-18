# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pce_sslkeylog.tshark_wrap (config building, find_tshark
candidate resolution, multi-interface argv, encoding hygiene)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pce_sslkeylog.tshark_wrap import TsharkConfig, find_tshark


# ---------------------------------------------------------------------------
# TsharkConfig.build_argv — single + multi interface
# ---------------------------------------------------------------------------


def test_config_single_interface_legacy_kwarg():
    """The pre-W2.1 single-string ``interface=...`` keyword still works
    (backward-compat shim) and produces one ``-i`` flag."""
    c = TsharkConfig(
        tshark_path=Path("tshark"),
        keylog_file=Path("/k"),
        interface="WLAN",
    )
    argv = c.build_argv()
    assert argv.count("-i") == 1
    assert "WLAN" in argv
    # The legacy property still resolves to the first iface
    assert c.interface == "WLAN"


def test_config_multi_interfaces_emits_multiple_i_flags():
    """W2.1: multiple ``-i`` flags so tshark merges captures from each."""
    c = TsharkConfig(
        tshark_path=Path("tshark"),
        keylog_file=Path("/k"),
        interfaces=["Adapter for loopback traffic capture", "WLAN"],
    )
    argv = c.build_argv()
    # tshark accepts repeated ``-i alias``; we want exactly two
    assert argv.count("-i") == 2
    # Order preserved (loopback first, WLAN second)
    i_positions = [i for i, a in enumerate(argv) if a == "-i"]
    assert argv[i_positions[0] + 1] == "Adapter for loopback traffic capture"
    assert argv[i_positions[1] + 1] == "WLAN"


def test_config_default_interfaces_is_any():
    c = TsharkConfig(tshark_path=Path("tshark"), keylog_file=Path("/k"))
    assert c.interfaces == ["any"]
    argv = c.build_argv()
    assert argv.count("-i") == 1
    assert "any" in argv


def test_config_allowed_hosts_emits_bpf_filter():
    c = TsharkConfig(
        tshark_path=Path("tshark"), keylog_file=Path("/k"),
        interfaces=["WLAN"],
        allowed_hosts=frozenset(["api.anthropic.com", "claude.ai"]),
    )
    argv = c.build_argv()
    assert "-f" in argv
    bpf = argv[argv.index("-f") + 1]
    assert "host api.anthropic.com" in bpf
    assert "host claude.ai" in bpf
    # Sorted order so the generated string is deterministic
    assert "api.anthropic.com or" in bpf or "or host api.anthropic.com" in bpf


def test_config_no_allowed_hosts_omits_bpf_filter():
    c = TsharkConfig(
        tshark_path=Path("tshark"), keylog_file=Path("/k"),
        interfaces=["Adapter for loopback traffic capture"],
    )
    argv = c.build_argv()
    assert "-f" not in argv


def test_config_keylog_and_display_filter_always_present():
    """Regardless of interface count, the keylog path + ``-Y http or http2``
    must always make it onto the command line."""
    c = TsharkConfig(
        tshark_path=Path("tshark"), keylog_file=Path("/x/keys.txt"),
        interfaces=["WLAN", "以太网"],
    )
    argv = c.build_argv()
    # Keylog formatted as ``-o tls.keylog_file:<path>``
    assert any(a.startswith("tls.keylog_file:") for a in argv), argv
    assert "-Y" in argv
    assert "http or http2" in argv
    # ek mode + line-buffer
    assert "-T" in argv
    assert "ek" in argv
    assert "-l" in argv


def test_config_handles_utf8_interface_names():
    """A user passing a Chinese interface alias (e.g. '以太网' = Ethernet)
    must end up verbatim in argv. subprocess.Popen on Windows passes args
    via CreateProcessW so UTF-8 strings round-trip correctly."""
    c = TsharkConfig(
        tshark_path=Path("tshark"), keylog_file=Path("/k"),
        interfaces=["以太网"],
    )
    argv = c.build_argv()
    assert "以太网" in argv


# ---------------------------------------------------------------------------
# find_tshark resolution rules
# ---------------------------------------------------------------------------


def test_find_tshark_env_override_takes_precedence(monkeypatch, tmp_path):
    """If ``PCE_TSHARK_PATH`` is set to a real file, it wins over PATH /
    standard install paths."""
    fake = tmp_path / "my_custom_tshark.exe"
    fake.write_text("")  # touch
    monkeypatch.setenv("PCE_TSHARK_PATH", str(fake))
    # Even if PATH has tshark, env wins
    found = find_tshark()
    assert found is not None
    assert Path(found) == fake


def test_find_tshark_env_override_ignored_if_missing(monkeypatch, tmp_path):
    """A bogus ``PCE_TSHARK_PATH`` should be ignored (not blow up); the
    lookup falls through to PATH / candidate paths."""
    monkeypatch.setenv("PCE_TSHARK_PATH", str(tmp_path / "does_not_exist"))
    # Should not raise; result depends on host but the env override
    # path must NOT be returned.
    found = find_tshark()
    if found is not None:
        assert Path(found).name == "tshark.exe" or Path(found).name == "tshark"


# ---------------------------------------------------------------------------
# detect_capture_interfaces — Windows multi-iface auto-detect
# ---------------------------------------------------------------------------


def test_detect_capture_interfaces_includes_loopback_default_and_clash(monkeypatch):
    """W2.1.2: when a Clash-style TUN adapter is present (common on
    Windows hosts with system-proxy AI access), auto-detect must include
    it. Otherwise Node-based CLIs whose TLS gets intercepted by Clash's
    WFP/NDIS hooks (e.g. gemini-cli) would never produce captures."""
    from pce_sslkeylog import tshark_wrap

    monkeypatch.setattr(tshark_wrap, "sys", type("s", (), {"platform": "win32"}))
    monkeypatch.setattr(
        tshark_wrap, "list_tshark_interfaces",
        lambda _t: [
            "vEthernet (Default Switch)",
            "WLAN",
            "Clash",
            "Adapter for loopback traffic capture",
            "以太网",
            "etwdump (Event Tracing for Windows (ETW) reader)",
        ],
    )
    monkeypatch.setattr(
        tshark_wrap, "_windows_default_route_iface",
        lambda: "WLAN",
    )
    ifaces = tshark_wrap.detect_capture_interfaces(Path("tshark"))
    assert "Adapter for loopback traffic capture" in ifaces
    assert "WLAN" in ifaces
    assert "Clash" in ifaces, (
        "Clash TUN adapter must be in auto-detect, otherwise Node-CLI "
        "traffic intercepted by Clash never reaches our daemon"
    )
    # vEthernet (Hyper-V Default Switch) and etwdump should NOT be
    # picked unless we have nothing else.
    assert "vEthernet (Default Switch)" not in ifaces
    assert "etwdump (Event Tracing for Windows (ETW) reader)" not in ifaces


def test_detect_capture_interfaces_no_duplicates(monkeypatch):
    """If the default-route iface name happens to also match the
    Loopback / virtual hint check, we must not list it twice."""
    from pce_sslkeylog import tshark_wrap

    monkeypatch.setattr(tshark_wrap, "sys", type("s", (), {"platform": "win32"}))
    monkeypatch.setattr(
        tshark_wrap, "list_tshark_interfaces",
        lambda _t: ["WLAN", "Adapter for loopback traffic capture"],
    )
    # default-route reports a NAME ALREADY in the loopback / list
    monkeypatch.setattr(
        tshark_wrap, "_windows_default_route_iface",
        lambda: "Adapter for loopback traffic capture",
    )
    ifaces = tshark_wrap.detect_capture_interfaces(Path("tshark"))
    assert ifaces.count("Adapter for loopback traffic capture") == 1


def test_detect_capture_interfaces_posix():
    """On POSIX, auto-detect returns the ``any`` pseudo-interface that
    Linux's libpcap supports for all-iface capture."""
    from pce_sslkeylog import tshark_wrap
    import sys as _sys
    if _sys.platform == "win32":
        # Can't easily monkey-patch sys.platform globally here; rely on
        # the Windows-specific tests above. Skip on Windows hosts.
        pytest.skip("POSIX-only test")
    ifaces = tshark_wrap.detect_capture_interfaces(Path("tshark"))
    assert ifaces == ["any"]


# ---------------------------------------------------------------------------
# Service install / print-unit (W2.3)
# ---------------------------------------------------------------------------


def test_service_print_unit_windows(capsys, monkeypatch):
    """``service print-unit`` on Windows renders a valid-looking Scheduled
    Task XML with the user's SID, python.exe, and the run command."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "win32")
    from pce_sslkeylog.__main__ import main
    rc = main(["service", "print-unit"])
    assert rc == 0
    out = capsys.readouterr().out
    # Required structural elements
    assert "<Task version" in out
    assert "<LogonTrigger>" in out
    assert "<UserId>" in out
    assert "<Command>" in out
    assert "pce_sslkeylog run" in out
    # Restart-on-failure block must use a valid interval (PT1M minimum,
    # not the earlier PT30S which schtasks rejected as an XML validation
    # error)
    assert "PT1M" in out


def test_stats_subcommand_runs_clean(capsys):
    """``stats`` should print at least the header + row counts without
    crashing, even when the DB is freshly initialised and has 0 rows
    of source_id='sslkeylog-default'."""
    from pce_sslkeylog.__main__ import main
    rc = main(["stats", "--limit", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "source_id='sslkeylog-default'" in out
    assert "total rows:" in out
    assert "distinct pairs:" in out


def test_stats_subcommand_host_filter(capsys):
    """``stats --host`` should narrow the totals to a single host
    (filter applied to all counters)."""
    from pce_sslkeylog.__main__ import main
    rc = main(["stats", "--host", "totally-fake-host-no-rows.example", "--limit", "0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "totally-fake-host-no-rows.example" in out
    # Totals should be 0 for a nonexistent host
    assert "total rows:           0" in out


def test_service_print_unit_posix(capsys, monkeypatch):
    """``service print-unit`` on POSIX renders a systemd user unit."""
    import sys as _sys
    monkeypatch.setattr(_sys, "platform", "linux")
    from pce_sslkeylog.__main__ import main
    rc = main(["service", "print-unit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[Unit]" in out
    assert "[Service]" in out
    assert "ExecStart=" in out
    assert "pce_sslkeylog run" in out
    assert "Restart=on-failure" in out
    assert "[Install]" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
