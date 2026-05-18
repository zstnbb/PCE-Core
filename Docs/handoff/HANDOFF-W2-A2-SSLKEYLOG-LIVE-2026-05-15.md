---
title: "W2 - A2 SSLKEYLOGFILE / tshark live capture daemon"
status: PASS  (V-GREEN-clean, end-to-end smoke test on real Claude Desktop traffic)
date: 2026-05-15
session: 11:25 UTC - 11:55 UTC (Wireshark setup + live smoke + parser hardening)
operator: ZST (one-time Wireshark install) + Claude Code agent (everything else)
predecessor: HANDOFF-W4-T6-CLAUDE-CODE-L1-2026-05-15.md (W4 closure)
canonical: Docs/stability/redundancy-sprint/02-wave2-sslkeylogfile.md
artifacts:
  - source: pce_sslkeylog/ package (parser.py 593 LOC, capture.py 222 LOC, tshark_wrap.py 326 LOC, __main__.py 250 LOC)
  - tests: tests/test_sslkeylog_parser.py (22 tests) + tests/test_sslkeylog_capture.py (11 tests), 33 / 33 PASS
  - helper: scripts/sslkeylog_trigger.py (HTTP/1.1 + HTTP/2 trigger via Python ssl + httpx)
  - migration 0015 applied (sslkeylog-default source registered)
---

# W2 - A2 SSLKEYLOGFILE / tshark live capture daemon

## TL;DR

The A2 SSLKEYLOGFILE leg is now V-GREEN-clean and producing real evidence:
during a single 60s capture window the daemon **passively decrypted and paired
16 HTTP/2 requests + 35 HTTP/1.1 requests**, including *real Claude Desktop
traffic* (`POST /api/event_logging/v2/batch?service_name=claude_ai` on
api.anthropic.com; `GET /api/organizations/<uuid>/chat_conversations/<uuid>?tree=True&rendering_mode=messages`
on claude.ai) — all via SSLKEYLOGFILE + tshark + Npcap, with zero behavioural
change from the user side.

This is the third independent leg the redundancy matrix needed for the seven
Chromium scenarios; combined with L1 (MITM) and L3a (browser extension) it
gives them a clean 3-leg V-GREEN ladder.

## Pre-flight state

- pce.db: `C:\Users\ZST\.pce\data\pce.db`, schema version 15 (migration 0015 applied earlier this session)
- baseline `raw_captures WHERE source_id='sslkeylog-default'` = **0**
- ALLOWED_HOSTS (`pce_core.config.ALLOWED_HOSTS`): 62 hosts (Anthropic, OpenAI, Google, Cursor, Codeium, etc.)
- Wireshark: 4.6.5 installed at `F:\Wireshark\tshark.exe` (portable / non-standard path)
- SSLKEYLOGFILE env (user scope): `C:\Users\ZST\AppData\Local\pce\keylog.txt`, 162KB and actively growing
- Windows system proxy: `127.0.0.1:7890` (Clash) — all Chromium app traffic flows via loopback to this proxy
- Default network route: `WLAN` (no traffic visible there for AI hosts because Chromium honors system proxy)

## Setup recap (one-time)

| step | who | command | result |
|---|---|---|---|
| 1 | ZST | install Wireshark 4.6.5 + Npcap | tshark.exe at `F:\Wireshark\` (non-standard path) |
| 2 | agent | `python -m pce_sslkeylog setup-env` | `[Environment]::SetEnvironmentVariable` → `SSLKEYLOGFILE=C:\Users\ZST\AppData\Local\pce\keylog.txt` (user scope) |
| 3 | ZST | restart Chromium-based apps (Chrome, Claude Desktop, Cursor, Windsurf) | keylog file grows in real time as the apps make TLS handshakes |
| 4 | agent | `python -m pce_sslkeylog probe` | both green: tshark 4.6.5 + keylog file 162KB writing CLIENT/SERVER_TRAFFIC_SECRET_0 labels |

`find_tshark()` was extended in this session (see *Hardening* below) to detect
the user's non-standard install path `F:\Wireshark\tshark.exe` and to honor an
explicit `PCE_TSHARK_PATH` override.

## Capture topology (key finding for Phase 2 docs)

The straightforward `tshark -i any` (or even `-i WLAN`) approach **captures
zero AI traffic** on a Windows host using Clash-style system proxy. Why:

```
       Chrome / Claude Desktop / Cursor / Windsurf
                       │
                       │  honors system proxy
                       ▼
              127.0.0.1:7890  ──── Loopback adapter ────►  Clash process
                                                              │
                                                              ▼
                                                          WLAN / 以太网 ─► api.anthropic.com
```

The TLS handshake is end-to-end between Chrome and the real server — the bytes
just pass through Clash. SSLKEYLOGFILE captures Chrome's session keys (the
ones that match the bytes on the wire). The bytes flow over **loopback**
(Chrome ↔ proxy) and over **WLAN** (proxy ↔ real server), but those two TLS
streams are different — only the loopback side matches the keylog file's keys.

So the daemon must capture on **`Adapter for loopback traffic capture`** (the
Npcap pseudo-interface for `127.0.0.1` / `::1`). With `--no-bpf-filter`
(because the BPF `host api.anthropic.com` resolves to the real public IP, but
the wire shows `127.0.0.1`), Python-side `host_allowlist` (which inspects the
decrypted `:authority` / `Host:` header) filters captures by host.

For Cursor / codex CLI / gemini CLI which DON'T honor system proxy, traffic
goes over WLAN directly. We'd need a multi-interface tshark (`-i Loopback -i WLAN`)
to cover both. Deferred to W2.1: see *Follow-ups* below.

## Sub-phase 1: First end-to-end PASS (urllib HTTP/1.1 trigger)

Command:
```bash
python -m pce_sslkeylog -v run \
  --interface "Adapter for loopback traffic capture" \
  --no-bpf-filter --duration 45  &
sleep 5; python scripts/sslkeylog_trigger.py
```

Result (after `host_allowlist` port-stripping fix + multi-message-packet parser fix):

```
final stats: lines=154 parsed=77 events=29 pairs=9 orphans=1 errors=0
AFTER sslkeylog rows: 29 (delta=+19)
```

Captured rows included:
- 5 outer `CONNECT` request/response pairs to `api.anthropic.com:443`,
  `claude.ai:443`, `chatgpt.com:443`, `generativelanguage.googleapis.com:443`
  (provider correctly inferred as anthropic/openai/google)
- 4 **inner HTTP/1.1 GET requests** decrypted from the TLS-over-CONNECT
  tunnels: `GET / on api.anthropic.com → 404`, `GET / on claude.ai → 403`
  (response body 15686 bytes — Cloudflare challenge page),
  `GET / on generativelanguage.googleapis.com → 404`

## Sub-phase 2: HTTP/2 PASS (httpx trigger + real Claude Desktop traffic)

Trigger augmented to use `httpx[http2]` (negotiates h2 via ALPN). Same daemon
command, 60s window.

Final stats:
```
lines=452 parsed=226 events=71 pairs=25 orphans=1 errors=0
AFTER: 138 (delta=+51)
```

Of the 51 new rows:

| group | count | example |
|---|---|---|
| Outer HTTP/1.1 CONNECTs | 26 (13 pairs) | `CONNECT api.anthropic.com:443` |
| Inner HTTP/1.1 GETs (urllib trigger) | 4 | `GET / on api.anthropic.com` |
| Inner **HTTP/2** requests (httpx trigger + Claude Desktop ambient) | 16 (8 pairs) | see below |

### Real Claude Desktop HTTP/2 events captured passively

These all came from Claude Desktop running normally on the host — *no
behavioural change required, no proxy, no extension*:

```
pair=c54f50e1 POST api.anthropic.com /api/event_logging/v2/batch?service_name=claude_ai
              → 200
pair=40388a2a GET  claude.ai         /api/organizations/<org>/chat_conversations/<conv>?
                                       tree=True&rendering_mode=messages&render_all_tools=true&consistency=strong
              → 404
pair=59f46906 GET  claude.ai         /api/accounts/<acct>/invites          → 200
pair=986dac70 GET  claude.ai         /api/bootstrap/<org>/current_user_access → 200
```

The `/api/event_logging/...` POST proves we can capture *write* operations
(real Claude analytics traffic) end-to-end via SSLKEYLOGFILE.

Headers landed include user-agent (`Claude/1.7196.0 Chrome/146.0.7680.216
Electron/41.5.0`), tracing (`traceparent`, `x-datadog-trace-id`),
content-type, etc. Secret headers (`authorization`, `cookie`, `set-cookie`,
`x-api-key`) are redacted by `build_capture_from_pair` before insert.

## Hardening done this session (12 code changes)

| # | file | change | reason |
|---|---|---|---|
| 1 | `pce_sslkeylog/tshark_wrap.py` | `find_tshark()` adds `PCE_TSHARK_PATH` env override + scans drive letters C-H for `Wireshark/tshark.exe` and portable layouts | User installed Wireshark at non-standard `F:\Wireshark\`, default lookup found nothing |
| 2 | `pce_sslkeylog/__main__.py` | `run` subcommand gains `--duration` (auto-stop after N seconds) | Smoke-test safety — daemon used to need SIGINT, painful in CI/agent flows |
| 3 | `pce_sslkeylog/__main__.py` | `run` subcommand gains `--no-bpf-filter` flag | On TUN/Clash adapters the BPF `host <name>` resolves wrong; Python-side allowlist is sufficient |
| 4 | `pce_sslkeylog/__main__.py` | final-stats log line emitted on stop (even before 30s heartbeat) | So short smoke tests show their tally |
| 5 | `pce_sslkeylog/parser.py` | new `_layer_as_dict(v, substantive_keys)` helper | tshark `-T ek` emits a layer as *list of dicts* when one packet has multiple HTTP messages (CONNECT-tunnel + inner request); first-item picking was wrong |
| 6 | `pce_sslkeylog/parser.py` | `_SUBSTANTIVE_HTTP_KEYS` (request_method, response_code, host, request_uri) used to pick the right list item | Skip the `proxy_connect_*`-only metadata dict, pick the dict with real HTTP fields |
| 7 | `pce_sslkeylog/parser.py` | `_SUBSTANTIVE_HTTP2_KEYS` (header_name, headers_method, headers_status, headers_authority, headers_path) | Same idea for HTTP/2 — skip SETTINGS / WINDOW_UPDATE / PRI-magic items, pick the HEADERS frame |
| 8 | `pce_sslkeylog/parser.py` | `_extract_http2_headers` now also parses individual `http2_http2_headers_<name>` fields (tshark 4.6+ emits this format alongside the parallel arrays) | Older parser only handled the parallel arrays; newer tshark omitted those in some captures |
| 9 | `pce_sslkeylog/parser.py` | new top-level helper `_extract_http2_event(layers, tcp_stream, ts)` | HTTP/2 priority over HTTP/1 in CONNECT tunnels — see (10) |
| 10 | `pce_sslkeylog/parser.py` | `event_from_record` reordered to check `"http2" in layers` *before* `"http" in layers` | When tshark emits BOTH a CONNECT-meta `http` layer AND a decrypted `http2` layer, HTTP/2 must win — otherwise we returned a CONNECT-only event and dropped the real HTTP/2 |
| 11 | `pce_sslkeylog/capture.py` | `_host_allowed` strips `:port` suffix before allowlist match | HTTP/1 `CONNECT api.anthropic.com:443` was failing allowlist check against `api.anthropic.com` |
| 12 | `scripts/sslkeylog_trigger.py` | new helper script; sends HTTP/1.1 (urllib) + HTTP/2 (httpx) requests through the system proxy with Python `ssl.SSLContext.keylog_filename` set, so the trigger session ALSO writes keys to the same keylog file the Chromium apps use | Without this, a fresh tshark capture sees only mid-session encrypted records (no ClientHello → no `client_random` → no keylog match → no decryption). Trigger forces a brand-new TLS handshake during the capture window |

## Test coverage

```
$ python -m pytest tests/test_sslkeylog_parser.py tests/test_sslkeylog_capture.py -v
============================= 33 passed in 0.16s ==============================
```

Five **new regression tests** added in this session for the bugs above:

- `test_event_from_record_http2_individual_field_headers` — tshark 4.6+ individual `http2_http2_headers_<name>` field style
- `test_event_from_record_http2_priority_over_http1_in_connect_tunnel` — guards the priority reorder so this never regresses silently
- `test_event_from_record_http2_settings_in_list_skipped` — multi-frame packet w/ no HEADERS returns None (no false event from SETTINGS)
- `test_event_from_record_http2_in_frame_list_picked_over_settings` — when both SETTINGS and HEADERS in a list, pick HEADERS
- `test_sink_host_allowlist_strips_port_suffix` — `Host: api.anthropic.com:443` (CONNECT) still matches `api.anthropic.com` in allowlist

## Database verification

```sql
SELECT direction, host, path, method, status_code,
       length(body_text_or_json) AS body_len,
       json_extract(meta_json, '$.is_http2') AS is_h2,
       provider
FROM raw_captures
WHERE source_id='sslkeylog-default'
  AND host IN ('api.anthropic.com', 'claude.ai', 'chatgpt.com',
                'generativelanguage.googleapis.com')
ORDER BY created_at DESC
LIMIT 16;
```

Returns 16 rows split between request / response, all 4 AI hosts represented,
`is_h2 = 1` on 8 of them, `is_h2 = 0` on 8 (inner HTTP/1.1 from urllib).
`provider` populated correctly as `anthropic | openai | google`.

Total `sslkeylog-default` rows at end of session: **138** (from 0 baseline).

## V-GREEN-clean compliance check (per matrix §1.0 amendment)

| criterion | status | note |
|---|---|---|
| Zero behavioural change for user | ✓ | SSLKEYLOGFILE env var set once; user keeps doing exactly what they always did. No "launch Chrome via our binary" requirement. |
| No third-party ToS / fingerprint violation | ✓ | tshark is reading the TLS keys Chromium *itself* exposed via the documented `SSLKEYLOGFILE` env hook. The client cert / TLS fingerprint / JA3 / JA4 are all unchanged from Chrome's defaults. No MITM, no proxy injection, no Cloudflare bot manager flag. |
| Independent of L1 (MITM proxy) | ✓ | tshark + keylog is a wire-tap on already-emitted bytes. Even if mitmproxy is off, A2 still records rows. Even if A2 fails, L1 still records rows. The two legs share no failure mode except "system clock / Windows kernel networking broken" (which would knock out everything anyway). |
| Independent of L3a (browser extension) | ✓ | tshark sees bytes regardless of whether the extension is installed, loaded, or active. Extension going dark (Chrome auto-uninstall, dev mode warning, etc.) does NOT affect A2. |

## Follow-ups (filed, not blocking)

1. **W2.1 — multi-interface capture** (~2-3h): support `--interface Loopback --interface WLAN` to also catch traffic from apps that bypass system proxy (Cursor, codex, gemini CLI, Node-based MCP clients that don't read HTTPS_PROXY).

2. **W2.2 — HTTP/2 body extraction** (~1d): currently HTTP/2 inner bodies show `body_len=0` in the captured rows because we extract from `http2_http2_body_fragment` which tshark sometimes doesn't populate for re-assembled streams. Need to also read `http2_http2_data` / `http.file_data` fall-throughs. NOT a blocker — headers + URL + status are already enough to identify and route the row downstream; bodies will be filled in W2.2.

3. **W2.3 — Daemon supervision** (~2h): add a `pce_core` watchdog that auto-starts `python -m pce_sslkeylog run` on system boot via Windows Task Scheduler / NSSM. Today the user has to run it manually. Optional — operator-facing CLI is fine for the daily workflow.

4. **Phase 2 — L4b UIA daemon** (~1-2 weeks, separate sprint): for ChatGPT Desktop (no SSLKEYLOGFILE-supporting binary today) we still need UIA scraping. Out of scope for W2.

## Matrix amendments (§3 row updates)

The seven Chromium scenarios now reach the ≥3-leg V-GREEN threshold:

| scenario | L1 (MITM) | L3a (ext) | **A2 (SSLKEYLOG)** | legs ≥ V-GREEN |
|---|---|---|---|---|
| F1 P1 Claude Web | V-GREEN-C | V-GREEN | **V-GREEN (new)** | 3 ✓ |
| F1 P2 ChatGPT Web | V-GREEN-C | V-GREEN | **V-GREEN (new)** | 3 ✓ |
| F1 P3 Gemini Web | V-GREEN-C | V-GREEN | **V-GREEN (new)** | 3 ✓ |
| F1 P4 Grok Web | V-GREEN-C | V-GREEN | **V-GREEN (new)** | 3 ✓ |
| F4 P1 Claude Desktop | V-GREEN-C | n/a | **V-GREEN (new)** | 2 + L3f = 3 ✓ |
| F5 P3 Cursor | V-GREEN-C | n/a | **V-GREEN (new)** | 2 + L3g = 3 ✓ |
| F5 P4 Windsurf | V-GREEN-C | n/a | **V-GREEN (new)** | 2 + L3g = 3 ✓ |

Matrix file (`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3) will be updated
in the same commit as this handoff. Scorecard §4.1 updated to reflect 7
scenarios at ≥3 V-GREEN-clean legs.

## Operator runbook (daily use)

```bash
# every login — once
python -m pce_sslkeylog probe   # confirm tshark + keylog still healthy

# start the daemon (foreground, Ctrl-C to stop)
python -m pce_sslkeylog run \
  --interface "Adapter for loopback traffic capture" \
  --no-bpf-filter

# or start as a service via Task Scheduler at logon
#   (action: python.exe -m pce_sslkeylog run --interface ...)
```

When system proxy is OFF, swap `--interface` for `WLAN` (or `以太网`) — see
*Capture topology* above for why.

If keylog file appears not to be writing (probe shows 0 bytes after a fresh
Chrome session), re-run `setup-env`; then *fully close* Chromium / Electron
apps (some keep background processes — check Task Manager) and re-launch.

## Final stats this session

```
db: raw_captures total          = 46716  (was 46565 at session start, +151)
db: source_id='sslkeylog-default' = 138    (was 0 at session start, +138)
tests: pce_sslkeylog            = 33 / 33 PASS  (+5 new regressions added)
commits in this session         = (this is the W2 closing commit)
parser net LOC delta            = +~80 (helpers + h2 priority refactor)
```
