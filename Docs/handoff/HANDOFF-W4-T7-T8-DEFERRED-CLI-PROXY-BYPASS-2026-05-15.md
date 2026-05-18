---
title: "W4-T7/T8 deferred — Codex/Gemini CLI L1 capture blocked by proxy bypass"
status: DEFERRED (V-HERMETIC unchanged; live-evidence blocked by engineering issue)
date: 2026-05-15
session: 08:39 UTC (failed runs) + 09:00 UTC (diagnostic)
operator: Claude Code agent (autonomous diagnostic)
predecessor: HANDOFF-W1-T8-CODEX-CLI-L3H-2026-05-15.md + HANDOFF-W1-T10-GEMINI-CLI-L3H-2026-05-15.md
canonical: Docs/stability/redundancy-sprint/04-wave4-third-leg.md (rows W4-T7, W4-T8)
artifacts:
  - mitmdump live log shows ZERO traffic to api.openai.com or generativelanguage.googleapis.com during the runs
  - codex CLI run "Terminated" (timeout, no proxy interception)
  - gemini CLI run succeeded ("2 + 2 is 4.") but bypassed proxy entirely
---

# W4-T7/T8 deferred — Codex / Gemini CLI L1 capture blocked

## TL;DR

Tried to capture L1 traffic for codex CLI and gemini CLI by routing
through the running mitmproxy on 127.0.0.1:8080 via `HTTPS_PROXY` env
var (same approach that worked for W4-T6 claude-code). Both CLIs
**bypassed the proxy entirely**: zero captures landed on `api.openai.com`
or `generativelanguage.googleapis.com`, and the mitmdump live log
records no connection attempts from these CLIs.

This is a CLI-level proxy bypass issue, **not a PCE bug**. Fixing it
requires per-CLI engineering work that is out of scope for the W4
batch.

## What we tried

### Approach 1: HTTPS_PROXY env var only
```
HTTPS_PROXY="http://127.0.0.1:8080" \
HTTP_PROXY="http://127.0.0.1:8080" \
codex exec --sandbox read-only "What is 2+2?"
```
Result: `Terminated` after timeout. No api.openai.com captures.

```
HTTPS_PROXY="http://127.0.0.1:8080" \
gemini --skip-trust -p "What is 2+2?"
```
Result: Returned "2 + 2 is 4." successfully. No generativelanguage captures
(CLI went direct, not via 127.0.0.1:8080).

### Approach 2: HTTPS_PROXY + NODE_TLS_REJECT_UNAUTHORIZED=0 (for gemini)
Same as approach 1 for gemini, with TLS cert rejection disabled. Still
no captures — confirms the proxy bypass is not a cert-trust issue.

### Approach 3: HTTPS_PROXY + SSL_CERT_FILE (for codex)
```
HTTPS_PROXY="http://127.0.0.1:8080" \
SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
codex login status
```
Result: codex returned "Logged in using ChatGPT" — CLI works, but again
no proxy traffic for the subsequent `exec` call.

## Root cause hypotheses

### Codex CLI (Rust binary, `@openai/codex` npm shim wraps a Rust executable)
The Rust HTTPS client (likely `reqwest` + `rustls` or `hyper-rustls`)
**does not honor the `HTTPS_PROXY` env var** by default. Rust's tokio
runtime does not pick up OS-level proxy settings either. Even if it did,
the bundled root certificate store (typically `webpki-roots` or
`rustls-native-certs`) does not include the mitmproxy CA, so any
TLS handshake would fail.

The websocket connection codex uses
(`wss://api.openai.com/v1/responses`) compounds the issue: WebSocket
TLS handshake through a CONNECT-tunnel proxy needs explicit support
that not every Rust WS library has out of the box.

Workarounds to investigate (all 2-3h+ engineering work each):
- (a) Set `CODEX_HTTPS_PROXY` or similar config if codex supports one
  (none documented today).
- (b) Patch the mitm CA into the bundled `webpki-roots`-equivalent at
  build time, or use `rustls-native-certs` so Windows trust store is
  honored.
- (c) Add a Windows-level transparent proxy (`netsh winhttp set proxy`)
  — currently shows "直接访问(没有代理服务器)", so even
  WINHTTP doesn't route.

### Gemini CLI (Node ESM, `@google/gemini-cli`)
Node 22+ ships `undici` as the default `fetch` HTTP client. Undici's
default Pool / Agent **does not honor `process.env.HTTPS_PROXY`**; you
must explicitly construct `new ProxyAgent(url)` and pass it to the
fetch call. The gemini CLI uses native fetch (no explicit ProxyAgent),
so the env var is ignored.

Workarounds:
- (a) Monkeypatch undici's default dispatcher via a preload script.
- (b) Use a different node version (Node 18 with `node-fetch` + the
  older `https` module that honors env-var proxies). Out of band.
- (c) System-level transparent proxy on Windows.

## What's NOT blocked

These deferrals do **not** affect the W1 evidence we already have:
- W1-T8 codex L3h via pce_cli_wrapper relay: **PASS** ✅
- W1-T9 codex L3g via 24 existing sessions: **PASS** ✅
- W1-T10 gemini L3h via pce_cli_wrapper relay: **PASS** ✅
- W1-T11 gemini L3g via fresh session + bug fix: **PASS** ✅

So F6 P7 Codex CLI and F6 P8 Gemini CLI **each have 2 V-GREEN legs**
(L3h + L3g). The W4-T7/T8 third leg (L1) is the only thing blocked.

## Status against REDUNDANCY-AUDIT-MATRIX

| Scenario | V-GREEN now | Target | Blocked by |
|---|:---:|:---:|---|
| F6 P7 Codex CLI | 2 (L3h + L3g) | 3 | L1 via mitmproxy (this handoff) |
| F6 P8 Gemini CLI | 2 (L3h + L3g) | 3 | L1 via mitmproxy (this handoff) |

P5.D.1 Phase A mid-gate (Tier-α 10/10 ≥3 V-GREEN) **cannot ship**
without one of:
- (a) Resolving the proxy bypass (engineering work, 1-2 day-week)
- (b) Adding a different third leg (e.g. clipboard L4a for the CLI
  output, but CLI text is usually piped not copied)
- (c) Accepting 2/3 V-GREEN as ship-bar for P7/P8 specifically, with
  a documented exception

## Recommended next step

Open an issue (or task in P5.D.1 backlog) titled
"P5.D.1 W4 — CLI L1 proxy bypass: codex + gemini" with:
- A standalone test that runs codex/gemini with explicit
  `HTTPS_PROXY` env var and asserts zero captures land (reproducing
  this finding)
- A spike branch that tries each of the 3 workarounds above and
  reports findings

Rough estimate: 1-2 day engineering for either codex (Rust) or
gemini (Node undici) workaround. Parallel work feasible.

This handoff is a **diagnostic record**, not a fix. P7 / P8 remain at
2 V-GREEN until one of the workarounds lands.
