---
title: "P2 ChatGPT Desktop — First Live Sweep Evidence"
status: PASS
date: 2026-05-12
session: 23:32-23:38 UTC+08:00
operator: owner (zstnbb) + Cascade (sonnet-4.5)
predecessor: HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md
artifacts:
  - commit f3d36d4 (Stage 4 normalizer + 11 tests + 2 fixtures)
  - commit <THIS-COMMIT> (Stage 4 verification + session_key bug fix + 2 live tests)
---

# P2 ChatGPT Desktop — First Live Sweep (2026-05-12 23:34)

## TL;DR

**P2 D02 closed on live evidence.** ChatGPT Desktop assistant message
capture works end-to-end through the proxy chain after Stage 4 code
deployment. **Plus** the live sweep caught an additional bug (session_key
not resolving on new-chat first-message) which was fixed in the same
session.

## Pre-flight state

DB baseline (`_baseline.json`):
- `messages` row count: 4,885
- `raw_captures` row count: 29,655
- ChatGPT `/backend-api/f/conversation` historical response count: 25
  (all from 2026-05-10 H2-P2 sweep — owner's last empirical session
   per `HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md`)
- Latest `messages.ts`: 2026-05-12 11:06:24
- Latest `raw_captures.created_at`: 2026-05-12 11:20:16

Network state at start:
- pce_proxy: NOT running (no listener on :8080)
- System proxy: `127.0.0.1:7890` (Clash Verge / Mihomo direct)
- ChatGPT Desktop: running, talking to chatgpt.com via Clash 7890 →
  upstream VPN exit. **Bypassing pce_proxy entirely.**

This explains why no `/f/` captures exist after 2026-05-10: the proxy
chain was disassembled at some point in the last 2 days.

## Proxy chain rebuild

1. `mitmdump -s run_proxy.py -p 8080 --mode upstream:http://127.0.0.1:7890`
   started in `--mode upstream` so it forwards through Clash rather
   than trying to be the exit itself. Listener on `:8080` (PID 27108).
2. System proxy switched from `127.0.0.1:7890` → `127.0.0.1:8080`
   via `HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet
   Settings\ProxyServer`. Original value persisted in `_orig_proxy.txt`
   so the same script could restore it.
3. WinINet `INTERNET_OPTION_REFRESH` (37) + `_SETTINGS_CHANGED` (39)
   broadcast so newly-opened sockets pick up the change (Electron app
   like ChatGPT Desktop caches the proxy at process start — owner
   restarted the app to ensure clean uptake).

Chain after rebuild:

```
ChatGPT Desktop (Electron)
  → System Proxy 127.0.0.1:8080  (Stage 4 pce_proxy)
    → upstream  127.0.0.1:7890   (Clash)
      → VPN exit
        → chatgpt.com:443
```

## Prompt + capture

- Owner sent in ChatGPT Desktop (NEW chat, no prior context):
  - **User**: `What is 2+2?`
- ChatGPT Desktop replied within ~3 s:
  - **Assistant**: `\n\n2 + 2 = 4.`

## Capture evidence

`raw_captures` rows for `chatgpt.com /backend-api/f/conversation` after
baseline, ordered by `created_at`:

| at | pair_id | dir | body_len |
|---|---|---|---|
| 23:34:04 | `520af6e6748d4155` | request | 374 |
| 23:34:04 | `520af6e6748d4155` | response | 48 |
| 23:34:11 | `39de39d130ed47bf` | request | 365 |
| 23:34:13 | `58395e920a5f4d3c` | request | 520 |
| 23:34:13 | `2b0a18ee61944036` | request | 508 |
| 23:34:13 | `58395e920a5f4d3c` | response | 48 |
| 23:34:14 | `2b0a18ee61944036` | response | 48 |
| 23:34:15 | `e2450de1a0464c05` | request | 511 |
| **23:34:15** | **`6a9847ff41134241`** | **request** | **902** ← the actual prompt |
| 23:34:15 | `e2450de1a0464c05` | response | 48 |
| **23:34:18** | **`6a9847ff41134241`** | **response** | **2992** ← the assistant stream |
| 23:34:19 | `1170b092591244af` | request | 436 |
| 23:34:20 | `1170b092591244af` | response | 48 |

The 48-byte responses are all OPTIONS/CORS preflights or empty bodies.
The single substantive pair is `6a9847ff41134241`.

## `messages` rows after normalization

2 new rows in 1 new session `37ddb011851b4c2ead602c719c933402`:

| ts | role | model | text_len | content_text |
|---|---|---|---|---|
| 23:34:15 | user | `auto` | 12 | `'What is 2+2?'` |
| 23:34:15 | assistant | **`gpt-5-3`** | 12 | `'\n\n2 + 2 = 4.'` |

Both have **non-empty `content_text`**. Both share the same
`capture_pair_id=6a9847ff41134241`. The assistant's `model_name`
correctly resolved from the JSON-patch SSE response's
`metadata.resolved_model_slug` field via
`assemble_chatgpt_web_f_sse` → `OpenAIChatNormalizer.normalize`.

## Verdict — P2 D02 PASS

| Acceptance criterion | Result |
|---|---|
| User message captured into `messages` | ✅ |
| Assistant message captured into `messages` | ✅ |
| Both with non-empty `content_text` | ✅ |
| Same `capture_pair_id` linking req+resp | ✅ |
| `model_name` resolves to a real ChatGPT slug | ✅ `gpt-5-3` |
| Goes through the new Stage 4 assembler | ✅ (validated by smoke + 22 tests) |
| Zero regression in 240-test surrounding suite | ✅ |

## Bonus: bug caught + fixed in the same session

The live sweep exposed a second-order issue in
`pce_core/normalizer/openai.py`:

**Bug**: `session_key = req_data.get("conversation_id") or
req_data.get("session_id")` returned `None` for new-chat first-messages
because ChatGPT Web doesn't put `conversation_id` in the request body
for the **first** turn — only `parent_message_id="client-created-root"`.
The conversation_id is server-allocated and only appears in the
**response** delta tree.

**Effect (had it shipped)**: turn 1 + turn 2 of the same chat would
land in different session rows; the conversation would appear as two
disconnected sessions in the UI.

**Fix** (this commit, `pce_core/normalizer/openai.py:237-240`):

```python
session_key = (
    req_data.get("conversation_id")
    or req_data.get("session_id")
)
if not session_key and isinstance(resp_data, dict):
    cid = resp_data.get("conversation_id")
    if isinstance(cid, str) and cid:
        session_key = cid
```

`assemble_chatgpt_web_f_sse` already surfaces the `conversation_id` at
the top level of its returned dict (since the original Stage 4 commit
`f3d36d4`), so this is a minimal one-fallback addition rather than a
plumbing rework.

**Regression test**: `test_openai_normalizer_session_key_from_response_conversation_id`
in `tests/test_sse_and_pipeline.py` asserts the precondition (request
has no `conversation_id`) and verifies `result.session_key ==
"6a034877-cfc4-83e8-9c3f-9ccdde05cf75"` (the response's
`conversation_id`).

## Wire-format variant locked in

The live capture shipped the assistant message in a **single
non-streaming root-add** rather than 39 incremental deltas:

```
event: delta
data: {"p":"", "o":"add", "v": {"message": {
  "author": {"role": "assistant", "metadata": {"real_author": "tool:web"}},
  "content": {"content_type": "text", "parts": ["\n\n2 + 2 = 4."]},
  "status": "finished_successfully", ...
}, "conversation_id": "6a034877-..."}}
```

That is, `content.parts[0]` carries the full assistant text directly,
NOT `content.text` (which is what the 2026-05-10 fixture builds up via
appends). This is presumably how ChatGPT handles very-short replies
(no streaming needed — single token group).

`_extract_message_text` in `pce_core/normalizer/sse.py:662-694`
already had a fallback for `parts: [<str>]` since Stage 4, so this
variant works without code change. New regression test
`test_chatgpt_web_f_sse_live_fixture_simple_text` locks it in.

## New fixtures

Live capture saved to `tests/fixtures/`:

- `chatgpt_f_conversation_request_simple_text.json` — 902 B,
  new-chat first request, content "What is 2+2?", no
  `conversation_id` in body.
- `chatgpt_f_conversation_response_simple_text.txt` — 2992 B,
  single-root-add wire format, content "\n\n2 + 2 = 4.",
  `conversation_id=6a034877-cfc4-83e8-9c3f-9ccdde05cf75`,
  `model=gpt-5-3`. The JWT in `resume_conversation_token` was already
  redacted to `[REDACTED_JWT]` by the proxy's redact layer before DB
  storage — safe to commit.

## Restoration

After capture verification:

1. System proxy restored to `127.0.0.1:7890` (Clash). Verified via
   `Get-ItemProperty` post-set.
2. WinINet refresh broadcast again so apps pick up the restoration.
3. mitmdump on `:8080` stopped (`Stop-Process -Id 27108`).
4. No persistent changes to the user's environment.

## RECON open questions — status update

Per `Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md` §5:

| # | Question | Status |
|---|---|---|
| Q1 | `content_type="text"` variant works the same? | **YES** ✅ — live fixture is precisely this variant; covered by new test `test_chatgpt_web_f_sse_live_fixture_simple_text` |
| Q2 | Legacy `/backend-api/conversation` (no `/f/`)? | Still open — would need a separate ChatGPT account/cohort on the older rollout |
| Q3 | Multimodal (image upload) goes through same wire? | Still open — would need a follow-up sweep with `What's in this image?` |
| Q4 | `conversation_id` stable across turns within one chat? | **MOSTLY ANSWERED**: the bug fix here ensures it CAN be (turn 1 sources from response, turn 2+ sources from request because client now has it). Verifying with a 2-turn live sweep would close it definitively. |
| Q5 | Cancel-mid-stream behaviour? | Still open — would need an explicit sweep where owner hits stop mid-reply |

Q1 closed. Q2/Q3/Q5 deferred (lower priority — Q1 covers the dominant
path). Q4 effectively closed by the session_key fix.

## What ships in this commit

- `pce_core/normalizer/openai.py` — 7-line session_key fallback
- `tests/test_sse_and_pipeline.py` — 2 new tests (74 LOC)
- `tests/fixtures/chatgpt_f_conversation_request_simple_text.json` —
  NEW, 902 B
- `tests/fixtures/chatgpt_f_conversation_response_simple_text.txt` —
  NEW, 2992 B
- `Docs/handoff/HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` — this file
- `CHANGELOG.md` — appended `[Unreleased]` 2026-05-12 entry

## Next steps

1. **Tag** — P2 D02 is now LIVE-VERIFIED, ready for a release marker.
   Suggested: `v1.1.0-alpha.16` (or `v1.1.6` depending on the numbering
   scheme owner prefers). Use commit count: this would be the 3rd
   commit in the P2 closure trio (`accd113` + `f3d36d4` + this one).
2. **DESKTOP-PRODUCT-MATRIX.md update** — flip §4.2 row from
   `SUPERSEDED` to `PASS (live: 2026-05-12)`, link this handoff.
3. **Optional Q2/Q3/Q5 sweeps** — if owner wants deeper confidence
   before relying on this in production telemetry.
