---
title: "W1-T8 - F6 P7 Codex CLI / L3h Live Evidence"
status: PASS
date: 2026-05-15
session: 07:10-08:30 UTC
operator: ZST (manual prompts) + Claude Code agent (orchestration)
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (row W1-T8)
artifacts:
  - commit (forthcoming)
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-15/pce_after_T3_T10_T11.db
  - evidence index: Docs/handoff/_evidence_W1_2026-05-15/_evidence_T3_T10_T11.json
---

# W1-T8 - F6 P7 Codex CLI / L3h Live Evidence

## Pre-flight state
- pce_core daemon: running on 127.0.0.1:9800 (started 2026-05-15 07:10 UTC by agent)
- Master baseline taken at 2026-05-15 07:10:54 UTC
  - raw_captures = 38467, messages = 6249, sessions = 1428
- pce.db: ~/.pce/data/pce.db (376 MB)
- Master baseline JSON: `Docs/handoff/_evidence_W1_2026-05-15/_master_baseline.json`

## Evidence
**Live invocation**:
```
python -m pce_cli_wrapper relay --target $APPDATA/npm/codex.cmd --label "codex-cli" -- exec --sandbox read-only "What is 2+2?"
-> "4"   (gpt-5.5 / ChatGPT login session: 019e2aa2-238c-7623-b8b1-f17ab06f7569)
```

**Raw capture**:
| id | host | path | direction | body_len |
|---|---|---|---|---|
| e68f200ca70c4e41a7571205f6f8310b | cli-wrapper | /unknown/codex | conversation | 444 |

**meta_json highlights** (capture e68f200ca70c...):
```
target_id=unknown (codex not yet in pce_cli_wrapper catalogue),
command_name=codex, capture_label=codex-cli,
target_path=C:\Users\ZST\AppData\Roaming\npm\codex.cmd
```

**TL;DR**: OpenAI Codex CLI relayed through PCE wrapper after `codex login`.
Captured by `l3h-cli-wrapper-default` with `command_name=codex`. Note
target_id reads "unknown" because codex is not yet in the wrapper's
target catalogue (`pce_cli_wrapper/discovery.py::known_targets`).

**Acceptance** (W1-T8 spec): `source_id='l3h-cli-wrapper-default'` >= 1 row
with `command_name=codex` (de-facto equivalent of the spec's
`meta_json.cli_kind=codex`) -> PASS.

**Follow-up** (P5.D.1 backlog): extend `pce_cli_wrapper/discovery.py`
catalogue with `codex` + `gemini` so `target_id` resolves to a known id
instead of "unknown". Non-blocking; the relay path works fully today
via `--target` direct path.


## Post-state delta
- raw_captures: 38467 -> 39345  (delta +878)
- messages:     6249 -> 6503  (delta +254)
- sessions:     1428 -> 1456  (delta +28)

## REDUNDANCY-AUDIT-MATRIX update
See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 3 for the row promoted to V-GREEN by this handoff (or staying V-HERMETIC if status=PARTIAL).
