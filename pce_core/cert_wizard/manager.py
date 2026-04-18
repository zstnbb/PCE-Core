# SPDX-License-Identifier: Apache-2.0
"""Cross-platform CA wizard for PCE.

The public helpers (:func:`install_ca`, :func:`uninstall_ca`, etc.) dispatch
to the correct OS backend via a :class:`CertManager`.  The manager is split
out so tests can inject a fake ``runner`` without monkey-patching globals.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform as _platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ..logging_config import log_event
from .models import (
    MITMPROXY_SUBJECT_MARKERS,
    CertInfo,
    CertOpResult,
    Platform,
)

logger = logging.getLogger("pce.cert_wizard")


# ---------------------------------------------------------------------------
# Platform detection + default paths
# ---------------------------------------------------------------------------

def detect_platform() -> Platform:
    """Detect the current OS family for trust-store dispatch."""
    sysname = _platform.system().lower()
    if sysname == "windows":
        return Platform.WINDOWS
    if sysname == "darwin":
        return Platform.MACOS
    if sysname == "linux":
        return Platform.LINUX
    return Platform.UNKNOWN


def default_ca_path() -> Path:
    """Default location mitmproxy writes its CA to (``~/.mitmproxy``)."""
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def default_ca_cer_path() -> Path:
    """Windows-friendly DER-encoded copy produced by mitmproxy."""
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer"


# ---------------------------------------------------------------------------
# Runner abstraction — makes the whole module unit-testable.
# ---------------------------------------------------------------------------

# A runner takes an argv list and returns (rc, stdout, stderr).
Runner = Callable[[Sequence[str]], "tuple[int, str, str]"]


def _default_runner(argv: Sequence[str]) -> "tuple[int, str, str]":
    """Real subprocess runner. Never raises — returns non-zero rc instead."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as e:
        return 127, "", f"command not found: {e}"
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", f"timed out: {e}"
    except Exception as e:          # noqa: BLE001
        return 1, "", f"runner error: {e}"


# ---------------------------------------------------------------------------
# Cert helpers: PEM / DER parsing for thumbprint & subject extraction
# ---------------------------------------------------------------------------

def _load_cert_bytes(cert_path: Path) -> bytes:
    """Return the DER bytes of the cert, auto-detecting PEM envelopes."""
    raw = cert_path.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in raw:
        import base64
        m = re.search(
            rb"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----",
            raw,
            re.DOTALL,
        )
        if not m:
            raise ValueError(f"cert at {cert_path} has PEM header but no body")
        return base64.b64decode(b"".join(m.group(1).split()))
    return raw


def _compute_sha1_thumbprint(cert_path: Path) -> str:
    """Return upper-case hex SHA-1 fingerprint of the DER-encoded cert."""
    der = _load_cert_bytes(cert_path)
    return hashlib.sha1(der).hexdigest().upper()


def _extract_subject_cn(cert_path: Path) -> str:
    """Best-effort extraction of the subject Common Name from a cert file.

    Uses ``cryptography`` if available for a proper X.509 parse, otherwise
    falls back to a regex scan of both the raw file bytes *and* the decoded
    DER body (mitmproxy always embeds "mitmproxy" in its CN, so a regex is
    sufficient for our identity check).
    """
    try:
        from cryptography import x509
        der = _load_cert_bytes(cert_path)
        cert = x509.load_der_x509_certificate(der)
        try:
            cn = cert.subject.get_attributes_for_oid(
                x509.NameOID.COMMON_NAME
            )[0].value
            return str(cn)
        except Exception:
            return cert.subject.rfc4514_string()
    except Exception:
        pass
    # Regex fallback: scan both PEM text and decoded DER bytes. PEM on its
    # own is base64 so the CN= token never appears until decoded.
    haystacks: list[bytes] = []
    try:
        haystacks.append(cert_path.read_bytes())
    except Exception:
        pass
    try:
        haystacks.append(_load_cert_bytes(cert_path))
    except Exception:
        pass
    for raw in haystacks:
        m = re.search(rb"CN\s*=\s*([^,\r\n\0]+)", raw)
        if m:
            return m.group(1).decode("ascii", errors="replace").strip()
    return "unknown"


def _is_pce_owned_subject(subject: str) -> bool:
    low = subject.lower()
    return any(marker in low for marker in MITMPROXY_SUBJECT_MARKERS)


# ---------------------------------------------------------------------------
# Cert manager — one instance per call, platform frozen at construction.
# ---------------------------------------------------------------------------

@dataclass
class CertManager:
    platform: Platform
    runner: Runner = _default_runner

    # ---- install -----------------------------------------------------------

    def install(self, cert_path: Path, dry_run: bool = False) -> CertOpResult:
        cert_path = Path(cert_path)
        if not cert_path.exists():
            return CertOpResult(
                ok=False,
                message=f"cert file not found: {cert_path}",
                platform=self.platform,
            )
        try:
            subject = _extract_subject_cn(cert_path)
            thumb = _compute_sha1_thumbprint(cert_path)
        except Exception as e:      # noqa: BLE001
            return CertOpResult(
                ok=False,
                message=f"failed to parse cert: {e}",
                platform=self.platform,
            )
        if not _is_pce_owned_subject(subject):
            return CertOpResult(
                ok=False,
                message=(
                    f"refusing to install non-PCE cert (subject={subject!r}); "
                    "PCE only manages mitmproxy-issued CAs"
                ),
                platform=self.platform,
                thumbprint_sha1=thumb,
            )

        if self.platform == Platform.WINDOWS:
            return self._install_windows(cert_path, thumb, dry_run=dry_run)
        if self.platform == Platform.MACOS:
            return self._install_macos(cert_path, thumb, dry_run=dry_run)
        if self.platform == Platform.LINUX:
            return self._install_linux(cert_path, thumb, dry_run=dry_run)
        return CertOpResult(
            ok=False,
            message=f"unsupported platform: {self.platform.value}",
            platform=self.platform,
            thumbprint_sha1=thumb,
        )

    def _install_windows(
        self, cert_path: Path, thumb: str, dry_run: bool,
    ) -> CertOpResult:
        cmd = ["certutil", "-addstore", "-f", "Root", str(cert_path)]
        if dry_run:
            return CertOpResult(
                ok=False,
                message="dry_run: would install CA into LocalMachine Root",
                platform=self.platform,
                needs_elevation=True,
                elevated_cmd=cmd,
                dry_run=True,
                thumbprint_sha1=thumb,
            )
        rc, out, err = self.runner(cmd)
        if rc == 0:
            log_event(logger, "cert.install.ok", platform="windows",
                      thumbprint_sha1=thumb)
            return CertOpResult(
                ok=True,
                message="installed into LocalMachine Root",
                platform=self.platform,
                thumbprint_sha1=thumb,
                store_id=thumb,
                stdout=out, stderr=err,
            )
        # Access denied is the dominant failure mode on Windows; surface
        # it as "needs elevation" so the UI can re-invoke via UAC.
        access_denied = (
            "access" in (out + err).lower() and "denied" in (out + err).lower()
        ) or rc in (5,)
        return CertOpResult(
            ok=False,
            message="certutil failed; elevation required"
                    if access_denied else f"certutil exited with {rc}",
            platform=self.platform,
            needs_elevation=access_denied,
            elevated_cmd=cmd if access_denied else None,
            thumbprint_sha1=thumb,
            stdout=out, stderr=err,
        )

    def _install_macos(
        self, cert_path: Path, thumb: str, dry_run: bool,
    ) -> CertOpResult:
        cmd = [
            "security", "add-trusted-cert",
            "-d",
            "-r", "trustRoot",
            "-k", "/Library/Keychains/System.keychain",
            str(cert_path),
        ]
        if dry_run:
            return CertOpResult(
                ok=False,
                message="dry_run: would install into System keychain as trust root",
                platform=self.platform,
                needs_elevation=True,
                elevated_cmd=cmd,
                dry_run=True,
                thumbprint_sha1=thumb,
            )
        rc, out, err = self.runner(cmd)
        if rc == 0:
            log_event(logger, "cert.install.ok", platform="macos",
                      thumbprint_sha1=thumb)
            return CertOpResult(
                ok=True,
                message="installed into System keychain",
                platform=self.platform,
                thumbprint_sha1=thumb,
                store_id=thumb,
                stdout=out, stderr=err,
            )
        needs_elev = rc in (1,) and ("authoriz" in err.lower() or "perm" in err.lower())
        return CertOpResult(
            ok=False,
            message="security(1) failed; elevation required"
                    if needs_elev else f"security(1) exited with {rc}",
            platform=self.platform,
            needs_elevation=needs_elev,
            elevated_cmd=cmd if needs_elev else None,
            thumbprint_sha1=thumb,
            stdout=out, stderr=err,
        )

    def _install_linux(
        self, cert_path: Path, thumb: str, dry_run: bool,
    ) -> CertOpResult:
        dest = Path("/usr/local/share/ca-certificates") / f"pce-mitmproxy-{thumb[:8]}.crt"
        cp_cmd = ["cp", str(cert_path), str(dest)]
        update_cmd = ["update-ca-certificates"]
        if dry_run:
            return CertOpResult(
                ok=False,
                message="dry_run: would copy cert and run update-ca-certificates",
                platform=self.platform,
                needs_elevation=True,
                elevated_cmd=cp_cmd + ["&&"] + update_cmd,
                dry_run=True,
                thumbprint_sha1=thumb,
                extra={"cp_cmd": cp_cmd, "update_cmd": update_cmd, "dest": str(dest)},
            )
        # Without root we can't write under /usr/local or run the update tool.
        if os.geteuid() != 0 if hasattr(os, "geteuid") else True:
            return CertOpResult(
                ok=False,
                message="elevation required to install into /usr/local/share/ca-certificates",
                platform=self.platform,
                needs_elevation=True,
                elevated_cmd=cp_cmd + ["&&"] + update_cmd,
                thumbprint_sha1=thumb,
                extra={"cp_cmd": cp_cmd, "update_cmd": update_cmd, "dest": str(dest)},
            )
        rc1, out1, err1 = self.runner(cp_cmd)
        if rc1 != 0:
            return CertOpResult(
                ok=False,
                message=f"cp failed with rc={rc1}",
                platform=self.platform, thumbprint_sha1=thumb,
                stdout=out1, stderr=err1,
            )
        rc2, out2, err2 = self.runner(update_cmd)
        if rc2 != 0:
            return CertOpResult(
                ok=False,
                message=f"update-ca-certificates failed with rc={rc2}",
                platform=self.platform, thumbprint_sha1=thumb,
                stdout=out1 + out2, stderr=err1 + err2,
            )
        log_event(logger, "cert.install.ok", platform="linux",
                  thumbprint_sha1=thumb, dest=str(dest))
        return CertOpResult(
            ok=True,
            message=f"installed to {dest}",
            platform=self.platform,
            thumbprint_sha1=thumb,
            store_id=dest.name,
            output_path=str(dest),
            stdout=out1 + out2, stderr=err1 + err2,
        )

    # ---- uninstall ---------------------------------------------------------

    def uninstall(
        self, thumbprint_sha1: str, dry_run: bool = False,
    ) -> CertOpResult:
        thumb = (thumbprint_sha1 or "").upper().replace(":", "").replace(" ", "")
        if not thumb or len(thumb) != 40:
            return CertOpResult(
                ok=False,
                message="expected 40-char hex SHA-1 thumbprint",
                platform=self.platform,
            )
        if self.platform == Platform.WINDOWS:
            return self._uninstall_windows(thumb, dry_run=dry_run)
        if self.platform == Platform.MACOS:
            return self._uninstall_macos(thumb, dry_run=dry_run)
        if self.platform == Platform.LINUX:
            return self._uninstall_linux(thumb, dry_run=dry_run)
        return CertOpResult(
            ok=False,
            message=f"unsupported platform: {self.platform.value}",
            platform=self.platform,
        )

    def _uninstall_windows(self, thumb: str, dry_run: bool) -> CertOpResult:
        cmd = ["certutil", "-delstore", "Root", thumb]
        if dry_run:
            return CertOpResult(
                ok=False, message="dry_run: would delete from Root store",
                platform=self.platform, needs_elevation=True,
                elevated_cmd=cmd, dry_run=True, thumbprint_sha1=thumb,
            )
        rc, out, err = self.runner(cmd)
        if rc == 0:
            log_event(logger, "cert.uninstall.ok", platform="windows",
                      thumbprint_sha1=thumb)
            return CertOpResult(ok=True, message="deleted from Root store",
                                platform=self.platform, thumbprint_sha1=thumb,
                                stdout=out, stderr=err)
        access_denied = "access" in (out + err).lower() and "denied" in (out + err).lower()
        return CertOpResult(
            ok=False,
            message="certutil failed; elevation required"
                    if access_denied else f"certutil exited with {rc}",
            platform=self.platform,
            needs_elevation=access_denied,
            elevated_cmd=cmd if access_denied else None,
            thumbprint_sha1=thumb,
            stdout=out, stderr=err,
        )

    def _uninstall_macos(self, thumb: str, dry_run: bool) -> CertOpResult:
        cmd = [
            "security", "delete-certificate",
            "-Z", thumb,
            "/Library/Keychains/System.keychain",
        ]
        if dry_run:
            return CertOpResult(
                ok=False, message="dry_run: would delete from System keychain",
                platform=self.platform, needs_elevation=True,
                elevated_cmd=cmd, dry_run=True, thumbprint_sha1=thumb,
            )
        rc, out, err = self.runner(cmd)
        if rc == 0:
            log_event(logger, "cert.uninstall.ok", platform="macos",
                      thumbprint_sha1=thumb)
            return CertOpResult(ok=True, message="deleted from System keychain",
                                platform=self.platform, thumbprint_sha1=thumb,
                                stdout=out, stderr=err)
        needs_elev = "authoriz" in err.lower() or "perm" in err.lower()
        return CertOpResult(
            ok=False,
            message="security(1) failed; elevation required"
                    if needs_elev else f"security(1) exited with {rc}",
            platform=self.platform,
            needs_elevation=needs_elev,
            elevated_cmd=cmd if needs_elev else None,
            thumbprint_sha1=thumb,
            stdout=out, stderr=err,
        )

    def _uninstall_linux(self, thumb: str, dry_run: bool) -> CertOpResult:
        fname = f"pce-mitmproxy-{thumb[:8].lower()}.crt"
        dest = Path("/usr/local/share/ca-certificates") / fname
        rm_cmd = ["rm", "-f", str(dest)]
        update_cmd = ["update-ca-certificates", "--fresh"]
        if dry_run:
            return CertOpResult(
                ok=False,
                message=f"dry_run: would rm {dest} and refresh trust store",
                platform=self.platform, needs_elevation=True,
                elevated_cmd=rm_cmd + ["&&"] + update_cmd,
                dry_run=True, thumbprint_sha1=thumb,
                extra={"rm_cmd": rm_cmd, "update_cmd": update_cmd,
                       "dest": str(dest)},
            )
        if os.geteuid() != 0 if hasattr(os, "geteuid") else True:
            return CertOpResult(
                ok=False,
                message="elevation required to modify /usr/local/share/ca-certificates",
                platform=self.platform, needs_elevation=True,
                elevated_cmd=rm_cmd + ["&&"] + update_cmd,
                thumbprint_sha1=thumb,
                extra={"rm_cmd": rm_cmd, "update_cmd": update_cmd,
                       "dest": str(dest)},
            )
        rc1, out1, err1 = self.runner(rm_cmd)
        rc2, out2, err2 = self.runner(update_cmd)
        if rc2 == 0:
            log_event(logger, "cert.uninstall.ok", platform="linux",
                      thumbprint_sha1=thumb)
            return CertOpResult(ok=True, message=f"removed {dest}",
                                platform=self.platform, thumbprint_sha1=thumb,
                                stdout=out1 + out2, stderr=err1 + err2)
        return CertOpResult(
            ok=False,
            message=f"update-ca-certificates exited with {rc2}",
            platform=self.platform, thumbprint_sha1=thumb,
            stdout=out1 + out2, stderr=err1 + err2,
        )

    # ---- export ------------------------------------------------------------

    def export(
        self, dest: Path, *, ca_path: Optional[Path] = None,
    ) -> CertOpResult:
        """Copy the mitmproxy CA to *dest*. Never needs elevation."""
        # When the caller explicitly names a source, honour it verbatim —
        # don't silently fall through to the default CA, that would mask a
        # typo or test error. The default-CA fallback only applies when no
        # path was supplied.
        if ca_path is not None:
            src = Path(ca_path)
            if not src.exists():
                return CertOpResult(
                    ok=False,
                    message=f"cert file not found at explicit ca_path: {src}",
                    platform=self.platform,
                )
        else:
            src = default_ca_path()
            if not src.exists():
                src_cer = default_ca_cer_path()
                if src_cer.exists():
                    src = src_cer
                else:
                    return CertOpResult(
                        ok=False,
                        message=(
                            f"mitmproxy CA not found at {src} "
                            "(has mitmdump run at least once?)"
                        ),
                        platform=self.platform,
                    )
        dest = Path(dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            thumb = _compute_sha1_thumbprint(src)
            log_event(logger, "cert.export.ok", src=str(src), dest=str(dest),
                      thumbprint_sha1=thumb)
            return CertOpResult(
                ok=True,
                message=f"copied CA to {dest}",
                platform=self.platform,
                thumbprint_sha1=thumb,
                output_path=str(dest),
            )
        except OSError as e:
            return CertOpResult(
                ok=False,
                message=f"export failed: {e}",
                platform=self.platform,
            )

    # ---- regenerate --------------------------------------------------------

    def regenerate(self, *, dry_run: bool = False) -> CertOpResult:
        """Delete the existing mitmproxy CA so mitmdump regenerates it.

        We do not generate a cert ourselves — mitmproxy owns the key
        material.  We just clear the file and then ``mitmdump --help`` to
        force regeneration (mitmdump auto-generates the CA when missing).
        The caller is expected to call :func:`install_ca` afterwards.
        """
        src = default_ca_path()
        cer = default_ca_cer_path()
        p12 = src.with_name("mitmproxy-ca.p12")
        key = src.with_name("mitmproxy-ca.pem")
        candidates = [src, cer, p12, key]
        if dry_run:
            return CertOpResult(
                ok=False,
                message="dry_run: would delete mitmproxy CA files and regenerate",
                platform=self.platform,
                dry_run=True,
                extra={"paths": [str(p) for p in candidates]},
            )
        removed = []
        for p in candidates:
            try:
                if p.exists():
                    p.unlink()
                    removed.append(str(p))
            except OSError as e:
                return CertOpResult(
                    ok=False,
                    message=f"could not remove {p}: {e}",
                    platform=self.platform,
                )
        # mitmdump will regenerate the CA on next launch. We don't trigger
        # it here to keep this function side-effect-light; the supervisor
        # or CLI caller should start mitmdump afterwards.
        log_event(logger, "cert.regenerate.cleared",
                  removed=removed)
        return CertOpResult(
            ok=True,
            message=("cleared CA files; next mitmdump launch will regenerate. "
                     f"removed={len(removed)} file(s)"),
            platform=self.platform,
            output_path=str(src),
            extra={"removed": removed},
        )

    # ---- list --------------------------------------------------------------

    def list_installed(self) -> List[CertInfo]:
        """Enumerate PCE-owned CAs in the current host trust store."""
        if self.platform == Platform.WINDOWS:
            return self._list_windows()
        if self.platform == Platform.MACOS:
            return self._list_macos()
        if self.platform == Platform.LINUX:
            return self._list_linux()
        return []

    def _list_windows(self) -> List[CertInfo]:
        rc, out, _err = self.runner(["certutil", "-store", "Root"])
        if rc != 0:
            return []
        return _parse_windows_certutil_store(out, platform=self.platform)

    def _list_macos(self) -> List[CertInfo]:
        rc, out, _err = self.runner([
            "security", "find-certificate", "-a", "-c", "mitmproxy",
            "-Z", "/Library/Keychains/System.keychain",
        ])
        if rc != 0:
            return []
        return _parse_macos_security_output(out, platform=self.platform)

    def _list_linux(self) -> List[CertInfo]:
        found: List[CertInfo] = []
        share = Path("/usr/local/share/ca-certificates")
        if not share.exists():
            return found
        for p in share.glob("pce-mitmproxy-*.crt"):
            try:
                subject = _extract_subject_cn(p)
                thumb = _compute_sha1_thumbprint(p)
            except Exception:
                continue
            found.append(CertInfo(
                subject=subject, issuer=subject,
                thumbprint_sha1=thumb, store_id=p.name,
                platform=self.platform, source_path=str(p),
            ))
        return found


# ---------------------------------------------------------------------------
# Windows / macOS list-output parsers (module-level for testability)
# ---------------------------------------------------------------------------

_WIN_SUBJECT_RE = re.compile(r"Subject:\s*(.+)")
_WIN_THUMB_RE = re.compile(r"Cert Hash\(sha1\):\s*([0-9A-Fa-f\s]+)")


def _parse_windows_certutil_store(
    output: str, *, platform: Platform = Platform.WINDOWS,
) -> List[CertInfo]:
    """Parse ``certutil -store Root`` output; return only PCE-owned entries."""
    entries: List[CertInfo] = []
    # certutil separates entries with a blank line after "=== Certificate".
    blocks = re.split(r"================ Certificate \d+ ================", output)
    for block in blocks:
        subj_m = _WIN_SUBJECT_RE.search(block)
        thumb_m = _WIN_THUMB_RE.search(block)
        if not (subj_m and thumb_m):
            continue
        subject = subj_m.group(1).strip()
        if not _is_pce_owned_subject(subject):
            continue
        thumb = re.sub(r"\s+", "", thumb_m.group(1)).upper()
        entries.append(CertInfo(
            subject=subject,
            issuer=subject,
            thumbprint_sha1=thumb,
            store_id=thumb,
            platform=platform,
        ))
    return entries


def _parse_macos_security_output(
    output: str, *, platform: Platform = Platform.MACOS,
) -> List[CertInfo]:
    """Parse ``security find-certificate -Z -a -c mitmproxy ...`` output."""
    entries: List[CertInfo] = []
    current_thumb: Optional[str] = None
    current_label: Optional[str] = None
    for line in output.splitlines():
        line_s = line.strip()
        if line_s.startswith("SHA-1 hash:"):
            current_thumb = line_s.split(":", 1)[1].strip().upper()
        elif '"labl"<blob>=' in line_s:
            # labl is the keychain label, e.g. "mitmproxy"
            m = re.search(r'"labl"<blob>="([^"]+)"', line_s)
            if m:
                current_label = m.group(1)
        elif line_s.startswith("keychain:") and current_thumb:
            subject = current_label or "mitmproxy"
            if _is_pce_owned_subject(subject):
                entries.append(CertInfo(
                    subject=subject, issuer=subject,
                    thumbprint_sha1=current_thumb,
                    store_id=current_thumb,
                    platform=platform,
                ))
            current_thumb = None
            current_label = None
    return entries


# ---------------------------------------------------------------------------
# Public functional API — thin wrappers, convenient for server & CLI.
# ---------------------------------------------------------------------------

def install_ca(
    cert_path: Optional[Path] = None,
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
) -> CertOpResult:
    """Install the mitmproxy CA into the host trust store."""
    mgr = CertManager(platform=platform or detect_platform(),
                      runner=runner or _default_runner)
    return mgr.install(cert_path or default_ca_path(), dry_run=dry_run)


def uninstall_ca(
    thumbprint_sha1: str,
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
) -> CertOpResult:
    mgr = CertManager(platform=platform or detect_platform(),
                      runner=runner or _default_runner)
    return mgr.uninstall(thumbprint_sha1, dry_run=dry_run)


def export_ca(
    dest: Path,
    *, ca_path: Optional[Path] = None,
    platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
) -> CertOpResult:
    mgr = CertManager(platform=platform or detect_platform(),
                      runner=runner or _default_runner)
    return mgr.export(Path(dest), ca_path=ca_path)


def regenerate_ca(
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
    dry_run: bool = False,
) -> CertOpResult:
    mgr = CertManager(platform=platform or detect_platform(),
                      runner=runner or _default_runner)
    return mgr.regenerate(dry_run=dry_run)


def list_ca(
    *, platform: Optional[Platform] = None,
    runner: Optional[Runner] = None,
) -> List[CertInfo]:
    mgr = CertManager(platform=platform or detect_platform(),
                      runner=runner or _default_runner)
    return mgr.list_installed()
