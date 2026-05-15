---
title: "W1-T5 - F1 Google AI Studio / L1 Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T5)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T5 - F1 Google AI Studio / L1 Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**: user opened https://aistudio.google.com (post proxy chain) and sent `What is 2+2?`.

**Raw captures**:
| id | source_id | direction | body_len | path |
|---|---|---|---|---|
| d4949efa1dff4a369a7f0a5ccd942f6a | browser-extension-default | conversation | 327 | /prompts/1gE4vlZ_TCeuIukuwrujOJ7kvISqkA3fE |

**Sessions emitted** (filtered to GAS title):
| id | session_key | tool_family | model_names | message_count | title_hint |
|---|---|---|---|---|---|
| 499f57cc901847929e882581708d5e3b | 1gE4vlZ_TCeuIukuwrujOJ7kvISqkA3fE | google-web | ["Gemini Flash-Lite Latest\ngemini-flash-lite-latest\nA... | 2 | The Sum Of Two Plus Two | Google AI Studio |

**TL;DR**: GAS conversation captured. The capture arrived via the L3a
browser extension (DOM extraction was faster than the L1 SSE stream
finishing), not via the L1 proxy. Per the redundancy goal any leg counts
toward V-GREEN; per the W1-T5 acceptance criterion `>=1 messages row +
model_name non-empty` is satisfied (model=Gemini Flash-Lite Latest).

**Acceptance** (W1-T5 spec): `aistudio.google.com` >= 1 pair + 1 messages
row + model_name non-empty -> PASS.

**Caveat**: This handoff did NOT exercise the L1 plane for GAS specifically.
The L1 leg for GAS therefore stays V-HERMETIC until a separate sweep
captures `aistudio.google.com` traffic via mitmproxy (likely needs an
allowlist tweak; not in W1 scope).


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
