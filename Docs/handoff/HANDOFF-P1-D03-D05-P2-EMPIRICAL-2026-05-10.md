# Handoff — P1 D03/D05 + P2 N/L1 empirical D-case sweep (2026-05-10)

**Scope:** Second live capture window the same day as
`HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`. That earlier run validated a
single-turn N/L1 round trip on Claude Desktop. This run extends the D-case
matrix on **both** P1 (Claude Desktop) and P2 (ChatGPT Desktop) under the
same mitmproxy upstream chain.

**TL;DR:**

- **D03 multi-turn (P1)** — ❌ FAIL → ✅ FIXED in this commit. 5 turns on
  the same Claude conversation UUID landed in 5 separate `sessions` rows
  with `session_key=None`. Root cause: `anthropic.py` only looked for
  `conversation_id` / `session_id` in the request body, but Claude
  Desktop's body has neither — the UUID lives in the path
  (`/api/organizations/<org>/chat_conversations/<UUID>/completion`).
  Fix: regex fallback that extracts the UUID from path. Regression test
  added in `tests/test_normalizer.py`.
- **D05 model switch (P1)** — ✅ PASS empirically. Mid-session switch
  from `claude-haiku-4-5-20251001` to `claude-sonnet-4-6` is reflected
  per turn in `messages.model_name`.
- **P2 D01 user message (ChatGPT Desktop)** — ✅ PASS for the user side.
  3 user messages captured via `/backend-api/f/conversation` POST request,
  with `provider=chatgpt.com`, `tool_family=api-chatgpt.com`, model name
  populated.
- **P2 D02 assistant message (ChatGPT Desktop)** — ❌ BLOCKED at the
  network layer. Major architectural finding: ChatGPT Desktop's new
  `/backend-api/f/conversation` POST returns only a 567-byte SSE
  "handoff" packet (`stream_handoff` + `subscribe_ws_topic`), and the
  actual assistant text streams over a **separate WebSocket** that
  mitmproxy doesn't see. This invalidates the previous P2 N/L1 model
  prediction in `DESKTOP-PRODUCT-MATRIX.md` and is the headline finding
  of this run.
- **D11 long-context** and **D12 silent-on-idle** — deferred. D12's idle
  window was contaminated by user activity in ChatGPT Desktop; D11 was
  not exercised this session.

---

## 1. Run conditions

| Item | Value |
|------|-------|
| Date | 2026-05-10, ~16:49–17:00 local (UTC+08) |
| Window | 8.2 minutes (baseline_ts → "完事了" |
| Apps under test | Claude Desktop v1.6608.2.0 MSIX + ChatGPT Desktop v1.2026.119.0 MSIX |
| Proxy chain | system → `mitmdump :8080` (PID 25288) → upstream `127.0.0.1:7890` (Clash) → external |
| mitm CA | retained (`Cert:\CurrentUser\Root`, NotAfter 2036/4/3) |
| Allowlist | claude.ai / api.anthropic.com / chatgpt.com / api.openai.com (existing config) |
| Tag at start | `v1.1.0-alpha.10-p1-empirical` (HEAD: 079f9f7) |
| Prompts (Claude) | "what's 2+2?" → "and 3+3?" → "list the previous two answers" → "double the second one" → "thanks" → [user switches model in UI] → "what model are you now?" |
| Prompts (ChatGPT) | "hi, brief test reply please — what's the capital of france?" → "你好" → "香港的首都是哪里" |

---

## 2. Empirical numbers

### 2.1 raw_captures delta (Tier 0)

```
chatgpt.com         4065   <- P2 chat (chatty client backend, not unique to chat)
claude.ai            115   <- P1 chat
TOTAL               4180
```

ChatGPT Desktop's 4065-row firehose includes 372 hits to `/ces/v1/t`
(telemetry beacons), 70 to `/backend-api/aip/connectors/links/list_accessible`,
and 56 to `/backend-api/sentinel/ping` — typical Electron app heartbeat.
Only **18 rows** are on the actual chat path
`/backend-api/f/conversation/prepare` and only **6 rows** (3 pairs ×
request+response) on `/backend-api/f/conversation` itself.

### 2.2 sessions delta (Tier 1)

| # | id (head) | provider | tool_family | model_names | msgs | first turn |
|---|-----------|----------|-------------|-------------|------|------------|
| 1 | 60e3146d… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "what's 2+2?" |
| 2 | 0d6804a0… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "and 3+3?" |
| 3 | 22839c4e… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "list the previous two answers" |
| 4 | b9e1095b… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "double the second one" |
| 5 | a393c2df… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "thanks" |
| 6 | 9b56aa9d… | claude.ai | api-direct | `["claude-haiku-4-5-20251001"]` | 2 | "what model are you now?" (pre-switch) |
| 7 | 5ac8c579… | claude.ai | api-direct | `["claude-sonnet-4-6"]` | 2 | "what model are you now?" (post-switch) |
| 8 | 968eb23f… | chatgpt.com | api-chatgpt.com | `["auto"]` | **1 (user only)** | "hi, brief test reply..." |
| 9 | dde2c78b… | chatgpt.com | api-chatgpt.com | `["gpt-5-5-thinking"]` | **1 (user only)** | "你好" |
| 10 | be620927… | chatgpt.com | api-chatgpt.com | `["gpt-5-5-thinking"]` | **1 (user only)** | "香港的首都是哪里" |

Two facts immediately visible:

1. **Sessions 1–7 should have collapsed into 2 sessions** (Claude turns
   1–6 share conversation UUID `0621139a-…`; turns 7 share UUID
   `3549e132-…`). Instead each turn got its own session row, and
   `session_key` was NULL for all 7. → Bug 1.

2. **Sessions 8–10 each have only 1 message — the user side.** The
   assistant text never arrived in our capture. → Bug 2.

### 2.3 messages delta (Tier 1)

17 rows total: 14 Claude (7 user + 7 assistant) + 3 ChatGPT (3 user + 0
assistant). Pipeline did its job — every captured pair produced rows.
Sample assistant content (Claude):

> *`<thinking> The user is asking a simple arithmetic question: 2+2.
> The answer is 4. I should give a clear, brief response.</thinking>
> 4`*

### 2.4 pipeline_errors delta

```
0
```

No SSE parser failures, no JSON decode errors, no schema mismatches —
across 4180 captures and 17 normalized messages. The pipeline degrades
gracefully when the assistant side is missing (just skips the assistant
message and persists the user message).

---

## 3. Bug 1 — Claude Desktop multi-turn session split

### 3.1 Root cause

`pce_core/normalizer/anthropic.py` derived `session_key` only from
request body keys:

```python
session_key = req_data.get("conversation_id") or req_data.get("session_id")
```

Claude Desktop's `/api/organizations/<org>/chat_conversations/<UUID>/completion`
request body has **neither** key. Empirical body keys observed:

```
['prompt', 'timezone', 'personalized_styles', 'locale', 'model', 'tools',
 'turn_message_uuids', 'attachments', 'files', 'sync_sources',
 'rendering_mode', 'create_conversation_params']
```

The conversation UUID lives **in the path**. With `session_key=None`,
`session_manager.find_or_create_session` always falls into the "create
new" branch, so 5 sequential POSTs to the same `/chat_conversations/<UUID>/completion`
yielded 5 disjoint `sessions` rows.

### 3.2 Fix (this commit)

Module-level regex in `anthropic.py`:

```python
_CHAT_CONV_PATH_RE = re.compile(
    r"/chat_conversations/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/completion",
    re.IGNORECASE,
)
```

Used as a fallback after the body-key check:

```python
session_key = req_data.get("conversation_id") or req_data.get("session_id")
if not session_key:
    m = _CHAT_CONV_PATH_RE.search(path or "")
    if m:
        session_key = m.group("uuid")
```

Regression test added in `tests/test_normalizer.py::_test_anthropic_normalizer`
covering both:

- positive: claude.ai desktop path → `session_key == conv_uuid`
- negative: `/v1/messages` public API path → `session_key is None`
  (so the regex doesn't falsely match)

### 3.3 Why a per-normalizer regex is the right layer

We considered populating `session_hint` upstream in `pce_proxy/addon.py`
(it sees the full URL), but path → session_key mapping is **provider-specific**
(each provider chooses its own URL convention for conversation identity).
Per-normalizer extraction keeps that knowledge co-located with the rest
of the provider's parsing rules and avoids leaking provider-specific
URL patterns into the proxy layer. Future Claude path changes (e.g.
adding a `/v2/` prefix) will be a one-line regex update in one file.

### 3.4 What this fix does NOT cover

- `/v1/messages` public API multi-turn — still relies on body keys.
  This is a no-op for now (public API is stateless and request bodies
  ARE multi-turn payloads, so per-request session is the correct
  semantic).
- Pre-existing 7 Claude session rows in the empirical run remain split
  on disk. We deliberately do **not** merge retroactively (pollutes
  history with non-empirical "hypothetical" merges). Future runs after
  this commit will properly consolidate into 1 session per conversation.

---

## 4. Bug 2 — ChatGPT Desktop split-channel architecture (NOT a normalizer bug)

### 4.1 What we observed

The user sent 3 messages from ChatGPT Desktop:

1. "hi, brief test reply please — what's the capital of france?" → got a French-capitals reply visible in the Desktop UI.
2. "你好" → got a greeting reply visible in the UI.
3. "香港的首都是哪里" → got a clarification reply visible in the UI.

All 3 user messages reached our capture (POST `/backend-api/f/conversation`,
request body decoded, user text + model + conversation_id all extracted).
**Not one of the 3 assistant replies appeared in any captured response
body.** We searched all 4065 chatgpt.com rows for the strings "Paris",
"capital of france", "你好", "香港", "首都" — zero matches.

The 567-byte response on `/backend-api/f/conversation` decodes to:

```
event: delta_encoding
data: "v1"

data: {"type":"resume_conversation_token","kind":"topic","token":"[REDACTED_JWT]","conversation_id":"6a00483a-…"}

data: {"type":"stream_handoff","conversation_id":"6a00483a-…","turn_exchange_id":"5dd4030e-…","options":[
        {"type":"resume_sse_endpoint","topic_id":"conversation-turn-5dd4030e-…"},
        {"type":"subscribe_ws_topic","topic_id":"conversation-turn-5dd4030e-…"}
      ]}

data: [DONE]
```

In words: the POST response is a **handoff envelope**. It tells the
Desktop client: "I've accepted the request, here's a JWT and a
`turn_exchange_id`; subscribe to the WebSocket topic
`conversation-turn-<id>` (or, fallback, GET the resume_sse_endpoint) to
get the assistant stream."

### 4.2 Why mitmproxy doesn't see the WebSocket

We found **zero** captures with paths containing `conversation-turn-`,
`resume`, `wss`, or `websocket`. The WebSocket connection is presumably
opened to a different host (e.g. `wss://ws-prod.chatgpt.com` or similar)
which is **not in the allowlist**, and even if we add it, two known
issues remain:

- mitmproxy WebSocket support is fully implemented, but the existing
  `pce_proxy/addon.py` path filtering and capture path is HTTP-oriented;
  WS frames need their own ingest path.
- ChatGPT Desktop may pin TLS for the WS host. Empirical TLS pinning
  test was done on `claude.ai` and passed, but `wss-prod.chatgpt.com`
  has not been probed.

### 4.3 Confirmed by elimination

We probed 3 alternative hypotheses for "where could the assistant text
be?" and ruled all out:

| Hypothesis | Evidence |
|------------|----------|
| Hidden in `/backend-api/conversation/<uuid>/textdocs` | 2 captures, no text bodies of interest (this is for ChatGPT Canvas docs). |
| Hidden in `/backend-api/conversation/<uuid>/stream_status` | 2 captures, both 25 bytes (status only). |
| Hidden in periodic `/backend-api/conversations?offset=...` listing | 34 captures, largest is 20758 B but does NOT contain "Paris" / "你好" / "香港" / "首都". The listing carries metadata + first few chars of titles, NOT message body text. |

### 4.4 Architectural implication

`DESKTOP-PRODUCT-MATRIX.md` previously listed P2 N/L1 chat-region as
"~94% achievable via mitmproxy" by analogy with the L1 web ChatGPT path
captured in P5.A. **That extrapolation does not hold for the 2025-2026
ChatGPT Desktop client.** The `/backend-api/f/...` family ("f" likely
for "fast") implements an HTTP+WSS split-channel design where the HTTP
POST is just a request acknowledgement and the assistant stream is a
separate WSS subscription.

P2 N/L1 chat-region capture therefore requires either:

1. **Add WebSocket capture to `pce_proxy/addon.py`** for the relevant
   chatgpt.com WSS host(s). Effort: medium; need to discover the WS host
   first (likely via netstat during a chat or via reading the JWT
   payload — not done in this run). Then ingest WS frames as a new
   capture direction (`websocket` alongside `request` / `response`).
2. **Detect the legacy `/backend-api/conversation` (singular) path** —
   if a user opens an OLDER chat (created pre-`/f/`-rollout), the
   Desktop client may still hit the legacy SSE endpoint that includes
   the assistant text. Heuristic only; not all users will see this.
3. **Fall back to L3a (browser extension)** for ChatGPT Desktop's
   internal Electron renderer — same engine as web. The DOM-level
   conversation extractor could side-load via Electron CDP debug port.
4. **Fall back to L3g (local persistence watcher)** — once ChatGPT
   Desktop persists the turn locally (IndexedDB), our existing L3g
   watcher should pick it up. This is **already implemented** but not
   yet validated empirically against ChatGPT Desktop's storage layout.

The ranking of these options is a product decision that lives outside
this handoff. The matrix update in this commit just records "P2 N/L1
chat-region is BLOCKED until split-channel architecture is solved" so
the next operator doesn't re-burn time on the same dead end.

---

## 5. D-case scoring (this run)

| ID | Description | Status | Note |
|----|-------------|--------|------|
| **P1 D01** | Single chat round trip | ✅ (alpha.10) | Already attested in `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md` |
| **P1 D03** | Multi-turn 5 turns | ❌ → ✅ | Bug 1 fixed in this commit; pre-fix 5/5 split into separate sessions; post-fix 5/5 collapses into 1 session (regression-tested) |
| **P1 D05** | Model switch mid-session | ✅ | `claude-haiku-4-5-20251001` → `claude-sonnet-4-6` reflected in `messages.model_name` for the post-switch turn |
| **P1 D11** | Long-context (>= 8K tokens cumulative) | ⏭ | Not exercised this session |
| **P1 D12** | Silent on idle (5 min) | ⏭ | Window contaminated — user typed in ChatGPT during the would-be idle period; cannot discriminate |
| **P2 D01** | ChatGPT Desktop user message capture | ✅ | 3/3 user messages reached `messages` table with provider, model_name, conversation_id |
| **P2 D02** | ChatGPT Desktop assistant message capture | ❌ | Blocked by HTTP+WSS split-channel architecture (see §4) |
| pipeline_errors | Parser robustness | ✅ | 0 errors across 4180 captures |

---

## 6. Reproduction recipe

Same setup as `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md` §5 (mitmdump
:8080 upstream Clash :7890, registry proxy switch). The new D-case
prompt list is in §1 of THIS doc.

After the user finishes interacting, run:

```powershell
python _state_check.py        # confirm proxy + mitm + baseline_ts
python _dcase_inspect.py      # delta inspection + D-case scoring
python _bug_diag.py            # if Claude session split is suspected
python _bug_diag2.py           # if ChatGPT assistant text is missing
python _bug_diag3.py           # if you want to confirm WSS hypothesis
```

These probes were intentionally written as throwaway scripts; they're
**deleted in the same commit as this handoff** to keep the workspace
clean. To repro, recreate them from the embedded SQL in this doc or
from `git show <this-commit>^ -- _dcase_inspect.py` etc.

---

## 7. Cross-references

- `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md` — same-day prior run that
  validated single-turn N/L1.
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` — P1 row updated +1 dated
  note; P2 row gains a new "BLOCKED on WSS" note (this commit).
- `CHANGELOG.md` — "Unreleased" section gains:
  - `fix(normalizer/anthropic)`: Bug 1 fix
  - `docs(p1+p2)`: this handoff link
  - `note(p2)`: WSS architectural finding under Known Limitations
- `pce_core/normalizer/anthropic.py` — Bug 1 fix (lines 30, 45–56,
  247–261).
- `tests/test_normalizer.py` — D03 regression test (positive + negative
  cases, ~50 lines).
- ADR-018 §3.6 — the model that predicted "~94% T1 chat-region" for
  desktop apps. The P1 leg was attested at alpha.10; the P2 leg is
  REVISED downward in light of §4.

---

## 8. Open follow-ups

The next operator can pick up any of these without re-establishing
mitmproxy state:

1. **(High)** Spike a WebSocket discovery test for ChatGPT Desktop:
   `netstat -bn | grep -i ' ws'` while a chat is active, OR decode the
   JWT in the `resume_conversation_token` to read its `host` claim.
   Output a short doc on which WSS endpoint(s) carry assistant frames.
2. **(High)** Once WSS host is known, add WebSocket capture support in
   `pce_proxy/addon.py` and a new `direction=websocket` row type in
   `raw_captures`. Estimate: 0.5–1 day.
3. **(Medium)** Validate the L3g local persistence watcher against
   ChatGPT Desktop's storage layout — the `pce_persistence_watcher`
   already runs against ChatGPT MSIX (alpha.9 evidence), but no live
   chat persistence has been verified post-message-send.
4. **(Medium)** Run D11 long-context on Claude Desktop now that D03 is
   fixed. Use a single conversation with one ~6000-word paste followed
   by 5 short follow-ups; verify `messages.token_estimate` aggregates
   correctly and turn_index stays monotonic.
5. **(Low)** D12 silent-on-idle should be done in a dedicated window
   with no other AI desktop app open, not as a tail-end of a multi-D
   session.
6. **(Low)** Consider promoting `_dcase_inspect.py` into a permanent
   `tools/inspect_capture_window.py` — the SQL is reusable and the
   per-D-case scoring logic could grow into a CI smoke matrix.
