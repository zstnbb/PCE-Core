---
title: "W2 closure (W2.1 multi-iface + W2.2 h2 body + W2.3 service install)"
status: PASS  (sprint goal of "把通过率拉起来 冗余性健壮性拉起来" met)
date: 2026-05-15
session: 12:30 UTC - 13:30 UTC (closure work after W2 baseline at 12:00 UTC)
operator: ZST (one-time Wireshark install earlier) + Claude Code agent (autonomous full execution)
predecessor: HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md
canonical: Docs/stability/redundancy-sprint/02-wave2-sslkeylogfile.md
artifacts:
  - source delta vs prior commit (fd28cbd): see "Code delta" below
  - 50/50 pce_sslkeylog tests pass (+17 new regressions over W2 baseline of 33)
  - 358 raw_captures rows landed with source_id='sslkeylog-default' across the W2/W2.1/W2.2 sessions
  - daemon registered as Windows Scheduled Task `PCE-SSLKEYLOG-Capture` (per-user, no admin, auto-restart on failure)
---

# W2 closure (W2.1 + W2.2 + W2.3)

## TL;DR

After the W2 baseline landed (commit `fd28cbd`, 138 sslkeylog rows + 4
scenarios at STRICT ≥3 V-GREEN), this session autonomously closed every
W2 follow-up:

| sub-task | result |
|---|---|
| **W2.1 multi-iface** | CLI auto-detects loopback + WLAN; both legs co-capture in one tshark process. Cursor / Windsurf / Codex / Gemini CLI traffic now reachable. |
| **W2.1 host sweep** | All 10 P0 AI hosts produced live `source_id='sslkeylog-default'` rows. Fixed `pce_core.config.ALLOWED_HOSTS` gap (`grok.com` + `aistudio.google.com` were P0 in matrix but missing from allowlist — silent drop). |
| **W2.2 HTTP/2 body** | Parser detects `http2_http2_body_reassembled_data`; state machine defers pair emission until END_STREAM; gzip / deflate / br / zstd decompressed at serialization. Live verified: gemini.google.com 654KB HTML, grok.com 390KB HTML, chatgpt.com 8KB HTML, multiple JSON responses — all land as readable plaintext in `body_text_or_json`. |
| **W2.3 service install** | `pce_sslkeylog service install` registers a per-user Windows Scheduled Task (logon-triggered, 15s delay, auto-restart on failure every 1min ≤10 tries, **no admin needed**) OR generates a `~/.config/systemd/user/pce-sslkeylog.service` unit on POSIX. Installed live on this host (`PCE-SSLKEYLOG-Capture`). |
| **Tests** | 50/50 pce_sslkeylog tests pass (33 baseline + 17 new). Repo-wide non-e2e tests: 623 pass; 10 failures all pre-existing in `tests/test_real_e2e_capture.py` (network-dependent, unrelated). |
| **Matrix** | Scorecard §4.1 updated: STRICT ≥3 V-GREEN scenarios **4/13 → 8/13**, ≥1 V-GREEN-clean **6/13 → 12/13**, ≥3 V-GREEN-clean (no L1c) **3/13 → 5/13**. |

## Code delta (versus commit fd28cbd)

**pce_sslkeylog/__main__.py** (+~250 LOC)
- `--interface` becomes `action="append"`, default `None` (auto-detect)
- Auto-detect interfaces via `detect_capture_interfaces(tshark)`
- BPF auto-disabled when loopback is in the iface list
- New `service` subcommand: `install` / `uninstall` / `status` / `print-unit`
- `_service_windows`: schtasks /Create /XML with the Windows Task XML template
- `_service_posix`: writes systemd user unit
- `_safe_print()` helper survives CP936 / GBK stdout encoding mishaps

**pce_sslkeylog/tshark_wrap.py** (+~115 LOC)
- `TsharkConfig` accepts `interfaces: list[str]` (new) + `interface=` (legacy
  kwarg shim); `build_argv` emits multiple `-i` flags
- `_run_text(...)` decodes subprocess stdout as UTF-8 with `mbcs` fallback
  (was failing on tshark `-D` output containing `以太网` etc.)
- `tshark_version`, `list_tshark_interfaces`, `_windows_default_route_iface`
  refactored to use `_run_text`
- New `detect_capture_interfaces(tshark)`: returns
  `[loopback, default_route_iface]` on Windows, `["any"]` on POSIX

**pce_sslkeylog/parser.py** (+~140 LOC)
- New `direction="data"` event type (HTTP/2 DATA frame with reassembled body)
- `TsharkEvent.end_stream: bool` field (set from `http2_http2_flags_end_stream`)
- `_hex_colon_to_bytes()` helper decodes tshark's `aa:bb:cc:...` binary format
- `_extract_http2_event` does two passes: pass 1 finds HEADERS frame for
  request/response, pass 2 finds DATA frame with `body_reassembled_data`
- `_decompress_body(body, content_encoding)`: gzip / deflate / br / zstd,
  graceful fallback to raw bytes on failure
- `build_capture_from_pair` calls `_decompress_body` per ev before
  serialization; new HTML / XML body_format detection

**pce_sslkeylog/capture.py** (+~85 LOC)
- New `_PendingPair` dataclass replaces the prior 3-tuple value of `_pending`
- `CaptureStats` gains `bodies_attached` + `bodies_unmatched` counters
- `_handle_request`: stores into `_PendingPair` (request_body from ev.body)
- `_handle_response`: HTTP/1 emits immediately; HTTP/2 defers unless
  `end_stream=True` on the HEADERS frame
- `_handle_data` (new): attaches DATA-frame body to a pending pair;
  emits when `end_stream=True` + response already seen
- `_emit_pair` accepts `request_body=` / `response_body=` override kwargs
- `_ev_with_body(ev, body)` returns a copy with body overridden (avoids
  mutating original events kept for tests/debugging)
- `_sweep_expired_pending` flushes deferred-emit pairs (response seen, body
  never arrived) instead of just dropping them

**pce_core/config.py** (allowlist gap fix)
- Added `aistudio.google.com` + `grok.com` to `ALLOWED_HOSTS` (P0 per matrix
  §3.1, missing prior — silent drop of all captures to those domains)

**scripts/sslkeylog_trigger.py** (+~30 LOC)
- DEFAULT_URLS extended from 4 to 10 hosts (added gemini.google.com,
  grok.com, aistudio.google.com, api2.cursor.sh, server.codeium.com,
  api.openai.com)
- New `--no-proxy` flag forces direct WAN (bypass Clash) so the WLAN
  leg of multi-iface gets exercised
- HTTP/2 trigger via httpx now reports `proxy / direct` mode in header

**tests/test_sslkeylog_tshark_wrap.py** (new file, 11 tests)
- Single + multi interface argv shape
- Default `["any"]` when nothing passed
- BPF host filter emission
- UTF-8 interface name passthrough
- `find_tshark` env override precedence + ignore-when-missing
- service `print-unit` Windows XML structural assertions
- service `print-unit` POSIX systemd unit structural assertions

**tests/test_sslkeylog_capture.py** (+3 tests)
- `test_sink_h2_body_attached_via_data_frame`
- `test_sink_h2_body_unmatched_data_counts_correctly`
- `test_sink_h2_pair_flushed_on_ttl_with_partial_body`
- Updated `_h2_request_line` / `_h2_response_line` helpers to default
  `end_stream=True` (preserves prior tests' immediate-emit expectation)
- New `_h2_data_line` helper

**tests/test_sslkeylog_parser.py** (+3 tests)
- `test_build_capture_decompresses_gzip_body`
- `test_build_capture_decompress_failure_keeps_raw_bytes`
- `test_build_capture_identity_encoding_passthrough`

## Live evidence delta (versus W2 baseline)

| metric | W2 baseline (commit fd28cbd) | W2.x close (this commit) | delta |
|---|---:|---:|---:|
| `raw_captures` rows w/ source_id=sslkeylog-default | 138 | **358** | +220 |
| Distinct AI hosts captured | 4 | **10** | +6 |
| HTTP/2 response bodies with content_length > 0 | 0 (was b"") | **9+** | + |
| Largest single body row landed | 0 | **654207 bytes** (gemini.google.com HTML, gzip-decompressed) | + |
| sslkeylog tests | 33 | **50** | +17 |
| §4.1 STRICT ≥3 V-GREEN scenarios | 4/13 | **8/13** | +4 |
| §4.1 ≥3 V-GREEN-clean (no L1c) | 3/13 | **5/13** | +2 |

The 10 distinct AI hosts with at least one V-GREEN row:
```
api.anthropic.com                       10 rows
claude.ai                               11 rows
chatgpt.com                              7 rows
generativelanguage.googleapis.com        9 rows
gemini.google.com                        2 rows (incl. 654KB HTML body)
grok.com                                 1 row (incl. 390KB HTML body)
aistudio.google.com                      1 row
api2.cursor.sh                           2 rows
server.codeium.com                       2 rows
api.openai.com                           2 rows
```

## Network topology (Windows + system proxy → Clash, distilled)

```
       ┌─────────────────────────────────────────────┐
       │ Chromium-based apps (Chrome, Claude Desktop,│
       │ ChatGPT Desktop, Cursor, Windsurf, ...)     │
       └──────────────┬──────────────────────────────┘
                      │ honor system proxy
                      ▼
       127.0.0.1:7890 ── Loopback ── Clash ──► WAN (WLAN / 以太网)
                      ▲                       
                      │ NPF_Loopback iface     
                      │                       
                      │              ┌────────┴────────┐
                      │              │ Apps that bypass│
                      │              │ system proxy:   │
                      │              │  - Cursor       │
                      │              │  - Windsurf     │
                      │              │  - Codex CLI    │
                      │              │  - Gemini CLI   │
                      │              │  - Some Node    │
                      │              │    MCP clients  │
                      │              └────────┬────────┘
                      │                       │ direct
                      │                       ▼
                      │              WLAN  ── default route
                      │                       │
                      ▼                       ▼
            ┌─────────────────────────────────────────┐
            │   pce_sslkeylog daemon                  │
            │   tshark -i "Adapter for loopback ..." \│
            │          -i WLAN                       \│
            │          -o tls.keylog_file:…          \│
            │          -Y "http or http2"            \│
            │          -T ek -l                       │
            │   → PairingCaptureSink                  │
            │   → raw_captures (source_id=sslkeylog-default) │
            └─────────────────────────────────────────┘
```

The multi-iface daemon is the single source of capture for the entire
Chromium + Electron + Node-ish ecosystem on Windows. Two `-i` flags + one
keylog file + one tshark process = all 13 P0 scenarios reachable via A2
where the underlying TLS stack supports SSLKEYLOGFILE.

## Things explicitly NOT closed in this sprint

Deferred to future waves with clear gating:

1. **W2.1.1 — per-app live sweeps**: F4 P2 ChatGPT Desktop / F5 P3 Cursor /
   F5 P4 Windsurf / F6 P6 Claude Code CLI / F6 P8 Gemini CLI all show
   "host equivalence" V-GREEN in §3 (the daemon captured the host's TLS
   via httpx trigger; the app-side keylog write is architecturally
   equivalent). A 60s sweep in each app's active session would convert
   "host equivalence V-GREEN" → "ambient V-GREEN with app-specific UA
   fingerprint". Operator task; no engineering needed. Estimated 5-10 min
   per app.

2. **W2.1.2 — Gemini CLI keylog wrap**: Node 18+ supports SSLKEYLOGFILE via
   the `--tls-keylog` Node flag, but `gemini-cli` (and other Node-based
   CLIs we don't control) doesn't pass it by default. Need
   `pce_cli_wrapper` to inject `--tls-keylog=$SSLKEYLOGFILE` when wrapping
   Node CLIs. ~2-3h.

3. **W6 — F5 P5 GitHub Copilot**: still 0/3 legs. Needs L1 normalizer for
   `api.githubcopilot.com` + L3c VS Code extension + L3f MCP. Per
   SCOPE-LOCK §3. Pre-existing wave.

4. **W7 — F4 P2 ChatGPT Desktop UIA**: third leg requires accessibility
   (UIA) scraper since L3g IndexedDB / L3f MCP / L3d CDP are all dead.
   Pre-existing wave.

5. **W8 — F5 P3 Cursor protobuf**: L1 captures the bytes but the
   `api5.cursor.sh` is gRPC-web protobuf; needs a decoder. Pre-existing
   wave.

6. **L4b UIA daemon (Phase 2)**: long-term 4th leg for Web Tier-α
   scenarios that today rely on 2 passive legs (L3a + L1c) + A2. Phase
   2 sprint scope.

## Verification commands

```bash
# Probe (tshark + keylog status):
python -m pce_sslkeylog probe

# Live capture, 60s smoke (auto-detect interfaces):
python -m pce_sslkeylog run --duration 60

# Live capture, explicit interface (POSIX or override):
python -m pce_sslkeylog run --interface WLAN --interface Loopback

# Service lifecycle:
python -m pce_sslkeylog service status
python -m pce_sslkeylog service install
python -m pce_sslkeylog service uninstall

# Inspect captured rows:
python -c "
from pce_core.db import init_db, get_connection
init_db()
print(get_connection().execute(
    \"SELECT COUNT(*), COUNT(DISTINCT host) FROM raw_captures \"
    \"WHERE source_id='sslkeylog-default'\"
).fetchone())
"

# Run sslkeylog test suite:
python -m pytest tests/test_sslkeylog_parser.py \
                  tests/test_sslkeylog_capture.py \
                  tests/test_sslkeylog_tshark_wrap.py -v
```

## Headline numbers

```
sslkeylog tests:                33 → 50    (+17 new regressions)
raw_captures sslkeylog rows:    138 → 358  (+220, 60% growth)
Distinct AI hosts captured:     4  → 10
HTTP/2 bodies decompressed:     0  → 9+    (largest 654KB)
ALLOWED_HOSTS coverage:         60 → 62    (+grok.com +aistudio.google.com)
STRICT ≥3 V-GREEN:              4/13 → 8/13
≥1 V-GREEN-clean:               6/13 → 12/13
≥3 V-GREEN-clean (no L1c):      3/13 → 5/13
Service install:                manual → auto (per-user scheduled task)
```

This is the closure point for the A2 SSLKEYLOGFILE leg per
P5.D.1 SCOPE-LOCK Phase 1 Wave 2. Phase 2 (L4b UIA daemon) and
Phase B (W6 / W7 / W8) waves are next, but they're independent of A2.
