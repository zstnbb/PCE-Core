# SPDX-License-Identifier: Apache-2.0
"""tools/dump_evidence.py — snapshot live-evidence for a P0 scenario.

Used by D-Day B3/B5/B6/B7/B8/B9/B10 blocks to convert raw DB rows
into:

    1. {scenario}.evidence.json — machine-readable summary
       (raw_captures row IDs, pair_ids, model_names, source_id
       histogram, byte counts, sample bodies)
    2. {scenario}.snapshot.db — sqlite slice containing only the rows
       relevant to the scenario within the time window
    3. {scenario}.handoff.md — partial handoff template with the
       summary numbers already filled in

Filtering policy: scenario_id → (source_id allow-list × host
allow-list × tool_family allow-list × time-window). All three filters
are OR within each list and AND across lists.

Usage::

    python tools/dump_evidence.py \\
        --scenario f6_p6_claude_code_cli \\
        --window-s 600 \\
        --out Docs/handoff/_evidence_D_DAY/

    python tools/dump_evidence.py \\
        --scenario f6_p6 --since 1778900000   # absolute baseline epoch

Output paths are derived from --out + scenario id.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ensure repo root is on sys.path so pce_core.config is importable when
# the tool is invoked from any CWD (e.g. via cron, scheduled task).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ----------------------------------------------------------------------
# Scenario → DB filter catalogue
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class LegFilter:
    """One capture lane within a scenario.

    A row counts toward this leg if its source_id matches AND
    (host_patterns is empty OR host matches any of them, possibly
    with a `:443`-style port suffix).
    """

    leg: str               # display label e.g. "L1", "A2"
    source_id: str         # raw_captures.source_id
    host_patterns: tuple[str, ...] = ()  # empty = match any host


@dataclass(frozen=True)
class ScenarioFilter:
    scenario_id: str
    label: str
    legs: tuple[LegFilter, ...]
    tool_families: tuple[str, ...] = ()

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted({lf.source_id for lf in self.legs}))

    @property
    def hosts(self) -> tuple[str, ...]:
        out: set[str] = set()
        for lf in self.legs:
            out.update(lf.host_patterns)
        return tuple(sorted(out))

    @property
    def expected_legs(self) -> tuple[str, ...]:
        return tuple(lf.leg for lf in self.legs)


def _net(host: str) -> tuple[str, ...]:
    """Real DNS hosts may appear bare or with a `:443` port suffix in
    sslkeylog rows. Generate both variants.
    """
    return (host, f"{host}:443")


SCENARIOS: dict[str, ScenarioFilter] = {
    "f4_p1_claude_desktop": ScenarioFilter(
        "f4_p1_claude_desktop",
        "Claude Desktop (Win MSIX/Squirrel + macOS)",
        legs=(
            LegFilter("L1",  "proxy-default",                  _net("api.anthropic.com") + _net("claude.ai")),
            LegFilter("L3g", "l3g-local-persistence-default",  ("chromium-indexeddb", "local-config", "local-agent-mode")),
            LegFilter("L3f", "mcp-default",                    ()),  # any host
            LegFilter("L3f-proxy", "mcp-proxy-default",        ()),
            LegFilter("A2",  "sslkeylog-default",              _net("api.anthropic.com") + _net("claude.ai")),
        ),
        tool_families=("api-direct", "anthropic-web", "claude-desktop-code"),
    ),
    "f4_p2_chatgpt_desktop": ScenarioFilter(
        "f4_p2_chatgpt_desktop",
        "ChatGPT Desktop (Win MSIX + macOS)",
        legs=(
            LegFilter("L1", "proxy-default",     _net("chatgpt.com") + _net("chat.openai.com")),
            LegFilter("A2", "sslkeylog-default", _net("chatgpt.com") + _net("chat.openai.com")),
        ),
        tool_families=("openai-web",),
    ),
    "f5_p3_cursor": ScenarioFilter(
        "f5_p3_cursor",
        "Cursor (IDE-class, gRPC-web protobuf)",
        legs=(
            LegFilter("L1",  "proxy-default",                  _net("api2.cursor.sh") + _net("api.cursor.sh") + _net("api3.cursor.sh")),
            LegFilter("L3g", "l3g-local-persistence-default",  ("local-cursor-chat",)),
            LegFilter("L3f", "mcp-default",                    ()),
            LegFilter("A2",  "sslkeylog-default",              _net("api2.cursor.sh") + _net("api.cursor.sh") + _net("api3.cursor.sh")),
        ),
        tool_families=("cursor-chat-l3g",),
    ),
    "f5_p4_windsurf": ScenarioFilter(
        "f5_p4_windsurf",
        "Windsurf (IDE-class MCP-aware)",
        legs=(
            LegFilter("L1",  "proxy-default",     _net("server.codeium.com") + _net("server.self-serve.windsurf.com")),
            LegFilter("L3f", "mcp-default",       ()),
            LegFilter("A2",  "sslkeylog-default", _net("server.codeium.com") + _net("server.self-serve.windsurf.com")),
        ),
    ),
    "f5_p5_github_copilot": ScenarioFilter(
        "f5_p5_github_copilot",
        "GitHub Copilot (VS Code)",
        legs=(
            LegFilter("L1",  "proxy-default",     _net("api.githubcopilot.com") + _net("copilot-proxy.githubusercontent.com")),
            LegFilter("L3f", "mcp-default",       ()),
            LegFilter("A2",  "sslkeylog-default", _net("api.githubcopilot.com") + _net("copilot-proxy.githubusercontent.com")),
        ),
    ),
    "f6_p6_claude_code_cli": ScenarioFilter(
        "f6_p6_claude_code_cli",
        "Claude Code CLI (@anthropic-ai/claude-code)",
        legs=(
            LegFilter("L1",  "proxy-default",                  _net("api.anthropic.com")),
            LegFilter("L3g", "l3g-local-persistence-default",  ("local-agent-mode", "local-claude-cli")),
            LegFilter("L3h", "l3h-cli-wrapper-default",        ("cli-wrapper",)),
        ),
        tool_families=("cowork-local-agent", "claude-desktop-code"),
    ),
    "f6_p7_codex_cli": ScenarioFilter(
        "f6_p7_codex_cli",
        "Codex CLI (OpenAI)",
        legs=(
            LegFilter("L1",  "proxy-default",                  _net("chatgpt.com") + _net("api.openai.com")),
            LegFilter("L3g", "l3g-local-persistence-default",  ("local-codex-cli",)),
            LegFilter("L3h", "l3h-cli-wrapper-default",        ("cli-wrapper",)),
        ),
        tool_families=("codex-cli-l3g",),
    ),
    "f6_p8_gemini_cli": ScenarioFilter(
        "f6_p8_gemini_cli",
        "Gemini CLI (Google)",
        legs=(
            LegFilter("L1",  "proxy-default",                  _net("generativelanguage.googleapis.com") + _net("cloudcode-pa.googleapis.com")),
            LegFilter("L3g", "l3g-local-persistence-default",  ("local-gemini-cli",)),
            LegFilter("L3h", "l3h-cli-wrapper-default",        ("cli-wrapper",)),
            LegFilter("A2",  "sslkeylog-default",              _net("generativelanguage.googleapis.com") + _net("cloudcode-pa.googleapis.com")),
        ),
        tool_families=("gemini-cli-l3g",),
    ),
}


# Short alias → full scenario id
SCENARIO_ALIASES = {
    "f4_p1": "f4_p1_claude_desktop",
    "f4_p2": "f4_p2_chatgpt_desktop",
    "f5_p3": "f5_p3_cursor",
    "f5_p4": "f5_p4_windsurf",
    "f5_p5": "f5_p5_github_copilot",
    "f6_p6": "f6_p6_claude_code_cli",
    "f6_p7": "f6_p7_codex_cli",
    "f6_p8": "f6_p8_gemini_cli",
}


def resolve_scenario(name: str) -> ScenarioFilter:
    name = name.strip().lower()
    if name in SCENARIOS:
        return SCENARIOS[name]
    if name in SCENARIO_ALIASES:
        return SCENARIOS[SCENARIO_ALIASES[name]]
    raise SystemExit(
        f"unknown scenario: {name!r}\n"
        f"  full ids: {list(SCENARIOS)}\n"
        f"  aliases:  {list(SCENARIO_ALIASES)}"
    )


# ----------------------------------------------------------------------
# Query layer
# ----------------------------------------------------------------------


@dataclass
class EvidenceReport:
    scenario_id: str
    label: str
    expected_legs: tuple[str, ...]
    db_path: str
    window_start_epoch: float
    window_end_epoch: float
    raw_captures_count: int = 0
    raw_captures_by_source: dict[str, int] = field(default_factory=dict)
    raw_captures_by_host: dict[str, int] = field(default_factory=dict)
    sample_pair_ids: list[str] = field(default_factory=list)
    sessions_count: int = 0
    sessions_by_tool_family: dict[str, int] = field(default_factory=dict)
    sample_model_names: list[str] = field(default_factory=list)
    messages_count: int = 0
    beacons_by_layer: dict[str, int] = field(default_factory=dict)
    legs_detected: list[str] = field(default_factory=list)
    legs_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _placeholders(values) -> str:
    """Build a `?,?,?` placeholder string for a fixed-length IN clause."""
    return ",".join("?" * len(tuple(values)))


def _build_leg_where(legs: tuple[LegFilter, ...]) -> tuple[str, list]:
    """Build a SQL OR-of-legs clause + the parameter list.

    Each leg becomes ``(source_id = ? AND host IN (?, ?, …))`` or
    ``(source_id = ?)`` when host_patterns is empty (match-any).
    Legs are OR'd together. The caller adds the time window AND on
    top.
    """
    parts: list[str] = []
    params: list = []
    for lf in legs:
        if lf.host_patterns:
            parts.append(
                f"(source_id = ? AND host IN ({_placeholders(lf.host_patterns)}))"
            )
            params.append(lf.source_id)
            params.extend(lf.host_patterns)
        else:
            parts.append("(source_id = ?)")
            params.append(lf.source_id)
    return "(" + " OR ".join(parts) + ")", params


def gather_report(
    conn: sqlite3.Connection,
    scenario: ScenarioFilter,
    window_start: float,
    window_end: float,
    db_path: str,
) -> EvidenceReport:
    report = EvidenceReport(
        scenario_id=scenario.scenario_id,
        label=scenario.label,
        expected_legs=scenario.expected_legs,
        db_path=db_path,
        window_start_epoch=window_start,
        window_end_epoch=window_end,
    )
    c = conn.cursor()

    # --- raw_captures ---------------------------------------------------
    leg_where, leg_params = _build_leg_where(scenario.legs)
    where = f"{leg_where} AND created_at BETWEEN ? AND ?"
    params = leg_params + [window_start, window_end]
    report.raw_captures_count = c.execute(
        f"SELECT COUNT(*) FROM raw_captures WHERE {where}", params
    ).fetchone()[0]

    for src, count in c.execute(
        f"SELECT source_id, COUNT(*) FROM raw_captures WHERE {where} "
        f"GROUP BY source_id ORDER BY 2 DESC",
        params,
    ):
        report.raw_captures_by_source[src] = count

    for host, count in c.execute(
        f"SELECT host, COUNT(*) FROM raw_captures WHERE {where} "
        f"GROUP BY host ORDER BY 2 DESC",
        params,
    ):
        report.raw_captures_by_host[host] = count

    report.sample_pair_ids = [
        row[0]
        for row in c.execute(
            f"SELECT DISTINCT pair_id FROM raw_captures WHERE {where} "
            f"AND pair_id IS NOT NULL LIMIT 10",
            params,
        ).fetchall()
    ]

    # --- sessions -------------------------------------------------------
    if scenario.tool_families:
        tf_ph = _placeholders(scenario.tool_families)
        s_params = list(scenario.tool_families) + [window_start, window_end]
        s_where = (
            f"tool_family IN ({tf_ph}) AND started_at BETWEEN ? AND ?"
        )
        report.sessions_count = c.execute(
            f"SELECT COUNT(*) FROM sessions WHERE {s_where}", s_params
        ).fetchone()[0]
        for tf, count in c.execute(
            f"SELECT tool_family, COUNT(*) FROM sessions WHERE {s_where} "
            f"GROUP BY tool_family ORDER BY 2 DESC",
            s_params,
        ):
            report.sessions_by_tool_family[tf] = count
        # sessions.model_names is a JSON array column. We harvest the
        # raw JSON strings; downstream readers can parse them.
        report.sample_model_names = [
            row[0]
            for row in c.execute(
                f"SELECT DISTINCT model_names FROM sessions WHERE {s_where} "
                f"AND model_names IS NOT NULL AND model_names != '[]' LIMIT 10",
                s_params,
            ).fetchall()
        ]

    # --- messages -------------------------------------------------------
    # Count messages whose capture_pair_id lives within the window's
    # raw_captures slice.
    report.messages_count = c.execute(
        f"SELECT COUNT(*) FROM messages "
        f"WHERE capture_pair_id IN ("
        f"  SELECT pair_id FROM raw_captures WHERE {where} "
        f"  AND pair_id IS NOT NULL"
        f")",
        params,
    ).fetchone()[0]

    # --- health_beacons (any layer, target matches scenario short id) ---
    short = scenario.scenario_id.replace("f4_p1_claude_desktop", "claude_desktop")
    short = short.replace("f4_p2_chatgpt_desktop", "chatgpt_desktop")
    short = short.replace("f5_p3_cursor", "cursor")
    short = short.replace("f5_p4_windsurf", "windsurf")
    short = short.replace("f5_p5_github_copilot", "github_copilot")
    short = short.replace("f6_p6_claude_code_cli", "claude_code")
    short = short.replace("f6_p7_codex_cli", "codex_cli")
    short = short.replace("f6_p8_gemini_cli", "gemini_cli")
    for layer, count in c.execute(
        "SELECT layer, COUNT(*) FROM health_beacons "
        "WHERE target = ? AND status = 'pass' "
        "AND ts BETWEEN ? AND ? GROUP BY layer",
        (short, window_start, window_end),
    ):
        report.beacons_by_layer[layer] = count

    # --- leg detection --------------------------------------------------
    # Map source_id present in window → leg label.
    source_to_leg = {
        "proxy-default": "L1",
        "browser-extension-default": "L3a",
        "cdp-embedded": "L3d",
        "l3g-local-persistence-default": "L3g",
        "mcp-default": "L3f",
        "mcp-proxy-default": "L3f",
        "l3h-cli-wrapper-default": "L3h",
        "sslkeylog-default": "A2",
    }
    detected = set()
    for src in report.raw_captures_by_source:
        if src in source_to_leg:
            detected.add(source_to_leg[src])
    report.legs_detected = sorted(detected)
    report.legs_missing = [
        leg for leg in scenario.expected_legs if leg not in detected
    ]
    return report


# ----------------------------------------------------------------------
# Snapshot — sqlite slice
# ----------------------------------------------------------------------


def write_snapshot(
    src_db: str,
    dst_db: Path,
    scenario: ScenarioFilter,
    window_start: float,
    window_end: float,
    body_bytes_cap: int = 2048,
    strip_bodies: bool = True,
) -> int:
    """Copy only the rows relevant to (scenario, window) into dst_db.

    Returns the number of raw_captures rows copied.

    ``body_bytes_cap`` truncates ``raw_captures.body_text_or_json`` to
    at most this many UTF-8 bytes per row. 0 = keep full body (can
    produce 100+ MB snapshots for proxy-default rows holding SSE
    streams). Default 2048 keeps the start of every body (enough to
    show shape + redaction worked) while staying well under per-row
    limits. The truncation is one-way — the original DB is untouched.
    """
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if dst_db.exists():
        dst_db.unlink()
    # Open dst first so its schema is fresh.
    src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst_db))

    src_c = src_conn.cursor()
    dst_c = dst_conn.cursor()

    # Replicate schema for the 4 tables we care about.
    for table in ("raw_captures", "sessions", "messages", "health_beacons"):
        ddl = src_c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        if ddl and ddl[0]:
            dst_c.execute(ddl[0])

    leg_where, leg_params = _build_leg_where(scenario.legs)
    rc_where = f"{leg_where} AND created_at BETWEEN ? AND ?"
    rc_params = leg_params + [window_start, window_end]

    # Resolve the index of body_text_or_json (column 13 per schema
    # inspection 2026-05-18 but harden against schema drift).
    rc_col_names = [
        info[1]
        for info in src_c.execute("PRAGMA table_info(raw_captures)").fetchall()
    ]
    try:
        body_col_idx = rc_col_names.index("body_text_or_json")
    except ValueError:
        body_col_idx = None

    rows = src_c.execute(
        f"SELECT * FROM raw_captures WHERE {rc_where}", rc_params
    ).fetchall()
    if rows and strip_bodies and body_col_idx is not None:
        # Privacy-preserving: drop all body bytes entirely. The
        # operator's source DB retains them; this snapshot proves
        # row existence + metadata (host, path, source_id, pair_id,
        # status_code, etc.) without leaking any user prompt or AI
        # response content.
        marker = "<stripped by dump_evidence — see source DB>"
        rows_to_insert = []
        for row in rows:
            new_row = list(row)
            new_row[body_col_idx] = marker if row[body_col_idx] else None
            rows_to_insert.append(tuple(new_row))
        col_count = len(rows_to_insert[0])
        dst_c.executemany(
            f"INSERT INTO raw_captures VALUES ({','.join(['?'] * col_count)})",
            rows_to_insert,
        )
    elif rows:
        if body_bytes_cap and body_col_idx is not None:
            capped: list[tuple] = []
            marker = f"\n…[truncated by dump_evidence cap={body_bytes_cap}]"
            for row in rows:
                body = row[body_col_idx]
                if isinstance(body, str) and len(body.encode("utf-8")) > body_bytes_cap:
                    # Truncate at byte boundary, then decode back; if
                    # we land mid-codepoint, shrink until clean.
                    raw = body.encode("utf-8")[:body_bytes_cap]
                    while raw:
                        try:
                            text = raw.decode("utf-8")
                            break
                        except UnicodeDecodeError:
                            raw = raw[:-1]
                    else:
                        text = ""
                    new_row = list(row)
                    new_row[body_col_idx] = text + marker
                    capped.append(tuple(new_row))
                else:
                    capped.append(row)
            rows_to_insert = capped
        else:
            rows_to_insert = rows

        col_count = len(rows_to_insert[0])
        dst_c.executemany(
            f"INSERT INTO raw_captures VALUES ({','.join(['?'] * col_count)})",
            rows_to_insert,
        )
    pair_ids = {r[5] for r in rows if r[5] is not None}  # column 5 = pair_id

    # Messages joined by capture_pair_id.
    if pair_ids:
        ph = ",".join("?" * len(pair_ids))
        m_rows = src_c.execute(
            f"SELECT * FROM messages WHERE capture_pair_id IN ({ph})",
            tuple(pair_ids),
        ).fetchall()
        if m_rows:
            mc = len(m_rows[0])
            dst_c.executemany(
                f"INSERT INTO messages VALUES ({','.join(['?'] * mc)})",
                m_rows,
            )

    # Sessions (best effort — match tool_family + window).
    if scenario.tool_families:
        tf_ph = _placeholders(scenario.tool_families)
        s_rows = src_c.execute(
            f"SELECT * FROM sessions "
            f"WHERE tool_family IN ({tf_ph}) "
            f"AND started_at BETWEEN ? AND ?",
            list(scenario.tool_families) + [window_start, window_end],
        ).fetchall()
        if s_rows:
            sc = len(s_rows[0])
            dst_c.executemany(
                f"INSERT INTO sessions VALUES ({','.join(['?'] * sc)})",
                s_rows,
            )

    # Beacons.
    b_rows = src_c.execute(
        "SELECT * FROM health_beacons WHERE ts BETWEEN ? AND ?",
        (window_start, window_end),
    ).fetchall()
    if b_rows:
        bc = len(b_rows[0])
        dst_c.executemany(
            f"INSERT INTO health_beacons VALUES ({','.join(['?'] * bc)})",
            b_rows,
        )

    dst_conn.commit()
    # VACUUM so the on-disk file shrinks to the actual data size (we
    # may have inserted truncated bodies into freshly-CREATEd tables
    # that retain their default page reservations otherwise).
    dst_conn.execute("VACUUM")
    dst_conn.close()
    src_conn.close()
    return len(rows)


# ----------------------------------------------------------------------
# Handoff template
# ----------------------------------------------------------------------


HANDOFF_TEMPLATE = """\
---
title: "D-Day Evidence — {scenario_id}"
status: {status}
date: {today}
session: D-Day {block}
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - {snapshot_rel}
  - {evidence_rel}
---

# {label}

## 1. TL;DR

In a {window_minutes:.1f}-minute window ending {window_end_iso}, the
PCE pipeline captured **{raw_captures_count} raw_captures rows** for
this scenario across {detected_count} of {expected_count} expected legs.

Legs detected: {legs_detected_pretty}
Legs missing:  {legs_missing_pretty}

## 2. Evidence shape

### 2.1 raw_captures by source_id

{src_table}

### 2.2 raw_captures by host

{host_table}

### 2.3 Sample pair_ids (first 10)

{pair_id_list}

### 2.4 sessions

- total: {sessions_count}
- by tool_family: {sessions_tf_pretty}
- sample model_names: {model_names_pretty}

### 2.5 messages

- total (joined via capture_pair_id): {messages_count}

### 2.6 health_beacons (pass, in window)

{beacons_table}

## 3. Snapshot

- {snapshot_rel} — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN {window_start} AND {window_end}
  AND source_id IN ({source_ids_quoted})
  AND host IN ({hosts_quoted})
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **{scenario_id}**:

- Evidence-backed legs this run: {legs_detected_count}
- Remaining gap to STRICT ≥3: {legs_gap}

## 5. Acceptance

- [{accept_box}] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under {snapshot_rel}

"""


def render_handoff(
    report: EvidenceReport,
    block: str,
    snapshot_rel: str,
    evidence_rel: str,
) -> str:
    def _table(d: dict[str, int]) -> str:
        if not d:
            return "_(none)_"
        return "\n".join(f"- `{k}`: {v}" for k, v in d.items())

    today = time.strftime("%Y-%m-%d")
    window_end_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.gmtime(report.window_end_epoch)
    )
    window_minutes = max(
        0.0, (report.window_end_epoch - report.window_start_epoch) / 60.0
    )
    detected = report.legs_detected
    expected = report.expected_legs
    legs_gap = max(0, 3 - len(detected))

    legs_missing_pretty = (
        ", ".join(report.legs_missing) if report.legs_missing else "_(none — all expected legs present)_"
    )
    legs_detected_pretty = ", ".join(detected) if detected else "_(none)_"
    model_names_pretty = (
        ", ".join(report.sample_model_names)
        if report.sample_model_names
        else "_(none)_"
    )
    sessions_tf_pretty = (
        ", ".join(f"{k}={v}" for k, v in report.sessions_by_tool_family.items())
        if report.sessions_by_tool_family
        else "_(none)_"
    )
    pair_id_list = (
        "\n".join(f"- `{pid}`" for pid in report.sample_pair_ids)
        if report.sample_pair_ids
        else "_(no paired rows in window)_"
    )

    status = "PASS" if report.raw_captures_count > 0 else "EMPTY_WINDOW"
    accept_box = "x" if report.raw_captures_count > 0 else " "

    source_ids_quoted = ", ".join(
        f"'{s}'" for s in SCENARIOS[report.scenario_id].source_ids
    )
    hosts_quoted = ", ".join(
        f"'{h}'" for h in SCENARIOS[report.scenario_id].hosts
    )

    return HANDOFF_TEMPLATE.format(
        scenario_id=report.scenario_id,
        label=report.label,
        status=status,
        today=today,
        block=block,
        snapshot_rel=snapshot_rel,
        evidence_rel=evidence_rel,
        window_minutes=window_minutes,
        window_end_iso=window_end_iso,
        raw_captures_count=report.raw_captures_count,
        detected_count=len(detected),
        expected_count=len(expected),
        legs_detected_pretty=legs_detected_pretty,
        legs_missing_pretty=legs_missing_pretty,
        legs_detected_count=len(detected),
        legs_gap=legs_gap,
        src_table=_table(report.raw_captures_by_source),
        host_table=_table(report.raw_captures_by_host),
        pair_id_list=pair_id_list,
        sessions_count=report.sessions_count,
        sessions_tf_pretty=sessions_tf_pretty,
        model_names_pretty=model_names_pretty,
        messages_count=report.messages_count,
        beacons_table=_table(report.beacons_by_layer),
        window_start=int(report.window_start_epoch),
        window_end=int(report.window_end_epoch),
        source_ids_quoted=source_ids_quoted,
        hosts_quoted=hosts_quoted,
        accept_box=accept_box,
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--scenario",
        required=True,
        help="Scenario id (full or alias, comma-separated for multi).",
    )
    p.add_argument(
        "--db",
        default=None,
        help="DB path (defaults to pce_core.config.DB_PATH).",
    )
    p.add_argument(
        "--window-s",
        type=float,
        default=600.0,
        help="Window length in seconds, ending at --until (default 600).",
    )
    p.add_argument(
        "--since",
        type=float,
        default=None,
        help="Absolute window start epoch seconds. Overrides --window-s.",
    )
    p.add_argument(
        "--until",
        type=float,
        default=None,
        help="Absolute window end epoch (default = now).",
    )
    p.add_argument(
        "--out",
        required=True,
        help="Output directory for {scenario}.{evidence.json,snapshot.db,handoff.md}.",
    )
    p.add_argument(
        "--block",
        default="B3",
        help="D-Day block label for the handoff frontmatter (B3 / B5 / B6 …).",
    )
    p.add_argument(
        "--body-bytes-cap",
        type=int,
        default=2048,
        help=(
            "Truncate raw_captures.body_text_or_json in the snapshot to "
            "this many UTF-8 bytes per row. 0 = keep full bodies "
            "(snapshots may exceed 100 MB on SSE-heavy scenarios). "
            "Ignored when --strip-bodies is enabled (default)."
        ),
    )
    p.add_argument(
        "--strip-bodies",
        dest="strip_bodies",
        action="store_true",
        default=True,
        help=(
            "Drop raw_captures.body_text_or_json entirely from the "
            "snapshot, replacing it with a fixed marker. Privacy "
            "default — production rows may contain user prompts."
        ),
    )
    p.add_argument(
        "--keep-bodies",
        dest="strip_bodies",
        action="store_false",
        help="Inverse of --strip-bodies; retain bodies subject to --body-bytes-cap.",
    )

    args = p.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.db is None:
        try:
            from pce_core.config import DB_PATH
            db_path = str(DB_PATH)
        except Exception as exc:
            print(f"ERROR: cannot import pce_core.config.DB_PATH: {exc}", file=sys.stderr)
            return 2
    else:
        db_path = args.db

    if not Path(db_path).exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 2

    until = args.until if args.until is not None else time.time()
    since = args.since if args.since is not None else (until - args.window_s)

    scenarios = [resolve_scenario(s) for s in args.scenario.split(",") if s.strip()]
    overall_rc = 0

    for scenario in scenarios:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            report = gather_report(conn, scenario, since, until, db_path)
        finally:
            conn.close()

        snapshot_path = out_dir / f"{scenario.scenario_id}.snapshot.db"
        evidence_path = out_dir / f"{scenario.scenario_id}.evidence.json"
        handoff_path = out_dir / f"{scenario.scenario_id}.handoff.md"

        rows_copied = write_snapshot(
            db_path,
            snapshot_path,
            scenario,
            since,
            until,
            body_bytes_cap=args.body_bytes_cap,
            strip_bodies=args.strip_bodies,
        )

        evidence_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        rel_snapshot = str(snapshot_path).replace("\\", "/")
        rel_evidence = str(evidence_path).replace("\\", "/")
        handoff_md = render_handoff(report, args.block, rel_snapshot, rel_evidence)
        handoff_path.write_text(handoff_md, encoding="utf-8")

        legs_str = "+".join(report.legs_detected) or "none"
        status = "PASS" if report.raw_captures_count > 0 else "EMPTY"
        print(
            f"[{status}] {scenario.scenario_id}: "
            f"rows={report.raw_captures_count} "
            f"sessions={report.sessions_count} "
            f"messages={report.messages_count} "
            f"legs=[{legs_str}] "
            f"missing=[{'+'.join(report.legs_missing) or 'none'}] "
            f"snapshot={rows_copied}rows"
        )

        if report.raw_captures_count == 0:
            overall_rc = 1

    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
