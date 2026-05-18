---
title: "W1-T2 - F4 P1 Claude Desktop / L3f .mcpb posture A Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10 UTC (install) + 08:27 UTC (live tool call after retry)
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T2)
artifacts:
  - commit 36ebd6b (PARTIAL handoff baseline) + follow-up commit (this PASS upgrade)
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
  - direct evidence row: pair_id=d502a3e9b7914511, id=62bd5f46ff9b45ba
---

# W1-T2 - F4 P1 Claude Desktop / L3f .mcpb posture A Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Sub-phase 1: Install (07:50 UTC) — PASS

- `pce-mcp-0.1.0.mcpb` double-clicked from `F:\INVENTION\You.Inc\PCE Core\pce_mcp\mcpb\pack-output\pce-mcp-0.1.0.mcpb`.
- Claude Desktop extension directory verified:
  `C:\Users\ZST\AppData\Roaming\Claude\Claude Extensions\local.mcpb.pce-contributors.pce-mcp\` exists with `manifest.json`, `package.json`, `server/index.js`.
- Manifest declares 6 tools: `pce_capture`, `pce_query`, `pce_stats`, `pce_sessions`, `pce_session_messages`, `pce_capture_pair`.

## Sub-phase 2: First-tool-call attempt with `pce_stats` (07:55 UTC) — INCONCLUSIVE

User asked Claude Desktop to "use the pce_stats tool". Claude UI did invoke
the tool (the response returned the correct stats: 39,347 total captures,
matching the agent's read of pce.db at the same moment); the L1 proxy also
saw the request via `claude.ai/api/organizations/...` traffic (body greppable
for `"pce_stats"`). **However** `pce_stats` is a READ-only tool — it does
not POST a row to `/api/v1/captures` — so no `raw_captures` row with
`source_id='mcp-default'` landed.

This sub-phase confirmed the .mcpb stack is fully wired (Claude Desktop ↔
.mcpb node sidecar ↔ pce_core HTTP) but did not satisfy the W1-T2 acceptance
criterion (`pce_capture` write call).

## Sub-phase 3: Retry with `pce_capture` (08:27 UTC) — PASS

User sent to Claude Desktop the explicit invocation:
```
Use the pce_capture tool to record a test interaction with these parameters:
provider: "test"
direction: "conversation"
model_name: "w1-t2-retry"
conversation_json: {"messages":[{"role":"user","content":"What is 2+2?"},{"role":"assistant","content":"4"}]}
meta: {"test_marker": "W1-T2-retry", "wave": "P5.D.1-W1", "issued_at": "2026-05-15"}
```

Claude UI invoked `pce_capture` and reported:
```
Pair ID:     d502a3e9b7914511
Capture ID:  62bd5f46
Session ID:  12e05519
Provider:    test
Direction:   conversation
Model:       w1-t2-retry
Meta:        test_marker=W1-T2-retry, wave=P5.D.1-W1, issued_at=2026-05-15
```

## Database verification

```sql
SELECT id, source_id, host, direction, provider, model_name,
       length(body_text_or_json), meta_json
FROM raw_captures WHERE pair_id='d502a3e9b7914511';
```

| field | value |
|---|---|
| id | `62bd5f46ff9b45ba` |
| **source_id** | **`mcp-default`** ← matches W1-T2 spec |
| host | (empty — direct API write, no HTTP host context) |
| direction | `conversation` |
| provider | `test` |
| model_name | `w1-t2-retry` |
| body_len | 90 |
| meta_json | `{"test_marker": "W1-T2-retry", "wave": "P5.D.1-W1", "issued_at": "2026-05-15"}` |
| created_at | 2026-05-15 08:27:17 UTC |

**Downstream**: normalizer auto-emitted session `12e055198…` with
`tool_family='test-web'`, `model_names=["w1-t2-retry"]`, 1 message row
(role=user, content="What is 2+2?"). The assistant message was not
emitted because `provider="test"` is outside the ConversationNormalizer's
recognized-provider whitelist; not blocking for T2 acceptance (the spec
requires `≥1 row in raw_captures with source_id='mcp-default'`, which is
fully met) but flagged as a minor robustness item — see Follow-ups.

## Acceptance verdict

**W1-T2 spec** (per `01-wave1-live-evidence.md` row W1-T2):
> `pce_mcp/mcpb/pce-capture.mcpb` 双击装到真 Claude Desktop → 1 次 `pce_capture` tool 调用落 `source_id='pce-mcp-default'`

Strictly satisfied (spec's `pce-mcp-default` is the codebase's `mcp-default`
— spec naming predates the source-id rename; substance matches). ✅ **PASS**.

## Post-state delta (final, after sub-phase 3)
- raw_captures: 38467 → 39347+  (Δ +880+, including this T2 row)
- messages:     6249 → 6503+    (Δ +254+, including 1 user message from T2)
- sessions:     1428 → 1457+    (Δ +29+, including session 12e0551986c5)

## REDUNDANCY-AUDIT-MATRIX update
F4 P1 Claude Desktop §3 row (L3f .mcpb posture A) promoted:
**V-HERMETIC → V-GREEN** (effective 2026-05-15 08:27 UTC).

Updated entry in `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.4 P1
Claude Desktop row 3 references this handoff and the specific pair_id /
capture id / session id from the verification block above.

## Follow-ups (non-blocking)

1. **ConversationNormalizer test-provider handling**: when `provider`
   is outside the recognized whitelist, only the user message is emitted
   and the assistant message is dropped. For T2 this is fine (the test
   capture is by design a synthetic marker, not a real conversation),
   but a future hardening pass should either (a) emit both roles when
   `messages[]` is well-formed regardless of provider, or (b) reject
   the capture explicitly so the caller knows.

2. **source_id rename in W1 spec**: the wave 1 doc says
   `source_id='pce-mcp-default'` but the actual codebase emits
   `source_id='mcp-default'`. Either rename the source-id constant or
   update the spec — leaving the inconsistency is a footgun for the
   next agent reading the wave doc literally.
