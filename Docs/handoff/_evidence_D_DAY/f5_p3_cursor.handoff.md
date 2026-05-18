---
title: "D-Day Evidence — f5_p3_cursor"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f5_p3_cursor.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f5_p3_cursor.evidence.json
---

# Cursor (IDE-class, gRPC-web protobuf)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **470 raw_captures rows** for
this scenario across 3 of 4 expected legs.

Legs detected: A2, L1, L3f
Legs missing:  L3g

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `proxy-default`: 439
- `mcp-default`: 23
- `sslkeylog-default`: 8

### 2.2 raw_captures by host

- `api2.cursor.sh`: 419
- `api3.cursor.sh`: 24
- `api.openai.com`: 10
- `generativelanguage.googleapis.com`: 5
- `api.anthropic.com`: 5
- `api2.cursor.sh:443`: 4
- ``: 3

### 2.3 Sample pair_ids (first 10)

- `184f9af3dd6c46bc`
- `b15a9aa8b98c4f36`
- `906b533235704422`
- `2c922a753bbe40c2`
- `72807aad7db54731`
- `f2fe42687a7d4ec8`
- `8a7f5f5c093a4dce`
- `cd5c5129868b49a3`
- `4c633f64cb5c4731`
- `2bb22be5d55c4bf7`

### 2.4 sessions

- total: 0
- by tool_family: _(none)_
- sample model_names: _(none)_

### 2.5 messages

- total (joined via capture_pair_id): 24

### 2.6 health_beacons (pass, in window)

_(none)_

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f5_p3_cursor.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('l3g-local-persistence-default', 'mcp-default', 'proxy-default', 'sslkeylog-default')
  AND host IN ('api.cursor.sh', 'api.cursor.sh:443', 'api2.cursor.sh', 'api2.cursor.sh:443', 'api3.cursor.sh', 'api3.cursor.sh:443', 'local-cursor-chat')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f5_p3_cursor**:

- Evidence-backed legs this run: 3
- Remaining gap to STRICT ≥3: 0

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f5_p3_cursor.snapshot.db

