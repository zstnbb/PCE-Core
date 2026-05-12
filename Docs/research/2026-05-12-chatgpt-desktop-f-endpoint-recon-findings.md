# ChatGPT Desktop `/backend-api/f/conversation` — RECON findings (2026-05-12)

> **Date**: 2026-05-12 (22:35 UTC+08)
> **Status**: empirical (post-RECON, post-reverification)
> **Supersedes**: `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md` §4 conclusion "P2 N/L1 chat-region BLOCKED on WSS capture"
> **Next**: Stage 3 standard update (MATRIX §4.2) + Stage 4 normalizer impl (`assemble_chatgpt_web_f_sse`)
> **Authority**: STANDARD-WORKFLOW §5 (RECON stage)

---

## TL;DR

The 2026-05-10 P2 empirical-run conclusion that ChatGPT Desktop's
assistant stream is "blocked on a separate WebSocket" is **incorrect**.

**What is actually happening**: `/backend-api/f/conversation` POST
returns a **full HTTP SSE stream** carrying the complete assistant
response, encoded as **JSON-patch delta events** against a message
tree. The 2026-05-10 `grep` search for "Paris" / "你好" returned zero
hits not because the text wasn't captured, but because delta tokens
are split across multiple `{"v": "..."}` chunks that no single line
contains a full search string.

No WebSocket capture infrastructure work needed. No `ALLOWED_HOSTS`
change needed. The entire P2 D02 blocker collapses to a single
missing piece: **a JSON-patch SSE delta assembler in
`pce_core/normalizer/`**.

---

## 1. Verification of 2026-05-10 conclusion

### 1.1 What the 2026-05-10 handoff claimed

`@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md` §4.1:

> "The 567-byte response on `/backend-api/f/conversation` decodes to:
>   event: delta_encoding
>   data: "v1"
>   data: {"type":"resume_conversation_token", ...}
>   data: {"type":"stream_handoff", ...}
>   data: [DONE]"
>
> "In words: the POST response is a **handoff envelope**."
>
> "We searched all 4065 chatgpt.com rows for the strings 'Paris',
>  'capital of france', '你好', '香港', '首都' — zero matches."

### 1.2 What the 2026-05-12 local `pce.db` actually contains

Queried `raw_captures` under `host='chatgpt.com'` +
`path LIKE '/backend-api/f/conversation%'` + `direction='response'`:

```
25 rows total.
Body lengths: 48, 48, 15829, ... (mix of 48-byte conduit_token responses
and kB-scale SSE bodies).
```

Largest row (id `62f1686f...`, 15829 bytes, status 200, body_format
`text`) is **NOT** a handoff envelope. Its SSE structure:

| Line / event | Count |
|---|---|
| `event: delta` | **39** |
| `data: {"type":"resume_conversation_token", ...}` | 1 |
| `data: {"type":"input_message", ...}` | 1 |
| `data: {"type":"server_ste_metadata", ...}` | 1 |
| `data: {"type":"message_marker", ...}` | 2 |
| `data: {"type":"message_stream_complete", ...}` | 1 |
| `data: {"type":"beacon_ui_response", ...}` | 1 |
| `data: [DONE]` | 1 |

**Lines containing `"role": "assistant"`**: 2 (inside the first delta's
embedded message skeleton).

### 1.3 Why 2026-05-10 saw zero assistant text

JSON-patch deltas split assistant tokens into discrete append ops.
For a response like "Paris", the wire format is literally:

```
event: delta
data: {"p": "/message/content/text", "o": "append", "v": "Par"}

event: delta
data: {"v": "is"}
```

A grep for "Paris" across each `data:` line's body never matches,
even though the full text is present when you run the append ops
in order.

---

## 2. The actual wire format

### 2.1 Event sequence

```
event: delta_encoding
data: "v1"

data: {"type": "resume_conversation_token", "kind": "topic",
       "token": "<JWT>", "conversation_id": "<uuid>"}

data: {"type": "input_message", "input_message": {
    "id": "<uuid>", "author": {"role": "user", ...},
    "content": {"content_type": "text", "parts": ["user text"]},
    "status": "finished_successfully", ...
}}

event: delta
data: {"p": "", "o": "add", "v": {
    "message": {
      "id": "<msg-uuid>",
      "author": {"role": "assistant", ...},
      "content": {"content_type": "code"|"text", "text": ""},
      "status": "in_progress",
      "metadata": {...}
    },
    "conversation_id": "<uuid>"
}}

event: delta
data: {"p": "/message/content/text", "o": "append", "v": "<first-token>"}

event: delta
data: {"v": "<subsequent-token>"}      # bare v — continues last (p,o)

... (many more bare {"v":...} deltas) ...

data: {"type": "message_marker", ...}

data: {"type": "server_ste_metadata", "metadata": {...}}

data: {"type": "message_stream_complete", "conversation_id": "<uuid>"}

data: {"type": "beacon_ui_response", ...}    # optional

data: [DONE]
```

### 2.2 Delta op shapes observed

| op | path | value | semantics |
|---|---|---|---|
| `add` | `""` (empty) | full message tree | initializes the assistant message skeleton; value is `{"message": {...}, "conversation_id": "..."}` |
| `append` | `/message/content/text` | string token | appends to the assistant's text field |
| (bare) | — | string token | continues the last `(path, op)` from the preceding delta |
| `replace` | `/message/status` | `"finished_successfully"` | finalizes status |
| `replace` | `/message/metadata/citations` | list | overwrites citations on completion |

**Critical**: the bare-`v` delta shape is the majority of the stream
(~95% of deltas in the observed row). A correct assembler MUST track
the "current patch target" across deltas — lose track and you drop
the assistant content.

### 2.3 Content-type variants

The initial `add` delta's `message.content` carries one of:

- `"content_type": "text"` — straightforward: `text` field is the
  assistant's reply. Most common case.
- `"content_type": "code"` + `"language": "json"` — the model is
  emitting a tool call (e.g. `search_query`). The `text` field
  accumulates a JSON payload that downstream consumers parse. A
  follow-up assistant message may arrive later in the same stream
  with `content_type: "text"` carrying the human-facing reply.
- `"content_type": "multimodal_text"` (inferred, not seen in the
  2026-05-12 sample) — `parts` array instead of `text`; each part
  may be text/image_asset_pointer.

### 2.4 Model slug observations

The captured row shows two distinct model_slug values in one response:

- `"resolved_model_slug": "i-5-mini"` — OpenAI's internal slug
- `"model_slug": "gpt-5-3-mini"` — downstream / displayed slug

Both are newer-than-GPT-4 identifiers; the OpenAI normalizer's
existing `_safe_model_name` logic may or may not extract these
correctly. Verify on Stage 4 implementation.

The `"model_switcher_deny"` field also lists slugs that are blocked
for this turn (e.g. model policy rails).

---

## 3. Supporting evidence files

The scratch inspection scripts that produced these findings:

- `_probe_peek.py` — dumps the first 3 `/backend-api/f/conversation`
  response rows with `substr(body, 1, 1500)`. Confirmed that row
  `62f1686f...` is 15829 bytes, not 567 bytes.
- `_probe_peek2.py` — counts SSE event types in the body. Confirmed
  39 `event: delta` lines + 2 `"role":"assistant"` hits.
- `_probe_peek3.py` — initial (broken) attempt to parse deltas as
  `data: {"type":"delta", ...}`. Confirmed this shape does NOT exist;
  deltas use the SSE `event: delta\ndata: {...}` 2-line pattern.
- `_probe_peek4.py` — working SSE parser + JSON-patch reassembler.
  Reconstructed 934 chars of assistant text from 36 delta chunks in
  row `62f1686f...`. Proved the append algorithm works.

These scratch files are deleted in the same commit as this doc (per
STANDARD-WORKFLOW §11.5 "throwaway scripts don't stay"). The extracted
algorithm moves to `pce_core/normalizer/sse.py` in a follow-up commit.

---

## 4. Implications for Stage 3 + Stage 4

### 4.1 MATRIX §4.2 revision needed

`Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.2 currently says:

> "**N/L1 chat-region for P2 is therefore BLOCKED on user-side only**
>  until WebSocket capture is added."

This is **wrong as of 2026-05-12**. Replace with:

> "**N/L1 chat-region for P2 is UNBLOCKED via HTTP SSE** — the
>  `/backend-api/f/conversation` response body carries the full
>  assistant stream in JSON-patch delta format. The 2026-05-10
>  'split-channel WSS' hypothesis is rebutted (the grep search
>  missed tokens split across delta chunks). Remaining work: a
>  JSON-patch delta assembler in the OpenAI normalizer."

### 4.2 No infra changes needed

- `ALLOWED_HOSTS` in `pce_core/config.py` does NOT need a new WSS host.
- `pce_proxy/addon.py::websocket_message` hook is NOT needed for P2
  chat-region. (It remains correct + necessary for P1 Claude Cowork,
  which genuinely uses WS.)
- `PCE_EXTRA_HOSTS` env-var workaround NOT needed.

### 4.3 Stage 4 work to implement

Single new function in `pce_core/normalizer/sse.py`:

```python
def assemble_chatgpt_web_f_sse(body: str) -> dict | None:
    """Parse ChatGPT Web/Desktop /backend-api/f/conversation SSE.

    Returns {role, content_text, content_json, model, ...} or None
    if the body is not recognizable as an /f/conversation stream.

    Algorithm:
      1. Split body into SSE events (event:, data:, blank-line separator).
      2. For data: {"type":"input_message"} — extract user turn.
      3. For event: delta + data: {...}:
         a. If {"p": "", "o": "add", "v": {message:...}} — init state
            with that message tree; remember `last_path = ""`.
         b. If {"p": "/x/y", "o": "append", "v": "str"} — append to
            that path in state; remember `last_path = "/x/y"`.
         c. If {"v": "str"} (bare) — append to `last_path`.
         d. If {"p": "...", "o": "replace", "v": ...} — overwrite.
      4. Return the final message.content.text plus metadata.
    """
```

Then wire it into `pce_core/normalizer/openai.py::normalize` as a
fallback after the standard SSE assembler returns None and the
request path matches `/backend-api/f/conversation`.

### 4.4 Test fixture

The 2026-05-12 captured body `62f1686f...` (15829 bytes, 39 deltas,
a tool-call content_type=code turn) can be used as a regression
fixture. Save to `tests/fixtures/chatgpt_f_conversation_response.txt`
and assert the assembler reconstructs the expected JSON tool-call
payload.

---

## 5. Open Questions

### §5 Q1 — content_type=text vs code variant coverage

The 2026-05-12 sample row used `content_type=code` (a search tool
call). We have NOT yet verified the simpler `content_type=text`
case end-to-end. Likely same assembler, but worth a second
capture to confirm.

**Closure**: run one more empirical chat turn with a non-tool-using
prompt (e.g. "what is 2+2?"), verify the assembler produces plain
text output.

**Impact**: if text variant works same as code, assembler is
universal. If it has different delta shape, assembler needs a
branch.

### §5 Q2 — legacy `/backend-api/conversation` coverage

ChatGPT Web historically used `/backend-api/conversation` (singular,
no `/f/` prefix). Is this endpoint still used by older accounts or
a/b test cohorts?

**Closure**: grep `raw_captures` for `path LIKE '/backend-api/conversation%'`
(without the `/f/`). If any rows exist, check their shape.

**Impact**: if legacy endpoint still active, normalizer needs to
route both paths.

### §5 Q3 — multimodal + image-generation cases

The OpenAI web UI supports image uploads + DALL-E image generation.
Their delta shape is unknown — likely uses `parts` array + binary
`image_asset_pointer` references.

**Closure**: capture one turn with an image upload + one turn with
image generation. Document delta shapes in a §2.5 addendum.

**Impact**: reuse existing `_extract_rich_content` attachment
handling in openai.py, but delta-path-wise this needs one more
op (likely `append` to `/message/content/parts/0/text` or similar).

### §5 Q4 — conversation_id stability across multi-turn

The `resume_conversation_token` contains a `conversation_id`. Does
this stay constant for all turns in a ChatGPT Web session? (Claude
Desktop required a regex fallback to extract the UUID from the URL
path — see HANDOFF-P1-D03-D05-P2-EMPIRICAL §3.)

**Closure**: send 3 consecutive turns; verify the conversation_id
is identical across all 3 response bodies. Verify `session_key`
collapses all 3 into one `sessions` row.

**Impact**: likely zero — conversation_id is plainly in every
delta's `v.conversation_id` field. But we should write the session
extraction logic to use that field explicitly, not trust implicit
grouping.

### §5 Q5 — cancel-mid-stream behavior (D04 analog)

ChatGPT Desktop's Stop button mid-stream — does it close the HTTP
connection (no `message_stream_complete`), or does it keep the
stream open and ship a final `message_stream_interrupted` event?

**Closure**: click Stop during a long response, query raw_captures,
check if the row has `message_stream_complete` or something else.

**Impact**: if connection closes, D04's existing cancel-recovery
sweeper handles it (request-only normalization). If a distinct
event type signals cancel, the assembler needs to recognize it.

---

## 6. Post-RECON sequencing (STANDARD-WORKFLOW compliance)

Per `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-STANDARD-WORKFLOW.md` §1
("不允许跨阶段"), the next agent should:

1. **Stage 3** — update `DESKTOP-PRODUCT-MATRIX.md` §4.2 with this
   2026-05-12 revision. Mark old 2026-05-10 "BLOCKED on WSS" note
   as superseded. Owner sign-off before Stage 4. *Single commit.*
2. **Stage 4** — implement `assemble_chatgpt_web_f_sse()` in
   `pce_core/normalizer/sse.py` + wire into `openai.py`. Add
   regression test with the captured fixture. *Single commit.*
3. **Stage 4 verification** — run a live sweep against ChatGPT
   Desktop (single-turn + 3-turn multi-turn); confirm 3/3 assistant
   replies land in `messages` table with non-empty `content_text`.
   *Same commit as (2) or follow-up handoff.*
4. **Stage 5** — tag (e.g. `v1.1.6` or `v1.1.0-alpha.16-p2-unblock`)
   + HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md + CHANGELOG update.

Do NOT skip Stage 3 — sign-off is needed before modifying the
OpenAI normalizer, because any change touches S0 ChatGPT Web
(19 T-cases) as well as P2 ChatGPT Desktop.

---

## 7. References

- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md` — 2026-05-10 handoff that contained the now-superseded WSS hypothesis
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §4.2 — to be updated in Stage 3
- `@f:\INVENTION\You.Inc\PCE Core\pce_core\normalizer\sse.py` — where the new assembler lands
- `@f:\INVENTION\You.Inc\PCE Core\pce_core\normalizer\openai.py` — where the assembler wires in
- `@f:\INVENTION\You.Inc\PCE Core\pce_proxy\addon.py::websocket_message` — confirmed NOT needed for P2 chat-region
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-STANDARD-WORKFLOW.md` — process authority

### Historical rows used as evidence (local `pce.db`, `~/.pce/data/pce.db`)

- row `62f1686f51ac4d3f88ef8d5f7bf6bc2e` — 15829-byte response, 39 deltas, `resolved_model_slug="i-5-mini"`, `content_type="code"`, search tool call
- rows `e9b64ec6...` + `591814227...` — 48-byte `/f/conversation/prepare` responses carrying `conduit_token` (not part of the main stream; likely used to authenticate the subsequent SSE request)
