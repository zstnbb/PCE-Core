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
