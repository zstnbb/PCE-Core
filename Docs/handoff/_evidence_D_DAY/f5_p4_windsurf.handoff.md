---
title: "D-Day Evidence — f5_p4_windsurf"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f5_p4_windsurf.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f5_p4_windsurf.evidence.json
---

# Windsurf (IDE-class MCP-aware)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **67 raw_captures rows** for
this scenario across 3 of 3 expected legs.

Legs detected: A2, L1, L3f
Legs missing:  _(none — all expected legs present)_

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `proxy-default`: 36
- `mcp-default`: 23
- `sslkeylog-default`: 8

### 2.2 raw_captures by host

- `server.codeium.com`: 40
- `api.openai.com`: 10
- `generativelanguage.googleapis.com`: 5
- `api.anthropic.com`: 5
- `server.codeium.com:443`: 4
- ``: 3

### 2.3 Sample pair_ids (first 10)

- `7da7fbdb98c74d97`
- `c2bf7f99cc72447c`
- `1c118ed1fcfe4401`
- `8917919551994416`
- `13c972c786fa42b3`
- `a4eadf8190464325`
- `e2d5582f7e9a4462`
- `1bf35bd424554373`
- `77c7f2af09e145af`
- `01681c3b6a5142bf`

### 2.4 sessions

- total: 0
- by tool_family: _(none)_
- sample model_names: _(none)_

### 2.5 messages

- total (joined via capture_pair_id): 24

### 2.6 health_beacons (pass, in window)

- `L1`: 1

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f5_p4_windsurf.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('mcp-default', 'proxy-default', 'sslkeylog-default')
  AND host IN ('server.codeium.com', 'server.codeium.com:443', 'server.self-serve.windsurf.com', 'server.self-serve.windsurf.com:443')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f5_p4_windsurf**:

- Evidence-backed legs this run: 3
- Remaining gap to STRICT ≥3: 0

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f5_p4_windsurf.snapshot.db

