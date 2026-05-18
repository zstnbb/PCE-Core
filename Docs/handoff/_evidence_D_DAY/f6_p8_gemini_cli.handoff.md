---
title: "D-Day Evidence — f6_p8_gemini_cli"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f6_p8_gemini_cli.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f6_p8_gemini_cli.evidence.json
---

# Gemini CLI (Google)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **64 raw_captures rows** for
this scenario across 4 of 4 expected legs.

Legs detected: A2, L1, L3g, L3h
Legs missing:  _(none — all expected legs present)_

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `sslkeylog-default`: 26
- `proxy-default`: 22
- `l3h-cli-wrapper-default`: 9
- `l3g-local-persistence-default`: 7

### 2.2 raw_captures by host

- `cloudcode-pa.googleapis.com`: 22
- `generativelanguage.googleapis.com:443`: 14
- `generativelanguage.googleapis.com`: 12
- `cli-wrapper`: 9
- `local-gemini-cli`: 7

### 2.3 Sample pair_ids (first 10)

- `0ee79b4949c545b0`
- `d6a3bc60439848f5`
- `72be104983764b68`
- `1e151c7eb7704675`
- `b688424ce4da4dd4`
- `47c680ffdd1b4009`
- `86898f1708714e73`
- `5654c89285754d23`
- `86001f0d6fb34cb0`
- `a3bd95107d7f4efc`

### 2.4 sessions

- total: 2
- by tool_family: gemini-cli-l3g=2
- sample model_names: ["gemini-3-flash-preview"]

### 2.5 messages

- total (joined via capture_pair_id): 13

### 2.6 health_beacons (pass, in window)

- `L1`: 1
- `L3g`: 1
- `L3h`: 2

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f6_p8_gemini_cli.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('l3g-local-persistence-default', 'l3h-cli-wrapper-default', 'proxy-default', 'sslkeylog-default')
  AND host IN ('cli-wrapper', 'cloudcode-pa.googleapis.com', 'cloudcode-pa.googleapis.com:443', 'generativelanguage.googleapis.com', 'generativelanguage.googleapis.com:443', 'local-gemini-cli')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f6_p8_gemini_cli**:

- Evidence-backed legs this run: 4
- Remaining gap to STRICT ≥3: 0

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f6_p8_gemini_cli.snapshot.db

