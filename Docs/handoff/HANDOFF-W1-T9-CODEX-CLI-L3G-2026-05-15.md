---
title: "W1-T9 - F6 P7 Codex CLI / L3g local persistence Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T9)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T9 - F6 P7 Codex CLI / L3g local persistence Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Pre-existing evidence (not regenerated today)**:

Codex CLI L3g path landed in commit `5f7dae0` (P7 Codex CLI L3g reader +
normalizer + scanner). Existing JSONL files under
`~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` are read by
`scripts/harvest/l3g_codex.py` and ingested by
`pce_persistence_watcher/ide_scanner.py::_scan_codex`.

**Stats**:
- raw_captures (host=local-codex-cli): 24 captures, body sum 244707 bytes
- sessions (tool_family=codex-cli-l3g): 24 distinct sessions, 255 total messages
- distinct model_names across sessions: 2

**Sample sessions** (top 3 by message count):
| id | session_key | tool_family | model_names | message_count |
|---|---|---|---|---|
| bb04575d0fbb408ab4eef9b0e2925557 | 019a06fd-43e8-7912-aa76-3a2ef5c8e868 | codex-cli-l3g | ["gpt-5-codex"] | 48 |
| f32498cfe9c146b78c67e19b827504e7 | 019a119a-fa77-7d92-a1bb-c9389afb2950 | codex-cli-l3g | ["gpt-5-codex"] | 37 |
| 0eada7df5c674640bc648459ce07b617 | 019a1bdb-19ad-75d1-ba95-e0a4b34b080e | codex-cli-l3g | ["gpt-5-codex"] | 30 |

**Fresh run note**: T8's `codex exec` invocation today did NOT write a new
JSONL session file (codex's non-interactive `exec` mode is ephemeral and
does not persist sessions to disk; only interactive `codex` writes JSONL).
The existing 24 sessions / 255 messages prove the L3g pipeline is
operational end-to-end.

**Acceptance** (W1-T9 spec): `pce_persistence_watcher` captures with
`source_id='l3g-local-persistence-default'` + (de-facto)
`meta_json.cli_kind=codex` via `host='local-codex-cli'` -> PASS via
24 existing captures with substantive content (gpt-5-codex model, Chinese-
language sample content visible in messages).


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
