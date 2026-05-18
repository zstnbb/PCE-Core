---
title: "W1-T6 - F1 Grok Web / L1 Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T6)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T6 - F1 Grok Web / L1 Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**: user opened https://grok.com (post proxy chain) and sent `What is 2+2?`.

**Raw captures** (top 5 by body size):
| id | source_id | direction | body_len | path |
|---|---|---|---|---|
| 68a1bddb75dd4e1c80a6217c66af9558 | browser-extension-default | network_intercept | 9015 | /rest/app-chat/conversations/new |
| 01fa8316d5184b2da5fb1a4fb9b62dd7 | browser-extension-default | network_intercept | 1179 | /rest/modes |
| 03ab6f3a846e46edbdb97f1a93bbdea7 | browser-extension-default | network_intercept | 1179 | /rest/modes |
| 15623581c2a7465eb2723cbfe37967c0 | browser-extension-default | conversation | 351 | /c/449867f5-528c-4a51-973c-b6145ce5a186 |
| fa976e0481364b76b2222ff5e2f02329 | browser-extension-default | network_intercept | 333 | /rest/rate-limits |

**Sessions emitted**:
| id | session_key | tool_family | message_count | title_hint |
|---|---|---|---|---|
| 4f67fe9c50444a6b88d4798fc9a6c9db | 449867f5-528c-4a51-973c-b6145ce5a186 | xai-web | 2 | Basic [REDACTED]: 2+2=4 - Grok |

**TL;DR**: Grok Web chat capture landed. The big 9015-byte
`/rest/app-chat/conversations/new` row is the actual chat send. xai-web
session produced with 2 messages.

**Acceptance** (W1-T6 spec): `grok.com` >= 1 pair + 1 messages row +
model_name non-empty -> PASS.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
