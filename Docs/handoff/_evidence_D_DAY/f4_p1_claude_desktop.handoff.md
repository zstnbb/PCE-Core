---
title: "D-Day Evidence — f4_p1_claude_desktop"
status: PASS
date: 2026-05-18
session: D-Day B3-B10-aggregate
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
artifacts:
  - Docs/handoff/_evidence_D_DAY/f4_p1_claude_desktop.snapshot.db
  - Docs/handoff/_evidence_D_DAY/f4_p1_claude_desktop.evidence.json
---

# Claude Desktop (Win MSIX/Squirrel + macOS)

## 1. TL;DR

In a 4320.0-minute window ending 2026-05-18T04:04:31, the
PCE pipeline captured **18077 raw_captures rows** for
this scenario across 4 of 5 expected legs.

Legs detected: A2, L1, L3f, L3g
Legs missing:  L3f-proxy

## 2. Evidence shape

### 2.1 raw_captures by source_id

- `proxy-default`: 12376
- `l3g-local-persistence-default`: 5578
- `sslkeylog-default`: 100
- `mcp-default`: 23

### 2.2 raw_captures by host

- `claude.ai`: 7607
- `local-agent-mode`: 5557
- `api.anthropic.com`: 4812
- `api.anthropic.com:443`: 39
- `claude.ai:443`: 23
- `local-config`: 17
- `api.openai.com`: 10
- `generativelanguage.googleapis.com`: 5
- `chromium-indexeddb`: 4
- ``: 3

### 2.3 Sample pair_ids (first 10)

- `7d2613a5fbea4efb`
- `b5cf7e7dbc57484f`
- `71e660c1a5c04e89`
- `6f3ba945aa98451e`
- `7e0fd51992c94052`
- `5a8d9a46796a4127`
- `ffb7741fbf8745b8`
- `de6e628668124b87`
- `9c16579c163b456b`
- `3daf9ec636134cfc`

### 2.4 sessions

- total: 1327
- by tool_family: api-direct=1277, anthropic-web=41, claude-desktop-code=9
- sample model_names: ["Opus 4.7\nAdaptive", "claude-opus-4-7"], ["claude-sonnet-4-6"], ["claude-opus-4-7", "claude-sonnet-4-6"], ["claude-opus-4-7"], ["claude-haiku-4-5-20251001"], ["claude-haiku-4-5-20251001", "claude-opus-4-7"], ["<synthetic>", "claude-opus-4-7", "claude-sonnet-4-6"], ["gpt-4"], ["gpt-4-turbo"], ["claude-3-opus-20240229"]

### 2.5 messages

- total (joined via capture_pair_id): 4738

### 2.6 health_beacons (pass, in window)

- `L1`: 10
- `L3g`: 1

## 3. Snapshot

- Docs/handoff/_evidence_D_DAY/f4_p1_claude_desktop.snapshot.db — sqlite slice of the raw_captures + messages +
  sessions + health_beacons rows for this window.

Reproduce queries:

```sql
SELECT source_id, host, COUNT(*)
FROM raw_captures
WHERE created_at BETWEEN 1778817871 AND 1779077071
  AND source_id IN ('l3g-local-persistence-default', 'mcp-default', 'mcp-proxy-default', 'proxy-default', 'sslkeylog-default')
  AND host IN ('api.anthropic.com', 'api.anthropic.com:443', 'chromium-indexeddb', 'claude.ai', 'claude.ai:443', 'local-agent-mode', 'local-config')
GROUP BY source_id, host;
```

## 4. Matrix impact

Update REDUNDANCY-AUDIT-MATRIX §3 row for **f4_p1_claude_desktop**:

- Evidence-backed legs this run: 4
- Remaining gap to STRICT ≥3: 0

## 5. Acceptance

- [x] ≥1 raw_captures row in the window for each detected leg
- [ ] handoff PR linked
- [ ] snapshot committed under Docs/handoff/_evidence_D_DAY/f4_p1_claude_desktop.snapshot.db

