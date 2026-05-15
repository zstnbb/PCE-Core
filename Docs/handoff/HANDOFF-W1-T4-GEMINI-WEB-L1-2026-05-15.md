---
title: "W1-T4 - F1 Gemini Web / L1 Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T4)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T4 - F1 Gemini Web / L1 Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**: user opened https://gemini.google.com (post `scripts/harvest/setup_proxy_chain.ps1`)
and sent `What is 2+2?`.

**Raw captures summary** (per source x direction):
| source_id | direction | n | max_body |
|---|---|---|---|
| browser-extension-default | conversation | 69 | 35867 |
| proxy-default | request | 43 | 9168 |
| proxy-default | response | 43 | 667446 |

**Sessions emitted** (top 3 by recency):
| id | session_key | tool_family | message_count | title_hint |
|---|---|---|---|---|
| 499f57cc901847929e882581708d5e3b | 1gE4vlZ_TCeuIukuwrujOJ7kvISqkA3fE | google-web | 2 | The Sum Of Two Plus Two | Google AI Studio |
| c2f0d86cbdd04494918db73c81c1359f | b0bb660342169f80 | google-web | 6 | Claude 订阅额度重置问题 - Google Gemini |
| e61c01d3c485431ba11ea38fa9910dbf | 217bdd9cdbd26eed | google-web | 55 | Claude Code 额度提升公告 - Google Gemini |

**TL;DR**: Gemini Web captured via both L1 proxy (mitmproxy 8080) and L3a
browser-extension simultaneously. 155 captures across both planes, 12
google-web sessions / 112 messages produced.

**Acceptance** (W1-T4 spec): `raw_captures.host='gemini.google.com'` >= 1
pair + >= 1 messages row + model_name non-empty -> PASS.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
