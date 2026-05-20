# SPDX-License-Identifier: Apache-2.0
"""System-state guard — snapshot, take-over, restore.

The Control Panel touches the host's system-proxy slot so PCE can MITM.
Whenever PCE modifies that slot we **must** put it back to exactly the
pre-PCE state when the user closes the program, regardless of how the
exit happens:

- clean close via tray → Quit                  (best case)
- Ctrl-C in the terminal (SIGINT)              (signal handler)
- desktop shortcut closed by clicking [×]      (Qt closeEvent)
- crash / kill -9 / Task Manager End Task      (next-startup recovery)

To survive the last case the snapshot is persisted to disk
(:data:`STATE_PATH`) BEFORE we mutate anything. The next time the panel
starts it inspects the file: if it exists, the previous run did not
clean up, so we restore from it immediately.

This module owns ONE piece of host state today (the system proxy). Add
new fields to :class:`SystemStateSnapshot` as new take-overs land.
"""

from __future__ import annotations

import atexit
import json
import logging
import signal
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from pce_core.proxy_toggle import (
    ProxyState,
    detect_platform,
    disable_system_proxy,
    enable_system_proxy,
    get_proxy_state,
)
from pce_core.proxy_toggle.models import Platform

logger = logging.getLogger("pce.system_state_guard")


def _dedup_bypass(items) -> list:
    """Remove duplicate bypass entries while preserving order.

    Windows accumulates duplicate ``<local>`` tokens in ProxyOverride
    when other tools (Clash etc.) write their own bypass list without
    de-duping first. Without this filter, the list grows by one entry
    on every PCE restart, which is harmless functionally but ugly
    and inflates the registry value indefinitely.
    """
    if not items:
        return []
    seen = set()
    out: list = []
    for v in items:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out

STATE_DIR: Path = Path.home() / ".pce" / "state"
STATE_PATH: Path = STATE_DIR / "system_state.json"
EMERGENCY_PS1_PATH: Path = STATE_DIR / "EMERGENCY_RESTORE.ps1"
EMERGENCY_CMD_PATH: Path = STATE_DIR / "EMERGENCY_RESTORE.cmd"
EMERGENCY_PY_PATH: Path = STATE_DIR / "EMERGENCY_RESTORE.py"

SNAPSHOT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Emergency restore scripts — written next to the snapshot whenever PCE
# takes over the system proxy. The scripts are PURE WINDOWS / PURE STDLIB
# so they can recover the user's machine even if:
#   - the PCE Python environment is uninstalled
#   - the PCE source tree is deleted
#   - the user has never opened PCE again
#
# The user can find them in File Explorer at ~/.pce/state/ and double-
# click the .cmd to restore. The Desktop also has an "Emergency Restore"
# shortcut pointing at the .cmd.
# ---------------------------------------------------------------------------

# Embedded scripts — kept inline rather than read from disk so even a
# partial PCE install can deploy them. Single source of truth: must
# stay in sync with scripts/pce_restore.{ps1,py,cmd} (see those files
# for the canonical versions).

# NB: deliberately ASCII-only — CMD on legacy code pages doesn't tolerate
# em-dashes or other Unicode in batch files.
_EMERGENCY_CMD = (
    "@echo off\r\n"
    "REM PCE Emergency Restore - auto-deployed by SystemStateGuard.\r\n"
    "REM Double-click to restore the system proxy to the pre-PCE state.\r\n"
    "cd /d \"%~dp0\"\r\n"
    "powershell.exe -NoProfile -ExecutionPolicy Bypass "
    "-File \"%~dp0EMERGENCY_RESTORE.ps1\" %*\r\n"
    "echo.\r\n"
    "echo Press any key to close...\r\n"
    "pause >nul\r\n"
)

_EMERGENCY_PS1 = r"""# Auto-deployed by PCE SystemStateGuard. Pure PowerShell.
# Restores Windows system proxy to the snapshot at system_state.json.
param([switch]$Disable, [switch]$Show)
$ErrorActionPreference = 'Continue'
$StateFile = "$PSScriptRoot\system_state.json"
$Key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'

$winInetSig = @'
[DllImport("wininet.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern bool InternetSetOption(
    IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
'@
function Invoke-WinINetRefresh {
    try {
        $t = Add-Type -MemberDefinition $winInetSig `
                      -Name 'PCEWinINet' -Namespace 'PCE' -PassThru
        [void]$t::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)
        [void]$t::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)
    } catch {}
}
function Show-Current {
    Write-Host "Snapshot   : $StateFile"
    if (Test-Path $StateFile) { Write-Host '             EXISTS' }
    else { Write-Host '             (absent)' }
    try { $ie = Get-ItemProperty -Path $Key -ErrorAction Stop } catch { return }
    Write-Host "ProxyEnable   : $($ie.ProxyEnable)"
    Write-Host "ProxyServer   : $($ie.ProxyServer)"
    Write-Host "ProxyOverride : $($ie.ProxyOverride)"
}
function Disable-Proxy {
    Set-ItemProperty -Path $Key -Name 'ProxyEnable' -Value 0
    Invoke-WinINetRefresh
    Write-Host '[OK] System proxy disabled.'
}
function Enable-Proxy { param([string]$H, [int]$P, [string[]]$B)
    $seen = @{}; $kept = @()
    foreach ($x in $B) { if (-not $seen.ContainsKey($x)) { $seen[$x]=1; $kept+=$x } }
    Set-ItemProperty -Path $Key -Name 'ProxyEnable' -Value 1
    Set-ItemProperty -Path $Key -Name 'ProxyServer' -Value "$($H):$P"
    if ($kept.Count -gt 0) {
        Set-ItemProperty -Path $Key -Name 'ProxyOverride' -Value ($kept -join ';')
    }
    Invoke-WinINetRefresh
    Write-Host "[OK] System proxy restored to $($H):$P"
}

Write-Host '============================================================'
Write-Host 'PCE Emergency Restore  (auto-deployed)'
Write-Host '============================================================'
Write-Host ''

if ($Show) { Show-Current; exit 0 }
if ($Disable) { Disable-Proxy; exit 0 }

if (Test-Path $StateFile) {
    Write-Host "Reading snapshot: $StateFile"
    try { $snap = Get-Content $StateFile -Raw | ConvertFrom-Json } catch {
        Write-Host "WARNING: snapshot unreadable: $_"
        Disable-Proxy
        Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
        exit 0
    }
    $p = $snap.proxy
    if ($p -and $p.enabled -and $p.host -and $p.port) {
        Write-Host "Restoring: $($p.host):$($p.port)"
        $bp = @(); if ($p.bypass) { $bp = @($p.bypass) }
        Enable-Proxy -H $p.host -P $p.port -B $bp
    } else {
        Write-Host 'Snapshot says proxy was OFF — disabling.'
        Disable-Proxy
    }
    Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Snapshot deleted.'
    exit 0
}

Write-Host 'No snapshot. Inspecting current state...'
try {
    $ie = Get-ItemProperty -Path $Key -ErrorAction Stop
    if ($ie.ProxyEnable -eq 1 -and ($ie.ProxyServer -match '127\.0\.0\.1:8080' -or
                                     $ie.ProxyServer -match 'localhost:8080')) {
        Write-Host "  ! Orphaned PCE proxy: $($ie.ProxyServer)"
        Write-Host '    Disabling.'
        Disable-Proxy
    } else {
        Write-Host "  Current: '$($ie.ProxyServer)' (enabled=$($ie.ProxyEnable))"
        Write-Host '  Not a PCE setting — leaving alone.'
    }
} catch { Write-Host "Could not read: $_" }
exit 0
"""

_EMERGENCY_PY_HEADER = (
    "# Auto-deployed by PCE SystemStateGuard. Stdlib-only.\n"
    "# If PowerShell is unavailable, run:  python EMERGENCY_RESTORE.py\n"
    "# Canonical source: scripts/pce_restore.py in the PCE repo.\n\n"
)


def _deploy_emergency_scripts() -> None:
    """Write the emergency restore scripts next to the snapshot.

    Idempotent (overwrites whatever's there). Best-effort — each script
    is written under its own try/except so one failing doesn't block
    the others. Losing a convenience script is always preferable to
    crashing during take-over.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not create state dir %s: %r", STATE_DIR, exc)
        return

    deployed = []
    try:
        EMERGENCY_PS1_PATH.write_text(_EMERGENCY_PS1, encoding="utf-8")
        deployed.append("ps1")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ps1 deploy failed: %r", exc)

    try:
        EMERGENCY_CMD_PATH.write_bytes(_EMERGENCY_CMD.encode("ascii"))
        deployed.append("cmd")
    except Exception as exc:  # noqa: BLE001
        logger.warning("cmd deploy failed: %r", exc)

    try:
        here = Path(__file__).resolve().parent.parent / "scripts" / "pce_restore.py"
        if here.is_file():
            EMERGENCY_PY_PATH.write_text(
                _EMERGENCY_PY_HEADER + here.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            deployed.append("py")
    except Exception as exc:  # noqa: BLE001
        logger.warning("py deploy failed: %r", exc)

    logger.info("deployed emergency scripts to %s: %s", STATE_DIR, deployed)


def _cleanup_emergency_scripts() -> None:
    """Remove emergency scripts when PCE shuts down cleanly.

    Their whole point is being available if PCE *doesn't* shut down
    cleanly, so when it does, take them off the user's disk so they
    don't accumulate."""
    for path in (EMERGENCY_PS1_PATH, EMERGENCY_CMD_PATH, EMERGENCY_PY_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class SystemStateSnapshot:
    """Persistable record of what the host looked like before PCE."""

    version: int = SNAPSHOT_SCHEMA_VERSION
    snapshotted_at: float = 0.0
    proxy: Optional[dict] = field(default=None)  # ProxyState.as_dict() or None
    pce_owns_proxy: bool = False                 # True after take_over()

    def to_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_path(cls, path: Path) -> Optional["SystemStateSnapshot"]:
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("snapshot at %s is unreadable JSON — ignoring", path)
            return None
        if data.get("version") != SNAPSHOT_SCHEMA_VERSION:
            logger.warning(
                "snapshot version mismatch (have %s, want %s) — ignoring",
                data.get("version"), SNAPSHOT_SCHEMA_VERSION,
            )
            return None
        return cls(
            version=int(data.get("version", 0)),
            snapshotted_at=float(data.get("snapshotted_at", 0.0)),
            proxy=data.get("proxy"),
            pce_owns_proxy=bool(data.get("pce_owns_proxy", False)),
        )


class SystemStateGuard:
    """One-process gatekeeper for "PCE changed your system, here's how to undo it".

    Lifecycle::

        guard = SystemStateGuard()
        guard.recover_from_crash()              # at process start
        ...
        guard.take_over_proxy(host, port)       # when PCE enables MITM
        ...
        guard.restore()                          # on clean exit (idempotent)

    :meth:`install_signal_handlers` wires the same :meth:`restore`
    into ``atexit`` + SIGINT/SIGTERM so a Ctrl-C or window-close still
    cleans up.
    """

    def __init__(self, state_path: Path = STATE_PATH):
        self._path: Path = state_path
        self._platform: Platform = detect_platform()
        self._lock = threading.RLock()
        self._snapshot: Optional[SystemStateSnapshot] = None
        self._installed: bool = False

    # ---- crash recovery ------------------------------------------------

    def recover_from_crash(self) -> bool:
        """If a snapshot file is present from a previous run, restore.

        Returns True if a recovery happened (UI may want to surface
        this as a toast / status-bar message)."""
        existing = SystemStateSnapshot.from_path(self._path)
        if existing is None:
            return False
        logger.warning(
            "found stale system_state snapshot (age %.0fs) — "
            "previous PCE run did not clean up. Reverting now.",
            time.time() - existing.snapshotted_at,
        )
        with self._lock:
            self._snapshot = existing
        self.restore(reason="crash-recovery")
        return True

    # ---- take-over -----------------------------------------------------

    def take_over_proxy(self, host: str, port: int) -> bool:
        """Snapshot current proxy state, then enable PCE's at ``host:port``.

        Idempotent: if a snapshot already exists AND the proxy is
        already pointed at ``host:port``, this is a no-op. That guard
        is what stops a double-click on Start from doing a redundant
        round-trip through the proxy_toggle code (which historically
        appended ``<local>`` on every call on some Windows setups).

        Returns True if the proxy was successfully enabled (or was
        already pointing at PCE), False on enable failure.
        """
        with self._lock:
            # Idempotency short-circuit
            if (self._snapshot is not None
                and self._snapshot.pce_owns_proxy):
                # Check the OS still has our setting; if so, do nothing.
                current = _safe_get_proxy_state(self._platform)
                if (current
                    and current.enabled
                    and current.host == host
                    and current.port == int(port)):
                    logger.debug(
                        "take_over_proxy: already owned at %s:%s — no-op",
                        host, port,
                    )
                    return True
                logger.info(
                    "take_over_proxy: previously owned but OS state drifted "
                    "(now %s:%s, want %s:%s) — re-applying",
                    current.host if current else "?",
                    current.port if current else "?",
                    host, port,
                )

            if self._snapshot is None:
                current = _safe_get_proxy_state(self._platform)
                proxy_dict = current.as_dict() if current else None
                # Dedup bypass so we don't snowball ``<local>`` etc.
                if proxy_dict and isinstance(proxy_dict.get("bypass"), list):
                    proxy_dict["bypass"] = _dedup_bypass(proxy_dict["bypass"])
                self._snapshot = SystemStateSnapshot(
                    snapshotted_at=time.time(),
                    proxy=proxy_dict,
                    pce_owns_proxy=False,
                )
                self._persist_locked()
                logger.info(
                    "system-proxy snapshot taken (was: %s)",
                    self._snapshot.proxy,
                )

            # Now apply our setting.
            try:
                result = enable_system_proxy(host=host, port=int(port))
                ok = bool(result.ok)
            except Exception as exc:  # noqa: BLE001
                logger.exception("enable_system_proxy raised: %r", exc)
                ok = False

            self._snapshot.pce_owns_proxy = ok
            self._persist_locked()
            return ok

    # ---- restore -------------------------------------------------------

    def restore(self, *, reason: str = "shutdown") -> None:
        """Put the system proxy back exactly as we found it.

        Safe to call repeatedly — once the snapshot file is gone we
        do nothing. ``reason`` only flavors the log line so we can
        tell graceful exits from crash-recovery.
        """
        with self._lock:
            if self._snapshot is None:
                # No state held in memory — try the disk in case this
                # call is happening from a signal handler that ran
                # before the panel object was fully wired.
                self._snapshot = SystemStateSnapshot.from_path(self._path)
                if self._snapshot is None:
                    return

            if not self._snapshot.pce_owns_proxy:
                # We snapshotted but never actually changed anything.
                logger.info("guard restore (%s): nothing to do", reason)
                self._delete_locked()
                self._snapshot = None
                return

            target = self._snapshot.proxy or {}
            try:
                if target.get("enabled") and target.get("host") and target.get("port"):
                    bypass = _dedup_bypass(target.get("bypass") or [])
                    enable_system_proxy(
                        host=str(target["host"]),
                        port=int(target["port"]),
                        bypass=bypass or None,
                    )
                    logger.info(
                        "guard restore (%s): re-enabled proxy → %s:%s (bypass=%d)",
                        reason, target["host"], target["port"], len(bypass),
                    )
                else:
                    disable_system_proxy()
                    logger.info("guard restore (%s): disabled system proxy", reason)
            except Exception as exc:  # noqa: BLE001 — must not raise from atexit
                logger.exception(
                    "guard restore (%s): proxy revert failed: %r",
                    reason, exc,
                )

            self._delete_locked()
            self._snapshot = None

    # ---- signal / atexit wiring ---------------------------------------

    def install_signal_handlers(self) -> None:
        """Idempotently register :meth:`restore` against atexit / signals."""
        with self._lock:
            if self._installed:
                return
            self._installed = True

        atexit.register(self._atexit_callback)
        # SIGTERM is best-effort on Windows; SIGINT works on both.
        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, self._signal_callback)
            except (OSError, ValueError):
                # signal can only be set from the main thread, etc.
                logger.debug("could not install handler for %s", sig_name)

    # ---- internals -----------------------------------------------------

    def _persist_locked(self) -> None:
        if self._snapshot is None:
            return
        try:
            self._snapshot.to_path(self._path)
        except OSError as exc:
            logger.warning("could not persist snapshot to %s: %r", self._path, exc)
        # Deploy emergency scripts so a force-killed PCE can be recovered
        # from outside the panel — by double-clicking the .cmd in
        # File Explorer or via the Desktop "Emergency Restore" shortcut.
        if self._snapshot.pce_owns_proxy:
            _deploy_emergency_scripts()

    def _delete_locked(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not delete snapshot %s: %r", self._path, exc)
        # Snapshot gone → emergency scripts no longer needed.
        _cleanup_emergency_scripts()

    def _atexit_callback(self) -> None:
        # atexit runs after threads stop — keep this dead simple.
        try:
            self.restore(reason="atexit")
        except Exception:  # noqa: BLE001 — atexit must not propagate
            logger.exception("atexit restore failed")

    def _signal_callback(self, signum, _frame) -> None:
        try:
            self.restore(reason=f"signal-{signum}")
        finally:
            # Re-raise the default handler so the program still dies.
            signal.signal(signum, signal.SIG_DFL)
            try:
                signal.raise_signal(signum)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton convenience
# ---------------------------------------------------------------------------

_singleton: Optional[SystemStateGuard] = None
_singleton_lock = threading.Lock()


def get_guard() -> SystemStateGuard:
    """Return the process-wide :class:`SystemStateGuard`.

    The Control Panel calls this once and shares the result with
    :class:`pce_app.service_manager.ServiceManager` so both halves
    see the same snapshot.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = SystemStateGuard()
        return _singleton


def _safe_get_proxy_state(platform: Platform) -> Optional[ProxyState]:
    try:
        return get_proxy_state(platform=platform)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_proxy_state failed: %r", exc)
        return None


__all__ = [
    "SystemStateGuard",
    "SystemStateSnapshot",
    "STATE_PATH",
    "get_guard",
]
