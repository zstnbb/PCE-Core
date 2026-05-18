---
title: "W1-T3 - F6 P6 Claude Code CLI / L3h Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T3)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T3 - F6 P6 Claude Code CLI / L3h Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**:
```
python -m pce_cli_wrapper relay --target $APPDATA/npm/claude.cmd --label "claude-code" -- -p "What is 2+2?"
-> "2 + 2 = 4."
```

**Raw capture**:
| id | host | path | direction | body_len |
|---|---|---|---|---|
| 80be86fcce9447528f5d6515404d2b7d | cli-wrapper | /claude-code/claude | conversation | 417 |

**meta_json highlights** (capture 80be86fcce94...):
```
target_id=claude-code, command_name=claude, capture_label=claude-code,
target_version=2.1.139, target_path=C:\Users\ZST\AppData\Roaming\npm\claude.cmd
```

**TL;DR**: L3h CLI wrapper relayed Anthropic Claude Code CLI with `What is 2+2?`,
PCE wrote 1 row with `source_id='l3h-cli-wrapper-default'`, meta carries
`command_name=claude` and `target_version=2.1.139`. Promotes the P6 Claude
Code CLI L3h leg from V-HERMETIC to V-GREEN.

**Acceptance** (W1-T3 spec): `source_id='l3h-cli-wrapper-default'` >= 1 row,
meta has `command_name=claude` -> PASS.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
