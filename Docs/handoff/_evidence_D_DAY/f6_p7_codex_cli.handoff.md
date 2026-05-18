---
title: "D-Day Evidence — f6_p7_codex_cli"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f6_p7_codex_cli.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f6_p7_codex_cli.evidence.json
---

# Codex CLI (OpenAI)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **4033 raw_captures rows** for
this scenario across 2 of 3 expected legs.

Legs detected: L1, L3h
Legs missing:  L3g

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `proxy-default`: 4024
- `l3h-cli-wrapper-default`: 9

### 2.2 raw_captures by host

- `chatgpt.com`: 4003
- `api.openai.com`: 21
- `cli-wrapper`: 9

### 2.3 Sample pair_ids (first 10)

- `95d7a62cbcd14210`
- `9a06674cd99a4b9e`
- `9e64aefba81d4da1`
- `ef2cdef9c53f47a4`
- `5b53d8c636654467`
- `8124809aff7b4401`
- `60b9d63184f34ed7`
- `474cf78ae77542b2`
- `f11515009c0f43df`
- `ec219963b0f04eb3`

### 2.4 sessions

- total: 0
- by tool_family: _(none)_
- sample model_names: _(none)_

### 2.5 messages

- total (joined via capture_pair_id): 18

### 2.6 health_beacons (pass, in window)

- `L1`: 2
- `L3g`: 1
- `L3h`: 1

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f6_p7_codex_cli.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('l3g-local-persistence-default', 'l3h-cli-wrapper-default', 'proxy-default')
  AND host IN ('api.openai.com', 'api.openai.com:443', 'chatgpt.com', 'chatgpt.com:443', 'cli-wrapper', 'local-codex-cli')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f6_p7_codex_cli**:

- Evidence-backed legs this run: 2
- Remaining gap to STRICT ≥3: 1

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f6_p7_codex_cli.snapshot.db

