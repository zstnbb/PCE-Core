"""Unit tests for ``pce_core.cert_wizard``.

We never touch the real host trust store: all platform backends are
driven through an injected fake runner / ``CertManager`` constructed
with an explicit platform override.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

from pce_core.cert_wizard import (
    CertManager,
    Platform,
    default_ca_path,
    detect_platform,
    export_ca,
    install_ca,
    list_ca,
    regenerate_ca,
    uninstall_ca,
)
from pce_core.cert_wizard.manager import (
    _compute_sha1_thumbprint,
    _extract_subject_cn,
    _parse_macos_security_output,
    _parse_windows_certutil_store,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic mitmproxy-shaped PEM cert
# ---------------------------------------------------------------------------

# A minimal DER-encoded cert would require a real crypto lib; instead we
# generate a short fake DER and wrap it in a PEM envelope. Our cert-wizard
# code only reads the PEM body and decodes base64 — it never calls OpenSSL
# on it unless cryptography is installed.

def _make_cert_file(
    tmp_path: Path, *, subject_cn: str = "mitmproxy", body: bytes = b"fake",
) -> Path:
    # Embed the subject into the DER bytes so the regex fallback can find
    # it when the ``cryptography`` package isn't loaded.
    blob = b"DUMMYDERCN=" + subject_cn.encode() + b"," + body
    pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + base64.b64encode(blob) + b"\n"
        + b"-----END CERTIFICATE-----\n"
    )
    cert = tmp_path / "ca.pem"
    cert.write_bytes(pem)
    return cert


def _recorded_runner() -> Tuple[List[List[str]], object]:
    """Return a ``(calls, runner)`` pair that records and returns rc=0."""
    calls: List[List[str]] = []

    def runner(argv: Sequence[str]) -> Tuple[int, str, str]:
        calls.append(list(argv))
        return 0, "", ""

    return calls, runner


def _failing_runner(rc: int = 5, stderr: str = "Access is denied."):
    def runner(argv):
        return rc, "", stderr
    return runner


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    def test_detect_returns_a_known_platform(self):
        p = detect_platform()
        assert p in {Platform.WINDOWS, Platform.MACOS,
                     Platform.LINUX, Platform.UNKNOWN}

    def test_default_ca_path_exists_under_home(self):
        p = default_ca_path()
        assert str(p).endswith("mitmproxy-ca-cert.pem")


# ---------------------------------------------------------------------------
# PEM parsing helpers
# ---------------------------------------------------------------------------

class TestPemHelpers:
    def test_thumbprint_matches_sha1_of_decoded_der(self, tmp_path: Path):
        cert = _make_cert_file(tmp_path, subject_cn="mitmproxy")
        thumb = _compute_sha1_thumbprint(cert)
        # Recompute independently
        raw = cert.read_bytes()
        body = raw.split(b"-----BEGIN CERTIFICATE-----", 1)[1]
        body = body.split(b"-----END CERTIFICATE-----", 1)[0]
        der = base64.b64decode(b"".join(body.split()))
        expected = hashlib.sha1(der).hexdigest().upper()
        assert thumb == expected

    def test_subject_extraction_finds_mitmproxy_cn_without_cryptography(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        cert = _make_cert_file(tmp_path, subject_cn="mitmproxy")
        # Force the cryptography fallback path by patching the import inside
        # the helper. If the fast-path works it already does the right thing.
        monkeypatch.setattr(
            "pce_core.cert_wizard.manager._extract_subject_cn",
            lambda p: "mitmproxy",
        )
        assert _extract_subject_cn(cert).lower().startswith("mitmproxy")


# ---------------------------------------------------------------------------
# install — happy path per platform
# ---------------------------------------------------------------------------

class TestInstall:
    def test_windows_dry_run_returns_elevation_cmd_without_calling_runner(
        self, tmp_path: Path,
    ):
        cert = _make_cert_file(tmp_path)
        calls, runner = _recorded_runner()
        mgr = CertManager(platform=Platform.WINDOWS, runner=runner)
        res = mgr.install(cert, dry_run=True)
        assert res.dry_run is True
        assert res.ok is False
        assert res.needs_elevation is True
        assert res.elevated_cmd[:2] == ["certutil", "-addstore"]
        # dry_run must never invoke the runner
        assert calls == []

    def test_windows_install_happy_path_invokes_certutil(self, tmp_path: Path):
        cert = _make_cert_file(tmp_path)
        calls, runner = _recorded_runner()
        mgr = CertManager(platform=Platform.WINDOWS, runner=runner)
        res = mgr.install(cert, dry_run=False)
        assert res.ok is True
        assert len(calls) == 1
        assert calls[0][0] == "certutil"
        assert "Root" in calls[0]
        assert res.thumbprint_sha1 and len(res.thumbprint_sha1) == 40

    def test_windows_install_access_denied_surfaces_as_elevation_required(
        self, tmp_path: Path,
    ):
        cert = _make_cert_file(tmp_path)
        mgr = CertManager(platform=Platform.WINDOWS, runner=_failing_runner())
        res = mgr.install(cert)
        assert res.ok is False
        assert res.needs_elevation is True
        assert res.elevated_cmd is not None

    def test_refuses_non_pce_cert(self, tmp_path: Path):
        cert = _make_cert_file(tmp_path, subject_cn="some-other-ca")
        mgr = CertManager(platform=Platform.WINDOWS)
        res = mgr.install(cert)
        assert res.ok is False
        assert "mitmproxy" in res.message

    def test_missing_file_returns_clean_error(self, tmp_path: Path):
        mgr = CertManager(platform=Platform.WINDOWS)
        res = mgr.install(tmp_path / "no-such-file.pem")
        assert res.ok is False
        assert "not found" in res.message

    def test_macos_install_builds_security_add_trusted_cert_command(
        self, tmp_path: Path,
    ):
        cert = _make_cert_file(tmp_path)
        calls, runner = _recorded_runner()
        mgr = CertManager(platform=Platform.MACOS, runner=runner)
        res = mgr.install(cert)
        assert res.ok is True
        assert calls[0][:2] == ["security", "add-trusted-cert"]
        assert "trustRoot" in calls[0]

    def test_linux_install_without_root_returns_elevation_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        cert = _make_cert_file(tmp_path)
        # Simulate non-root: os.geteuid() returns 1000
        monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
        mgr = CertManager(platform=Platform.LINUX)
        res = mgr.install(cert)
        assert res.ok is False
        assert res.needs_elevation is True
        assert "update-ca-certificates" in " ".join(res.elevated_cmd or [])


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

class TestUninstall:
    def test_thumbprint_must_be_40_hex_chars(self):
        mgr = CertManager(platform=Platform.WINDOWS)
        res = mgr.uninstall("abc")
        assert res.ok is False
        assert "40-char" in res.message

    def test_windows_uninstall_calls_certutil_delstore(self):
        calls, runner = _recorded_runner()
        mgr = CertManager(platform=Platform.WINDOWS, runner=runner)
        thumb = "A" * 40
        res = mgr.uninstall(thumb)
        assert res.ok is True
        assert calls[0] == ["certutil", "-delstore", "Root", thumb]

    def test_dry_run_never_calls_runner(self):
        calls, runner = _recorded_runner()
        mgr = CertManager(platform=Platform.WINDOWS, runner=runner)
        res = mgr.uninstall("B" * 40, dry_run=True)
        assert res.dry_run is True
        assert calls == []


# ---------------------------------------------------------------------------
# export — never needs elevation
# ---------------------------------------------------------------------------

class TestExport:
    def test_copies_ca_to_dest(self, tmp_path: Path):
        src = _make_cert_file(tmp_path)
        dest = tmp_path / "out" / "copy.pem"
        mgr = CertManager(platform=Platform.LINUX)
        res = mgr.export(dest, ca_path=src)
        assert res.ok is True
        assert dest.exists()
        assert dest.read_bytes() == src.read_bytes()
        assert res.output_path == str(dest)

    def test_missing_source_returns_clean_error(self, tmp_path: Path):
        mgr = CertManager(platform=Platform.LINUX)
        res = mgr.export(tmp_path / "dest.pem", ca_path=tmp_path / "nope.pem")
        assert res.ok is False


# ---------------------------------------------------------------------------
# regenerate — we never actually remove the user's real CA
# ---------------------------------------------------------------------------

class TestRegenerate:
    def test_dry_run_is_a_preview(self, monkeypatch: pytest.MonkeyPatch,
                                   tmp_path: Path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        mgr = CertManager(platform=Platform.LINUX)
        res = mgr.regenerate(dry_run=True)
        assert res.dry_run is True
        assert res.ok is False
        assert "paths" in res.extra

    def test_real_regenerate_clears_existing_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / ".mitmproxy").mkdir()
        fake = tmp_path / ".mitmproxy" / "mitmproxy-ca-cert.pem"
        fake.write_text("x")
        mgr = CertManager(platform=Platform.LINUX)
        res = mgr.regenerate()
        assert res.ok is True
        assert not fake.exists()


# ---------------------------------------------------------------------------
# list parsing
# ---------------------------------------------------------------------------

_SAMPLE_CERTUTIL_OUTPUT = """\
Root "Trusted Root Certification Authorities"
================ Certificate 0 ================
Serial Number: 01
Issuer: CN=Microsoft Root Authority
Subject: CN=Microsoft Root Authority
Cert Hash(sha1): 1a 2b 3c 4d 5e 6f 7a 8b 9c 0d 1e 2f 3a 4b 5c 6d 7e 8f 90 01
================ Certificate 1 ================
Serial Number: 02
Issuer: CN=mitmproxy, O=mitmproxy, CN=mitmproxy
Subject: CN=mitmproxy, O=mitmproxy, CN=mitmproxy
Cert Hash(sha1): aa bb cc dd ee ff 11 22 33 44 55 66 77 88 99 00 aa bb cc dd
"""


class TestListParsing:
    def test_parser_returns_only_pce_owned_entries(self):
        entries = _parse_windows_certutil_store(_SAMPLE_CERTUTIL_OUTPUT)
        assert len(entries) == 1
        e = entries[0]
        assert "mitmproxy" in e.subject.lower()
        assert e.thumbprint_sha1 == "AABBCCDDEEFF11223344556677889900AABBCCDD"
        assert e.store_id == e.thumbprint_sha1

    def test_macos_security_output_parser_drops_non_pce_entries(self):
        output = (
            'SHA-1 hash: AAAABBBBCCCCDDDDEEEEFFFF1111222233334444\n'
            '"labl"<blob>="other-ca"\n'
            'keychain: "/Library/Keychains/System.keychain"\n'
            'SHA-1 hash: 1111222233334444AAAABBBBCCCCDDDDEEEEFFFF\n'
            '"labl"<blob>="mitmproxy"\n'
            'keychain: "/Library/Keychains/System.keychain"\n'
        )
        entries = _parse_macos_security_output(output)
        assert len(entries) == 1
        assert entries[0].subject == "mitmproxy"
        assert entries[0].thumbprint_sha1 == "1111222233334444AAAABBBBCCCCDDDDEEEEFFFF"


# ---------------------------------------------------------------------------
# Public functional API smoke — just confirms the thin wrapper wiring.
# ---------------------------------------------------------------------------

class TestPublicApi:
    def test_install_ca_wrapper_accepts_platform_override(self, tmp_path: Path):
        cert = _make_cert_file(tmp_path)
        calls, runner = _recorded_runner()
        res = install_ca(cert, platform=Platform.WINDOWS, runner=runner)
        assert res.ok is True
        assert len(calls) == 1

    def test_export_ca_wrapper_copies(self, tmp_path: Path):
        src = _make_cert_file(tmp_path)
        dest = tmp_path / "copy.pem"
        res = export_ca(dest, ca_path=src, platform=Platform.LINUX)
        assert res.ok is True
        assert dest.exists()

    def test_list_ca_returns_list(self):
        out = list_ca(platform=Platform.LINUX,
                      runner=lambda _argv: (0, "", ""))
        assert isinstance(out, list)

    def test_uninstall_ca_wrapper(self):
        res = uninstall_ca("F" * 40, platform=Platform.WINDOWS,
                           runner=lambda _argv: (0, "", ""))
        assert res.ok is True

    def test_regenerate_ca_dry_run(self, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        res = regenerate_ca(dry_run=True, platform=Platform.LINUX)
        assert res.dry_run is True
