# SPDX-License-Identifier: Apache-2.0
"""Wave 3 — Capture Supervisor v1 unit tests (ADR-021).

Coverage matrix per `Docs/stability/redundancy-sprint/03-wave3-supervisor-v1.md` §5:

  - dedup    (12)
  - policy    (8)
  - status    (5)
  - api       (6)
  Total      31
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Mapping, Optional

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pce_core.capture_supervisor.api import (
    CaptureSupervisorState,
    LegSignalProvider,
    StaticSignalProvider,
    build_default_state,
    build_router,
)
from pce_core.capture_supervisor.dedup import (
    CaptureDedup,
    ClaimResult,
    compute_fingerprint,
)
from pce_core.capture_supervisor.policy import (
    Leg,
    Scenario,
    ScenarioRegistry,
    ScenariosError,
    load_scenarios,
)
from pce_core.capture_supervisor.status import (
    LegHealth,
    ScenarioStatus,
    compute_status,
    degraded_scenarios,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simple_registry() -> ScenarioRegistry:
    """Build a small in-memory registry for fast tests."""
    raw = {
        "schema_version": 1,
        "scenarios": [
            {
                "id": "f_test_a",
                "label": "Test A",
                "tier": "S0",
                "redundancy_target": 3,
                "legs": [
                    {"source": "L1_mitm", "priority": 1,
                     "independent_basis": "net_stack"},
                    {"source": "L3a_browser_ext", "priority": 2,
                     "independent_basis": "page_dom"},
                    {"source": "L4a_clipboard", "priority": 3,
                     "independent_basis": "user_action"},
                ],
            },
            {
                "id": "f_test_b",
                "label": "Test B (phase B)",
                "tier": "D2",
                "redundancy_target": 3,
                "phase_b": True,
                "legs": [
                    {"source": "L1_mitm", "priority": 1,
                     "independent_basis": "net_stack"},
                    {"source": "L4b_accessibility", "priority": 2,
                     "independent_basis": "ui_automation_tree"},
                ],
            },
        ],
    }
    return load_scenarios(raw=raw)


# ===========================================================================
# DEDUP — 12 tests
# ===========================================================================

def test_dedup_first_claim_is_primary():
    d = CaptureDedup(window_s=30)
    r = d.claim("p-1", "fp-1", "L1_mitm")
    assert r.is_primary is True
    assert r.primary_source == "L1_mitm"


def test_dedup_second_claim_is_duplicate():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm")
    r = d.claim("p-1", "fp-1", "L3a_browser_ext")
    assert r.is_primary is False
    assert r.primary_source == "L1_mitm"


def test_dedup_different_pair_not_dedupe():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm")
    r = d.claim("p-2", "fp-1", "L1_mitm")
    assert r.is_primary is True


def test_dedup_different_fingerprint_not_dedupe():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm")
    r = d.claim("p-1", "fp-2", "L1_mitm")
    assert r.is_primary is True


def test_dedup_window_expiry_30s():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm", now=1000.0)
    r = d.claim("p-1", "fp-1", "L1_mitm", now=1031.0)
    assert r.is_primary is True


def test_dedup_lru_cap():
    d = CaptureDedup(window_s=300, max_entries=3)
    d.claim("p-1", "fp-1", "L1_mitm")
    d.claim("p-2", "fp-1", "L1_mitm")
    d.claim("p-3", "fp-1", "L1_mitm")
    d.claim("p-4", "fp-1", "L1_mitm")  # evicts p-1
    # p-1 should now be considered fresh again.
    r = d.claim("p-1", "fp-1", "L1_mitm")
    assert r.is_primary is True


def test_dedup_thread_safety():
    d = CaptureDedup(window_s=30)
    primaries: list[bool] = []
    lock = threading.Lock()

    def worker():
        r = d.claim("p-thread", "fp-thread", f"src-{threading.get_ident()}")
        with lock:
            primaries.append(r.is_primary)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one thread should have observed is_primary=True.
    assert sum(1 for p in primaries if p) == 1


def test_compute_fingerprint_deterministic():
    fp1 = compute_fingerprint("POST", "chatgpt.com", "/conv",
                               body=b"hello", ts_bucket_5min=42)
    fp2 = compute_fingerprint("POST", "chatgpt.com", "/conv",
                               body=b"hello", ts_bucket_5min=42)
    assert fp1 == fp2
    assert len(fp1) == 64


def test_compute_fingerprint_5min_bucket():
    """Same request 5 minutes apart must NOT collide as duplicate."""
    fp_a = compute_fingerprint("POST", "chatgpt.com", "/conv",
                                body=b"hello", ts_bucket_5min=42)
    fp_b = compute_fingerprint("POST", "chatgpt.com", "/conv",
                                body=b"hello", ts_bucket_5min=43)
    assert fp_a != fp_b


def test_compute_fingerprint_body_truncation():
    """Bodies > 1024 bytes only hash the first 1024."""
    body = b"x" * 2000
    body_truncated = b"x" * 1024 + b"different_after_1024"
    fp_a = compute_fingerprint("POST", "h", "/p", body=body,
                                ts_bucket_5min=1)
    fp_b = compute_fingerprint("POST", "h", "/p", body=body_truncated,
                                ts_bucket_5min=1)
    assert fp_a == fp_b


def test_dedup_metrics_emitted():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm")
    d.claim("p-1", "fp-1", "L3a_browser_ext")
    m = d.metrics()
    assert m["claims_total"] == 2
    assert m["claims_primary"] == 1
    assert m["claims_duplicate"] == 1
    assert m["hit_ratio"] == 0.5


def test_dedup_reset():
    d = CaptureDedup(window_s=30)
    d.claim("p-1", "fp-1", "L1_mitm")
    d.reset()
    r = d.claim("p-1", "fp-1", "L1_mitm")
    assert r.is_primary is True
    m = d.metrics()
    assert m["claims_total"] == 1
    assert m["claims_duplicate"] == 0


# ===========================================================================
# POLICY — 8 tests
# ===========================================================================

def test_policy_load_bundled_scenarios_yaml():
    """The shipped scenarios.yaml has 13 P0 scenarios."""
    registry = load_scenarios()
    assert len(registry) == 13
    expected = {
        "f1_chatgpt_web", "f1_claude_web", "f1_gemini_web",
        "f1_gas", "f1_grok_web",
        "f4_p1_claude_desktop", "f4_p2_chatgpt_desktop",
        "f5_p3_cursor", "f5_p4_windsurf", "f5_p5_github_copilot",
        "f6_p6_claude_code_cli", "f6_p7_codex_cli", "f6_p8_gemini_cli",
    }
    assert set(registry.ids()) == expected


def test_policy_redundancy_target_default_3():
    raw = {
        "schema_version": 1,
        "scenarios": [{
            "id": "f_x", "label": "X", "tier": "S0",
            "legs": [
                {"source": "L1_mitm", "independent_basis": "a", "priority": 1},
                {"source": "L3a_browser_ext", "independent_basis": "b", "priority": 2},
                {"source": "L4a_clipboard", "independent_basis": "c", "priority": 3},
            ],
        }],
    }
    r = load_scenarios(raw=raw)
    assert r.scenarios[0].redundancy_target == 3


def test_policy_independent_basis_must_unique():
    raw = {
        "schema_version": 1,
        "scenarios": [{
            "id": "f_x", "label": "X", "tier": "S0", "redundancy_target": 3,
            "legs": [
                {"source": "L1_mitm", "independent_basis": "same", "priority": 1},
                {"source": "L3a_browser_ext", "independent_basis": "same", "priority": 2},
            ],
        }],
    }
    with pytest.raises(ScenariosError, match="duplicate independent_basis"):
        load_scenarios(raw=raw)


def test_policy_unknown_source_rejected():
    raw = {
        "schema_version": 1,
        "scenarios": [{
            "id": "f_x", "label": "X", "tier": "S0",
            "legs": [{"source": "L99_imaginary", "independent_basis": "a", "priority": 1}],
        }],
    }
    with pytest.raises(ScenariosError, match="unknown source"):
        load_scenarios(raw=raw)


def test_policy_unsupported_schema_version_rejected():
    raw = {"schema_version": 999, "scenarios": []}
    with pytest.raises(ScenariosError, match="schema_version"):
        load_scenarios(raw=raw)


def test_policy_duplicate_scenario_id_rejected():
    raw = {
        "schema_version": 1,
        "scenarios": [
            {"id": "dup", "label": "A", "tier": "S0", "legs": [
                {"source": "L1_mitm", "independent_basis": "a", "priority": 1}]},
            {"id": "dup", "label": "B", "tier": "S0", "legs": [
                {"source": "L1_mitm", "independent_basis": "b", "priority": 1}]},
        ],
    }
    with pytest.raises(ScenariosError, match="duplicate scenario id"):
        load_scenarios(raw=raw)


def test_policy_phase_b_marker_propagated():
    registry = _make_simple_registry()
    a = registry.get("f_test_a")
    b = registry.get("f_test_b")
    assert a is not None and a.phase_b is False
    assert b is not None and b.phase_b is True


def test_policy_leg_by_source_lookup():
    registry = _make_simple_registry()
    a = registry.get("f_test_a")
    leg = a.leg_by_source("L3a_browser_ext")
    assert leg is not None and leg.priority == 2
    assert a.leg_by_source("L99_nonexistent") is None


# ===========================================================================
# STATUS — 5 tests
# ===========================================================================

def test_status_redundant_3_active():
    registry = _make_simple_registry()
    now = 10_000.0
    sigs = {
        ("f_test_a", "L1_mitm"): {"last_pass_ts": now - 60, "last_fail_ts": None},
        ("f_test_a", "L3a_browser_ext"): {"last_pass_ts": now - 60, "last_fail_ts": None},
        ("f_test_a", "L4a_clipboard"): {"last_pass_ts": now - 60, "last_fail_ts": None},
    }
    snap = compute_status(registry, sigs, now=now)
    a = next(s for s in snap.scenarios if s.id == "f_test_a")
    assert a.legs_active == 3
    assert a.status is ScenarioStatus.REDUNDANT
    assert a.color == "green"


def test_status_minimal_2_active():
    registry = _make_simple_registry()
    now = 10_000.0
    sigs = {
        ("f_test_a", "L1_mitm"): {"last_pass_ts": now - 60, "last_fail_ts": None},
        ("f_test_a", "L3a_browser_ext"): {"last_pass_ts": now - 60, "last_fail_ts": None},
    }
    snap = compute_status(registry, sigs, now=now)
    a = next(s for s in snap.scenarios if s.id == "f_test_a")
    assert a.legs_active == 2
    assert a.status is ScenarioStatus.MINIMAL
    assert a.color == "yellow"


def test_status_impaired_1_active():
    registry = _make_simple_registry()
    now = 10_000.0
    sigs = {
        ("f_test_a", "L1_mitm"): {"last_pass_ts": now - 60, "last_fail_ts": None},
    }
    snap = compute_status(registry, sigs, now=now)
    a = next(s for s in snap.scenarios if s.id == "f_test_a")
    assert a.legs_active == 1
    assert a.status is ScenarioStatus.IMPAIRED
    assert a.color == "orange"


def test_status_down_with_recent_fails_red():
    registry = _make_simple_registry()
    now = 10_000.0
    sigs = {
        ("f_test_a", "L1_mitm"): {"last_pass_ts": None, "last_fail_ts": now - 60},
    }
    snap = compute_status(registry, sigs, now=now)
    a = next(s for s in snap.scenarios if s.id == "f_test_a")
    assert a.legs_active == 0
    assert a.status is ScenarioStatus.DOWN
    assert a.color == "red"


def test_status_24h_window_excludes_old_passes():
    registry = _make_simple_registry()
    now = 10_000.0
    sigs = {
        # Pass 25h ago — outside the 24h window, must not count.
        ("f_test_a", "L1_mitm"): {"last_pass_ts": now - 25 * 3600, "last_fail_ts": None},
    }
    snap = compute_status(registry, sigs, now=now, window_hours=24.0)
    a = next(s for s in snap.scenarios if s.id == "f_test_a")
    assert a.legs_active == 0
    # All UNKNOWN → grey, not red.
    assert a.color == "grey"


# ===========================================================================
# API — 6 tests
# ===========================================================================

def _make_app(registry: ScenarioRegistry,
              signals: Optional[Mapping] = None) -> tuple[FastAPI, CaptureSupervisorState]:
    state = CaptureSupervisorState(
        registry=registry,
        dedup=CaptureDedup(window_s=30, max_entries=100),
        leg_signal_provider=StaticSignalProvider(signals or {}),
    )
    app = FastAPI()
    app.include_router(build_router(state))
    return app, state


def test_api_status_returns_all_scenarios():
    registry = _make_simple_registry()
    app, _ = _make_app(registry)
    client = TestClient(app)
    resp = client.get("/api/v1/supervisor/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == 1
    assert {s["id"] for s in data["scenarios"]} == {"f_test_a", "f_test_b"}


def test_api_scenario_detail_ok():
    registry = _make_simple_registry()
    app, _ = _make_app(registry)
    client = TestClient(app)
    resp = client.get("/api/v1/supervisor/scenario/f_test_a")
    assert resp.status_code == 200
    assert resp.json()["id"] == "f_test_a"


def test_api_scenario_detail_404():
    registry = _make_simple_registry()
    app, _ = _make_app(registry)
    client = TestClient(app)
    resp = client.get("/api/v1/supervisor/scenario/nope")
    assert resp.status_code == 404


def test_api_register_pro_leg_ok():
    registry = _make_simple_registry()
    app, state = _make_app(registry)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/supervisor/legs/register",
        json={
            "source": "L0_kernel",
            "scenario_ids": ["f_test_a"],
            "independent_basis": "wfp_winsock_layer",
            "agent_pid": 12345,
            "agent_version": "pro-1.0.0",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["rejected_scenarios"] == []
    listing = client.get("/api/v1/supervisor/legs/registered").json()
    assert listing["count"] == 1


def test_api_register_pro_leg_basis_collision_rejected():
    registry = _make_simple_registry()
    app, _ = _make_app(registry)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/supervisor/legs/register",
        json={
            "source": "L0_kernel",
            "scenario_ids": ["f_test_a"],
            "independent_basis": "net_stack",  # already used by L1_mitm
        },
    )
    body = resp.json()
    assert body["accepted"] is False
    assert "f_test_a" in body["rejected_scenarios"]
    assert body["rejection_reason"] is not None
    assert "independent_basis" in body["rejection_reason"]


def test_api_degraded_endpoint_suppresses_phase_b():
    registry = _make_simple_registry()
    # No signals → both scenarios down. f_test_b is phase_b, suppressed.
    app, _ = _make_app(registry, signals={})
    client = TestClient(app)
    resp = client.get("/api/v1/supervisor/degraded")
    assert resp.status_code == 200
    body = resp.json()
    # f_test_a all UNKNOWN -> color grey, status down, but legs_active=0 -> alert
    # f_test_b suppressed
    alert_ids = [a["scenario_id"] for a in body["alerts"]]
    assert "f_test_a" in alert_ids
    assert "f_test_b" not in alert_ids
    # Toggle: explicitly disable suppression -> phase_b shows up
    resp2 = client.get("/api/v1/supervisor/degraded?suppress_phase_b=false")
    alert_ids_all = [a["scenario_id"] for a in resp2.json()["alerts"]]
    assert "f_test_b" in alert_ids_all
