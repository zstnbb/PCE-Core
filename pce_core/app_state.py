# SPDX-License-Identifier: Apache-2.0
"""PCE Core – User-facing app state (onboarding, preferences).

Backs a small, human-readable JSON file at
``{DATA_DIR}/state.json`` that the desktop shell, dashboard and CLI all
share. Keep this module intentionally tiny: anything bigger than a flat
key/value bag belongs in SQLite, not here.

State fields consumed by P3:

- ``onboarding_completed``       bool — true after the user finishes the
                                  wizard at least once.
- ``onboarding_version``         int  — which wizard revision they last
                                  completed (so we can re-prompt on major
                                  upgrades).
- ``onboarding_steps``           dict — per-step completion snapshot
                                  ``{step_id: "done"|"skipped"|"failed"}``.
- ``preferences``                dict — user toggles (retention days,
                                  redact tokens, phoenix auto-start, …).
- ``first_launch_at``            float — epoch seconds of very first boot.
- ``last_launch_at``             float — epoch seconds of most recent boot.

Policies:

- Reads never raise on missing / corrupt files — they degrade to the
  defaults so a botched edit can't brick the app.
- Writes are atomic: write to ``state.json.tmp`` then rename.
- Functions accept an optional ``path`` parameter for tests.
- All time values are UTC epoch seconds.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR

logger = logging.getLogger("pce.app_state")

# Bumped when we want to force returning users back through the wizard
# (e.g. a new step was added or an old one materially changed).
CURRENT_ONBOARDING_VERSION = 1

# Canonical step IDs the wizard walks through. The HTML keeps the same
# IDs; keep them in sync if you add a step.
ONBOARDING_STEPS: tuple[str, ...] = (
    "welcome",
    "certificate",
    "extension",
    "proxy",
    "privacy",
    "done",
)

_DEFAULT_PREFERENCES: dict[str, Any] = {
    "retention_days": 0,            # 0 = keep forever (honours env too)
    "redact_tokens": True,
    "auto_start_core": True,
    "auto_open_dashboard": True,
    "phoenix_auto_start": False,
    "check_for_updates": True,
    "send_anonymous_telemetry": False,
}


def default_state() -> dict[str, Any]:
    now = time.time()
    return {
        "onboarding_completed": False,
        "onboarding_version": 0,
        "onboarding_steps": {},
        "preferences": dict(_DEFAULT_PREFERENCES),
        "first_launch_at": now,
        "last_launch_at": now,
    }


def _state_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(DATA_DIR) / "state.json"


def load_state(path: Optional[Path] = None) -> dict[str, Any]:
    """Load state from disk, merging with :func:`default_state` for forward compat.

    Missing file → defaults.
    Corrupt JSON → defaults + a WARNING log.
    Valid JSON missing some keys → defaults merged in so new keys work
    seamlessly on upgrade.
    """
    sp = _state_path(path)
    data: dict[str, Any] = default_state()
    try:
        if sp.exists():
            with sp.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                _deep_merge(data, raw)
            else:
                logger.warning("app_state.json is not a dict, ignoring")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("app_state.json unreadable: %s – falling back to defaults", exc)
    # Preferences merge so newly-added defaults appear automatically.
    prefs = data.setdefault("preferences", {})
    for k, v in _DEFAULT_PREFERENCES.items():
        prefs.setdefault(k, v)
    return data


def save_state(state: dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomically persist ``state`` back to disk. Returns the file path."""
    sp = _state_path(path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    # tempfile in the same directory so os.replace is atomic across Windows.
    fd, tmp_name = tempfile.mkstemp(
        prefix="state-", suffix=".tmp.json", dir=str(sp.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, sp)
    except Exception:
        # Clean up tmp file on failure so we don't leak junk.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return sp


def update_state(
    patch: dict[str, Any], path: Optional[Path] = None,
) -> dict[str, Any]:
    """Shallow-merge ``patch`` into the current state (nested dicts merge too).

    Returns the post-merge state dict.
    """
    current = load_state(path)
    _deep_merge(current, patch)
    save_state(current, path)
    return current


def touch_last_launch(path: Optional[Path] = None) -> dict[str, Any]:
    """Record the current epoch as the most recent boot time."""
    return update_state({"last_launch_at": time.time()}, path=path)


# ---------------------------------------------------------------------------
# Onboarding helpers
# ---------------------------------------------------------------------------

def needs_onboarding(state: Optional[dict[str, Any]] = None,
                     path: Optional[Path] = None) -> bool:
    """Return True when the user should see the wizard on next launch.

    Triggered when:
    - They've never completed onboarding.
    - They completed an older wizard version and we bumped
      :data:`CURRENT_ONBOARDING_VERSION`.
    """
    s = state if state is not None else load_state(path)
    if not s.get("onboarding_completed"):
        return True
    return int(s.get("onboarding_version", 0)) < CURRENT_ONBOARDING_VERSION


def mark_step(
    step_id: str,
    status: str = "done",
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Record completion state for one wizard step. Validates the step id."""
    if step_id not in ONBOARDING_STEPS:
        raise ValueError(f"unknown onboarding step: {step_id!r}")
    if status not in ("done", "skipped", "failed", "pending"):
        raise ValueError(f"bad status: {status!r}")
    current = load_state(path)
    steps = dict(current.get("onboarding_steps") or {})
    steps[step_id] = {"status": status, "at": time.time()}
    current["onboarding_steps"] = steps
    save_state(current, path)
    return current


def complete_onboarding(path: Optional[Path] = None) -> dict[str, Any]:
    """Mark the wizard as fully completed at the current version."""
    return update_state({
        "onboarding_completed": True,
        "onboarding_version": CURRENT_ONBOARDING_VERSION,
        "onboarding_completed_at": time.time(),
    }, path=path)


def reset_onboarding(path: Optional[Path] = None) -> dict[str, Any]:
    """Dev helper — clear the wizard's progress so it re-appears on next launch."""
    return update_state({
        "onboarding_completed": False,
        "onboarding_version": 0,
        "onboarding_steps": {},
    }, path=path)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``src`` into ``dst`` in place. Returns ``dst``."""
    for key, value in src.items():
        if (
            key in dst
            and isinstance(dst[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


__all__ = [
    "CURRENT_ONBOARDING_VERSION",
    "ONBOARDING_STEPS",
    "complete_onboarding",
    "default_state",
    "load_state",
    "mark_step",
    "needs_onboarding",
    "reset_onboarding",
    "save_state",
    "touch_last_launch",
    "update_state",
]
