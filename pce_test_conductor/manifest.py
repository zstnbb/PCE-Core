# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.manifest — load and validate target manifests.

Each ``targets/<id>.yaml`` declares one PCE target (a lane × product
pair) with everything the conductor needs to:

- discover the case set (`case_standard_module`)
- spawn pytest (`runner.args` + `marker_filter` + `timeout_s`)
- import the adapter (`adapter_class` via ``importlib`` at dispatch)
- locate the canary store (`canary_dir`)
- filter health beacons (`health_beacon_filter`)
- attribute ownership (`ownership.codeowner`)

The manifest is **metadata only**. The conductor uses ``importlib``
to dynamic-dispatch the adapter — Pro lanes plug in via the same
mechanism without violating ADR-010's import-direction rule
(OSS conductor never `import pce_pro.*`; it loads modules by string
name at run-time, returning ``infra_error: pro_module_unavailable`` if
the Pro package isn't installed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("pce.conductor.manifest")


# Default location of the targets directory (alongside this file).
# Overridable via ``load_manifest(targets_dir=...)`` for tests.
DEFAULT_TARGETS_DIR: Path = Path(__file__).parent / "targets"


@dataclass
class RunnerConfig:
    """Pytest invocation parameters."""

    type: str = "pytest"
    args: list[str] = field(default_factory=list)
    marker_filter: Optional[str] = None
    timeout_s: int = 600

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RunnerConfig":
        if not d:
            return cls()
        return cls(
            type=str(d.get("type", "pytest")),
            args=[str(a) for a in d.get("args", [])],
            marker_filter=d.get("marker_filter"),
            timeout_s=int(d.get("timeout_s", 600)),
        )


@dataclass
class HealthBeaconFilter:
    """Filter applied when rolling up health beacons for this target."""

    lane: str
    target: str

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Optional["HealthBeaconFilter"]:
        if not d:
            return None
        return cls(lane=str(d["lane"]), target=str(d["target"]))


@dataclass
class Ownership:
    codeowner: str = "@zstnbb"
    fallback_owner: str = "@zstnbb"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Ownership":
        if not d:
            return cls()
        return cls(
            codeowner=str(d.get("codeowner", "@zstnbb")),
            fallback_owner=str(d.get("fallback_owner", "@zstnbb")),
        )


@dataclass
class Target:
    """Canonical target manifest. Mirrors ADR-017 §5.2 example."""

    target_id: str
    display_name: str
    lane: str
    tier: str
    plane: list[str]
    primary_layer: str
    fallback_layers: list[str]
    adapter_class: Optional[str]
    runner: RunnerConfig
    case_standard_module: Optional[str]
    canary_dir: str
    health_beacon_filter: Optional[HealthBeaconFilter]
    ownership: Ownership
    adapter_provider: str = "oss"  # "oss" | "pro"

    # Where on disk this manifest came from (for diagnostics).
    source_path: Optional[Path] = None

    def to_summary(self) -> dict[str, Any]:
        """Project to the JSON-serialisable shape used by ``list_targets``."""
        return {
            "target_id": self.target_id,
            "display_name": self.display_name,
            "lane": self.lane,
            "tier": self.tier,
            "plane": list(self.plane),
            "primary_layer": self.primary_layer,
            "fallback_layers": list(self.fallback_layers),
            "adapter_class": self.adapter_class,
            "case_standard_module": self.case_standard_module,
            "canary_dir": self.canary_dir,
            "health_beacon_filter": (
                {"lane": self.health_beacon_filter.lane,
                 "target": self.health_beacon_filter.target}
                if self.health_beacon_filter else None
            ),
            "ownership": {
                "codeowner": self.ownership.codeowner,
                "fallback_owner": self.ownership.fallback_owner,
            },
            "adapter_provider": self.adapter_provider,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_manifest(target_id: str, *, targets_dir: Optional[Path] = None) -> Target:
    """Load a single target manifest by id.

    Raises ``FileNotFoundError`` if no ``<target_id>.yaml`` exists in
    ``targets_dir``.
    """
    base = targets_dir or DEFAULT_TARGETS_DIR
    path = base / f"{target_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"target manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _from_yaml_dict(data, source_path=path)


def list_target_ids(*, targets_dir: Optional[Path] = None) -> list[str]:
    """Return all ``<target_id>.yaml`` filenames (without extension), sorted."""
    base = targets_dir or DEFAULT_TARGETS_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


def load_all(*, targets_dir: Optional[Path] = None) -> list[Target]:
    """Load every manifest in ``targets_dir``. Bad manifests are skipped + logged."""
    out: list[Target] = []
    for tid in list_target_ids(targets_dir=targets_dir):
        try:
            out.append(load_manifest(tid, targets_dir=targets_dir))
        except Exception as exc:
            logger.warning(
                "failed to load manifest %r: %r — skipping",
                tid, exc,
            )
    return out


def _from_yaml_dict(data: dict[str, Any], *, source_path: Optional[Path] = None) -> Target:
    """Convert a parsed YAML dict into a ``Target``. Validates required keys."""
    required = ("target_id", "lane", "tier", "primary_layer", "canary_dir")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(
            f"manifest missing required keys: {missing} "
            f"(source={source_path})"
        )
    return Target(
        target_id=str(data["target_id"]),
        display_name=str(data.get("display_name", data["target_id"])),
        lane=str(data["lane"]),
        tier=str(data["tier"]),
        plane=[str(p) for p in data.get("plane", [])],
        primary_layer=str(data["primary_layer"]),
        fallback_layers=[str(p) for p in data.get("fallback_layers", [])],
        adapter_class=data.get("adapter_class"),
        runner=RunnerConfig.from_dict(data.get("runner")),
        case_standard_module=data.get("case_standard_module"),
        canary_dir=str(data["canary_dir"]),
        health_beacon_filter=HealthBeaconFilter.from_dict(data.get("health_beacon_filter")),
        ownership=Ownership.from_dict(data.get("ownership")),
        adapter_provider=str(data.get("adapter_provider", "oss")),
        source_path=source_path,
    )
