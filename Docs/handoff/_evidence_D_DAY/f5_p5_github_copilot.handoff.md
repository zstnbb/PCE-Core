---
title: "D-Day Evidence — f5_p5_github_copilot"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f5_p5_github_copilot.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f5_p5_github_copilot.evidence.json
---

# GitHub Copilot (VS Code)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **25 raw_captures rows** for
this scenario across 2 of 3 expected legs.

Legs detected: L1, L3f
Legs missing:  A2

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `mcp-default`: 23
- `proxy-default`: 2

### 2.2 raw_captures by host

- `api.openai.com`: 10
- `generativelanguage.googleapis.com`: 5
- `api.anthropic.com`: 5
- ``: 3
- `api.githubcopilot.com`: 2

### 2.3 Sample pair_ids (first 10)

- `50f6e7d0a29f4eca`
- `d502a3e9b7914511`
- `0514d1b099284937`
- `d5b7f253252f4516`
- `c05758c1549347ac`
- `5dc95e3ed21f4b6a`
- `6a41f74b09294754`
- `ce998e60d2114283`
- `451a417715924e11`
- `756e9c0c1c444c4d`

### 2.4 sessions

- total: 0
- by tool_family: _(none)_
- sample model_names: _(none)_

### 2.5 messages

- total (joined via capture_pair_id): 24

### 2.6 health_beacons (pass, in window)

_(none)_

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f5_p5_github_copilot.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('mcp-default', 'proxy-default', 'sslkeylog-default')
  AND host IN ('api.githubcopilot.com', 'api.githubcopilot.com:443', 'copilot-proxy.githubusercontent.com', 'copilot-proxy.githubusercontent.com:443')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f5_p5_github_copilot**:

- Evidence-backed legs this run: 2
- Remaining gap to STRICT ≥3: 1

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f5_p5_github_copilot.snapshot.db

