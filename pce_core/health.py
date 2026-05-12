# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Health beacon API (P5.C.1 Meta-Pipeline third pillar).

This module is the read/write surface for the **health-as-data**
contract defined in:

- ``Docs/docs/engineering/META-PIPELINE-FRAMEWORK.md`` §2.3
- ``Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md``
- ``Docs/docs/engineering/adr/ADR-019-maintenance-as-first-class-concern.md`` §3.1 契约 A

A ``HealthBeacon`` is **system state** (a single point-in-time signal
that a lane / layer / target was healthy or not). It is **not** business
data — beacons never contain user conversation content, API keys, or PII.
Server-side validation (``validate_meta``) rejects any beacon that looks
like it carries forbidden fields.

The four canonical operations:

1. ``record_beacon(beacon)`` — write one beacon row (called by lane
   adapters via local HTTP POST or directly in tests).
2. ``get_beacon(beacon_id)`` — single-row read for drill-down.
3. ``compute_matrix(window_hours)`` — aggregate lanes × targets into the
   Dashboard "Lane Health" matrix shape (HEALTH-MATRIX §4.2).
4. ``compute_timeseries(lane, target, hours, bucket_s)`` — bucketed
   hourly counts for charting (HEALTH-MATRIX §4.3).

All write paths are **idempotent and best-effort**: rate limiting,
clock-skew rejection, PII detection, and JSON-size capping happen
before the row is inserted. Failures are surfaced via the return type
(``BeaconRejection``) so the HTTP layer can map them to 400 / 429
responses.

See also:

- ``pce_core/migrations/0013_health_beacons.py`` — table definition
- ``pce_core/server.py`` ``/api/v1/health/beacon`` endpoints
- ``pce_core/models.py`` ``HealthBeaconIn`` / ``HealthBeaconRecord``
- ``tests/test_health_beacon.py``
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from .db import get_connection

logger = logging.getLogger("pce.health")


# ---------------------------------------------------------------------------
# Canonical enums (mirror PCE-PIPELINE-HEALTH-MATRIX §2.1 / §2.2)
# ---------------------------------------------------------------------------

#: 4 lane axis (per META-PIPELINE-FRAMEWORK §1.3).
LANES: tuple[str, ...] = ("browser", "desktop", "cli", "mcp")

#: UCS 5-layer + recognised sub-layers (PCE-PIPELINE-HEALTH-MATRIX §2.2).
LAYERS: tuple[str, ...] = (
    "L0",
    "L1",
    "L2",
    "L3a", "L3b", "L3c", "L3d", "L3e", "L3f", "L3g", "L3h",
    "L4a", "L4b", "L4c",
)

#: Beacon status enum (mirrors SQL CHECK constraint in migration 0013).
STATUSES: tuple[str, ...] = ("pass", "fail", "skip", "degraded", "infra_error")

#: Color severity ladder (HEALTH-MATRIX §5.1). Higher index = more severe.
_COLOR_SEVERITY: tuple[str, ...] = ("green", "grey", "yellow", "red")

#: Maximum acceptable wall-clock drift between client beacon ``ts`` and
#: server ``time.time()``. Anti-replay + anti-clock-skew. Per HEALTH-MATRIX §2.2.
MAX_TS_SKEW_S: float = 300.0

#: Maximum serialised ``meta`` payload size (bytes). Per HEALTH-MATRIX §2.2.
MAX_META_BYTES: int = 4096

#: Maximum ``target`` string length. Per HEALTH-MATRIX §2.2.
MAX_TARGET_LEN: int = 64

#: Case_id format — single uppercase letter + 2 digits (T01 / D03 / C05 / E12 / K07 / M09).
_CASE_ID_RE: re.Pattern[str] = re.compile(r"^[A-Z]\d{2}$")

#: Target name format — lowercase alnum + dash/underscore.
_TARGET_RE: re.Pattern[str] = re.compile(r"^[a-z0-9_\-]+$")

#: Forbidden meta keys (case-insensitive). Beacon must never carry these.
#: This is a deny-list, not an allow-list, because per-lane meta payloads
#: vary widely. The deny-list explicitly catches the obvious PII / secret
#: shapes so a misbehaving adapter can't accidentally leak content.
_FORBIDDEN_META_KEYS: frozenset[str] = frozenset({
    "content_text", "content_json", "body", "request_body", "response_body",
    "api_key", "apikey", "token", "access_token", "refresh_token",
    "cookie", "cookies", "authorization", "auth",
    "user_email", "user_id", "userid", "username", "email",
    "password", "secret", "pii",
})

#: Per-(lane, target) heartbeat throttle window — at most 1 case_id-less
#: beacon per (lane, target) per ``_HEARTBEAT_WINDOW_S`` seconds is kept
#: by the rate limiter. Per HEALTH-MATRIX §3.1.
_HEARTBEAT_WINDOW_S: float = 60.0

#: Burst limit for case-bound beacons (per lane+target). 10/sec → soft
#: dropped (logged) past this. Per HEALTH-MATRIX §2.2 "beacon 总频率".
_BEACON_BURST_PER_SEC: int = 10


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HealthBeacon:
    """One health-as-data signal. Mirrors PCE-PIPELINE-HEALTH-MATRIX §2.1.

    Required fields define **what** was observed (lane + layer + target +
    status + when). Optional fields enrich the observation (which case
    triggered it, how long it took, lane-specific metadata, which
    selectors hit). All optional fields default to ``None`` / empty so
    a minimal heartbeat beacon needs only the 5 required fields.
    """

    # Required
    lane: str           # one of LANES
    layer: str          # one of LAYERS
    target: str         # short product id, e.g. "chatgpt" / "claude_desktop"
    status: str         # one of STATUSES
    ts: float           # unix epoch seconds (client clock)

    # Optional
    case_id: Optional[str] = None     # e.g. "T01" / "D03" / None for heartbeat
    elapsed_ms: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)
    dom_selector_hits: Optional[dict[str, int]] = None

    def to_row(self) -> tuple:
        """Project to the 9-column row order of ``health_beacons`` (id
        excluded — autoincrement).

        Returns ``(lane, layer, target, case_id, status, ts, elapsed_ms,
        meta_json, selector_hits_json)``.
        """
        return (
            self.lane,
            self.layer,
            self.target,
            self.case_id,
            self.status,
            float(self.ts),
            int(self.elapsed_ms) if self.elapsed_ms is not None else None,
            json.dumps(self.meta, ensure_ascii=False) if self.meta else None,
            json.dumps(self.dom_selector_hits, ensure_ascii=False)
                if self.dom_selector_hits else None,
        )


@dataclass
class BeaconRejection:
    """A validation failure. Caller (HTTP endpoint) maps ``code`` to status."""

    code: Literal[
        "invalid_lane", "invalid_layer", "invalid_status",
        "invalid_target", "invalid_case_id",
        "ts_skew", "meta_too_large", "pii_detected",
        "rate_limited",
    ]
    message: str


# ---------------------------------------------------------------------------
# Validation helpers (pure functions, no IO)
# ---------------------------------------------------------------------------

def validate_beacon(beacon: HealthBeacon, *, now: Optional[float] = None) -> Optional[BeaconRejection]:
    """Run the full validation pipeline against one beacon.

    Returns ``None`` on success or a ``BeaconRejection`` on the first
    failed check (fail-fast). Validation is **pure** — no DB or HTTP
    side effects — so it is safe to call from tests and from any
    transport layer.
    """
    if beacon.lane not in LANES:
        return BeaconRejection("invalid_lane", f"lane must be one of {LANES}, got {beacon.lane!r}")
    if beacon.layer not in LAYERS:
        return BeaconRejection("invalid_layer", f"layer must be one of {LAYERS}, got {beacon.layer!r}")
    if beacon.status not in STATUSES:
        return BeaconRejection("invalid_status", f"status must be one of {STATUSES}, got {beacon.status!r}")
    if not beacon.target or len(beacon.target) > MAX_TARGET_LEN or not _TARGET_RE.match(beacon.target):
        return BeaconRejection(
            "invalid_target",
            f"target must match {_TARGET_RE.pattern} and be ≤ {MAX_TARGET_LEN} chars",
        )
    if beacon.case_id is not None and not _CASE_ID_RE.match(beacon.case_id):
        return BeaconRejection(
            "invalid_case_id",
            f"case_id must match {_CASE_ID_RE.pattern}, got {beacon.case_id!r}",
        )

    # Clock-skew check (defends against replay + adapter clock drift).
    server_now = now if now is not None else time.time()
    if abs(beacon.ts - server_now) > MAX_TS_SKEW_S:
        return BeaconRejection(
            "ts_skew",
            f"beacon ts is {abs(beacon.ts - server_now):.0f}s off server clock (max {MAX_TS_SKEW_S}s)",
        )

    # Meta payload validation (size + forbidden keys).
    meta_rejection = validate_meta(beacon.meta)
    if meta_rejection is not None:
        return meta_rejection

    return None


def validate_meta(meta: dict[str, Any]) -> Optional[BeaconRejection]:
    """Reject meta dicts that carry PII / secrets / business content."""
    if not meta:
        return None

    # Deny-list scan (recursive — Pro adapters might nest things in
    # ``layer_meta`` style; we still don't want a leak).
    for key in _walk_keys(meta):
        if key.lower() in _FORBIDDEN_META_KEYS:
            return BeaconRejection(
                "pii_detected",
                f"meta contains forbidden key: {key!r} (see PCE-PIPELINE-HEALTH-MATRIX §2.1)",
            )

    # Size cap (post-serialise so we count what hits SQLite).
    serialised = json.dumps(meta, ensure_ascii=False)
    if len(serialised.encode("utf-8")) > MAX_META_BYTES:
        return BeaconRejection(
            "meta_too_large",
            f"meta exceeds {MAX_META_BYTES} bytes after serialisation",
        )

    return None


def _walk_keys(obj: Any) -> Iterable[str]:
    """Yield every dict key reached from ``obj`` (depth-first)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per-process)
# ---------------------------------------------------------------------------

# We keep a tiny rolling-window deque per (lane, target) tuple. This is
# explicitly per-process and per-installation (PCE Core is local-first,
# there is no shared cache). On process restart the buckets reset, which
# is acceptable: rate limiting is a noise-control measure, not security.

#: Rate-limit bucket key. Per HEALTH-MATRIX §2.2 / §3.1 the canonical
#: limit is per ``(lane, target)`` — NOT per ``(lane, target, case_id)``,
#: because a sweep firing T01..T20 in one second on a single target is
#: exactly the firehose the rate limiter is supposed to clip. The 3rd
#: tuple element distinguishes the two distinct windows (60s heartbeat
#: vs 1s case-bound burst) so they don't interfere with each other.
_RateKey = tuple[str, str, str]  # (lane, target, "heartbeat" | "case")

# Per-key list of recent beacon timestamps (server clock).
_rate_buckets: dict[_RateKey, list[float]] = {}


def _rate_check(beacon: HealthBeacon, *, now: float) -> bool:
    """Return True if the beacon should be accepted, False if throttled.

    Two limits apply, per HEALTH-MATRIX §3.1:

      - **Heartbeat (no case_id)**: at most one accepted per
        ``_HEARTBEAT_WINDOW_S`` seconds per (lane, target).
      - **Case-bound (case_id)**: at most ``_BEACON_BURST_PER_SEC`` per
        rolling 1s window per (lane, target). The case_id itself is NOT
        part of the bucket key — a sweep firing T01..T20 in one second
        on a single target collectively shares the 10/sec budget.
    """
    if beacon.case_id is None:
        key: _RateKey = (beacon.lane, beacon.target, "heartbeat")
        bucket = _rate_buckets.setdefault(key, [])
        if bucket and (now - bucket[-1]) < _HEARTBEAT_WINDOW_S:
            return False
        bucket.append(now)
        # Heartbeat bucket only needs the last entry — trim.
        if len(bucket) > 1:
            del bucket[:-1]
        return True

    # Case-bound path — 1s sliding window, shared across all case_ids
    # for the same (lane, target).
    key = (beacon.lane, beacon.target, "case")
    bucket = _rate_buckets.setdefault(key, [])
    cutoff = now - 1.0
    bucket[:] = [t for t in bucket if t >= cutoff]
    if len(bucket) >= _BEACON_BURST_PER_SEC:
        return False
    bucket.append(now)
    return True


def reset_rate_buckets() -> None:
    """Clear the in-memory rate buckets. **Test-only helper.**"""
    _rate_buckets.clear()


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------

def record_beacon(
    beacon: HealthBeacon,
    *,
    db_path: Optional[Path] = None,
    now: Optional[float] = None,
    rate_limit: bool = True,
) -> int | BeaconRejection:
    """Persist one beacon, returning its rowid or a ``BeaconRejection``.

    The function is the single canonical write path. It is called both
    by ``POST /api/v1/health/beacon`` (transport layer in
    ``server.py``) and by in-process test helpers.

    Side effects: opens a SQLite connection, writes one row to
    ``health_beacons``, commits. On rejection nothing is written.

    Args:
        beacon: the dataclass instance to persist.
        db_path: optional override (tests / multi-DB tools); defaults
            to the system config.
        now: optional injected server clock (tests / clock-skew probes).
        rate_limit: set False to bypass the rate limiter (tests / batch
            re-import).
    """
    server_now = now if now is not None else time.time()
    rejection = validate_beacon(beacon, now=server_now)
    if rejection is not None:
        logger.debug(
            "health beacon rejected at validate",
            extra={"event": "health.reject", "pce_fields": {
                "code": rejection.code, "lane": beacon.lane, "target": beacon.target,
            }},
        )
        return rejection

    if rate_limit and not _rate_check(beacon, now=server_now):
        logger.debug(
            "health beacon throttled",
            extra={"event": "health.throttled", "pce_fields": {
                "lane": beacon.lane, "target": beacon.target, "case_id": beacon.case_id,
            }},
        )
        return BeaconRejection("rate_limited", "beacon throttled by rate limiter")

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO health_beacons "
            "(lane, layer, target, case_id, status, ts, elapsed_ms, "
            " meta_json, selector_hits_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            beacon.to_row(),
        )
        conn.commit()
        rowid = int(cur.lastrowid or 0)
        return rowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------

def get_beacon(beacon_id: int, *, db_path: Optional[Path] = None) -> Optional[dict]:
    """Return one beacon by rowid, or ``None`` if not present."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, lane, layer, target, case_id, status, ts, elapsed_ms, "
            "       meta_json, selector_hits_json, created_at "
            "FROM health_beacons WHERE id = ?",
            (beacon_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_beacons(
    *,
    lane: Optional[str] = None,
    target: Optional[str] = None,
    case_id: Optional[str] = None,
    status: Optional[str] = None,
    since_ts: Optional[float] = None,
    limit: int = 100,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Filter beacons by any subset of (lane, target, case_id, status, since_ts).

    Result is ordered by ``ts DESC`` (most recent first). ``limit`` is
    capped at 1000 to keep the dashboard polling cheap.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if lane is not None:
        clauses.append("lane = ?")
        params.append(lane)
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if case_id is not None:
        clauses.append("case_id = ?")
        params.append(case_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if since_ts is not None:
        clauses.append("ts >= ?")
        params.append(since_ts)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    capped = max(1, min(int(limit), 1000))

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, lane, layer, target, case_id, status, ts, elapsed_ms, "
            "       meta_json, selector_hits_json, created_at "
            f"FROM health_beacons{where} "
            "ORDER BY ts DESC LIMIT ?",
            (*params, capped),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row | tuple) -> dict:
    """Decode one health_beacons row into a JSON-friendly dict."""
    (bid, lane, layer, target, case_id, status, ts,
     elapsed_ms, meta_json, selector_hits_json, created_at) = row
    return {
        "id": int(bid),
        "lane": lane,
        "layer": layer,
        "target": target,
        "case_id": case_id,
        "status": status,
        "ts": float(ts),
        "elapsed_ms": int(elapsed_ms) if elapsed_ms is not None else None,
        "meta": json.loads(meta_json) if meta_json else {},
        "dom_selector_hits": json.loads(selector_hits_json) if selector_hits_json else None,
        "created_at": float(created_at),
    }


# ---------------------------------------------------------------------------
# Aggregation: matrix view (HEALTH-MATRIX §4.2)
# ---------------------------------------------------------------------------

# Tier registry — which targets are S0 / S1 / D0 / D1 / etc. The
# canonical source is ``Docs/stability/DESKTOP-PRODUCT-MATRIX.md`` +
# ``SITE-TIER-MATRIX.md``. We replicate the **plane requirement** rule
# here so the color computation can be standalone (no cross-module
# call). Targets not in this map default to tier=None, plane_required=1
# (i.e. no multi-plane gate).
_TARGET_TIER_GATE: dict[str, tuple[str, int]] = {
    # Browser S0
    "chatgpt": ("S0", 2),
    "claude_ai": ("S0", 2),
    "gemini": ("S0", 2),
    # Browser S1
    "google_ai_studio": ("S1", 2),
    "perplexity": ("S1", 2),
    # Desktop D0
    "claude_desktop": ("D0", 2),
    "cursor": ("D0", 2),
    "claude_code": ("D0", 2),
    # Desktop D1
    "windsurf": ("D1", 1),
    "codex_cli": ("D1", 1),
    "gemini_cli": ("D1", 1),
    # Desktop D2
    "chatgpt_desktop": ("D2", 1),
    "copilot": ("D2", 1),
}


def compute_matrix(
    *,
    window_hours: float = 24.0,
    now: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Build the cross-lane matrix view (HEALTH-MATRIX §4.2).

    Iterates the last ``window_hours`` of beacons and groups by
    (lane, target). For each target produces:
        - tier, plane_count, last_pass_ts
        - pass / fail / skip / degraded counts
        - pass_rate_24h (assumed-window proportion of pass / total)
        - case_breakdown — latest status per case_id
        - color — green / yellow / red / grey per HEALTH-MATRIX §5.1

    The lane-level color is the max severity across its targets.
    Empty lanes degrade to ``grey`` (health_unknown).
    """
    server_now = now if now is not None else time.time()
    since = server_now - window_hours * 3600.0

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT lane, layer, target, case_id, status, ts "
            "FROM health_beacons WHERE ts >= ? ORDER BY ts DESC",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    # Group rows by (lane, target).
    by_target: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        key = (row[0], row[2])
        by_target.setdefault(key, []).append(row)

    lanes: dict[str, dict] = {ln: {"targets": {}, "color": "grey"} for ln in LANES}

    for (lane, target), target_rows in by_target.items():
        target_view = _summarise_target(lane, target, target_rows, server_now)
        # Defensive: unknown-lane rows are dropped from the matrix
        # rather than crashing the dashboard.
        if lane in lanes:
            lanes[lane]["targets"][target] = target_view

    # Roll lane colors up from targets.
    for lane in LANES:
        target_colors = [t["color"] for t in lanes[lane]["targets"].values()]
        lanes[lane]["color"] = _max_severity(target_colors) if target_colors else "grey"

    return {
        "lanes": lanes,
        "computed_at": server_now,
        "window_hours": window_hours,
        "schema_version": 1,
    }


def _summarise_target(lane: str, target: str, rows: list[tuple], server_now: float) -> dict:
    """Reduce one target's rows in the window into a matrix entry."""
    tier, plane_required = _TARGET_TIER_GATE.get(target, (None, 1))
    distinct_planes = {row[1] for row in rows if row[4] == "pass"}  # layer of PASS beacons

    counts = {"pass": 0, "fail": 0, "skip": 0, "degraded": 0, "infra_error": 0}
    last_pass_ts: Optional[float] = None
    case_latest: dict[str, dict] = {}  # case_id -> {status, ts}
    last_fail_ts_d0: Optional[float] = None  # 6h zero-pass detection

    for (_lane, _layer, _target, case_id, status, ts) in rows:
        counts[status] = counts.get(status, 0) + 1
        if status == "pass" and (last_pass_ts is None or ts > last_pass_ts):
            last_pass_ts = float(ts)
        if status == "fail" and (last_fail_ts_d0 is None or ts > last_fail_ts_d0):
            last_fail_ts_d0 = float(ts)
        if case_id and case_id not in case_latest:
            # rows are pre-sorted by ts DESC so the first hit is latest
            case_latest[case_id] = {"status": status, "ts": float(ts)}

    total = sum(counts.values()) or 1
    pass_rate = counts["pass"] / total if total > 0 else 0.0
    plane_count = len(distinct_planes)
    color = _compute_color(
        tier=tier,
        plane_count=plane_count,
        plane_required=plane_required,
        counts=counts,
        last_pass_ts=last_pass_ts,
        pass_rate=pass_rate,
        server_now=server_now,
    )

    return {
        "tier": tier,
        "plane_count": plane_count,
        "plane_required": plane_required,
        "last_pass_ts": last_pass_ts,
        "pass_count_24h": counts["pass"],
        "fail_count_24h": counts["fail"],
        "skip_count_24h": counts["skip"],
        "degraded_count_24h": counts["degraded"],
        "infra_error_count_24h": counts["infra_error"],
        "pass_rate_24h": round(pass_rate, 4),
        "case_breakdown": case_latest,
        "color": color,
    }


def _compute_color(
    *,
    tier: Optional[str],
    plane_count: int,
    plane_required: int,
    counts: dict[str, int],
    last_pass_ts: Optional[float],
    pass_rate: float,
    server_now: float,
) -> str:
    """Map status counts + plane redundancy to one of green/yellow/red/grey.

    Rules (priority top-to-bottom) per HEALTH-MATRIX §5.1:

    - RED if any of:
        - D0/S0 target with plane_count < plane_required
        - ≥3 fail beacons in window (for D0/S0)
        - last pass > 6h ago AND we have any fail / degraded
    - YELLOW if any of:
        - 1-2 fail beacons in window (D0/S0)
        - pass_rate < 0.80 (with at least 1 fail or degraded)
        - ≥1 degraded beacon (any tier)
    - GREEN otherwise (pass_rate ≥ 0.90 + plane_count ≥ required)
    - GREY if no beacons in window (caller passes counts all zero)
    """
    total = sum(counts.values())
    if total == 0:
        return "grey"

    is_critical_tier = tier in ("S0", "D0")
    fails = counts.get("fail", 0)
    degraded = counts.get("degraded", 0)
    six_hours_ago = server_now - 6 * 3600.0
    stale_pass = last_pass_ts is None or last_pass_ts < six_hours_ago

    if is_critical_tier and plane_count < plane_required:
        return "red"
    if is_critical_tier and fails >= 3:
        return "red"
    if stale_pass and (fails > 0 or degraded > 0):
        return "red"

    if is_critical_tier and fails >= 1:
        return "yellow"
    if pass_rate < 0.80 and (fails > 0 or degraded > 0):
        return "yellow"
    if degraded >= 1:
        return "yellow"

    if pass_rate >= 0.90:
        return "green"
    # Mid band: pass but no margin (e.g. all skips). Treat as grey
    # rather than green to flag attention.
    return "grey"


def _max_severity(colors: list[str]) -> str:
    """Return the most severe color from a list (red > yellow > grey > green)."""
    best = -1
    result = "green"
    for c in colors:
        try:
            idx = _COLOR_SEVERITY.index(c)
        except ValueError:
            continue
        if idx > best:
            best = idx
            result = c
    return result


# ---------------------------------------------------------------------------
# Aggregation: timeseries view (HEALTH-MATRIX §4.3)
# ---------------------------------------------------------------------------

def compute_timeseries(
    *,
    lane: str,
    target: str,
    hours: float = 24.0,
    bucket_s: int = 3600,
    now: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Return per-bucket counts of beacons for one (lane, target).

    The bucketing is aligned to wall-clock seconds, not since-now, so
    the same query at different times produces the same buckets (useful
    for client-side caching / chart x-axis).
    """
    server_now = now if now is not None else time.time()
    since = server_now - hours * 3600.0

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT ts, status FROM health_beacons "
            "WHERE lane = ? AND target = ? AND ts >= ? "
            "ORDER BY ts ASC",
            (lane, target, since),
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[float, dict[str, int]] = {}
    for (ts, status) in rows:
        bucket_ts = float(int(ts // bucket_s) * bucket_s)
        bucket = buckets.setdefault(
            bucket_ts,
            {"ts": bucket_ts, "pass": 0, "fail": 0, "skip": 0,
             "degraded": 0, "infra_error": 0},
        )
        bucket[status] = bucket.get(status, 0) + 1

    return {
        "lane": lane,
        "target": target,
        "buckets": sorted(buckets.values(), key=lambda b: b["ts"]),
        "bucket_size_s": int(bucket_s),
        "window_hours": float(hours),
    }


# ---------------------------------------------------------------------------
# Retention helper (called by retention.py daily sweep)
# ---------------------------------------------------------------------------

def emit_beacon(
    *,
    lane: str,
    layer: str,
    target: str,
    status: str,
    case_id: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    meta: Optional[dict] = None,
    dom_selector_hits: Optional[dict] = None,
    ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Convenience wrapper around ``record_beacon`` for lane hook sites.

    Differences vs ``record_beacon``:

    - Constructs the dataclass from kwargs (saves boilerplate at hook
      sites in ``pce_cli_wrapper`` / ``pce_mcp_proxy`` / desktop
      drivers).
    - Defaults ``ts`` to ``time.time()`` if not provided (lane code
      rarely needs to control the timestamp).
    - **Best-effort**: swallows all exceptions, returns ``None`` on
      failure or rejection. This is the canonical contract for the lane
      hook side per HEALTH-MATRIX §3.2 — "失败重试 ... silently dropped
      + log warning (优先保住产品体验, 健康度可丢)". The host's primary
      function must never break because of a health beacon failure.

    Returns the rowid on success, ``None`` on any failure / rejection.
    """
    try:
        beacon = HealthBeacon(
            lane=lane,
            layer=layer,
            target=target,
            status=status,
            ts=ts if ts is not None else time.time(),
            case_id=case_id,
            elapsed_ms=elapsed_ms,
            meta=dict(meta or {}),
            dom_selector_hits=dom_selector_hits,
        )
        result = record_beacon(beacon, db_path=db_path)
        if isinstance(result, BeaconRejection):
            logger.debug(
                "emit_beacon rejected",
                extra={"event": "health.emit.reject", "pce_fields": {
                    "code": result.code, "lane": lane, "target": target,
                }},
            )
            return None
        return int(result)
    except Exception as exc:  # pragma: no cover — defensive guard
        logger.debug(
            "emit_beacon swallowed exception",
            extra={"event": "health.emit.error", "pce_fields": {
                "lane": lane, "target": target, "error": repr(exc),
            }},
        )
        return None


def purge_old_beacons(
    *,
    retention_days: int = 90,
    now: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Delete beacons older than ``retention_days``. Returns rows deleted.

    Default 90 days matches HEALTH-MATRIX §2.3 retention policy. Called
    on a daily sweep — failures are not raised so the regular retention
    loop never crashes due to a missing health table on a freshly-
    migrated DB.
    """
    cutoff = (now if now is not None else time.time()) - retention_days * 86400.0
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM health_beacons WHERE ts < ?", (cutoff,))
        conn.commit()
        return int(cur.rowcount or 0)
    except sqlite3.OperationalError:
        # Table not present yet (running on a pre-0013 DB). Treat as
        # zero rows removed.
        logger.debug("purge_old_beacons: health_beacons table missing")
        return 0
    finally:
        conn.close()
