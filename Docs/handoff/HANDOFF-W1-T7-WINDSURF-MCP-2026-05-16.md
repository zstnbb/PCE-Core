---
title: "W1-T7 — F5 P4 Windsurf L3f MCP real-path V-GREEN"
status: PASS  (Windsurf scenario crosses STRICT ≥3 V-GREEN)
date: 2026-05-16
session: 01:00-01:50 UTC (Windsurf MCP install + 2 schema-version remediation cycles + real MCP-path verification)
operator: ZST (refresh MCP buttons + paste test prompts in Cascade)
agent: Claude Code (config edits + main-repo migration patch + verification)
predecessor: HANDOFF-W2.1.2-W6T1-2026-05-15.md
canonical: Docs/stability/REDUNDANCY-AUDIT-MATRIX.md §3.5 F5 P4
artifacts:
  - raw_captures id=`94cce037ca5b465d9b913bdc453c2437`, pair_id=`30c32c7f3e9040d1...`
  - sessions id=`6cd79d397fbe4319949ea85dd7ccaaf6`, **created_via=`mcp`** ← MCP-path smoking gun
  - C:\Users\ZST\.codeium\windsurf\mcp_config.json (pce server entry, env.PYTHONPATH-based)
  - main repo migrations/0014_clipboard_source.py + 0015_sslkeylog_source.py copied from worktree
  - main repo migrations/__init__.py EXPECTED_SCHEMA_VERSION: 13 → 15
---

# W1-T7 — F5 P4 Windsurf L3f MCP real-path V-GREEN

## TL;DR

F5 P4 Windsurf now has its 3rd V-GREEN leg via **MCP middleware (L3f)** —
Cascade (Windsurf's chat agent) successfully invoked PCE's `pce_capture`
tool through the registered `pce` MCP server, the call flowed over
JSON-RPC 2.0 stdio, and a `raw_captures` row + `sessions` row landed in
`pce.db` with `source_id='mcp-default'` and `sessions.created_via='mcp'`
(the smoking gun confirming the row came from MCP protocol, not from any
fallback path).

Matrix scorecard delta: **STRICT ≥3 V-GREEN 9/13 → 10/13**.

## How we got there (3 cycles)

### Cycle 1 — Initial config + Windsurf rejection

Wrote `pce` entry into `C:\Users\ZST\.codeium\windsurf\mcp_config.json`:
```json
"pce": {
  "command": "python",
  "args": ["-m", "pce_mcp"],
  "cwd": "F:\\INVENTION\\You.Inc\\PCE Core"
}
```

Cascade UI showed **`pce: Error`** in the MCP Registry panel. Cascade
fell back to `python -c "from pce_mcp.server import pce_capture(...)"`
— direct function call, bypassing MCP protocol — and reported the
schema mismatch error:
```
RuntimeError: Database schema_version=15 is newer than this build
expects (13). You appear to have downgraded PCE.
```

**Diagnosis**: When Windsurf spawned `python -m pce_mcp`, it resolved
to main repo's `pce_core`. Main repo was still on `EXPECTED_SCHEMA_VERSION=13`
(my worktree commits with migrations 0014/0015 + bump to 15 had not
been merged back). The live `pce.db` had been migrated to v15 by the
worktree's sslkeylog daemon → main-repo pce_core's `apply_migrations`
guard refused to start.

Cascade attempted its own fix: wrote stub migrations 0014/0015 + bumped
`__init__.py` to 15 *in main repo*. But the change either didn't
persist to disk (IDE buffer / process isolation) or was reverted —
follow-up retries still saw the v13 expected version.

### Cycle 2 — Patch main repo from worktree's canonical versions

```bash
cp worktrees/.../pce_core/migrations/0014_clipboard_source.py \
   F:\INVENTION\You.Inc\PCE Core\pce_core\migrations\
cp worktrees/.../pce_core/migrations/0015_sslkeylog_source.py \
   F:\INVENTION\You.Inc\PCE Core\pce_core\migrations\
sed -i 's/^EXPECTED_SCHEMA_VERSION = 13/EXPECTED_SCHEMA_VERSION = 15/' \
   F:\INVENTION\You.Inc\PCE Core\pce_core\migrations\__init__.py
```

Verified main-repo `init_db()` no longer raises, and `pce_mcp` initializes
cleanly with a JSON-RPC handshake test:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | \
  python -m pce_mcp
# → {"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"PCE - Personal Cognitive Engine","version":"1.27.0"},...}}
```

But Cascade now reported **`MCP Server Error: failed to initialize
server: transport error: transport closed`**. The MCP plugin spawned
the server but the process exited before completing the handshake.

### Cycle 3 — Windsurf strips `cwd`, switch to PYTHONPATH env

Inspecting the live config after Windsurf saved it (via the
`system-reminder` echoing the file state) revealed Windsurf had
silently **dropped the `cwd` field** during reformat, and on init
failure had set **`disabled: true`** — likely a Windsurf-side schema
that doesn't recognize `cwd`.

Without cwd, `python -m pce_mcp` spawned from Windsurf's default
directory (somewhere under user home or Windsurf install) where
`pce_mcp` package isn't importable → `ModuleNotFoundError` → process
exits → MCP transport closed.

Fix: use `env.PYTHONPATH` instead (Windsurf preserves the `env` field):
```json
"pce": {
  "command": "C:\\Users\\ZST\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
  "args": ["-m", "pce_mcp"],
  "env": { "PYTHONPATH": "F:\\INVENTION\\You.Inc\\PCE Core" },
  "disabled": false
}
```

User clicked the ↻ refresh button next to `pce` in the MCP Registry.
Cascade UI immediately showed:
```
pce  • Enabled       6/6 tools
```

Real-path test prompt:
```
Use the pce_capture tool to record this test interaction:
  provider: "test"
  direction: "conversation"
  model_name: "windsurf-w1-t7-real-mcp"
  conversation_json: {"messages":[{"role":"user","content":"Windsurf MCP real path 2+2"},{"role":"assistant","content":"4"}]}
  meta: {"test_marker": "W1-T7-windsurf-mcp-REAL", "wave": "P5.D.1-W1-T7", "issued_at": "2026-05-15"}
```

Cascade's reply (no fallback this time — direct MCP tool call):
```
Captured
pair_id:    30c32c7f3e9040d1
capture_id: 94cce037
session_id: 6cd79d39 (normalized)
```

## Database verification

```sql
SELECT id, pair_id, source_id, direction, provider, model_name,
       json_extract(meta_json, '$.test_marker') AS marker,
       json_extract(meta_json, '$.wave')        AS wave
FROM raw_captures WHERE pair_id LIKE '30c32c7f%';
```

| field | value |
|---|---|
| id | `94cce037ca5b465d9b913bdc453c2437` |
| pair_id | `30c32c7f3e9040d1...` |
| **source_id** | **`mcp-default`** ✅ |
| direction | `conversation` |
| provider | `test` |
| model_name | `windsurf-w1-t7-real-mcp` |
| marker | `W1-T7-windsurf-mcp-REAL` |
| wave | `P5.D.1-W1-T7` |

```sql
SELECT id, source_id, provider, message_count, title_hint, created_via
FROM sessions WHERE id LIKE '6cd79d39%';
```

| field | value |
|---|---|
| id | `6cd79d397fbe4319949ea85dd7ccaaf6` |
| source_id | `mcp-default` |
| provider | `test` |
| message_count | 1 |
| title_hint | `Windsurf MCP real path 2+2` |
| **created_via** | **`mcp`** ← **MCP protocol path smoking gun** |

Compare: the cycle-1 fallback row (`pair_id=b932b672`, written via Cascade's
direct Python invocation of `from pce_mcp.server import pce_capture`)
landed with the same `source_id='mcp-default'` but does NOT carry
`created_via='mcp'`. The two paths are byte-distinguishable.

## Matrix delta

§3.5 F5 P4 Windsurf row 2 (L3f MCP):
- Before: `V-HERMETIC → V-GREEN W1-T7` (待签字)
- After:  **`V-GREEN ✅ (W1-T7 closed 2026-05-16)`**

§3.5 F5 P4 summary:
- Before: 有效 V-GREEN: 2 (L1c + A2 via host equivalence)
- After:  **有效 V-GREEN: 3 ✅** (L1c + L3f MCP + A2). **STRICT 达标**.

§4.1 scorecard:
- STRICT ≥3 V-GREEN (incl. V-GREEN-C):  9/13 → **10/13** ✅
- ≥1 V-GREEN-clean:                     13/13 (unchanged)
- ≥3 V-GREEN-clean (no L1c):            6/13 (unchanged — Windsurf clean-only still 2 legs: L3f + A2; needs L3d CDP for 3 clean)

## Side-finding: main repo / worktree migration drift

The two-cycle remediation surfaced a real divergence:

- **Worktree** branch `claude/priceless-proskuriakova-b37af3` carries
  the canonical 0014/0015 migrations + EXPECTED_SCHEMA_VERSION=15
  (committed in W2 series: `69bb1d2`, `06e4192`).
- **Main repo** working tree still on EXPECTED_SCHEMA_VERSION=13, no
  0014/0015 files. Live `pce.db` is at v15 because the worktree's
  daemon migrated it.

Result: any process running from main repo's pce_core (e.g. Windsurf
spawning `python -m pce_mcp` with `PYTHONPATH=<main-repo>`) refuses
to start.

Fix applied in this session: copied worktree's 0014/0015 + bumped
__init__.py to main repo's `pce_core/migrations/`. Main repo is now
self-consistent with the live DB schema. When the worktree branch
eventually merges to main, the duplicate files will collapse to the
canonical version (same content).

## Operator-side state

- `C:\Users\ZST\.codeium\windsurf\mcp_config.json` permanently registers
  the `pce` MCP server with PYTHONPATH-based import resolution. Survives
  Windsurf reformatting (it strips `cwd` but preserves `env`).
- `pce` MCP server: **Enabled** in Cascade UI. All 6 tools exposed
  (`pce_capture`, `pce_query`, `pce_stats`, `pce_sessions`,
  `pce_session_messages`, `pce_capture_pair`).
- Right-side panel shows `sajitmmw_ecl: Error` — that's user's separate
  HTTP-based MCP server (pointed at `http://127.0.0.1:23986/mcp`),
  unrelated to PCE. Its local service isn't running.

## Next

Two operator-actionable items remain in the immediate queue:
1. **F1 GAS (3rd leg)**: L1 isolated sweep — disable browser extension,
   run aistudio.google.com chat with mitmproxy on. ~5 min operator
   time.
2. **F5 P5 Copilot (2nd/3rd leg)**: install MCP server in VS Code
   (same pattern as Windsurf — drop a pce entry into Copilot's MCP
   config). ~5 min.

Both should be quick now that the migration / config pattern is
established.
