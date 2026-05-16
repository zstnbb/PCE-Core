---
title: "W1 — 3 CLI scenarios first 3/3 V-GREEN (P6 + P7 + P8)"
status: PASS
date: 2026-05-16
session: 16:30-17:00 UTC+08, agent-driven single session
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
covers_tasks: [W1-T3, W1-T8, W1-T9, W1-T10, W1-T11]
artifacts:
  - commit 34fffc1 (Phase A skeleton, this session: previously WIP, now committed)
  - commit 3df23f4 (beacon_target alias mechanism)
  - commit a5fa33b (L1 addon + L3g scanner emit_beacon)
  - commit b551003 (cloudcode-pa.googleapis.com allowlist for Gemini oauth-personal)
  - evidence dir: Docs/handoff/_evidence_W1_2026-05-16/
  - 3 scenarios at 3/3 V-GREEN in supervisor /status
---

## TL;DR

P5.D.1 Wave 1 closure for the 3 CLI scenarios in Tier-α. After 5
commits (Phase A skeleton + supervisor alias mechanism + emit_beacon
in L1 addon + L3g scanner + Gemini host fix), **f6_p6, f6_p7, f6_p8
all reach 3/3 V-GREEN** on the supervisor `/api/v1/supervisor/status`
endpoint. This is the **first scenario to flip color from grey to
green** since the SCOPE-LOCK was adopted on 2026-05-15.

## What landed

### 1. Phase A skeleton committed (commit 34fffc1)

The previous agent session (2026-05-15) left ~30 untracked files in
the main repo working tree containing the entire Phase A code
skeleton. This session committed them as one atomic landing:

- `pce_core/capture_supervisor/` (api/dedup/policy/status + scenarios.yaml)
- `pce_core/cert_wizard/keylog.py` + `pce_proxy/keylog_mode.py` (W2 skeleton)
- 2 new migrations (0014 clipboard, 0015 sslkeylog) bumping
  EXPECTED_SCHEMA_VERSION 13→15
- 3 new tools (render_redundancy_matrix.py, check_redundancy_targets.py,
  auto_issue_on_redundancy_degraded.py) + repair_adapter.py extension
- All 10 wave docs + SCOPE-LOCK + REDUNDANCY-AUDIT-MATRIX +
  REDUNDANCY-IMPLEMENTATION-PLAN + REDUNDANCY-MATRIX.svg
- 6 handoffs (kickoff + midgate + W1-T1 + W6/W7/W8 kickoffs +
  W1 evidence dir README)
- ADR-021 Adopted, ADR-022/023 drafts

### 2. Supervisor target-alias mechanism (commit 3df23f4)

**Problem found**: scenarios.yaml's strict `target = '<scenario_id>__<source>'`
beacon convention had **zero matching emitters** in the codebase. All
capture paths (wrapper, mcp_proxy) write simple target names like
`claude_code` / `codex_cli`. Supervisor permanently stayed grey/down.

**Fix**: added 3 optional fields to each Leg:

```yaml
beacon_target: claude_code     # simple target name
beacon_lane:   cli             # health_beacons.lane
beacon_layer:  L3h             # health_beacons.layer
```

`HealthBeaconSignalProvider` runs a second query keyed on
`(target, lane, layer)` and merges results into the canonical query.
Most-specific match wins, falling through `(target+lane+layer)` →
`(target+lane)` → `(target+layer)` → `(target,)`.

This is **backwards compatible**: any future capture path can still
use the strict convention; capture paths that use simple names just
add the alias fields to scenarios.yaml.

Also: `pce_cli_wrapper/discovery.py` now registers `codex-cli` and
`gemini-cli` targets (previously only `claude-code`), enabling
`pce-cli-wrapper install` to drop L3h shim files for all 3 CLIs.

### 3. emit_beacon in L1 addon + L3g scanner (commit a5fa33b)

Three capture-path modules gained beacon-emit logic:

| Module | Purpose | Beacons emitted per pass |
|---|---|---|
| `pce_proxy/addon.py` | L1 mitmproxy capture | One per response (host→target map) |
| `pce_persistence_watcher/ide_scanner.py` | L3g IDE state walker | One per non-empty install (copilot/cursor/codex/gemini) |
| `pce_persistence_watcher/__main__.py` | L3g Claude state walker | One each for `claude_code` (cli) + `claude_desktop` (desktop) |

Addon's `_derive_l1_beacon_target(host, path)` covers 13 hosts/path-prefixes
mapping to the 13 P0 scenarios. Status: 5xx → fail, everything else → pass
(the leg is alive even on 4xx; only upstream-broken counts as fail).

### 4. Gemini cloudcode-pa.googleapis.com allowlist (commit b551003)

Gemini CLI 0.42 with `security.auth.selectedType='oauth-personal'`
routes through **`cloudcode-pa.googleapis.com/v1internal:streamGenerate*`**
(Google internal name "Cloud Code Personal Assistant"). That host was
not on `ALLOWED_HOSTS`; addon silently dropped the flow and `f6_p8`
stayed at 2/3 even with mitmproxy actively passing TCP traffic.

Discovered by running `gemini --skip-trust -p 'test'` with
`HTTPS_PROXY` set and inspecting mitmdump.log for the request line.

## Acceptance evidence

`supervisor /api/v1/supervisor/status` output post-fix:

```
=== TIER-α STATUS (Phase A) — 3/10 fully GREEN ===
f1_chatgpt_web               grey   0/3
f1_claude_web                grey   0/3
f1_gemini_web                grey   0/3
f1_gas                       grey   0/3
f1_grok_web                  grey   0/3
f4_p1_claude_desktop         orange 1/3  (L3g green)
f5_p4_windsurf               grey   0/3
f6_p6_claude_code_cli        green  3/3  ✅ L1 + L3g + L3h
f6_p7_codex_cli              green  3/3  ✅ L1 + L3g + L3h
f6_p8_gemini_cli             green  3/3  ✅ L1 + L3g + L3h
```

Per-scenario beacon evidence (`Docs/handoff/_evidence_W1_2026-05-16/3cli_3of3_evidence.json`):

| Scenario | L1 last pass | L3g last pass | L3h last pass |
|---|---|---|---|
| f6_p6_claude_code_cli | 1778922161 | 1778921288 | 1778920223 |
| f6_p7_codex_cli | 1778922072 | 1778920920 | 1778920235 |
| f6_p8_gemini_cli | 1778922072 | 1778920920 | 1778920252 |

All three beacons inside the 24h health window. `legs_active=3`,
`color='green'`, `status='REDUNDANT'`.

## SOP that worked (replicable)

```powershell
# 1. Ensure mitmproxy running on 8080 with PCE addon
mitmdump -s F:\INVENTION\You.Inc\PCE Core\run_proxy.py -p 8080 `
         --mode upstream:http://127.0.0.1:7890 `
         --set stream_large_bodies=1m

# 2. Install L3h shims for the 3 CLIs (one-time)
python -m pce_cli_wrapper install
# → C:\Users\ZST\AppData\Local\PCE\bin\{claude,codex,gemini}.{cmd,ps1}

# 3. Fire fresh prompts (HTTPS_PROXY needed because git bash export
#    doesn't propagate to npm/node child processes on Windows)
powershell -Command "$env:HTTPS_PROXY='http://127.0.0.1:8080'; codex exec 'say four'"
powershell -Command "$env:HTTPS_PROXY='http://127.0.0.1:8080'; gemini --skip-trust -p 'say four'"
claude --print "say four"   # already system-proxy-aware on Windows

# 4. Run scanner to ingest local jsonl (L3g)
python -m pce_persistence_watcher.ide_scanner scan
python -m pce_persistence_watcher scan --only code_tab    # P6 path

# 5. Query supervisor /status
curl http://127.0.0.1:9800/api/v1/supervisor/status
```

## What's not done — carried to W1-T1/T2/T4/T5/T6/T7 + W4

| ID | Scenario | Missing leg | Blocker | Plan |
|---|---|---|---|---|
| W1-T1 | f4_p1 (Claude Desktop) | L3f MCP-proxy | Need MCP server proxied via `pce_mcp_proxy` + Claude Desktop config edit + fire 1 cowork tool call | Day 2 |
| W1-T2 | f4_p1 (Claude Desktop) | L3f .mcpb posture A | Need `pce_mcp/mcpb/*.mcpb` packed + drag-installed | Day 2 |
| W1-T4 | f1_gemini_web | L1 (need Chrome traffic) | Playwright on Hurricane profile | Day 3 |
| W1-T5 | f1_gas | L1 | Same | Day 3 |
| W1-T6 | f1_grok_web | L1 | Playwright (profile TBD) | Day 3 |
| W1-T7 | f5_p4 (Windsurf) | L3f MCP + L1 | Need Windsurf launched in PCE-aware terminal | Day 3 |
| (W4-T*) | Web 4 + Windsurf | L4a clipboard | Need clipboard_monitor running + a copy event | Day 4 |

## Verification one-liners

```powershell
# Re-prove the 3 CLI scenarios are still green any time:
python -m pce_persistence_watcher.ide_scanner scan
curl http://127.0.0.1:9800/api/v1/supervisor/scenario/f6_p6_claude_code_cli
curl http://127.0.0.1:9800/api/v1/supervisor/scenario/f6_p7_codex_cli
curl http://127.0.0.1:9800/api/v1/supervisor/scenario/f6_p8_gemini_cli

# Supervisor unit tests
python -m pytest tests/test_capture_supervisor.py -q
# → 31 passed
```

## Reference

- Scope authority: `Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
- Pre-flight: `Docs/handoff/HANDOFF-P5D1-KICKOFF-2026-05-14.md`
- Midgate (skeleton): `Docs/handoff/HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md`
- Wave 1 plan: `Docs/stability/redundancy-sprint/01-wave1-live-evidence.md`
- Evidence dir: `Docs/handoff/_evidence_W1_2026-05-16/3cli_3of3_evidence.json`

## One-liner anchor

**3 CLI scenarios fully green; 3/10 Tier-α; alias mechanism unblocked
all future emit_beacon writers. Web 5 + P1 L3f + P4 L1 next.**
