---
title: "D-Day Evidence — f6_p6_claude_code_cli"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f6_p6_claude_code_cli.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f6_p6_claude_code_cli.evidence.json
---

# Claude Code CLI (@anthropic-ai/claude-code)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **10359 raw_captures rows** for
this scenario across 3 of 3 expected legs.

Legs detected: L1, L3g, L3h
Legs missing:  _(none — all expected legs present)_

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `l3g-local-persistence-default`: 5557
- `proxy-default`: 4793
- `l3h-cli-wrapper-default`: 9

### 2.2 raw_captures by host

- `local-agent-mode`: 5557
- `api.anthropic.com`: 4793
- `cli-wrapper`: 9

### 2.3 Sample pair_ids (first 10)

- `7d2613a5fbea4efb`
- `71e660c1a5c04e89`
- `3daf9ec636134cfc`
- `792f54ce26ca48d0`
- `6897b2b0034a4ff4`
- `67343e1f49e84e2b`
- `f71f2e017dba424e`
- `ee59a97e2bd14119`
- `947f8747eccb4512`
- `6773b5a49cb14473`

### 2.4 sessions

- total: 10
- by tool_family: claude-desktop-code=9, cowork-local-agent=1
- sample model_names: ["claude-sonnet-4-6"], ["claude-opus-4-7", "claude-sonnet-4-6"], ["claude-haiku-4-5-20251001"], ["claude-haiku-4-5-20251001", "claude-opus-4-7"], ["<synthetic>", "claude-opus-4-7", "claude-sonnet-4-6"], ["claude-opus-4-7"], ["<synthetic>", "claude-opus-4-7"]

### 2.5 messages

- total (joined via capture_pair_id): 4692

### 2.6 health_beacons (pass, in window)

- `L1`: 133
- `L3g`: 1
- `L3h`: 4

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f6_p6_claude_code_cli.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('l3g-local-persistence-default', 'l3h-cli-wrapper-default', 'proxy-default')
  AND host IN ('api.anthropic.com', 'api.anthropic.com:443', 'cli-wrapper', 'local-agent-mode', 'local-claude-cli')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f6_p6_claude_code_cli**:

- Evidence-backed legs this run: 3
- Remaining gap to STRICT ≥3: 0

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f6_p6_claude_code_cli.snapshot.db

