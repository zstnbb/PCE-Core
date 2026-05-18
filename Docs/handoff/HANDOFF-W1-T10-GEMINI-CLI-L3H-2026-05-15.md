---
title: "W1-T10 - F6 P8 Gemini CLI / L3h Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T10)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T10 - F6 P8 Gemini CLI / L3h Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**:
```
python -m pce_cli_wrapper relay --target $APPDATA/npm/gemini.cmd --label "gemini-cli" -- --skip-trust -p "What is 2+2?"
-> "4"
```

**Raw capture**:
| id | host | path | direction | body_len |
|---|---|---|---|---|
| 75a147d5b45a47bcad79e3b49d1997a2 | cli-wrapper | /unknown/gemini | conversation | 433 |

**meta_json highlights** (capture 75a147d5b45a...):
```
target_id=unknown (gemini not yet in pce_cli_wrapper catalogue),
command_name=gemini, capture_label=gemini-cli,
target_path=C:\Users\ZST\AppData\Roaming\npm\gemini.cmd
```

**TL;DR**: Google Gemini CLI relayed through PCE wrapper.
`source_id='l3h-cli-wrapper-default'` with `command_name=gemini`.

**Acceptance** (W1-T10 spec): `source_id='l3h-cli-wrapper-default'` >= 1
row with `command_name=gemini` (de-facto `cli_kind=gemini`) -> PASS.

**Follow-up**: same as T8 - extend pce_cli_wrapper catalogue.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
