---
title: "W1-T1 — F4 P1 Claude Desktop / L3f MCP-proxy Live Evidence"
status: TODO            # → fill PASS / FAIL / DEGRADED on completion
date: 2026-05-14        # → fill actual run date
session: HH:MM-HH:MM <UTC offset>
operator: <name>
predecessor: HANDOFF-P5D1-KICKOFF-2026-05-14.md
canonical: Docs/stability/redundancy-sprint/01-wave1-live-evidence.md (§1 row W1-T1)
artifacts:
  - commit <sha>
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-14/w1_t1.db
---

# W1-T1 — F4 P1 Claude Desktop / L3f MCP-proxy Live Evidence

> **TEMPLATE — fill in during execution.** Do NOT commit until status=PASS.
>
> **目标**: live sign-off `pce_mcp_proxy` against real Claude Desktop
> cowork tool call → `raw_captures.source_id='mcp-proxy-default'`
> evidence + REDUNDANCY-AUDIT §3 row upgrade V-HERMETIC → V-GREEN.

---

## 1 · TL;DR

<1 sentence summary of what was done and the outcome>

---

## 2 · Pre-flight state

### 2.1 Environment

- Claude Desktop version: <e.g. 1.6608.2.0 MSIX>
- pce_mcp_proxy version: <commit sha or v>
- pce_core server PID: <pid>
- mitmproxy: NOT running (this task uses MCP stdio, not HTTP proxy)

### 2.2 Baseline (`_baseline.json`)

```json
{
  "raw_captures_count": <N>,
  "messages_count": <M>,
  "latest_created_at": "<iso8601>",
  "mcp_proxy_default_count": <K>
}
```

---

## 3 · Setup

### 3.1 Install pce_mcp_proxy as Claude Desktop MCP server

```powershell
# Install per pce_mcp_proxy/install.py
python -m pce_mcp_proxy install --upstream <upstream-mcp-server>
```

Verify `claude_desktop_config.json` updated:

```json
{
  "mcpServers": {
    "<original-mcp>": {
      "command": "...",
      "args": [...]
    },
    "pce-proxy-default": {
      "command": "python",
      "args": ["-m", "pce_mcp_proxy", "relay", "--upstream", "<original-mcp>"]
    }
  }
}
```

### 3.2 Restart Claude Desktop

```powershell
Stop-Process -Name "Claude" -Force -ErrorAction SilentlyContinue
Start-Process "shell:appsFolder\Claude_pzs8sxrjxfjjc!Claude"
```

Wait ~10s for app to load.

---

## 4 · Trigger cowork tool call

Open Claude Desktop. In a new conversation, ask:

```
Read the file at C:\Users\<user>\Documents\test.txt and tell me what's in it.
```

(or any prompt that triggers a tool call routed through the upstream MCP)

Expected behavior:
- Claude Desktop calls `<upstream-mcp>` tool
- pce_mcp_proxy intercepts the JSON-RPC frame
- pce_mcp_proxy POSTs to `pce_core` ingest API with
  `source_id='mcp-proxy-default'`
- raw_captures gets new row(s)

Wait 10s for proxy to flush.

---

## 5 · Capture evidence (raw_captures diff)

```sql
SELECT id, created_at, source_id, direction, pair_id, host, path, length(body_text_or_json) AS body_len
FROM raw_captures
WHERE source_id = 'mcp-proxy-default'
  AND created_at > <baseline.latest_created_at>
ORDER BY created_at;
```

Expected ≥ 1 row:

| id | created_at | direction | pair_id | path | body_len |
|---|---|---|---|---|---|
| <uuid> | <ts> | request | <pair> | <jsonrpc-method> | <bytes> |
| <uuid> | <ts> | response | <pair> | <same-method> | <bytes> |

Decoded JSON-RPC frame summary:
```json
{
  "method": "tools/call",
  "params": {
    "name": "<upstream-tool>",
    "arguments": {...}
  }
}
```

---

## 6 · messages rows (if any normalization happened)

```sql
SELECT id, ts, role, length(content_text) AS content_len, capture_pair_id
FROM messages
WHERE capture_pair_id IN (<pair_ids from §5>)
ORDER BY ts;
```

Expected: depends on whether normalizer maps MCP frames into messages.
For W1-T1 acceptance, raw_captures rows are sufficient — messages
correlation is bonus.

---

## 7 · REDUNDANCY-AUDIT-MATRIX update

`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.4 F4 Claude Desktop
P1 row #4 (L3f MCP posture B):

```diff
- | 5 | L3f MCP (posture B `pce_mcp_proxy`) | 同上 | `test_pce_mcp_proxy_stdio.py` R01-R11 同 mock | **V-HERMETIC** |
+ | 5 | L3f MCP (posture B `pce_mcp_proxy`) | 同上 | `HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md`: <N> raw_captures rows / pair_id <pair_id> | **V-GREEN** |
```

§4 summary table for F4 P1 row:

```diff
- | F4 Claude Desktop (P1) | **2** | ❌ 差 1 | L3f MCP live sign-off (2~3h, 硬件就绪) |
+ | F4 Claude Desktop (P1) | **3** | ✅ 达标 | — |
```

---

## 8 · Cleanup

```powershell
# 1. Restore claude_desktop_config.json (if modified)
python -m pce_mcp_proxy uninstall

# 2. Stop pce_core server (if no other tasks pending)
# (skip — keep running for next W1 task)

# 3. Save SQLite snapshot
Copy-Item <pce_data_dir>\pce.db Docs\handoff\_evidence_W1_2026-05-14\w1_t1.db
```

---

## 9 · Acceptance criteria (per `01-wave1-live-evidence.md` §6)

```
[ ] ≥ 1 raw_captures row with source_id='mcp-proxy-default'
[ ] pair_id linkage (request + response)
[ ] meta_json includes mcp method name
[ ] REDUNDANCY-AUDIT §3 F4 P1 row #4 upgraded to V-GREEN
[ ] §4 summary table updated F4 P1: 2 → 3 ✅
[ ] _evidence_W1_2026-05-14/w1_t1.db committed
```

---

## 10 · Notes / blockers (if any)

<empty if PASS; describe any partial / blocked situations here>
