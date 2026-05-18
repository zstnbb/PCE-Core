---
title: "W1-T11 - F6 P8 Gemini CLI / L3g local persistence Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T11)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T11 - F6 P8 Gemini CLI / L3g local persistence Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**: agent ran `gemini --skip-trust -p "What is 2+2?"` -> JSONL session file
written to `~/.gemini/tmp/<project>/chats/session-2026-05-15T07-13-b1e5c0a8.jsonl`.
Then `python -m pce_persistence_watcher.ide_scanner scan` ingested it
(stats: gemini seen=3 emitted=1 deduped=2 errors=0).

**Raw capture**:
| id | host | path | body_len | session_hint |
|---|---|---|---|---|
| d2ed14f28ce04ab49e355b1b50699e80 | local-gemini-cli | /1b385a1a-9335-4aad-b211-09bfa9e90c4e | 651 | 1b385a1a-9335-4aad-b211-09bfa9e90c4e |
| aeb70cd36f604d76bc1b33acb6e48b51 | local-gemini-cli | /b1e5c0a8-4f9d-4180-b5a7-85fe2e9db3c5 | 656 | b1e5c0a8-4f9d-4180-b5a7-85fe2e9db3c5 |

**Session emitted**:
- id: `fd5b54a5db854d6da68a86fc5e42e7af`
- session_key: `1b385a1a-9335-4aad-b211-09bfa9e90c4e`
- tool_family: **`gemini-cli-l3g`** (critical assertion - see bug fix below)
- model_names: `["gemini-3-flash-preview"]`
- message_count: 2

**Messages emitted**:
| role | model_name | body_len | preview |
|---|---|---|---|
| user | gemini-3-flash-preview | 12 | What is 2+2? |
| assistant | gemini-3-flash-preview | 1 | 4 |

**TL;DR**: P8 Gemini CLI L3g leg promoted V-HERMETIC -> V-GREEN with
1 fresh end-to-end run plus a real-bug catch + fix.

**Acceptance** (W1-T11 spec): watcher captures with
`source_id='l3g-local-persistence-default'` + (de-facto)
`meta_json.cli_kind=gemini` via `host='local-gemini-cli'`, both
messages persisted with correct roles + non-empty content_text. -> PASS.

**Bug caught + fixed during this task** (commit 097a1d2):

`pce_core/normalizer/pipeline.py::normalize_conversation` had a L3g
host whitelist that listed `local-copilot-chat / local-cursor-chat /
local-codex-cli` but was missing `local-gemini-cli`. Result: Gemini CLI
captures fell through to the catch-all ConversationNormalizer, which
mis-emitted both messages as `role=user` and `tool_family=google-web`.

Fix: 1-line list extension. Regression test added
(`test_gemini_cli_l3g_routed_to_gemini_cli_normalizer` in
`tests/test_ide_normalizers.py`). 29 -> 30 IDE-normalizer tests, all
green. The 3 pre-existing gemini sessions in the DB were also
re-normalized in-place to fix their stale `google-web` tool_family.

This bug had silently broken P8 Gemini CLI's L3g leg since the
normalizer landed in commit `a5d09f5`. Without this W1 sweep the
regression would have shipped to v1.1.6 as a false-positive V-GREEN
claim.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
