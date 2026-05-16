# SPDX-License-Identifier: Apache-2.0
"""Capture Supervisor — policy / scenarios loader (ADR-021 §3.2).

Loads ``scenarios.yaml`` (the 13 P0 scenarios per SCOPE-LOCK) into typed
records and enforces the **independence invariant**: within a single
scenario, every leg's ``independent_basis`` must be unique. Violations
mean the scenario can never reach ``redundancy_target`` legitimately
(two legs sharing a basis count as 1.5, not 2 — REDUNDANCY-AUDIT §1.3).

The loader also enforces:

- ``schema_version == 1`` — anything else is rejected loudly so we
  never silently mis-parse a future shape.
- Every ``source`` value is a member of ``CaptureSource`` (the
  CaptureEvent v2 enum); typos turn into hard load failures.
- ``redundancy_target`` defaults to ``3`` if omitted.
- ``priority`` defaults to ``len(legs)`` (last) when missing.

This module is **pure** — no I/O beyond ``open()``, no global state.
The :class:`ScenarioRegistry` instance returned can be cached at the
FastAPI app layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

import yaml

from ..capture_event import CaptureSource as _CaptureSourceLiteral

# `CaptureSource` is a `Literal[...]` alias; pull the legal values out so
# we can validate scenarios.yaml against it at load time.
_CAPTURE_SOURCES: frozenset[str] = frozenset(_CaptureSourceLiteral.__args__)  # type: ignore[attr-defined]

_DEFAULT_SCHEMA_VERSION = 1
_DEFAULT_REDUNDANCY_TARGET = 3


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Leg:
    """A single capture leg inside one scenario."""

    source: str                       # CaptureSource literal value
    priority: int                     # smaller = preferred
    independent_basis: str            # uniqueness key inside the scenario
    # Optional simple beacon target name (e.g. 'claude_code'). When set,
    # supervisor accepts health_beacons rows where `target` equals this
    # value (in addition to the canonical '<scenario_id>__<source>'
    # convention). Lets capture-side modules write natural target names
    # without knowing their scenario_id; supervisor does the reverse
    # lookup via this field.
    beacon_target: Optional[str] = None
    # Optional (lane, layer) hint used together with beacon_target for
    # the alias lookup. Defaults derived from `source` if absent (best-
    # effort split on '_').
    beacon_lane: Optional[str] = None
    beacon_layer: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "priority": self.priority,
            "independent_basis": self.independent_basis,
            "beacon_target": self.beacon_target,
            "beacon_lane": self.beacon_lane,
            "beacon_layer": self.beacon_layer,
        }


@dataclass(frozen=True)
class Scenario:
    """One of the 13 P0 scenarios, fully resolved."""

    id: str
    label: str
    tier: str
    redundancy_target: int
    legs: tuple[Leg, ...]
    phase_b: bool = False

    def leg_by_source(self, source: str) -> Optional[Leg]:
        for leg in self.legs:
            if leg.source == source:
                return leg
        return None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "redundancy_target": self.redundancy_target,
            "phase_b": self.phase_b,
            "legs": [leg.as_dict() for leg in self.legs],
        }


@dataclass
class ScenarioRegistry:
    """Indexed view of the loaded scenarios."""

    schema_version: int
    scenarios: tuple[Scenario, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self._by_id: dict[str, Scenario] = {s.id: s for s in self.scenarios}

    def get(self, scenario_id: str) -> Optional[Scenario]:
        return self._by_id.get(scenario_id)

    def __contains__(self, scenario_id: str) -> bool:
        return scenario_id in self._by_id

    def __iter__(self):
        return iter(self.scenarios)

    def __len__(self) -> int:
        return len(self.scenarios)

    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.scenarios)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class ScenariosError(ValueError):
    """Raised when scenarios.yaml is malformed."""


def load_scenarios(
    path: str | Path | None = None,
    *,
    raw: Optional[Mapping] = None,
) -> ScenarioRegistry:
    """Read, validate, and freeze ``scenarios.yaml``.

    Parameters
    ----------
    path
        File path to load. Defaults to the bundled ``scenarios.yaml`` next
        to this module.
    raw
        Pre-parsed dict; bypasses the file read (handy in tests).
    """
    if raw is None:
        if path is None:
            path = Path(__file__).resolve().parent / "scenarios.yaml"
        path = Path(path)
        if not path.exists():
            raise ScenariosError(f"scenarios.yaml not found at {path}")
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, Mapping):
        raise ScenariosError(
            f"scenarios.yaml top level must be a mapping, got {type(raw).__name__}"
        )

    schema_version = raw.get("schema_version", _DEFAULT_SCHEMA_VERSION)
    if schema_version != _DEFAULT_SCHEMA_VERSION:
        raise ScenariosError(
            f"scenarios.yaml schema_version={schema_version!r} is not supported "
            f"(expected {_DEFAULT_SCHEMA_VERSION})"
        )

    scenarios_raw = raw.get("scenarios", [])
    if not isinstance(scenarios_raw, list):
        raise ScenariosError("scenarios.yaml 'scenarios' must be a list")

    scenarios = tuple(_parse_scenario(s) for s in scenarios_raw)

    # Cross-scenario uniqueness: each scenario id must be unique.
    seen_ids: set[str] = set()
    for sc in scenarios:
        if sc.id in seen_ids:
            raise ScenariosError(f"duplicate scenario id: {sc.id!r}")
        seen_ids.add(sc.id)

    return ScenarioRegistry(schema_version=schema_version, scenarios=scenarios)


def _parse_scenario(node: Mapping) -> Scenario:
    if not isinstance(node, Mapping):
        raise ScenariosError(
            f"scenario entry must be a mapping, got {type(node).__name__}"
        )
    sid = _require_str(node, "id")
    label = _require_str(node, "label")
    tier = _require_str(node, "tier")
    target = int(node.get("redundancy_target", _DEFAULT_REDUNDANCY_TARGET))
    if target < 1:
        raise ScenariosError(f"scenario {sid!r}: redundancy_target must be ≥ 1")
    phase_b = bool(node.get("phase_b", False))

    legs_raw = node.get("legs", [])
    if not isinstance(legs_raw, list) or not legs_raw:
        raise ScenariosError(f"scenario {sid!r}: 'legs' must be a non-empty list")
    legs = tuple(_parse_leg(sid, idx, leg) for idx, leg in enumerate(legs_raw))

    # Enforce independent_basis uniqueness within the scenario.
    bases = [leg.independent_basis for leg in legs]
    dupes = _duplicates(bases)
    if dupes:
        raise ScenariosError(
            f"scenario {sid!r}: duplicate independent_basis: {sorted(dupes)} "
            f"(REDUNDANCY-AUDIT §1.3 — same basis = 1.5 legs, not 2)"
        )

    return Scenario(
        id=sid, label=label, tier=tier,
        redundancy_target=target, legs=legs, phase_b=phase_b,
    )


def _parse_leg(scenario_id: str, idx: int, node: Mapping) -> Leg:
    if not isinstance(node, Mapping):
        raise ScenariosError(
            f"scenario {scenario_id!r} leg #{idx}: must be a mapping"
        )
    source = _require_str(node, "source")
    if source not in _CAPTURE_SOURCES:
        raise ScenariosError(
            f"scenario {scenario_id!r} leg #{idx}: unknown source {source!r} "
            f"(must be one of CaptureSource enum)"
        )
    priority = int(node.get("priority", idx + 1))
    if priority < 1:
        raise ScenariosError(
            f"scenario {scenario_id!r} leg #{idx}: priority must be ≥ 1"
        )
    basis = _require_str(node, "independent_basis")
    beacon_target = node.get("beacon_target")
    beacon_lane = node.get("beacon_lane")
    beacon_layer = node.get("beacon_layer")
    return Leg(
        source=source, priority=priority, independent_basis=basis,
        beacon_target=beacon_target if isinstance(beacon_target, str) else None,
        beacon_lane=beacon_lane if isinstance(beacon_lane, str) else None,
        beacon_layer=beacon_layer if isinstance(beacon_layer, str) else None,
    )


def _require_str(node: Mapping, key: str) -> str:
    val = node.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ScenariosError(f"missing or empty field {key!r}")
    return val.strip()


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for v in values:
        if v in seen:
            dupes.add(v)
        else:
            seen.add(v)
    return dupes
