# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Per-app proxy bypass list (P5.A-7, UCS §3.1).

Some Tier-1 applications ship with certificate pinning or enterprise MDM
that refuses even a correctly-installed PCE CA. Routing such apps through
the proxy leaves the user stranded: traffic drops entirely instead of
falling back to the app's normal direct-to-upstream connection.

The bypass list lets the user opt an individual app OUT of proxying
while keeping the rest of their system routed through PCE. Bypassed
apps are launched with a *clean* parent environment (no ``HTTP_PROXY``
/ ``HTTPS_PROXY`` / ``NODE_EXTRA_CA_CERTS``) so their HTTPS goes direct
as if PCE weren't installed.

Storage: ``{DATA_DIR}/app_bypass.json`` — a single JSON dict::

    {
        "bypassed": ["chatgpt-desktop", "claude-desktop"],
        "updated_at": 1714000000.0
    }

Policies (mirrored from ``app_state.py``):

- **Forgiving reads** — missing or corrupt file → empty bypass list;
  logged at WARNING but never raised.
- **Atomic writes** — ``tempfile.mkstemp`` + ``os.replace`` so a crash
  mid-write can't leave the user with a half-file that rejects parsing.
- **Test-friendly** — every function accepts an optional ``path``
  argument so pytest fixtures can isolate state.
- **App-name canonicalisation** — we store the ``ElectronApp.name`` slug
  (lowercase, hyphenated), not the display name, so renames of the
  display string don't invalidate user preferences.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

logger = logging.getLogger("pce.app_bypass")

_FILENAME = "app_bypass.json"


def _bypass_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(DATA_DIR) / _FILENAME


def _empty_state() -> dict:
    return {"bypassed": [], "updated_at": 0.0}


def load_bypass(path: Optional[Path] = None) -> dict:
    """Return the persisted bypass state; degrades to empty on any error."""
    sp = _bypass_path(path)
    if not sp.exists():
        return _empty_state()
    try:
        with sp.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "app_bypass.json unreadable: %s – returning empty state", exc,
        )
        return _empty_state()

    if not isinstance(raw, dict):
        logger.warning("app_bypass.json is not a dict, ignoring")
        return _empty_state()

    bypassed_raw = raw.get("bypassed", [])
    if not isinstance(bypassed_raw, list):
        logger.warning("app_bypass.bypassed is not a list, ignoring")
        bypassed_raw = []

    # Deduplicate + drop non-string entries defensively.
    seen: set[str] = set()
    bypassed: list[str] = []
    for item in bypassed_raw:
        if not isinstance(item, str):
            continue
        slug = item.strip().lower()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        bypassed.append(slug)

    updated_at = raw.get("updated_at") or 0.0
    try:
        updated_at = float(updated_at)
    except (TypeError, ValueError):
        updated_at = 0.0

    return {"bypassed": bypassed, "updated_at": updated_at}


def save_bypass(
    bypassed: list[str],
    *,
    path: Optional[Path] = None,
) -> Path:
    """Atomically persist ``bypassed`` to disk. Returns the file path.

    App names are normalised (stripped + lowercased + deduplicated) so
    the on-disk representation is stable regardless of caller hygiene.
    """
    sp = _bypass_path(path)
    sp.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    normalised: list[str] = []
    for name in bypassed or []:
        if not isinstance(name, str):
            continue
        slug = name.strip().lower()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        normalised.append(slug)

    payload = {
        "bypassed": normalised,
        "updated_at": time.time(),
    }

    fd, tmp_name = tempfile.mkstemp(
        prefix="app_bypass-", suffix=".tmp.json", dir=str(sp.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, sp)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return sp


def is_app_bypassed(name: str, *, path: Optional[Path] = None) -> bool:
    """Quick launcher-side check: should this app skip the PCE proxy?"""
    if not name:
        return False
    slug = name.strip().lower()
    state = load_bypass(path)
    return slug in state["bypassed"]


def set_app_bypassed(
    name: str,
    bypassed: bool,
    *,
    path: Optional[Path] = None,
) -> dict:
    """Toggle a single app. Returns the resulting full state."""
    if not name or not isinstance(name, str):
        raise ValueError("set_app_bypassed: name must be a non-empty string")
    slug = name.strip().lower()
    current = load_bypass(path)
    current_set = set(current["bypassed"])
    if bypassed:
        current_set.add(slug)
    else:
        current_set.discard(slug)
    # Preserve original insertion-ish order for stable display.
    new_list = [s for s in current["bypassed"] if s in current_set]
    for s in sorted(current_set):
        if s not in new_list:
            new_list.append(s)
    save_bypass(new_list, path=path)
    return load_bypass(path)
