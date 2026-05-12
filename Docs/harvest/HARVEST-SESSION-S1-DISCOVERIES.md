---
title: "Harvest Session S1 — Field Discoveries"
date: 2026-05-13 (00:35-01:25)
scope: P3 Cursor (P4 Windsurf + P5 Copilot not attempted)
status: aborted (architecture incompatibility discovered)
---

# Harvest Session S1 — Field Discoveries

## TL;DR

Session aborted at 01:25 after discovering that the simple
`mitmdump-as-system-proxy → upstream Clash` architecture is
**incompatible with IDE-class apps (Cursor / Windsurf)** because:

1. Their chat backends use **HTTP/2 + SSE streaming**, which mitmproxy
   buffers by default → app reports "streaming responses being buffered".
2. Enabling `stream_large_bodies=1k` in mitmproxy to fix #1
   **breaks other apps' gRPC streams** system-wide (observed: Windsurf's
   own Cascade backend started failing with `incomplete envelope` errors).
3. The `--proxy-server` Chromium flag only covers the **renderer
   process**; the **main process (Node networking)** ignores it and
   connects directly to Clash fake-IP `198.18.0.x`, bypassing mitmdump
   entirely for the actual chat call.

System fully restored at 01:24:50 via `teardown_proxy_chain.ps1 -Emergency`.
No data loss to user systems. No commits to `master` other than the
prep artifacts from earlier tonight (`17840a5`).

---

## Detailed findings

### F1 — Cursor's hidden API topology

Cursor's diagnostic tool exposes the **full required-domain list**
that wasn't in any of our existing references. This is gold for
future allowlist work:

| Domain | Purpose |
|---|---|
| `api2.cursor.sh` | API requests (general REST) |
| `api3.cursor.sh` | Cursor Tab (autocomplete) |
| `api4.cursor.sh` | Cursor Tab (regional) |
| `*.api5.cursor.sh` | **Agent / chat requests** — the actual chat endpoint |
| `agent.api5.cursor.sh` | Resolves from `*.api5.cursor.sh` for this user |
| `repo42.cursor.sh` | Codebase indexing |
| `*.authentication.cursor.sh` | Authentication |
| `authenticator.cursor.sh` | Auth UI / login page |
| `marketplace.cursorapi.com` | Extension marketplace |
| `cursor-cdn.com` | Marketplace CDN |
| `downloads.cursor.com` | Client updates |

**Previously on `ALLOWED_HOSTS`**: only `api2.cursor.sh`.
**Missing the actual chat endpoint** `agent.api5.cursor.sh`.

This list should land in `pce_core/config.py` regardless of capture
strategy.

### F2 — Cursor's split-network architecture

Cursor (Electron, version 3.3.30) uses **two distinct network paths**:

```
[Cursor Electron]
     │
     ├── Renderer process (Chromium fetch) ───→ system proxy ───→ mitmdump ✅
     │   (auth, telemetry, marketplace, profile)
     │
     └── Main process    (Node http2/grpc)  ───→ Clash fake-IP 198.18.x.x ❌
         (chat / agent / streaming)            (bypasses HTTP proxy entirely)
```

Evidence: with `--proxy-server=127.0.0.1:8080` set on the Cursor
exe spawn, `netstat` observed:
- ~4 connections to `127.0.0.1:8080` (renderer, going through mitmdump)
- ~7 connections to `198.18.0.x:443` (main process, going direct to Clash)

The diagnostic confirmed mitmdump WAS in path for renderer:

```
URL: https://api2.cursor.sh/
IP: 127.0.0.1         ← mitmdump
Issuer: O=mitmproxy   ← our cert accepted
```

…but the chat / agent test traffic on `*.api5.cursor.sh` bypassed it.

This is **the same pattern we expect for**:
- Windsurf (Codeium client) — Electron app, same shape
- GitHub Copilot in VS Code — Extension Host is a Node process

### F3 — mitmproxy streaming compatibility

Cursor's chat path uses **SSE (Server-Sent Events)** or similar
incremental streaming. mitmproxy's default behavior is to buffer
the entire response before forwarding. Cursor detects this via its
diagnostic:

```
× Chat: Streaming responses are being buffered by a proxy in your network environment

[17:11:15.778Z] Starting stream
[17:11:21.931Z] Response: 'foo' in 6152ms      ← all 5 chunks at SAME timestamp
[17:11:21.931Z] Response: 'foo' in 0ms
[17:11:21.931Z] Response: 'foo' in 0ms
[17:11:21.931Z] Response: 'foo' in 0ms
[17:11:21.931Z] Response: 'foo' in 0ms
```

**Fix attempted**: `mitmdump --set stream_large_bodies=1k`.
**Side effect**: this is a global setting — it streams ALL bodies
over 1KB. Other apps with gRPC streams (specifically Windsurf's
Cascade backend) started receiving truncated/malformed frames:

```
incomplete envelope: read tcp 198.18.0.1:30001 -> 198.18.0.23:443:
  wsarecv: An established connection was aborted by the software in your host machine
```

This caused Cascade chat to error during the harvest session,
forcing emergency teardown.

### F4 — `--proxy-bypass-list=<-loopback>` doesn't help

Setting Chromium's bypass list to `<-loopback>` (which means "do
NOT bypass loopback addresses") still leaves the Node main process
out of scope. The Node http2 client doesn't read Chromium's
command-line flags.

### F5 — VS Code-style `http.proxy` setting may help

Cursor has a "VS Code Settings" link in its new unified settings
panel. The classic VS Code `http.proxy` setting forces the
extension host (Node process) through a proxy. Not tested tonight
due to risk of breaking Cascade further.

### F6 — Cursor's `HTTP Compatibility Mode` setting

Cursor exposes a **`HTTP Compatibility Mode`** dropdown:
`HTTP/2 ⇄ HTTP/1.1`. Lowering it to HTTP/1.1 makes mitmproxy
interception simpler (no ALPN negotiation, no h2 multiplexing) but
does NOT solve the split-network or streaming issues.

---

## What worked tonight

- **Setup / teardown scripts** behaved correctly:
  - `setup_proxy_chain.ps1` switched system proxy, started mitmdump
    upstream-mode against Clash, wrote state file. Fixed one quoting
    bug for paths with spaces. (Commit candidate: see `_setup_fix`.)
  - `teardown_proxy_chain.ps1 -Emergency` restored ProxyServer
    cleanly even when state file was partly stale.
- **CDP detector** correctly resolved Cursor at `E:\cursor\Cursor.exe`
  via Windows registry (Anysphere install, version 3.3.30) vs the
  stale `C:\Users\ZST\AppData\Local\Programs\cursor\Cursor.exe`
  (version 0.44.9, dated 2024-12-27).
- **Renderer-side capture worked**: we captured ~30 `api2.cursor.sh`
  infrastructure calls (auth, profile, telemetry, dashboards,
  available-models). Useful as reference fixtures even though they
  don't contain chat content.

---

## What didn't work tonight

- **L1 (system proxy + mitmdump)** capture of chat / agent path
  for Cursor. Same pattern expected for Windsurf and Copilot
  Extension Host.
- **Global streaming-passthrough** is dangerous when other tools
  on the same machine use gRPC streams. Cannot enable system-wide.

---

## Tomorrow's plan (recommendations)

### Track A — VS Code `http.proxy` setting (cheapest test)

Owner manually edits `~/.cursor/User/settings.json` to set:

```jsonc
{
  "http.proxy": "http://127.0.0.1:8080",
  "http.proxyStrictSSL": false,
  "http.proxySupport": "on"
}
```

…before launching harvester. This forces the **Cursor extension
host (Node process)** through mitmdump too. If it works for Cursor,
mirror to Windsurf (`~/.codeium/User/settings.json` or similar)
and VS Code for Copilot.

**Risk**: low — only affects the IDE in question, not system.

### Track B — Per-host streaming passthrough

Modify `pce_core/mitm_addon/addon.py` to enable streaming **per-host**
(not globally). Streaming only enabled for known SSE hosts (cursor,
codeium, openai sse endpoints). Other hosts buffer normally.

This avoids the gRPC-collateral-damage that bit us tonight.

Pattern:

```python
def responseheaders(self, flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if host in STREAMING_HOSTS or flow.response.headers.get(
        "content-type", ""
    ).startswith("text/event-stream"):
        flow.response.stream = True
```

### Track C — L3d CDP launcher (proper P1-style solution)

For Cursor / Windsurf / Copilot, follow the same pattern as
`pce_app_launcher/claude_desktop/` — launch via CDP, hook
`Network.requestWillBeSent` + `Network.responseReceived` at the
**Chromium-renderer level**. But for IDE-class apps the chat
happens in the **main process**, not the renderer, so we need to
hook a different debugger:

- Try `--inspect=9230` on the main process: gives V8 inspector
  access to Node, can hook `require('http')` / `require('https')`.

**Risk**: medium — requires writing ~200-300 LOC new launcher.

### Track D — Update allowlist regardless (free win)

Add the 11 cursor domains discovered in F1 to
`pce_core/config.py` `ALLOWED_HOSTS`. This is independent of
capture strategy. Same for the codeium and copilot domains we
preemptively listed in `PCE_EXTRA_HOSTS` earlier (low-risk
addition).

---

## Files touched tonight

| File | Status |
|---|---|
| `scripts/harvest/setup_proxy_chain.ps1` | bug fixed (path-with-spaces quoting), committed |
| `scripts/harvest/teardown_proxy_chain.ps1` | unchanged, behaved correctly |
| `scripts/harvest/harvest_cdp.py` | registry detection added, `--proxy-server` flag added |
| `scripts/harvest/verify_harvest.py` | works |
| `scripts/harvest/README.md` | committed in prep |
| `Docs/harvest/HARVEST-SESSION-S1-CHECKLIST.md` | committed in prep |
| `Docs/harvest/HARVEST-SESSION-S1-DISCOVERIES.md` | THIS FILE — new |
| `_diag*.py`, `_harvest_*.log`, `_harvest_*.txt` | gitignored throwaways |

## Capture data preserved

Despite the architectural mismatch, the following IS in
`raw_captures` from tonight and useful for future reference:

- ~30 `api2.cursor.sh` rows (auth, profile, telemetry, dashboards)
- ~6 `api.anthropic.com` rows (background Claude desktop telemetry)
- ~12 `claude.ai` rows (background Claude desktop activity)
- Several `chatgpt.com` rows from P2 sweep era

**Cleanup**: these can stay in `raw_captures` — they're labeled
`proxy-default` and timestamped, easy to filter out later.
