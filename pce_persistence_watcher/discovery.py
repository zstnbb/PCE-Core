# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.discovery – locate target-app installs.

Given an OS, this module finds which closed-source Electron AI apps are
installed and returns the per-app filesystem layout the rest of the
watcher will read from. It knows about the following channels:

- **Windows MSIX** (ADR-018's motivating case): install location lives
  under ``C:\\Program Files\\WindowsApps\\<PackageFullName>`` (not
  directly readable by the current user — we don't need to read it;
  the user-writable state lives in ``%LOCALAPPDATA%\\Packages\\
  <PackageFamilyName>\\LocalCache\\Roaming\\<AppName>`` which IS
  readable). Resolved via ``powershell Get-AppxPackage``.

- **Windows Squirrel** (legacy): install location is
  ``%LOCALAPPDATA%\\<Publisher><AppName>\\<version>\\`` with state
  directly under ``%APPDATA%\\<AppName>``.

- **macOS**: apps at ``/Applications/<Name>.app`` with state at
  ``~/Library/Application Support/<Name>``.

- **Linux**: apps packaged as Flatpak / snap / AppImage vary; state
  usually at ``~/.config/<Name>``.

For each install we return an ``AppInstall`` describing:

- ``app_id``: stable short id used throughout the watcher
  (``"claude-desktop"`` / ``"chatgpt-desktop"``).
- ``channel``: ``"msix"`` | ``"squirrel"`` | ``"mac-dmg"`` | ``"linux"``.
- ``version``: human string when resolvable, else None.
- ``roots``: a dict of named filesystem anchors (see ``_AppLayout``).

The JSON-based data sources (``agent_sessions.py``) and the LevelDB
source (``leveldb_reader.py``) consume these anchors and are therefore
platform-agnostic themselves.

Silence on a missing install is the intended behaviour: ``discover()``
returns ``[]`` and upstream code treats that as "skip".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("pce.persistence_watcher.discovery")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AppInstall:
    """One discovered install of a target application.

    Roots are the named anchors ``agent_sessions.py`` and
    ``leveldb_reader.py`` walk. A root may be absent (not yet created
    by the app) — consumers MUST tolerate ``roots.get(name)`` returning
    None or a non-existent path.
    """

    app_id: str
    channel: str
    version: Optional[str]
    install_location: Optional[Path]
    roots: dict[str, Path] = field(default_factory=dict)

    def root(self, name: str) -> Optional[Path]:
        p = self.roots.get(name)
        if p is None:
            return None
        return p if p.exists() else None


# ---------------------------------------------------------------------------
# Windows MSIX discovery
# ---------------------------------------------------------------------------

# Stable partial AppxPackage names per target app. We match with
# ``-like '*Name*'`` semantics so a Microsoft Store re-publish that
# shifts the PublisherHash does not break discovery.
_MSIX_NAME_PATTERNS: dict[str, str] = {
    "claude-desktop": "Claude",
    "chatgpt-desktop": "ChatGPT",
}


def _run_powershell(script: str, timeout: float = 8.0) -> Optional[str]:
    """Run a PowerShell command and return stdout, or None on failure."""
    if sys.platform != "win32":
        return None
    for pwsh in ("pwsh", "powershell"):
        exe = shutil.which(pwsh)
        if not exe:
            continue
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("powershell call failed: %s", exc)
            continue
        if proc.returncode == 0:
            return proc.stdout
        logger.debug("powershell non-zero exit %d: %s", proc.returncode, proc.stderr[:200])
    return None


def _discover_msix(app_id: str, name_pattern: str) -> Optional[AppInstall]:
    """Return the AppInstall if app is MSIX-installed on Windows, else None."""
    # Request the fields we need in a stable machine-parseable form.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$p = Get-AppxPackage -Name '*{name_pattern}*' | Select-Object -First 1;"
        "if ($p) {"
        "  $obj = [ordered]@{"
        "    PackageFullName = $p.PackageFullName;"
        "    PackageFamilyName = $p.PackageFamilyName;"
        "    InstallLocation = $p.InstallLocation;"
        "    Version = $p.Version.ToString();"
        "  };"
        "  ConvertTo-Json -Compress -InputObject $obj"
        "}"
    )
    raw = _run_powershell(script)
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Minimal JSON parser — the PS output shape is known & flat so we
    # use stdlib json to avoid a schema validation roundtrip.
    try:
        import json as _json  # local import keeps cold-start minimal
        data = _json.loads(raw)
    except ValueError:
        logger.debug("MSIX query returned non-JSON: %r", raw[:200])
        return None
    if not isinstance(data, dict):
        return None

    package_family_name = data.get("PackageFamilyName")
    install_location = data.get("InstallLocation")
    version = data.get("Version")

    local_appdata = os.environ.get("LOCALAPPDATA")
    roots: dict[str, Path] = {}
    if local_appdata and package_family_name:
        # Windows MSIX apps redirect writes to a per-package virtual
        # store. The user-readable mirror of %APPDATA%\<app> is at
        # LocalCache\Roaming\<AppName>.
        cache_root = Path(local_appdata) / "Packages" / package_family_name / "LocalCache" / "Roaming"
        if cache_root.exists():
            # Pick the first child directory — Claude Desktop writes
            # to ``...Roaming\Claude``. We don't hard-code ``Claude``
            # because ChatGPT Desktop or future apps may differ.
            for child in cache_root.iterdir():
                if child.is_dir():
                    roots["app_profile"] = child
                    roots["local_storage_leveldb"] = child / "Local Storage" / "leveldb"
                    roots["indexeddb"] = child / "IndexedDB"
                    roots["agent_sessions"] = child / "local-agent-mode-sessions"
                    roots["vm_bundles"] = child / "vm_bundles"
                    roots["claude_code_vm"] = child / "claude-code-vm"
                    # P5.B.7 (2026-05-11): inline Code-tab session
                    # pointer dir. Pairs cliSessionId↔transcript at
                    # ~/.claude/projects/<encoded-cwd>/<cliSessId>.jsonl
                    # via the JSON pointer at
                    # claude-code-sessions/<user>/<org>/local_<sess>.json
                    # See `Docs/research/2026-05-11-code-tab-recon-findings.md`.
                    roots["claude_code_sessions"] = child / "claude-code-sessions"
                    # P5.B.7 (2026-05-11): inline Code-tab JSONL
                    # transcripts live OUTSIDE app_profile, in user
                    # home (~/.claude/projects/<encoded-cwd>/<cliSessId>.jsonl).
                    # Same dir is shared with standalone P6 Claude
                    # Code CLI; entrypoint:"claude-desktop" field on
                    # each line discriminates Desktop vs CLI origin.
                    roots["claude_projects"] = Path.home() / ".claude" / "projects"
                    roots["logs"] = child / "logs"
                    break

    return AppInstall(
        app_id=app_id,
        channel="msix",
        version=version if isinstance(version, str) else None,
        install_location=Path(install_location) if install_location else None,
        roots=roots,
    )


# ---------------------------------------------------------------------------
# Windows Squirrel discovery (legacy installer)
# ---------------------------------------------------------------------------

_SQUIRREL_LAYOUT: dict[str, dict[str, str]] = {
    "claude-desktop": {
        "install_parent": "AnthropicClaude",
        "state_appdata_dir": "Claude",
    },
}


def _discover_squirrel(app_id: str) -> Optional[AppInstall]:
    if sys.platform != "win32":
        return None
    layout = _SQUIRREL_LAYOUT.get(app_id)
    if layout is None:
        return None

    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if not local_appdata:
        return None

    install_parent = Path(local_appdata) / layout["install_parent"]
    if not install_parent.exists():
        return None

    exe = install_parent / "Claude.exe"
    if not exe.exists():
        # Some Squirrel builds put the exe under a versioned subdir
        # (e.g. ``app-1.0.0/Claude.exe``). Grab the latest.
        versioned = sorted(
            (p for p in install_parent.iterdir() if p.is_dir() and p.name.startswith("app-")),
            reverse=True,
        )
        if versioned:
            candidate = versioned[0] / "Claude.exe"
            if candidate.exists():
                exe = candidate

    state_root = None
    if appdata and layout.get("state_appdata_dir"):
        state_root = Path(appdata) / layout["state_appdata_dir"]

    roots: dict[str, Path] = {}
    if state_root and state_root.exists():
        roots["app_profile"] = state_root
        roots["local_storage_leveldb"] = state_root / "Local Storage" / "leveldb"
        roots["indexeddb"] = state_root / "IndexedDB"
        roots["agent_sessions"] = state_root / "local-agent-mode-sessions"
        roots["vm_bundles"] = state_root / "vm_bundles"
        roots["claude_code_vm"] = state_root / "claude-code-vm"
        roots["claude_code_sessions"] = state_root / "claude-code-sessions"
        roots["claude_projects"] = Path.home() / ".claude" / "projects"
        roots["logs"] = state_root / "logs"

    version: Optional[str] = None
    try:
        # VersionInfo probe via PowerShell — avoids needing pywin32.
        script = (
            f"(Get-Item -LiteralPath '{exe}').VersionInfo.ProductVersion"
        )
        raw = _run_powershell(script)
        if raw:
            version = raw.strip() or None
    except Exception:  # pragma: no cover — defensive
        pass

    return AppInstall(
        app_id=app_id,
        channel="squirrel",
        version=version,
        install_location=exe if exe.exists() else install_parent,
        roots=roots,
    )


# ---------------------------------------------------------------------------
# macOS discovery
# ---------------------------------------------------------------------------

_MAC_LAYOUT: dict[str, dict[str, str]] = {
    "claude-desktop": {
        "app_bundle_name": "Claude.app",
        "support_dir_name": "Claude",
    },
    "chatgpt-desktop": {
        "app_bundle_name": "ChatGPT.app",
        "support_dir_name": "com.openai.chat",
    },
}


def _discover_mac(app_id: str) -> Optional[AppInstall]:
    if sys.platform != "darwin":
        return None
    layout = _MAC_LAYOUT.get(app_id)
    if layout is None:
        return None

    for prefix in (Path("/Applications"), Path.home() / "Applications"):
        bundle = prefix / layout["app_bundle_name"]
        if bundle.exists():
            support = Path.home() / "Library" / "Application Support" / layout["support_dir_name"]
            roots: dict[str, Path] = {}
            if support.exists():
                roots["app_profile"] = support
                roots["local_storage_leveldb"] = support / "Local Storage" / "leveldb"
                roots["indexeddb"] = support / "IndexedDB"
                roots["agent_sessions"] = support / "local-agent-mode-sessions"
                roots["claude_code_sessions"] = support / "claude-code-sessions"
                roots["claude_projects"] = Path.home() / ".claude" / "projects"
                roots["logs"] = support / "logs"
            return AppInstall(
                app_id=app_id,
                channel="mac-dmg",
                version=None,  # version probe via ``mdls`` is best-effort; v0.1
                install_location=bundle,
                roots=roots,
            )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_DISCOVERY_ORDER: tuple[tuple[str, ...], ...] = (
    # app_id, (per-channel discovery callable tag)
    ("claude-desktop", "msix", "squirrel", "mac"),
    ("chatgpt-desktop", "msix", "mac"),
)


def discover(
    *,
    app_filter: Optional[Iterable[str]] = None,
) -> list[AppInstall]:
    """Return all discovered installs across the current OS's channels.

    ``app_filter`` — optional set/list of ``app_id`` strings to restrict
    scanning. Empty or None ⇒ scan everything known.

    Multiple channels for the same ``app_id`` are both returned (e.g.
    a machine with MSIX Claude Desktop AND a leftover Squirrel copy).
    The first entry in the list is always the "primary" channel for the
    OS (MSIX on Windows, DMG on macOS).
    """
    wanted: Optional[set[str]] = None
    if app_filter:
        wanted = {s.lower() for s in app_filter}

    results: list[AppInstall] = []
    for row in _DISCOVERY_ORDER:
        app_id = row[0]
        if wanted is not None and app_id not in wanted:
            continue
        channels = row[1:]
        for ch in channels:
            inst: Optional[AppInstall] = None
            if ch == "msix":
                pattern = _MSIX_NAME_PATTERNS.get(app_id)
                if pattern:
                    inst = _discover_msix(app_id, pattern)
            elif ch == "squirrel":
                inst = _discover_squirrel(app_id)
            elif ch == "mac":
                inst = _discover_mac(app_id)
            if inst is not None:
                results.append(inst)
    return results


def summarise(installs: list[AppInstall]) -> dict[str, object]:
    """Return a JSON-serialisable summary of discovered installs.

    Used by ``python -m pce_persistence_watcher discover`` and by
    ``tests/e2e_l3g/`` to assert on discovery output shape.
    """
    out: list[dict[str, object]] = []
    for i in installs:
        out.append({
            "app_id": i.app_id,
            "channel": i.channel,
            "version": i.version,
            "install_location": str(i.install_location) if i.install_location else None,
            "roots": {
                name: {
                    "path": str(path),
                    "exists": path.exists(),
                }
                for name, path in i.roots.items()
            },
        })
    return {
        "installs": out,
        "count": len(installs),
    }
