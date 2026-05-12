# SPDX-License-Identifier: Apache-2.0
"""Tests for the P5.C.1 Meta-Pipeline health-as-data layer.

Covers (per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.1 acceptance):

- Validation (lane/layer/status/target/case_id, PII deny-list, ts skew, meta size)
- ``record_beacon`` + ``get_beacon`` round trip
- ``list_beacons`` filtering
- ``compute_matrix`` lane × target colour rules (HEALTH-MATRIX §5.1)
- ``compute_timeseries`` bucket alignment
- ``purge_old_beacons`` retention
- Rate limiter (heartbeat / case-burst)
- ``emit_beacon`` swallows all errors (never raises)
- Lane smoke for cli / mcp / desktop / browser (acceptance gate)
- HTTP endpoints via FastAPI TestClient

The eight-test minimum from the acceptance gate is exceeded so any
single regression yields a localised failure signal rather than a
generic "health beacons broken" red.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

# Ensure ``pce_core`` is importable when tests are invoked from the
# repository root without an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pce_core.db import init_db  # noqa: E402
from pce_core.health import (  # noqa: E402
    BeaconRejection,
    HealthBeacon,
    compute_matrix,
    compute_timeseries,
    emit_beacon,
    get_beacon,
    list_beacons,
    purge_old_beacons,
    record_beacon,
    reset_rate_buckets,
    validate_beacon,
    validate_meta,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_pce_db(tmp_path: Path) -> Path:
    """Empty PCE SQLite DB with migrations 0001–0013 applied."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    return db_path


@pytest.fixture(autouse=True)
def _reset_rate_buckets() -> Iterator[None]:
    """Rate limiter is in-memory + process-wide — reset between tests so
    a heartbeat from a previous test never throttles the next one."""
    reset_rate_buckets()
    yield
    reset_rate_buckets()


def _make_beacon(**overrides) -> HealthBeacon:
    """Tiny factory for the canonical 'valid beacon' used across cases."""
    base = dict(
        lane="browser",
        layer="L3a",
        target="chatgpt",
        status="pass",
        ts=time.time(),
        case_id="T01",
        elapsed_ms=2341,
        meta={"url": "https://chatgpt.com/c/xyz", "extension_version": "1.0.0"},
    )
    base.update(overrides)
    return HealthBeacon(**base)


# ---------------------------------------------------------------------------
# 1. Validation — happy path + each rejection code
# ---------------------------------------------------------------------------

def test_validate_happy_path() -> None:
    assert validate_beacon(_make_beacon()) is None


def test_validate_invalid_lane() -> None:
    r = validate_beacon(_make_beacon(lane="server"))
    assert isinstance(r, BeaconRejection) and r.code == "invalid_lane"


def test_validate_invalid_layer() -> None:
    r = validate_beacon(_make_beacon(layer="L99"))
    assert isinstance(r, BeaconRejection) and r.code == "invalid_layer"


def test_validate_invalid_status() -> None:
    r = validate_beacon(_make_beacon(status="kinda_ok"))
    assert isinstance(r, BeaconRejection) and r.code == "invalid_status"


def test_validate_invalid_target() -> None:
    # uppercase letters not permitted by _TARGET_RE
    r = validate_beacon(_make_beacon(target="ChatGPT"))
    assert isinstance(r, BeaconRejection) and r.code == "invalid_target"


def test_validate_invalid_case_id() -> None:
    r = validate_beacon(_make_beacon(case_id="T1"))   # missing 2-digit suffix
    assert isinstance(r, BeaconRejection) and r.code == "invalid_case_id"


def test_validate_pii_detected_top_level() -> None:
    r = validate_beacon(_make_beacon(meta={"api_key": "sk-12345"}))
    assert isinstance(r, BeaconRejection) and r.code == "pii_detected"


def test_validate_pii_detected_nested() -> None:
    """PII deny-list is recursive — nested forbidden keys are also rejected."""
    r = validate_beacon(_make_beacon(meta={"layer_meta": {"cookie": "x"}}))
    assert isinstance(r, BeaconRejection) and r.code == "pii_detected"


def test_validate_ts_skew() -> None:
    r = validate_beacon(_make_beacon(ts=time.time() - 10_000))
    assert isinstance(r, BeaconRejection) and r.code == "ts_skew"


def test_validate_meta_too_large() -> None:
    big = {"blob": "x" * 5000}
    assert validate_meta(big).code == "meta_too_large"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 2. record_beacon / get_beacon round trip
# ---------------------------------------------------------------------------

def test_record_and_get_round_trip(tmp_pce_db: Path) -> None:
    beacon = _make_beacon()
    rowid = record_beacon(beacon, db_path=tmp_pce_db)
    assert isinstance(rowid, int) and rowid > 0
    row = get_beacon(rowid, db_path=tmp_pce_db)
    assert row is not None
    assert row["lane"] == "browser"
    assert row["target"] == "chatgpt"
    assert row["status"] == "pass"
    assert row["case_id"] == "T01"
    assert row["meta"]["extension_version"] == "1.0.0"


def test_record_rejected_does_not_write(tmp_pce_db: Path) -> None:
    bad = _make_beacon(lane="server")
    result = record_beacon(bad, db_path=tmp_pce_db)
    assert isinstance(result, BeaconRejection)
    # No row written
    rows = list_beacons(db_path=tmp_pce_db)
    assert rows == []


# ---------------------------------------------------------------------------
# 3. list_beacons filtering
# ---------------------------------------------------------------------------

def test_list_beacons_filters(tmp_pce_db: Path) -> None:
    reset_rate_buckets()
    record_beacon(_make_beacon(lane="browser", target="chatgpt", case_id="T01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="browser", target="chatgpt", case_id="T02"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="desktop", layer="L3d",
                               target="claude_desktop", case_id="D01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="cli", layer="L3h",
                               target="claude_code", case_id="K01",
                               status="fail"),
                  db_path=tmp_pce_db)

    assert len(list_beacons(db_path=tmp_pce_db)) == 4
    assert len(list_beacons(lane="browser", db_path=tmp_pce_db)) == 2
    assert len(list_beacons(target="claude_desktop", db_path=tmp_pce_db)) == 1
    assert len(list_beacons(status="fail", db_path=tmp_pce_db)) == 1
    assert len(list_beacons(case_id="T02", db_path=tmp_pce_db)) == 1


# ---------------------------------------------------------------------------
# 4. compute_matrix colour rules (HEALTH-MATRIX §5.1)
# ---------------------------------------------------------------------------

def test_matrix_empty_all_grey(tmp_pce_db: Path) -> None:
    m = compute_matrix(db_path=tmp_pce_db)
    for lane in ("browser", "desktop", "cli", "mcp"):
        assert m["lanes"][lane]["color"] == "grey"
        assert m["lanes"][lane]["targets"] == {}


def test_matrix_four_lanes_one_target_each(tmp_pce_db: Path) -> None:
    """The P5.C.1 acceptance shape: 4 lane × ≥1 target populated."""
    reset_rate_buckets()
    record_beacon(_make_beacon(lane="browser", target="chatgpt"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="desktop", layer="L3d",
                               target="claude_desktop", case_id="D01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="cli", layer="L3h",
                               target="claude_code", case_id="K01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="mcp", layer="L3f",
                               target="filesystem", case_id=None),
                  db_path=tmp_pce_db)

    m = compute_matrix(db_path=tmp_pce_db)
    for lane in ("browser", "desktop", "cli", "mcp"):
        targets = m["lanes"][lane]["targets"]
        assert len(targets) == 1, f"lane {lane} should have 1 target, got {targets}"


def test_matrix_color_red_on_three_d0_fails(tmp_pce_db: Path) -> None:
    """Per HEALTH-MATRIX §5.1: D0 target with ≥3 fail beacons → red."""
    reset_rate_buckets()
    now = time.time()
    # 3 fails on a D0 target in the last 24h.
    for i in range(3):
        record_beacon(
            _make_beacon(lane="desktop", layer="L3d",
                         target="claude_desktop",
                         status="fail", case_id=f"D0{i}",
                         ts=now - i),
            db_path=tmp_pce_db,
        )
    m = compute_matrix(db_path=tmp_pce_db)
    cd = m["lanes"]["desktop"]["targets"]["claude_desktop"]
    assert cd["color"] == "red", cd
    assert m["lanes"]["desktop"]["color"] == "red"


def test_matrix_color_red_on_plane_count_below_required(tmp_pce_db: Path) -> None:
    """Per HEALTH-MATRIX §5.1: D0/S0 target with plane_count < required → red.

    ChatGPT is S0 and requires 2 planes (N+H). One PASS on L3a only (1
    plane) must still color red because plane_required=2.
    """
    reset_rate_buckets()
    record_beacon(_make_beacon(lane="browser", target="chatgpt", layer="L3a"),
                  db_path=tmp_pce_db)
    # No L1 PASS — only one distinct plane in the window.
    m = compute_matrix(db_path=tmp_pce_db)
    cg = m["lanes"]["browser"]["targets"]["chatgpt"]
    assert cg["plane_count"] == 1
    assert cg["plane_required"] == 2
    assert cg["color"] == "red"


def test_matrix_color_green_on_two_planes_high_pass_rate(tmp_pce_db: Path) -> None:
    """Per HEALTH-MATRIX §5.1: 24h pass_rate ≥ 90% + plane ≥ required → green."""
    reset_rate_buckets()
    # 9 PASS on L3a + 9 PASS on L1 + 1 SKIP → pass_rate ≈ 0.95
    now = time.time()
    for i in range(9):
        record_beacon(_make_beacon(lane="browser", target="chatgpt",
                                   layer="L3a", case_id=f"T{i:02d}",
                                   ts=now - i),
                      db_path=tmp_pce_db)
        record_beacon(_make_beacon(lane="browser", target="chatgpt",
                                   layer="L1", case_id=f"T{i:02d}",
                                   ts=now - i - 0.1),
                      db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="browser", target="chatgpt",
                               layer="L1", case_id="T19",
                               status="skip", ts=now - 1),
                  db_path=tmp_pce_db)
    m = compute_matrix(db_path=tmp_pce_db)
    cg = m["lanes"]["browser"]["targets"]["chatgpt"]
    assert cg["plane_count"] == 2
    assert cg["pass_rate_24h"] >= 0.90
    assert cg["color"] == "green"


# ---------------------------------------------------------------------------
# 5. compute_timeseries
# ---------------------------------------------------------------------------

def test_timeseries_bucketing(tmp_pce_db: Path) -> None:
    reset_rate_buckets()
    now = time.time()
    # Three beacons all in the current hourly bucket.
    for _ in range(3):
        record_beacon(_make_beacon(lane="browser", target="chatgpt", ts=now),
                      db_path=tmp_pce_db)
    # Disable rate limiter for a 4th beacon (same bucket but case_id varied).
    record_beacon(
        _make_beacon(lane="browser", target="chatgpt", case_id="T02",
                     status="fail", ts=now - 1),
        db_path=tmp_pce_db,
    )
    ts = compute_timeseries(lane="browser", target="chatgpt",
                            hours=1, bucket_s=3600, db_path=tmp_pce_db)
    assert len(ts["buckets"]) == 1
    assert ts["buckets"][0]["pass"] + ts["buckets"][0]["fail"] >= 2
    assert ts["bucket_size_s"] == 3600


# ---------------------------------------------------------------------------
# 6. Retention
# ---------------------------------------------------------------------------

def test_purge_old_beacons(tmp_pce_db: Path) -> None:
    """Old beacons (> retention window) are deleted; recent ones survive."""
    reset_rate_buckets()
    now = time.time()
    # Recent — should survive
    record_beacon(_make_beacon(target="recent", ts=now), db_path=tmp_pce_db)
    # 100 days old — should be purged
    record_beacon(_make_beacon(target="old", ts=now - 100 * 86400,
                               case_id=None),
                  db_path=tmp_pce_db, now=now - 100 * 86400 + 1)

    deleted = purge_old_beacons(retention_days=90, db_path=tmp_pce_db, now=now)
    assert deleted == 1
    remaining = list_beacons(db_path=tmp_pce_db)
    assert len(remaining) == 1
    assert remaining[0]["target"] == "recent"


# ---------------------------------------------------------------------------
# 7. Rate limiter
# ---------------------------------------------------------------------------

def test_rate_limiter_heartbeat_collapses_to_one_per_minute(tmp_pce_db: Path) -> None:
    """Heartbeat (no case_id) is collapsed to ≤ 1 per 60s per (lane, target)."""
    reset_rate_buckets()
    now = time.time()
    first = record_beacon(_make_beacon(case_id=None, ts=now),
                          db_path=tmp_pce_db, now=now)
    second = record_beacon(_make_beacon(case_id=None, ts=now + 1),
                           db_path=tmp_pce_db, now=now + 1)
    assert isinstance(first, int)
    assert isinstance(second, BeaconRejection) and second.code == "rate_limited"


def test_rate_limiter_case_burst(tmp_pce_db: Path) -> None:
    """Case-bound beacons allow 10/sec per (lane, target); 11th gets throttled."""
    reset_rate_buckets()
    now = time.time()
    accepted = 0
    last_reject: BeaconRejection | None = None
    for i in range(11):
        r = record_beacon(_make_beacon(case_id=f"T{i:02d}", ts=now),
                          db_path=tmp_pce_db, now=now)
        if isinstance(r, int):
            accepted += 1
        elif isinstance(r, BeaconRejection):
            last_reject = r
    assert accepted == 10
    assert last_reject is not None and last_reject.code == "rate_limited"


# ---------------------------------------------------------------------------
# 8. emit_beacon never raises
# ---------------------------------------------------------------------------

def test_emit_beacon_swallows_validation_failure(tmp_pce_db: Path) -> None:
    """emit_beacon returns None on rejection — never raises."""
    out = emit_beacon(
        lane="not_a_lane",          # invalid
        layer="L3a",
        target="chatgpt",
        status="pass",
        db_path=tmp_pce_db,
    )
    assert out is None
    # Nothing persisted.
    assert list_beacons(db_path=tmp_pce_db) == []


def test_emit_beacon_happy_path(tmp_pce_db: Path) -> None:
    out = emit_beacon(
        lane="browser",
        layer="L3a",
        target="chatgpt",
        status="pass",
        case_id="T01",
        elapsed_ms=1234,
        meta={"site_name": "ChatGPT"},
        db_path=tmp_pce_db,
    )
    assert isinstance(out, int) and out > 0


# ---------------------------------------------------------------------------
# 9. Four-lane smoke tests (acceptance gate)
# ---------------------------------------------------------------------------

def test_lane_smoke_cli_observer_emits_beacon(tmp_pce_db: Path) -> None:
    """Driving CliWrapperObserver.emit() should produce one cli beacon."""
    reset_rate_buckets()
    from pce_cli_wrapper.capture import CliWrapperObserver, RelayResult

    obs = CliWrapperObserver(db_path=tmp_pce_db, dry_run=False)
    res = RelayResult(
        target_id="claude-code",
        command_name="claude",
        provider="anthropic",
        target_path=Path("/usr/local/bin/claude"),
        args=["claude", "--version"],
        cwd=Path("."),
        started_at_ns=1_000_000_000,
        finished_at_ns=1_000_000_000 + 50_000_000,  # 50ms later
        exit_code=0,
        stdout_bytes=b"Claude Code 0.5.12\n",
    )
    obs.emit(res)

    beacons = list_beacons(lane="cli", db_path=tmp_pce_db)
    assert len(beacons) == 1
    assert beacons[0]["target"] == "claude_code"
    assert beacons[0]["status"] == "pass"
    assert beacons[0]["layer"] == "L3h"


def test_lane_smoke_mcp_observer_emits_beacon(tmp_pce_db: Path) -> None:
    """Driving JsonRpcObserver.observe() should produce one mcp beacon."""
    reset_rate_buckets()
    from pce_mcp_proxy.capture import JsonRpcObserver
    import json as _json

    obs = JsonRpcObserver(upstream_name="filesystem", db_path=tmp_pce_db)
    frame = _json.dumps({"jsonrpc": "2.0", "method": "tools/list",
                         "id": 1, "params": {}}).encode("utf-8")
    obs.observe("request", frame)

    beacons = list_beacons(lane="mcp", db_path=tmp_pce_db)
    assert len(beacons) >= 1
    assert beacons[0]["target"] == "filesystem"
    assert beacons[0]["layer"] == "L3f"


def test_lane_smoke_desktop_driver_emits_beacon(tmp_pce_db: Path) -> None:
    """DesktopDriver.emit_health_beacon writes one desktop beacon."""
    reset_rate_buckets()
    from tests.e2e_desktop_ui.drivers.base import DesktopDriver

    class _StubDriver(DesktopDriver):
        PRODUCT_NAME = "StubDesktop"
        HEALTH_TARGET = "claude_desktop"
        HEALTH_LAYER = "L3g"

        def focus(self) -> None: ...
        def click_composer(self) -> None: ...
        def send_message(self, text, *, wait_done=True, wait_timeout=60.0): ...
        def wait_done(self, request_pair_id, *, timeout=60.0): ...
        def cancel_current(self) -> bool: return False
        def new_chat(self) -> bool: return False

    drv = _StubDriver()
    # Bypass the global DB_PATH by injecting via emit_beacon's own path.
    # The base helper does not expose db_path, so this verifies the in-
    # process call path. We patch get_connection via env to keep the
    # acceptance gate cheap: use the system DB but assert at least 1
    # desktop beacon ends up in the table for the just-recorded target.
    # The cleaner alternative — exposing db_path on the base helper —
    # is left to P5.C.2 once Test Conductor needs it.
    import pce_core.health as _h

    captured: list = []
    orig = _h.record_beacon

    def _spy(beacon, *, db_path=None, **kw):
        return orig(beacon, db_path=tmp_pce_db, **kw)

    _h.record_beacon = _spy
    try:
        out = drv.emit_health_beacon(status="pass", case_id="D01",
                                     elapsed_ms=4321,
                                     meta={"product_version": "1.6608.2.0"})
    finally:
        _h.record_beacon = orig

    assert isinstance(out, int) and out > 0
    beacons = list_beacons(lane="desktop", db_path=tmp_pce_db)
    assert len(beacons) == 1
    assert beacons[0]["target"] == "claude_desktop"
    assert beacons[0]["layer"] == "L3g"
    assert beacons[0]["case_id"] == "D01"


def test_lane_smoke_browser_via_http_endpoint(tmp_pce_db: Path, monkeypatch) -> None:
    """POST /api/v1/health/beacon writes a browser-lane beacon end-to-end.

    The browser lane has no in-process Python hook — beacons travel
    through chrome extension → background.ts → POST. This test proves
    the HTTP entry path works.
    """
    reset_rate_buckets()
    # Route get_connection at the FastAPI layer to our tmp DB.
    import pce_core.db as _db
    monkeypatch.setattr(_db, "DB_PATH", tmp_pce_db)

    from fastapi.testclient import TestClient
    from pce_core.server import app

    client = TestClient(app)
    resp = client.post(
        "/api/v1/health/beacon",
        json={
            "lane": "browser",
            "layer": "L3a",
            "target": "chatgpt",
            "status": "pass",
            "ts": time.time(),
            "case_id": "T01",
            "elapsed_ms": 1500,
            "meta": {"site_name": "ChatGPT", "extension_version": "1.0.0"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


# ---------------------------------------------------------------------------
# 10. HTTP endpoint behaviour
# ---------------------------------------------------------------------------

def test_endpoint_rejects_pii(tmp_pce_db: Path, monkeypatch) -> None:
    """POST /api/v1/health/beacon returns 400 with code=pii_detected."""
    reset_rate_buckets()
    import pce_core.db as _db
    monkeypatch.setattr(_db, "DB_PATH", tmp_pce_db)

    from fastapi.testclient import TestClient
    from pce_core.server import app

    client = TestClient(app)
    resp = client.post(
        "/api/v1/health/beacon",
        json={
            "lane": "browser",
            "layer": "L3a",
            "target": "chatgpt",
            "status": "pass",
            "ts": time.time(),
            "meta": {"api_key": "sk-12345"},  # forbidden
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "pii_detected"


def test_endpoint_matrix_after_record(tmp_pce_db: Path, monkeypatch) -> None:
    """GET /api/v1/health/matrix reflects recently-recorded beacons."""
    reset_rate_buckets()
    import pce_core.db as _db
    monkeypatch.setattr(_db, "DB_PATH", tmp_pce_db)

    # Seed one beacon per lane.
    record_beacon(_make_beacon(lane="browser", target="chatgpt"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="desktop", layer="L3d",
                               target="claude_desktop", case_id="D01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="cli", layer="L3h",
                               target="claude_code", case_id="K01"),
                  db_path=tmp_pce_db)
    record_beacon(_make_beacon(lane="mcp", layer="L3f",
                               target="filesystem", case_id=None),
                  db_path=tmp_pce_db)

    from fastapi.testclient import TestClient
    from pce_core.server import app

    client = TestClient(app)
    resp = client.get("/api/v1/health/matrix")
    assert resp.status_code == 200, resp.text
    matrix = resp.json()
    assert set(matrix["lanes"].keys()) == {"browser", "desktop", "cli", "mcp"}
    for lane in ("browser", "desktop", "cli", "mcp"):
        targets = matrix["lanes"][lane]["targets"]
        assert len(targets) == 1


def test_endpoint_beacon_detail_404(tmp_pce_db: Path, monkeypatch) -> None:
    """GET /api/v1/health/beacon/{id} returns 404 for missing rows."""
    import pce_core.db as _db
    monkeypatch.setattr(_db, "DB_PATH", tmp_pce_db)

    from fastapi.testclient import TestClient
    from pce_core.server import app

    client = TestClient(app)
    resp = client.get("/api/v1/health/beacon/999999")
    assert resp.status_code == 404
