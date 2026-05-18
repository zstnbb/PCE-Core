---
title: "D-Day Evidence — f4_p2_chatgpt_desktop"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f4_p2_chatgpt_desktop.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f4_p2_chatgpt_desktop.evidence.json
---

# ChatGPT Desktop (Win MSIX + macOS)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **4028 raw_captures rows** for
this scenario across 2 of 2 expected legs.

Legs detected: A2, L1
Legs missing:  _(none — all expected legs present)_

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `proxy-default`: 4005
- `sslkeylog-default`: 23

### 2.2 raw_captures by host

- `chatgpt.com`: 4012
- `chatgpt.com:443`: 14
- `chat.openai.com`: 2

### 2.3 Sample pair_ids (first 10)

- `0e86c458b4af40ea`
- `440c121168194e39`
- `d00c01d0897d4f21`
- `5ee2c89eda93458e`
- `10e485243ca34074`
- `e5ba5d8da3ca498b`
- `25e8c5b91fbd4cee`
- `2dec82d7685a4894`
- `dd0841cb1dab45ad`
- `7189a68abd90429d`

### 2.4 sessions

- total: 0
- by tool_family: _(none)_
- sample model_names: _(none)_

### 2.5 messages

- total (joined via capture_pair_id): 3

### 2.6 health_beacons (pass, in window)

_(none)_

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f4_p2_chatgpt_desktop.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('proxy-default', 'sslkeylog-default')
  AND host IN ('chat.openai.com', 'chat.openai.com:443', 'chatgpt.com', 'chatgpt.com:443')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f4_p2_chatgpt_desktop**:

- Evidence-backed legs this run: 2
- Remaining gap to STRICT ≥3: 1

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f4_p2_chatgpt_desktop.snapshot.db

