---
title: "W1-T2 - F4 P1 Claude Desktop / L3f .mcpb posture A Live Evidence"
status: PARTIAL
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T2)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T2 - F4 P1 Claude Desktop / L3f .mcpb posture A Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Pre-flight installation**:
- pce-mcp.mcpb double-clicked from `F:\INVENTION\You.Inc\PCE Core\pce_mcp\mcpb\pack-output\pce-mcp-0.1.0.mcpb`
- Claude Desktop extension directory verified: `C:\Users\ZST\AppData\Roaming\Claude\Claude Extensions\local.mcpb.pce-contributors.pce-mcp` exists (True)

**Live tool invocation**: NOT VERIFIED.

User reportedly asked Claude Desktop to call the `pce_stats` tool, but
no `pce_capture` / `pce_stats` call landed in `raw_captures` with
`source_id='mcp-default'` since the 2026-05-15 07:10 baseline. Count
of `mcp-default` captures since baseline: **0**.

**Likely causes** (un-investigated):
(a) Claude Desktop UI replied without actually invoking the pce_stats
    tool (Claude UI sometimes answers from prior context instead of
    triggering an MCP call when the question is short).
(b) Claude Desktop needs a restart after `.mcpb` install for the
    extension to load (manifest_version=0.2 may have hot-load issues).
(c) The pce-mcp node sidecar failed to reach 127.0.0.1:9800 from the
    extension sandbox.

**Acceptance** (W1-T2 spec): `pce_capture` tool call lands
`source_id='pce-mcp-default'` >= 1 row -> NOT MET.

**Recommended follow-up** (5-min retry next session):
1. Stop Claude Desktop entirely (taskkill if needed).
2. Verify `pce_core.server` running: `curl http://127.0.0.1:9800/api/v1/health`.
3. Reopen Claude Desktop.
4. In a fresh conversation, send exactly: `Use the pce_stats tool to show database statistics`.
5. Verify with: `SELECT * FROM raw_captures WHERE source_id='mcp-default' ORDER BY created_at DESC LIMIT 1`.

Until the retry lands evidence, P1 Claude Desktop's L3f .mcpb posture-A
leg stays V-HERMETIC (.mcpb path validated as installable; runtime path
not yet live-verified).


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
