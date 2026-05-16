---
title: "P5.D.1 Phase A Mid-gate — Code Skeleton Closed (machine loop alive)"
status: PARTIAL_PASS
date: 2026-05-15
session: agent-driven, single-session
predecessor: HANDOFF-P5D1-KICKOFF-2026-05-14.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
artifacts:
  - 1 wave5 handoff (this file)
  - capture_supervisor package (~700 LOC) + 13 P0 scenarios.yaml
  - 4 W5 tools (render / check / auto-issue / repair_adapter ext)
  - 31 unit tests GREEN (test_capture_supervisor)
  - ADR-021 status: Adopted (this commit)
  - check_import_direction.py: capture_supervisor moved to OSS
  - REDUNDANCY-AUDIT-MATRIX §4.1 + §7.3 refreshed
  - **NO tag yet (waiting on Phase B 13/13 + W1/W4 live signoff)**
---

## TL;DR

P5.D.1 Phase A **code skeleton** closed in a single agent session
on 2026-05-15. Three goals at this gate:

| Goal | Acceptance | Status |
|---|---|---|
| **G2 Runtime** | `/api/v1/supervisor/status` exposes 13 scenarios with leg health, dedup metrics, Phase B markers | ✅ alive |
| **G3 Visibility** | `REDUNDANCY-MATRIX.svg` renders 13 cards nightly; `redundancy-degraded` auto-issue channel armed | ✅ alive |
| **G1 Coverage** | 13 P0 scenarios each ≥ 3 V-GREEN | ⏳ blocked on W1+W4 owner-side live signoff (Tier-α 10) and Phase B (Tier-β 3) |

The G1 gap is the **owner-side live evidence work** described in §3
below — it cannot be done by the agent harness alone, by design.

## What landed

### Wave 3 — Capture Supervisor v1 (ADR-021 Adopted)

```
pce_core/capture_supervisor/
├── __init__.py
├── dedup.py        — (pair_id, fingerprint) 30s sliding-window LRU
├── policy.py       — scenarios.yaml loader + invariant enforcement
├── status.py       — leg health → scenario state machine
├── api.py          — /api/v1/supervisor/* router
└── scenarios.yaml  — 13 P0 (5 Web + 8 Desktop), independent_basis enforced
```

Mounted at FastAPI lifespan in `pce_core/server.py`; exposes:

- `GET /api/v1/supervisor/status`
- `GET /api/v1/supervisor/scenario/{id}`
- `GET /api/v1/supervisor/degraded`
- `POST /api/v1/supervisor/legs/register` (Pro registration per ADR-021 §3.2)
- `GET /api/v1/supervisor/legs/registered`

ADR-021 status: Proposed → **Adopted (2026-05-15)**.
`scripts/check_import_direction.py` updated; CI green
(452 files clean per `python scripts/check_import_direction.py`).

### Wave 5 — Nightly Closure tooling

| Deliverable | Path |
|---|---|
| W5-T1 | `tools/render_redundancy_matrix.py` + initial `Docs/stability/REDUNDANCY-MATRIX.svg` (fallback render) |
| W5-T2 | `tools/check_redundancy_targets.py` + `tools/auto_issue_on_redundancy_degraded.py` + `.github/workflows/nightly-probe.yml` (`redundancy-matrix` job, 5 steps) |
| W5-T3 | `.github/ISSUE_TEMPLATE/redundancy-degraded.yml` |
| W5-T4 | `tools/repair_adapter.py --redundancy-degraded <scenario_id>` path + scenario→target map |
| W5-T5 | `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §4.1 sprint-state note + §7.3 cadence |
| W5-T6 | this file + W6/W7/W8 kickoffs (siblings under `Docs/handoff/`) |
| W5-T7 | `CHANGELOG.md` Phase A milestone (NOT a tag) |

### Tests

`tests/test_capture_supervisor.py` — 31 unit tests, all GREEN:

  - dedup    12  (LRU semantics, 5min bucket, thread safety)
  - policy    8  (load 13 scenarios, basis uniqueness, schema version)
  - status    5  (state machine + 24h window)
  - api       6  (endpoint shapes + Pro registration + degraded suppress)

Run:

```powershell
python -m pytest tests/test_capture_supervisor.py -v --tb=short
# 31 passed in ~0.5s
```

## What's NOT done (carry to next sessions)

### G1 — Tier-α (10 scenarios) live signoff

Requires owner at the keyboard. Each scenario needs ≥ 3 V-GREEN legs
proven by a real `raw_captures` row from the live product. Plan:

| Scenario | Legs to sign | Owner action |
|---|---|---|
| f1_chatgpt_web | L1 + L3a + L3d | open chatgpt.com, send 1 msg, snapshot SQLite |
| f1_claude_web | L1 + L3a + L3d | same on claude.ai |
| f1_gemini_web | L1 + L3a + L4a | same on gemini.google.com |
| f1_gas | L1 + L3a + L4a | same on aistudio.google.com |
| f1_grok_web | L1 + L3a + L4a | same on grok.com |
| f4_p1_claude_desktop | L1 + L3g + L3f | open Claude Desktop, send 1 msg |
| f5_p4_windsurf | L1 + L3f + L4a | open Windsurf, run 1 query |
| f6_p6_claude_code_cli | L1 + L3g + L3h | run claude-code in a real repo |
| f6_p7_codex_cli | L1 + L3g + L3h | run codex CLI |
| f6_p8_gemini_cli | L1 + L3g + L3h | run gemini CLI |

Once each scenario has 3 leg evidence in `raw_captures`, the 24h
health_beacon flow turns the supervisor /status row green automatically;
SVG re-render then flips that card from grey to green.

### G1 — Tier-β (3 scenarios)

Phase B 6-week wall-clock work — see W6/W7/W8 kickoffs (this commit).

### Wave 2 — A2 SSLKEYLOGFILE production path

Code skeleton (`pce_core/cert_wizard/keylog.py` +
`pce_proxy/keylog_mode.py` + 16 tests) — landed in this session as a
**module skeleton**; live integration to mitmproxy addon and live test
against P1/P2 still requires owner's machine.

### ADR-022 / ADR-023

Drafts exist (per kickoff §5.4 / §5.5); marking Adopted is the gate
for W7 / W8 to start. Owner sign-off + dependent ADR PRs.

## Verification (one-liner)

```powershell
# 1. ADR-021 import gate
python scripts/check_import_direction.py
# → OK: import-direction clean across 452 Python file(s).

# 2. capture_supervisor unit tests
python -m pytest tests/test_capture_supervisor.py -v
# → 31 passed

# 3. server boots with supervisor router attached
python -c "from pce_core import server; print(server.app.title)"
# → PCE Core API + log line: supervisor.api.attached scenarios=13

# 4. SVG renderer (fallback mode is fine when /status is offline)
python -m tools.render_redundancy_matrix --quiet
ls Docs/stability/REDUNDANCY-MATRIX.svg
# → ~10KB SVG with 13 scenario cards (all-grey "no data" by design)

# 5. Redundancy check tool
python -m tools.check_redundancy_targets --threshold red --output /tmp/r.json
# → exit 1 (server offline = soft fail), or exit 0 with alerts JSON

# 6. repair_adapter --redundancy-degraded path
python -m tools.repair_adapter --redundancy-degraded f4_p1_claude_desktop
# → exit 1 with message about scenario→target mapping (server offline)
```

## ADR closures

- **ADR-021** (capture_supervisor OSS boundary) — **Adopted** in this commit
- **ADR-018 Phase 5** (SSLKEYLOGFILE production) — code skeleton ready,
  status remains Phase 5 in-progress until live evidence lands
- **ADR-022 / ADR-023** — pending Phase B kickoff (drafts in
  `Docs/docs/engineering/adr/`)

## Reference

- Scope authority: `Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
- Plan: `Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md`
- Phase B kickoffs (this commit):
  - `Docs/handoff/HANDOFF-W6-COPILOT-KICKOFF-2026-05-19.md`
  - `Docs/handoff/HANDOFF-W7-CHATGPT-DESKTOP-UIA-KICKOFF-2026-05-19.md`
  - `Docs/handoff/HANDOFF-W8-CURSOR-PROTOBUF-KICKOFF-2026-05-19.md`
- Final closing template: `Docs/stability/redundancy-sprint/09-wave9-final-closure.md` §8 (待 Phase B 关闭后启用)

## One-liner anchor

**Code skeleton closed: G2/G3 alive. G1 Tier-α blocked on owner-side
live signoff (10 scenarios × ~5 min each). G1 Tier-β blocked on Phase
B (W6/W7/W8). v1.1.6 tag remains on hold per STRICT MODE.**
