# SPDX-License-Identifier: Apache-2.0
"""Capture Supervisor — HTTP surface (ADR-021 §3.5).

Three endpoints:

- ``GET  /api/v1/supervisor/status``                 — full snapshot
- ``GET  /api/v1/supervisor/scenario/{scenario_id}`` — single scenario
- ``POST /api/v1/supervisor/legs/register``          — Pro registration
                                                       (ADR-021 §3.2)

The router is constructed lazily so :class:`CaptureDedup` and the
scenario registry can be wired up at FastAPI lifespan time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .dedup import CaptureDedup
from .policy import ScenarioRegistry, load_scenarios
from .status import (
    SupervisorSnapshot,
    compute_status,
    degraded_scenarios,
)

logger = logging.getLogger("pce.capture_supervisor.api")


def _merge_signal(
    out: dict[tuple[str, str], dict[str, Optional[float]]],
    key: tuple[str, str],
    status: str,
    ts: float,
) -> None:
    """Update last_pass_ts / last_fail_ts in-place, preferring the most
    recent timestamp (so multiple query results don't clobber freshness).
    """
    if key not in out:
        out[key] = {"last_pass_ts": None, "last_fail_ts": None}
    field = "last_pass_ts" if status == "pass" else "last_fail_ts"
    prev = out[key].get(field)
    if prev is None or ts > prev:
        out[key][field] = ts


# ---------------------------------------------------------------------------
# Pro leg registration request body
# ---------------------------------------------------------------------------

class LegRegistrationIn(BaseModel):
    """Body of POST /api/v1/supervisor/legs/register (ADR-021 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., min_length=1,
                        description="CaptureSource literal (e.g. 'L0_kernel').")
    scenario_ids: list[str] = Field(...,
                        description="Scenarios this leg covers.")
    independent_basis: str = Field(..., min_length=1,
                        description="Uniqueness key (must not collide with existing legs in the scenario).")
    agent_pid: Optional[int] = Field(default=None, ge=0)
    agent_version: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=512)


class LegRegistrationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool
    rejected_scenarios: list[str] = []
    rejection_reason: Optional[str] = None
    registered_at: float


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------

@dataclass
class CaptureSupervisorState:
    """Wire-up holder; one instance per FastAPI app."""

    registry: ScenarioRegistry
    dedup: CaptureDedup
    leg_signal_provider: "LegSignalProvider"
    pro_legs: dict[tuple[str, str], dict] = field(default_factory=dict)


class LegSignalProvider:
    """Abstract source of ``(scenario_id, source) → {last_pass_ts, last_fail_ts}``.

    Default implementation reads from ``pce_core.health.health_beacons``
    via :func:`pce_core.health.get_beacon`. Tests override with a
    simple mapping.
    """

    def get_signals(
        self,
        registry: ScenarioRegistry,
        *,
        now: float,
        window_hours: float,
    ) -> Mapping[tuple[str, str], Mapping[str, Optional[float]]]:
        raise NotImplementedError


class StaticSignalProvider(LegSignalProvider):
    """Signals supplied as a frozen mapping; useful in tests."""

    def __init__(
        self,
        signals: Mapping[tuple[str, str], Mapping[str, Optional[float]]],
    ) -> None:
        self._signals = dict(signals)

    def get_signals(
        self,
        registry: ScenarioRegistry,
        *,
        now: float,
        window_hours: float,
    ) -> Mapping[tuple[str, str], Mapping[str, Optional[float]]]:
        return self._signals


class HealthBeaconSignalProvider(LegSignalProvider):
    """Pulls per-(scenario, leg) signals from ``health_beacons``.

    Beacon target convention: ``target = "<scenario_id>__<leg_source>"``
    (double underscore separator), ``lane`` is the leg source's family
    (``browser`` / ``desktop`` / ``cli`` / ``mcp`` — for our purposes
    we just bucket by leg-prefix). A beacon row with status='pass'
    updates ``last_pass_ts``; status='fail' updates ``last_fail_ts``.

    The ``health_beacons`` schema (P5.C.1) stores
    ``(lane, layer, target, case_id, status, ts, elapsed_ms, meta_json,
    selector_hits_json)``. We do one bulk query keyed on the target
    convention and let the loader's window window_hours filter.
    """

    def __init__(self, *, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path

    def get_signals(
        self,
        registry: ScenarioRegistry,
        *,
        now: float,
        window_hours: float,
    ) -> Mapping[tuple[str, str], Mapping[str, Optional[float]]]:
        try:
            from pce_core.db import get_connection
        except ImportError:  # pragma: no cover — defensive
            return {}
        # Build BOTH lookup tables:
        #   - strict: target = "<scenario_id>__<source>"
        #   - alias: (target, lane, layer) → leg, when scenarios.yaml leg
        #     declares `beacon_target`/`beacon_lane`/`beacon_layer`. This
        #     lets capture-side modules emit beacons with natural simple
        #     target names (e.g. ``claude_code``, ``codex_cli``) without
        #     knowing their scenario_id.
        target_to_key: dict[str, tuple[str, str]] = {}
        alias_to_key: dict[tuple[str, Optional[str], Optional[str]], tuple[str, str]] = {}
        for scenario in registry.scenarios:
            for leg in scenario.legs:
                target_to_key[f"{scenario.id}__{leg.source}"] = (scenario.id, leg.source)
                if leg.beacon_target:
                    alias_to_key[(leg.beacon_target, leg.beacon_lane, leg.beacon_layer)] = (
                        scenario.id, leg.source,
                    )
        if not target_to_key:
            return {}
        cutoff = now - window_hours * 3600.0
        out: dict[tuple[str, str], dict[str, Optional[float]]] = {
            key: {"last_pass_ts": None, "last_fail_ts": None}
            for key in target_to_key.values()
        }
        try:
            conn = get_connection(self._db_path)
        except Exception:  # pragma: no cover — defensive
            return out
        try:
            # --- Strict canonical query ---
            placeholders = ",".join("?" * len(target_to_key))
            sql = (
                "SELECT target, status, MAX(ts) as last_ts "
                f"FROM health_beacons "
                f"WHERE target IN ({placeholders}) AND ts >= ? "
                "AND status IN ('pass', 'fail') "
                "GROUP BY target, status"
            )
            params = list(target_to_key.keys()) + [cutoff]
            rows = conn.execute(sql, params).fetchall()
            for target, status, last_ts in rows:
                key = target_to_key.get(target)
                if key is None or last_ts is None:
                    continue
                _merge_signal(out, key, status, float(last_ts))

            # --- Alias query: simple target + lane + layer ---
            if alias_to_key:
                alias_targets = sorted({t for (t, _l, _ly) in alias_to_key})
                ph = ",".join("?" * len(alias_targets))
                sql2 = (
                    "SELECT target, lane, layer, status, MAX(ts) as last_ts "
                    f"FROM health_beacons "
                    f"WHERE target IN ({ph}) AND ts >= ? "
                    "AND status IN ('pass', 'fail') "
                    "GROUP BY target, lane, layer, status"
                )
                rows2 = conn.execute(sql2, list(alias_targets) + [cutoff]).fetchall()
                for atarget, alane, alayer, status, last_ts in rows2:
                    # Try most-specific (target, lane, layer) match first,
                    # then (target, lane, None), (target, None, layer),
                    # (target, None, None).
                    key = (
                        alias_to_key.get((atarget, alane, alayer))
                        or alias_to_key.get((atarget, alane, None))
                        or alias_to_key.get((atarget, None, alayer))
                        or alias_to_key.get((atarget, None, None))
                    )
                    if key is None or last_ts is None:
                        continue
                    _merge_signal(out, key, status, float(last_ts))
        except Exception:  # pragma: no cover — defensive
            logger.exception("HealthBeaconSignalProvider query failed (degrading to UNKNOWN)")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return out


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------

def build_router(state: CaptureSupervisorState) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supervisor", tags=["supervisor"])

    @router.get("/status")
    def get_status() -> dict:
        snapshot = _snapshot_now(state)
        return snapshot.as_dict()

    @router.get("/scenario/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict:
        if scenario_id not in state.registry:
            raise HTTPException(status_code=404, detail="scenario not found")
        snapshot = _snapshot_now(state)
        for r in snapshot.scenarios:
            if r.id == scenario_id:
                return r.as_dict()
        # Should be unreachable given the registry membership check.
        raise HTTPException(status_code=500, detail="snapshot inconsistency")

    @router.get("/degraded")
    def get_degraded(suppress_phase_b: bool = True) -> dict:
        snapshot = _snapshot_now(state)
        rows = degraded_scenarios(snapshot, suppress_phase_b=suppress_phase_b)
        return {
            "computed_at": snapshot.computed_at,
            "suppress_phase_b": suppress_phase_b,
            "alerts": [
                {
                    "scenario_id": r.id,
                    "label": r.label,
                    "tier": r.tier,
                    "phase_b": r.phase_b,
                    "redundancy_target": r.redundancy_target,
                    "legs_active": r.legs_active,
                    "status": r.status.value,
                    "color": r.color,
                    "legs": [leg.as_dict() for leg in r.legs],
                }
                for r in rows
            ],
        }

    @router.post("/legs/register", response_model=LegRegistrationOut)
    def register_leg(payload: LegRegistrationIn) -> LegRegistrationOut:
        rejected: list[str] = []
        reason: Optional[str] = None
        for sid in payload.scenario_ids:
            scenario = state.registry.get(sid)
            if scenario is None:
                rejected.append(sid)
                reason = f"unknown scenario_id: {sid!r}"
                continue
            # Independence: refuse if the basis collides with an existing leg
            # in the scenario.
            if any(leg.independent_basis == payload.independent_basis
                   for leg in scenario.legs):
                rejected.append(sid)
                reason = (
                    f"independent_basis {payload.independent_basis!r} already "
                    f"used by an existing leg in scenario {sid!r}"
                )
                continue
            state.pro_legs[(sid, payload.source)] = {
                "source": payload.source,
                "independent_basis": payload.independent_basis,
                "agent_pid": payload.agent_pid,
                "agent_version": payload.agent_version,
                "notes": payload.notes,
                "registered_at": time.time(),
            }
        return LegRegistrationOut(
            accepted=not rejected,
            rejected_scenarios=rejected,
            rejection_reason=reason,
            registered_at=time.time(),
        )

    @router.get("/legs/registered")
    def list_pro_legs() -> dict:
        return {
            "count": len(state.pro_legs),
            "legs": [
                {"scenario_id": sid, **info}
                for (sid, _src), info in state.pro_legs.items()
            ],
        }

    return router


# ---------------------------------------------------------------------------
# Helper: build snapshot using current state + clock
# ---------------------------------------------------------------------------

def _snapshot_now(state: CaptureSupervisorState) -> SupervisorSnapshot:
    now = time.time()
    signals = state.leg_signal_provider.get_signals(
        state.registry, now=now, window_hours=24.0,
    )
    return compute_status(
        state.registry,
        signals,
        now=now,
        window_hours=24.0,
        dedup_metrics=state.dedup.metrics(),
    )


# ---------------------------------------------------------------------------
# Convenience: build state with defaults
# ---------------------------------------------------------------------------

def build_default_state(
    *,
    scenarios_path: Optional[Path] = None,
    signal_provider: Optional[LegSignalProvider] = None,
    dedup_window_s: float = 30.0,
    dedup_max_entries: int = 10000,
) -> CaptureSupervisorState:
    """One-call setup for FastAPI lifespan registration."""
    registry = load_scenarios(scenarios_path)
    dedup = CaptureDedup(window_s=dedup_window_s, max_entries=dedup_max_entries)
    provider = signal_provider or HealthBeaconSignalProvider()
    return CaptureSupervisorState(
        registry=registry,
        dedup=dedup,
        leg_signal_provider=provider,
    )
