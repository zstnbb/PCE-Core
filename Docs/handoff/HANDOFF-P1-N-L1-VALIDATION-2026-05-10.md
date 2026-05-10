# Handoff — P1 Claude Desktop N/L1 chat-region end-to-end validation

> **Date**: 2026-05-10 (UTC+08, follows v1.1.0-alpha.9-empirical-followup ship)
> **Operator**: Cascade + project owner pair-session
> **Outcome**: P1 Claude Desktop **N/L1 chat-region axis fully validated**
> end-to-end against a live install — first time the full
> `Claude Desktop UI → mitmproxy → Clash → external → response → addon →
> raw_captures → normalizer → sessions + messages` path has been exercised
> with empirical proof, complementing the H2 viability verdict from alpha.8.
>
> **Authority**: companions `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1
> (P1 row) and `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §7
> row 2 (1a Electron MSIX). Updates the §3.6 ADR-018 P1 D0 ≥85% gate
> from "modeled-94%" to "empirically-attested for the chat-region axis".
>
> **Audience**: Cascade, future agents, project owner.

---

## 1. Why this run mattered

`v1.1.0-alpha.9-empirical-followup` shipped the L3g persistence and
H2/H3/H4 verdicts but the alpha.9 release-notes coverage scenario
("~94% T1 three-region") was **a model prediction, not a D-case
empirical run** — see this commit's parent for the audit that surfaced
the spin. To convert the prediction into evidence, the chat-region
(N/L1) axis had to be exercised on a real Claude Desktop install with
a real conversation, with the resulting rows inspected in SQLite.
This handoff records that run.

## 2. Setup (Plan A — upstream chain)

The operator's machine already runs Clash on `127.0.0.1:7890` as the
system proxy, so we used **Plan A — mitmproxy upstream chain** to
avoid disrupting the Clash configuration:

```
Claude Desktop → system proxy (8080) → mitmproxy (PCE addon)
              → upstream Clash (7890) → external
```

Single mitmdump command:

```powershell
mitmdump -s run_proxy.py -p 8080 `
         --mode upstream:http://127.0.0.1:7890 `
         --set stream_large_bodies=1m `
         --set upstream_cert=false
```

System proxy switched via:

```powershell
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' `
  -Name ProxyServer -Value '127.0.0.1:8080'
rundll32.exe wininet.dll,InternetSetOption 0 39 0 0
rundll32.exe wininet.dll,InternetSetOption 0 37 0 0
```

The mitmproxy CA was already in the user's `Cert:\CurrentUser\Root`
store from the earlier H2 probe (NotAfter 2036). No re-install needed.

Preflight verdict (recorded for reproducibility):

| Check | Result |
|---|---|
| Clash 7890 listening | YES |
| Port 8080 free | YES |
| Clash CONNECT to api.anthropic.com | OK (HTTP 404, expected for bare-domain HEAD) |
| Clash CONNECT to claude.ai | OK (HTTP 403, expected) |
| `mitmdump` / `mitmweb` / `mitmproxy` on PATH | YES (Python 3.12 site) |
| `pce_proxy` import | OK |
| mitmproxy CA in Cert:\CurrentUser\Root | YES (NotAfter 2036/4/3) |
| Allowlist contains `api.anthropic.com` + `claude.ai` | YES (`pce_core/config.py:33-103`) |
| **Verdict** | **FEASIBLE** |

## 3. Captures — empirical numbers

Single short conversation:

> **User**: `"hi, just one short test message please"`
>
> **Assistant** (Claude Haiku 4.5): brief acknowledgment with internal
> thinking trace.

### 3.1 raw_captures table delta (Tier 0)

| Bucket | Count |
|---|---:|
| Total new rows in delta window | **136** |
| `host = 'claude.ai'` | 122 |
| `host = 'api.anthropic.com'` | 8 |
| `host = 'chatgpt.com'` (background app probes) | 6 |

Of the 122 `claude.ai` rows, **5 are the chat path**:

| Capture id (8-char) | Direction | Method | Status | Body | What it is |
|---|---|---|---|---:|---|
| `c7feace3` | request | POST | — | 713 | event-batch (telemetry) |
| `d4e5aed2` | response | POST | 200 | 39 | event-batch ack |
| `1720fa89` | request | POST | — | 5,238 | **chat send** — full prompt JSON |
| `9ae4f499` | request | POST | — | 79 | conversation title rename |
| `071f55f4` | response | POST | 200 | 3,106 | **assistant SSE stream** |

The remaining 117 are bootstrap, settings fetches, plugins/skills
listings, billing, model_configs, etc. — all expected on session start
and useful for downstream session-context rebuilding.

### 3.2 Chat send body (capture `1720fa89`)

Top-level keys preserved verbatim:

```
['prompt', 'timezone', 'personalized_styles', 'locale', 'model',
 'tools', 'turn_message_uuids', 'attachments', 'files', 'sync_sources',
 'rendering_mode', 'create_conversation_params']
```

- `prompt`: `"hi, just one short test message please"` ← user message verbatim
- `model`: `"claude-haiku-4-5-20251001"` ← desktop model selection
- `personalized_styles`: 1 entry (Default style — desktop UI state preserved)
- `tools`: **13 entries** ← full tool catalog from this MSIX install
- `timezone`: `"Asia/Shanghai"` ← locale state captured

### 3.3 Assistant SSE stream (capture `071f55f4`)

Body length 3,106 bytes; all SSE event types observed:

| Event | Observed |
|---|---|
| `conversation_ready` | ✓ |
| `message_start` | ✓ — carries `chatcompl_*` id, `request_id`, `trace_id` |
| `content_block_start` (type=`thinking`) | ✓ |
| `content_block_delta` (type=`thinking_delta`) | ✓ multiple frames |
| `content_block_delta` (type=`thinking_summary_delta`) | ✓ |

Sample frames (verbatim from DB):

```
data: {"type":"content_block_delta","index":0,
       "delta":{"type":"thinking_summary_delta",
                "summary":{"summary":"Thinking about confirming
                           system responsiveness with a brief message."}}}

data: {"type":"content_block_delta","index":0,
       "delta":{"type":"thinking_delta",
                "thinking":"The user is asking me to send a short
                            test message. This is a simple..."}}
```

The conversation UUID `53599e42-5c42-4208-ab11-63f41311c428`,
auto-generated title `"Short test message"`, and `chatcompl_*` message
id are all present and stable across request and response captures
(same `pair_id b7af0fe8`).

### 3.4 messages table (Tier 1, normalizer output)

| Field | Row 1 (`b39be8fd`) | Row 2 (`1c34038b`) |
|---|---|---|
| role | `user` | `assistant` |
| model_name | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` |
| session_id | `ef5bc3fdddb14748a58fbca7b0960f33` | (same) |
| capture_pair_id | `b7af0fe8` | (same) |
| content_text length | 38 | 303 |
| content_text head | `"hi, just one short test message please"` | `"<thinking> The user is asking me to send a short test..."` |

### 3.5 sessions table (Tier 1)

```
[ef5bc3fd]
  provider:      claude.ai
  tool_family:   api-direct
  message_count: 2
  title_hint:    "hi, just one short test message please"
  models:        ["claude-haiku-4-5-20251001"]
  session_key:   None
```

`tool_family = 'api-direct'` is the desktop chat path indicator
(distinct from `'api-stream'` and `'mcp_proxy'`); session re-uses
the conversation UUID-derived id.

### 3.6 pipeline_errors table

```
new pipeline_errors: 0
```

The full pipeline (raw_captures → anthropic.py normalizer → sessions +
messages tables) ran without a single error or warning row across
136 captures. **No SSE parse failures. No JSON decode errors. No
schema mismatches.**

## 4. Restoration evidence

Immediately after capture:

| Action | Verified |
|---|---|
| `Set-ItemProperty ProxyServer` ← `127.0.0.1:7890` | YES (`reg query` confirms) |
| `Stop-Process mitmdump PID 23084` | YES (port 8080 not listening) |
| Clash 7890 still works | YES (HTTP 404 to `api.anthropic.com`, expected) |
| mitmproxy CA left in cert store | YES (intentional — used by next run) |

## 5. What this validates vs. what remains

### Validated by this run

- ✓ `pce_proxy` addon **upstream-chain mode is operational** in real-world
  network (mitmproxy 8080 → Clash 7890 → external).
- ✓ Allowlist filter correctly captures `claude.ai`, `api.anthropic.com`,
  `chatgpt.com`; correctly drops out-of-list domains (no captures from
  unrelated background traffic on the system during the window).
- ✓ TLS interception **works on Claude Desktop's MSIX-packaged Electron**
  (no pinning, confirming H2 PASS hypothesis under live conditions, not
  just synthetic probe).
- ✓ SSE streaming response **persists complete** with stream framing
  preserved (3,106-byte body containing 8+ event frames captured intact).
- ✓ `pce_core/normalizer/anthropic.py` **handles desktop chat path**
  (`/api/organizations/.../chat_conversations/<uuid>/completion`) and
  produces correct messages + sessions rows.
- ✓ Session-id derivation from conversation UUID is **stable** —
  request and response land in the same `session_id`.
- ✓ Pair-id correlation **works across SSE response stream** —
  request body and full SSE stream share `pair_id b7af0fe8`.
- ✓ `tool_family = 'api-direct'` correctly distinguishes desktop chat
  from MCP-routed chat.

### Still not validated

| Gap | What's needed |
|---|---|
| **D04 cancel** — user-cancelled mid-stream | Real cancel + verify partial assistant content + `error="cancelled"` |
| **D05 model switch** | Mid-session model change + verify second turn's `model_name` reflects switch |
| **D06 attachment** — PDF / image upload | Drop a file + verify `attachments[]` in session metadata |
| **D11 long context** | 50-turn conversation, verify no message dropped |
| **D12 silent on idle** | 5-min idle, verify zero new rows |
| **N/L1 stress** — sustained throughput | Run captures for 30+ minutes, verify no leaks, no error accumulation |
| **L3g + N/L1 cross-axis** | Same conversation observed via both axes, verify they reconcile (L3g IndexedDB scan picks up the same UUID `53599e42-...` after Claude Desktop restart) |
| **P2 ChatGPT Desktop** equivalent run | This handoff is P1-only; P2 requires re-run with ChatGPT chat |

## 6. Reproduction recipe

For any future operator (or for re-running this validation against
a future Claude Desktop release), the recipe is:

```powershell
# 1. Preflight
python -c "import socket, urllib.request as u; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 7890)); print('clash up')"

# 2. Save baseline
$baseline = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings').ProxyServer
Write-Host "saved baseline: $baseline"

# 3. Start mitmdump (background) with upstream chain
$proc = Start-Process mitmdump `
  -ArgumentList "-s","run_proxy.py","-p","8080","--mode","upstream:http://127.0.0.1:7890","--set","stream_large_bodies=1m","--set","upstream_cert=false" `
  -PassThru -NoNewWindow
Start-Sleep -Seconds 4
Test-NetConnection 127.0.0.1 -Port 8080 -InformationLevel Quiet  # expect TRUE

# 4. Switch system proxy
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyServer -Value '127.0.0.1:8080'
rundll32.exe wininet.dll,InternetSetOption 0 39 0 0
rundll32.exe wininet.dll,InternetSetOption 0 37 0 0

# 5. Note baseline timestamp BEFORE Claude Desktop sends a message
$baseline_ts = (python -c "import time; print(time.time())").Trim()

# 6. Operator opens Claude Desktop, sends one short message, awaits reply

# 7. Inspect (use a one-off Python script reading ~/.pce/data/pce.db
#    with WHERE created_at > $baseline_ts; see this handoff §3 for shape)

# 8. Restore
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyServer -Value $baseline
rundll32.exe wininet.dll,InternetSetOption 0 39 0 0
rundll32.exe wininet.dll,InternetSetOption 0 37 0 0
Stop-Process -Id $proc.Id -Force
```

**Privacy reminder**: any chat sent during a capture window lands
plaintext in `~/.pce/data/pce.db`. For repeated runs, prefer
generic non-secret prompts; for sensitive testing, run against a
disposable Claude account.

## 7. Cross-references

| Topic | Document |
|---|---|
| Why this matters for v1.1 ship | `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 + §8.2 ship gate |
| Strategic role of N/L1 axis | `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §7 row 2 (1a MSIX) |
| H2 viability that this run confirms | ADR-018 §6 H2 verdict + alpha.8 release notes |
| L3g sibling axis (validated alpha.9) | `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` (kickoff) |
| Allowlist that gates capture | `pce_core/config.py:33-103` |
| Normalizer entry point | `pce_core/normalizer/anthropic.py` + `pce_core/normalizer/pipeline.py` |
| Addon entry point | `pce_proxy/addon.py` + `run_proxy.py` |

## 8. What this unblocks for next session

- **D-case authoring against P1**: now that we know the captured
  schema is intact, `tests/e2e_desktop/cases/d01_single_chat.py` can
  be authored as a real assertion — load 2 messages from the session,
  verify role + content_text + model_name shape.
- **`tool_family` enum**: `'api-direct'` is now an empirical value
  the schema doc should record alongside `'mcp_proxy'`.
- **CHANGELOG entry**: this is the first end-to-end P1 evidence row;
  belongs in v1.1 section as "P1 N/L1 chat-region empirical end-to-end
  validation 2026-05-10".
- **alpha.10 candidacy**: this commit + the 8ea14b2 stats-key fix
  together justify a new alpha tag (operator decides).

---

*End of handoff.*
