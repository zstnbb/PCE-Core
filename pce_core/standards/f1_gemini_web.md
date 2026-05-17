---
target_id: f1_gemini_web
domain: gemini.google.com
tier: S0
last_validated: 2026-05-17
validator: manual-w4r-draft-2026-05-17
schema_version: 1
---

# Capability

User can send a free-form text prompt to Gemini (gemini.google.com/app) and
receive an AI-generated text response. The response renders progressively (not
always token-streamed -- Gemini often delivers in larger chunks) and persists
in a transcript. New chats produce a `/app/<id>` or `/chat/<id>` URL.

# Expected user-visible behaviour

1. User opens gemini.google.com/app. A prompt input is visible at the bottom.
2. User types text into the prompt.
3. User presses Enter (or clicks the send button). The user turn is added to
   the transcript.
4. Within ~15s (Gemini is measurably slower to first token than ChatGPT), an
   assistant response appears in a new turn.
5. The URL transitions to `/app/<id>` for a new conversation.

# Critical DOM landmarks

- "prompt input field" -- rich-text-editor / contenteditable; not a plain textarea.
- "send button" -- arrow icon button next to the input.
- "assistant response container" -- one element per response, distinguishable
  from user turns via attribute.
- "model selector" -- top toolbar dropdown for picking Flash / Pro / etc.
- "conversation history sidebar" -- left-rail list of prior chats.

# Verification scripts

- t00_smoke
- t01_basic_chat
- t02_streaming_complete
- t04_new_chat_url
- t08_regenerate

A leg is V-GREEN if its verification script passes against real
gemini.google.com using the current adapter yaml.

# Redundancy plan

- L1 (extension capture): `background.js` intercepts responses to
  `/_/BardChatUi/data` (the internal Bard data endpoint) and reconstructs the
  message from the framed response. Endpoint name is stable from the Bard era.
- L2 (proxy capture): mitmproxy CA captures the same `/_/BardChatUi/data`
  endpoint at the network layer.
- L3 (DOM capture): `universal-extractor.js` reads the rendered assistant
  message text from the DOM after the response container is detected stable.

# Known instability surfaces

- The `/_/BardChatUi/data` endpoint encodes payloads as Google's "frame" format
  (newline-prefixed length headers + JSON arrays of arrays). Google has changed
  the array shape >=1 time per year since launch.
- Gemini sometimes delivers responses non-streamed (a single late chunk); the
  streaming verification probe must accept both progressive and bulk delivery.
- The login flow goes through `accounts.google.com` and can require an extra
  consent click on first run; new profiles will fail t00_smoke until consented.
- Anti-bot heuristics on this domain are more aggressive than ChatGPT or
  Claude; headless+extension trips them more often.

# Not-our-concern

- Gems / system instruction surfaces (P1+ per ADR-005).
- Image generation (Imagen) beyond detection -- P1+.
- Deep Research mode session capture beyond detection -- P1+.
- Google Workspace integration surfaces (out of scope).
