---
target_id: f1_claude_web
domain: claude.ai
tier: S0
last_validated: 2026-05-17
validator: manual-w4r-draft-2026-05-17
schema_version: 1
---

# Capability

User can send a free-form text prompt to Claude (claude.ai) and receive an
AI-generated text response. The response streams incrementally and is persisted
in the conversation transcript when complete. New chats land on a session URL
matching `/chat/<uuid>`.

# Expected user-visible behaviour

1. User opens claude.ai (or claude.ai/new for a fresh session). A prompt input
   is visible near the bottom of the page.
2. User types text into the prompt.
3. User presses Enter (or clicks the send arrow). The prompt is appended to
   the transcript as the user turn.
4. Within ~10s, the assistant turn begins streaming above the input.
5. Streaming completes; the URL is now `/chat/<uuid>` for a new conversation.

# Critical DOM landmarks

- "prompt input field" -- contenteditable composer; ProseMirror-based.
- "send button" -- arrow-shaped affordance, becomes enabled when input has text.
- "assistant message" -- each assistant turn has a distinguishable container
  with author role.
- "conversation transcript" -- chronological turn list above the composer.
- "stop generating" -- visible only during streaming.

# Verification scripts

- t00_smoke
- t01_basic_chat
- t02_streaming_complete
- t04_new_chat_url
- t08_regenerate

A leg is V-GREEN if the verification script passes on a real claude.ai session
with the current adapter yaml and PCE extension build.

# Redundancy plan

- L1 (extension capture): `background.js` intercepts responses to
  `/api/organizations/<org>/chat_conversations/<conv>` and reconstructs the
  streamed assistant message from the response body.
- L2 (proxy capture): mitmproxy CA captures the same endpoint at the network
  layer independent of any extension process. Same content, different code path.
- L3 (DOM capture): `universal-extractor.js` reads the assistant message text
  from the rendered DOM. Survives changes to the streaming format as long as
  the rendered text remains intact.

# Known instability surfaces

- The `/api/organizations/<uuid>/chat_conversations/...` URL scheme has been
  stable for >12 months but the response envelope (JSONL chunk shape) has
  changed at least once.
- Claude periodically tightens organization-level URL scoping; the
  `session_url_pattern` regex may need to widen when this happens.
- claude.ai issues a "trust this device" CSRF token on first load that can
  invalidate previously logged-in profiles after long idle periods --
  resurfaces as login-wall in headless sessions.

# Not-our-concern

- Project mode artifact uploads beyond detection (P1+ per ADR-005).
- Computer-use / Claude-with-tools agentic flows (P1+).
- Voice / vision (P1+).
- Workspace-level admin surfaces (out of scope; not a capture target).
