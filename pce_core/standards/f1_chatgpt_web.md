---
target_id: f1_chatgpt_web
domain: chatgpt.com
tier: S0
last_validated: 2026-05-17
validator: manual-w4r-draft-2026-05-17
schema_version: 1
---

# Capability

User can send a free-form text prompt to ChatGPT and receive an AI-generated
text response. The response streams in incrementally and is persisted in the
visible conversation transcript when complete.

# Expected user-visible behaviour

1. User opens chatgpt.com. A prompt input field is visible at the bottom of the page.
2. User types text into the input field.
3. User presses Enter (or clicks the send button). The prompt is displayed
   as the user's turn in the conversation area.
4. Within ~10s, an assistant response begins streaming into a new turn above
   the input.
5. Streaming completes; the response remains visible and the URL updates to
   `/c/<conversation-id>` if this was a new chat.

# Critical DOM landmarks

- "prompt input field" -- the single primary contenteditable / textarea where
  the user types. Sometimes a ProseMirror editor, sometimes a plain textarea.
- "send button" -- the explicit submit affordance. May be visually hidden
  when the input is empty; appears once text is entered.
- "assistant response container" -- the DOM region where the streamed
  response appears. Each response is one element with an author-role attribute.
- "conversation history" -- prior turns are stacked above the input, scrollable.
- "stop button" -- shown only while a response is streaming; user can interrupt.

# Verification scripts

- t00_smoke
- t01_basic_chat
- t02_streaming_complete
- t04_new_chat_url
- t08_regenerate

A leg is V-GREEN if its verification script passes on a real chatgpt.com
session using the current adapter yaml and the running PCE extension build.

# Redundancy plan

- L1 (extension capture): browser-extension `background.js` intercepts the
  network response to `/backend-api/conversation`, parses the SSE stream, and
  reconstructs the assistant message. This is the canonical capture path.
- L2 (proxy capture): mitmproxy CA installed in the Chromium profile captures
  the same `/backend-api/conversation` request/response at the network layer.
  Independent of the extension's process and of any DOM-side rendering bugs.
- L3 (DOM capture): `universal-extractor.js` (content script) walks the
  response container in the DOM and reads the assistant text. Independent of
  the network capture pipelines: it survives even if the SSE endpoint shape
  changes, as long as the rendered DOM still carries the text.

# Known instability surfaces

- `selectors.send_button` has been renamed by ChatGPT >=3 times in the past
  year; `data-testid` values are not stable.
- The SSE response chunk shape (data-stream-finished attribute, event names)
  has shifted at least once in 2026-Q1.
- ProseMirror initialisation latency on cold cache can push input-appear time
  past the default selector-resolution budget; adapter yaml `timeouts_ms.input_appear`
  is set to 30s for this reason.
- `/c/<id>` URL scheme has been stable but the path prefix was briefly `/chat/`
  in late 2025.

# Not-our-concern

- Voice mode capture (P1+ scenario per ADR-005; v1 covers text only).
- Image generation outputs (DALL-E results) -- detection in scope per T12,
  but full capture of generated image bytes is P1+.
- Code Interpreter Python execution output -- detection in scope per T13,
  but session-state replay is P1+.
- Canvas / Custom GPTs / Project chats beyond detection -- P1+.
