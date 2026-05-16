# SPDX-License-Identifier: Apache-2.0
"""Capture Supervisor — status state machine (ADR-021 §3.3).

Computes per-scenario health from a leg-level health map. The mapping
from ``legs_active`` to status / colour is::

    legs_active >= redundancy_target  →  redundant   (green)
    legs_active == 2                  →  minimal     (yellow)
    legs_active == 1                  →  impaired    (orange)
    legs_active == 0                  →  down        (red)

A leg is ``green`` iff it has logged a PASS in the last 24h **and** has
no fail logged after that PASS within the same window. When the
status feed has no data at all (e.g. on a freshly bootstrapped install
where no nightly probe has run yet) we report ``unknown`` per leg and
set scenario-level status to ``down`` with ``color=grey``.

This module is **stateless** — callers (the api layer) pass in a
``leg_signals`` mapping. The signal source is typically
``pce_core.health.health_beacons`` but the function only depends on the
shape of the inputs, which keeps it easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Optional

from .policy import Scenario, ScenarioRegistry


# ---------------------------------------------------------------------------
# State enums
# ---------------------------------------------------------------------------

class LegHealth(str, Enum):
    GREEN = "green"     # PASS within 24h
    YELLOW = "yellow"   # PASS within 24h but a more recent FAIL exists
    RED = "red"         # only FAIL within 24h
    UNKNOWN = "unknown" # no signal in the window


class ScenarioStatus(str, Enum):
    REDUNDANT = "redundant"
    MINIMAL = "minimal"
    IMPAIRED = "impaired"
    DOWN = "down"


_STATUS_COLOR: dict[ScenarioStatus, str] = {
    ScenarioStatus.REDUNDANT: "green",
    ScenarioStatus.MINIMAL: "yellow",
    ScenarioStatus.IMPAIRED: "orange",
    ScenarioStatus.DOWN: "red",
}


# ---------------------------------------------------------------------------
# Per-leg / per-scenario status records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LegStatus:
    source: str
    priority: int
    independent_basis: str
    health: LegHealth
    last_pass_ts: Optional[float]
    last_fail_ts: Optional[float]

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "priority": self.priority,
            "independent_basis": self.independent_basis,
            "health": self.health.value,
            "last_pass_ts": self.last_pass_ts,
            "last_fail_ts": self.last_fail_ts,
        }


@dataclass(frozen=True)
class _ScenarioReport:
    id: str
    label: str
    tier: str
    redundancy_target: int
    phase_b: bool
    legs_active: int
    legs_degraded: int
    status: ScenarioStatus
    color: str
    legs: tuple[LegStatus, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "redundancy_target": self.redundancy_target,
            "phase_b": self.phase_b,
            "legs_active": self.legs_active,
            "legs_degraded": self.legs_degraded,
            "status": self.status.value,
            "color": self.color,
            "legs": [leg.as_dict() for leg in self.legs],
        }


@dataclass(frozen=True)
class SupervisorSnapshot:
    """Top-level response payload."""

    schema_version: int
    computed_at: float
    window_hours: float
    scenarios: tuple[_ScenarioReport, ...]
    dedup_metrics: dict[str, int | float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "computed_at": self.computed_at,
            "window_hours": self.window_hours,
            "scenarios": [s.as_dict() for s in self.scenarios],
            "dedup_metrics": dict(self.dedup_metrics),
        }


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

def _classify_leg(
    last_pass_ts: Optional[float],
    last_fail_ts: Optional[float],
    *,
    now: float,
    window_s: float,
) -> LegHealth:
    cutoff = now - window_s
    pass_in_window = last_pass_ts is not None and last_pass_ts >= cutoff
    fail_in_window = last_fail_ts is not None and last_fail_ts >= cutoff
    if not pass_in_window and not fail_in_window:
        return LegHealth.UNKNOWN
    if pass_in_window and not fail_in_window:
        return LegHealth.GREEN
    if pass_in_window and fail_in_window:
        # Most-recent wins.
        if (last_fail_ts or 0) > (last_pass_ts or 0):
            return LegHealth.YELLOW    # was healthy, just regressed
        return LegHealth.GREEN
    return LegHealth.RED


def _scenario_status(
    legs_active: int,
    redundancy_target: int,
    legs_total: int,
) -> ScenarioStatus:
    if legs_active >= redundancy_target:
        return ScenarioStatus.REDUNDANT
    if legs_active >= 2:
        return ScenarioStatus.MINIMAL
    if legs_active == 1:
        return ScenarioStatus.IMPAIRED
    return ScenarioStatus.DOWN


def compute_status(
    registry: ScenarioRegistry,
    leg_signals: Mapping[tuple[str, str], Mapping[str, Optional[float]]],
    *,
    now: float,
    window_hours: float = 24.0,
    dedup_metrics: Optional[Mapping[str, int | float]] = None,
) -> SupervisorSnapshot:
    """Roll up per-leg signals into a top-level snapshot.

    Parameters
    ----------
    registry
        Loaded scenarios from :func:`load_scenarios`.
    leg_signals
        Map keyed by ``(scenario_id, source)`` whose values are
        ``{"last_pass_ts": float|None, "last_fail_ts": float|None}``.
        Missing keys → ``LegHealth.UNKNOWN``.
    now
        Reference time (so callers and tests can pin clocks).
    window_hours
        Health window in hours.
    dedup_metrics
        Snapshot from :meth:`CaptureDedup.metrics`. Echoed verbatim onto
        the response for transparency.
    """
    window_s = window_hours * 3600.0
    reports: list[_ScenarioReport] = []
    for scenario in registry.scenarios:
        leg_reports: list[LegStatus] = []
        legs_active = 0
        legs_degraded = 0
        for leg in scenario.legs:
            sig = leg_signals.get((scenario.id, leg.source), {}) or {}
            last_pass = sig.get("last_pass_ts")
            last_fail = sig.get("last_fail_ts")
            health = _classify_leg(
                last_pass_ts=last_pass,
                last_fail_ts=last_fail,
                now=now,
                window_s=window_s,
            )
            if health == LegHealth.GREEN:
                legs_active += 1
            elif health == LegHealth.YELLOW:
                legs_degraded += 1
            leg_reports.append(LegStatus(
                source=leg.source,
                priority=leg.priority,
                independent_basis=leg.independent_basis,
                health=health,
                last_pass_ts=last_pass,
                last_fail_ts=last_fail,
            ))
        # Sort legs deterministically by priority for the API output.
        leg_reports.sort(key=lambda ls: (ls.priority, ls.source))
        status = _scenario_status(
            legs_active=legs_active,
            redundancy_target=scenario.redundancy_target,
            legs_total=len(leg_reports),
        )
        # No-data short-circuit: if every leg is UNKNOWN, treat as grey
        # rather than red so a fresh install doesn't look broken.
        if all(ls.health == LegHealth.UNKNOWN for ls in leg_reports):
            color = "grey"
        else:
            color = _STATUS_COLOR[status]
        reports.append(_ScenarioReport(
            id=scenario.id,
            label=scenario.label,
            tier=scenario.tier,
            redundancy_target=scenario.redundancy_target,
            phase_b=scenario.phase_b,
            legs_active=legs_active,
            legs_degraded=legs_degraded,
            status=status,
            color=color,
            legs=tuple(leg_reports),
        ))
    return SupervisorSnapshot(
        schema_version=registry.schema_version,
        computed_at=now,
        window_hours=window_hours,
        scenarios=tuple(reports),
        dedup_metrics=dict(dedup_metrics or {}),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def status_color(status: ScenarioStatus) -> str:
    """Stable mapping for renderers."""
    return _STATUS_COLOR[status]


def degraded_scenarios(
    snapshot: SupervisorSnapshot,
    *,
    suppress_phase_b: bool = True,
) -> list[_ScenarioReport]:
    """Return scenarios whose ``legs_active < redundancy_target``.

    Used by the redundancy-degraded auto-issue tool. Phase-B markers are
    suppressed by default since those scenarios are *expected* to be
    impaired during the build-out window.
    """
    out: list[_ScenarioReport] = []
    for r in snapshot.scenarios:
        if r.legs_active >= r.redundancy_target:
            continue
        if suppress_phase_b and r.phase_b:
            continue
        out.append(r)
    return out
