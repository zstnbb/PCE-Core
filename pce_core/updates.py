# SPDX-License-Identifier: Apache-2.0
"""PCE Core – version / update checker (P3.6, TASK-005 §5.6).

Design constraints:

- **No external dependencies.** We use only ``urllib`` so this works even
  before the user installs optional extras (phoenix, litellm, …).
- **Offline-safe.** A failed fetch never raises — it surfaces a
  structured error so the UI can say "couldn't reach manifest" rather
  than dying.
- **User-configurable.** The manifest URL is configurable via the
  ``PCE_UPDATE_MANIFEST_URL`` env var or a ``preferences.update_manifest_url``
  entry in ``app_state.json``. Setting it to an empty string disables the
  check entirely.
- **Cached.** Repeated calls within a cooldown window return the cached
  result so the tray can poll freely.

Manifest format
===============

We accept two shapes — either a PCE-native manifest or the subset of the
GitHub releases API we need::

    # PCE-native
    {
      "latest_version": "0.2.0",
      "release_date": "2026-04-20",
      "download_url": "https://pce.example/pce-0.2.0-win-x64.zip",
      "changelog_url": "https://pce.example/CHANGELOG.md",
      "notes": "Bug fixes + Phoenix integration"
    }

    # GitHub releases ``/repos/{owner}/{repo}/releases/latest``
    {
      "tag_name": "v0.2.0",
      "html_url": "https://github.com/.../releases/tag/v0.2.0",
      "body": "## What's new\\n…",
      "published_at": "2026-04-20T18:12:00Z"
    }

The parser normalises both into the PCE-native shape so callers always
see the same fields.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import __version__
from .logging_config import log_event

logger = logging.getLogger("pce.updates")


# ---------------------------------------------------------------------------
# Defaults / configuration
# ---------------------------------------------------------------------------

DEFAULT_MANIFEST_URL = os.environ.get(
    "PCE_UPDATE_MANIFEST_URL",
    "https://raw.githubusercontent.com/zstnbb/PCE-Core/main/releases/manifest.json",
)

# Skip the network if the user opted out via preferences / env.
UPDATES_DISABLED = os.environ.get("PCE_UPDATES_DISABLED", "").strip().lower() in (
    "1", "true", "yes", "on",
)

# Simple in-process cache — avoid hammering the manifest host when the
# tray polls every few minutes.
_CACHE_TTL_S = 60.0 * 5  # five minutes
_FETCH_TIMEOUT_S = 5.0

_cache: dict[str, Any] = {
    "at": 0.0,
    "manifest_url": "",
    "result": None,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class UpdateCheckResult:
    """Public envelope returned by :func:`check_for_updates`."""

    current_version: str = __version__
    latest_version: Optional[str] = None
    update_available: bool = False
    disabled: bool = False
    manifest_url: Optional[str] = None
    release_date: Optional[str] = None
    download_url: Optional[str] = None
    changelog_url: Optional[str] = None
    notes: Optional[str] = None
    error: Optional[str] = None
    fetched_at: Optional[float] = None
    cached: bool = False
    raw_manifest: Optional[dict[str, Any]] = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_for_updates(
    *,
    manifest_url: Optional[str] = None,
    current_version: str = __version__,
    force_refresh: bool = False,
    timeout: float = _FETCH_TIMEOUT_S,
) -> UpdateCheckResult:
    """Consult the manifest URL and decide whether a newer version exists.

    Always returns an :class:`UpdateCheckResult` — network failures, bad
    JSON, etc. are captured in ``error``.
    """
    if UPDATES_DISABLED:
        return UpdateCheckResult(
            current_version=current_version,
            disabled=True,
            error=None,
        )

    url = _resolve_manifest_url(manifest_url)
    if not url:
        return UpdateCheckResult(
            current_version=current_version,
            disabled=True,
            error="no_manifest_url",
            manifest_url=None,
        )

    cached = _cached_result(url)
    if cached is not None and not force_refresh:
        cached.cached = True
        return cached

    try:
        manifest, fetched_at = _fetch_manifest(url, timeout=timeout)
    except Exception as exc:
        log_event(
            logger, "updates.fetch_failed",
            level=logging.WARNING,
            manifest_url=url, error=f"{type(exc).__name__}: {exc}",
        )
        return UpdateCheckResult(
            current_version=current_version,
            manifest_url=url,
            error=f"fetch_failed: {type(exc).__name__}: {exc}",
        )

    try:
        normalised = _normalise_manifest(manifest)
    except Exception as exc:
        log_event(
            logger, "updates.parse_failed",
            level=logging.WARNING,
            manifest_url=url, error=f"{type(exc).__name__}: {exc}",
        )
        return UpdateCheckResult(
            current_version=current_version,
            manifest_url=url,
            raw_manifest=manifest,
            error=f"parse_failed: {exc}",
        )

    latest = normalised.get("latest_version")
    available = bool(latest) and _is_newer(latest, current_version)

    result = UpdateCheckResult(
        current_version=current_version,
        latest_version=latest,
        update_available=available,
        manifest_url=url,
        release_date=normalised.get("release_date"),
        download_url=normalised.get("download_url"),
        changelog_url=normalised.get("changelog_url"),
        notes=normalised.get("notes"),
        fetched_at=fetched_at,
        raw_manifest=manifest,
    )

    _cache["at"] = fetched_at
    _cache["manifest_url"] = url
    _cache["result"] = result

    log_event(
        logger, "updates.checked",
        current=current_version, latest=latest, update_available=available,
    )
    return result


def clear_cache() -> None:
    """Drop the in-process cache — used by tests and ``--force-refresh``."""
    _cache["at"] = 0.0
    _cache["manifest_url"] = ""
    _cache["result"] = None


# ---------------------------------------------------------------------------
# Semver-ish comparison
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def _parse_version(v: str) -> tuple[int, int, int, str]:
    """Very lenient semver parser. Returns ``(major, minor, patch, suffix)``.

    Unrecognised forms return ``(-1, -1, -1, raw)`` so sorting keeps them
    before proper versions.
    """
    if not isinstance(v, str):
        return (-1, -1, -1, str(v))
    match = _SEMVER_RE.match(v.strip())
    if not match:
        return (-1, -1, -1, v)
    major = int(match.group(1) or "0")
    minor = int(match.group(2) or "0")
    patch = int(match.group(3) or "0")
    return (major, minor, patch, v)


def _is_newer(candidate: str, baseline: str) -> bool:
    """``True`` iff ``candidate`` parses as strictly newer than ``baseline``."""
    c = _parse_version(candidate)
    b = _parse_version(baseline)
    return c[:3] > b[:3]


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def _normalise_manifest(manifest: Any) -> dict[str, Any]:
    """Coerce either manifest shape into the PCE-native dict.

    Raises ``ValueError`` when neither shape matches.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")

    # Shape A — PCE-native.
    if "latest_version" in manifest:
        return {
            "latest_version": str(manifest.get("latest_version") or "").strip() or None,
            "release_date": manifest.get("release_date"),
            "download_url": manifest.get("download_url"),
            "changelog_url": manifest.get("changelog_url"),
            "notes": manifest.get("notes"),
        }

    # Shape B — GitHub releases.
    if "tag_name" in manifest:
        tag = str(manifest.get("tag_name") or "").strip()
        # ``v0.2.0`` → ``0.2.0``. Keep the tag around for the UI though.
        latest = tag.lstrip("v") or None
        assets = manifest.get("assets") or []
        download_url = None
        if isinstance(assets, list) and assets:
            first = assets[0]
            if isinstance(first, dict):
                download_url = first.get("browser_download_url")
        return {
            "latest_version": latest,
            "release_date": manifest.get("published_at"),
            "download_url": download_url or manifest.get("html_url"),
            "changelog_url": manifest.get("html_url"),
            "notes": manifest.get("body"),
        }

    raise ValueError("unknown manifest shape")


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_manifest(url: str, *, timeout: float) -> tuple[dict[str, Any], float]:
    """Blocking HTTP GET + JSON parse. Raises on network / decode failure."""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"pce-core/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, socket.timeout) as exc:
        raise ConnectionError(str(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid manifest JSON: {exc}") from exc
    return data, time.time()


def _resolve_manifest_url(override: Optional[str]) -> Optional[str]:
    """Prefer ``override`` → preference override → env default."""
    if override is not None:
        return override.strip() or None
    # Pull ``preferences.update_manifest_url`` from persisted app state.
    try:
        from . import app_state
        prefs = app_state.load_state().get("preferences") or {}
        pref_url = prefs.get("update_manifest_url")
        if isinstance(pref_url, str) and pref_url.strip():
            return pref_url.strip()
    except Exception:
        logger.debug("app_state probe failed (non-fatal)", exc_info=True)
    return DEFAULT_MANIFEST_URL.strip() or None


def _cached_result(url: str) -> Optional[UpdateCheckResult]:
    if _cache["manifest_url"] != url:
        return None
    if not _cache["result"]:
        return None
    age = time.time() - float(_cache["at"] or 0.0)
    if age > _CACHE_TTL_S:
        return None
    return _cache["result"]  # type: ignore[return-value]


__all__ = [
    "DEFAULT_MANIFEST_URL",
    "UPDATES_DISABLED",
    "UpdateCheckResult",
    "check_for_updates",
    "clear_cache",
]
