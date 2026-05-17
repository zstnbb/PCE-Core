---
target_id: f1_grok_web
domain: grok.com
tier: S0
last_validated: 2026-05-17
validator: manual-w4r-draft-2026-05-17
schema_version: 1
---

# Capability

User can send a free-form text prompt to Grok (grok.com -- the standalone xAI
product, NOT the X/Twitter integrated surface) and receive an AI-generated
text response. The response streams incrementally and persists in a transcript.

# Expected user-visible behaviour

1. User opens grok.com. A prompt input is visible at the bottom.
2. User types text into the prompt input.
3. User presses Enter (or clicks the send button). The prompt is added to the
   transcript.
4. Within ~10s, the assistant response begins streaming above the input.
5. Streaming completes; the response remains visible. URL may update to a
   per-conversation path; this is more variable on grok.com than on the other
   four sites (Grok has reshaped its URL scheme multiple times in 2025-2026).

# Critical DOM landmarks

- "prompt input field" -- textarea-based composer; simpler than ChatGPT's
  ProseMirror.
- "send button" -- arrow / paper-plane affordance.
- "assistant response container" -- one container per turn, distinguishable
  by attribute.
- "conversation history" -- chronological turn stack above the input.
- "model selector" -- where present, allows switching Grok / Grok Heavy / etc.

# Verification scripts

- t00_smoke
- t01_basic_chat
- t02_streaming_complete
- t08_regenerate

t04_new_chat_url is excluded from Grok's P0 set: the conversation URL scheme
has been unstable across 2025-2026 product re-launches, so we rely on the other
four legs for V-GREEN and treat URL pattern as P1.

A leg is V-GREEN if its verification script passes on a real grok.com session
with the current adapter yaml.

# Redundancy plan

- L1 (extension capture): `background.js` intercepts grok.com chat endpoints
  (path determined at runtime from the adapter yaml -- grok has rotated these
  more than any other P0 site). Reconstructs the streamed assistant message.
- L2 (proxy capture): mitmproxy CA captures the same endpoint family at the
  network layer.
- L3 (DOM capture): `universal-extractor.js` reads the rendered assistant
  response from the DOM. This is the most reliable leg for grok in practice
  because network endpoints rotate faster than the visual layout.

# Known instability surfaces

- grok.com is the most volatile of the 5 P0 sites: endpoint paths, response
  envelope shape, conversation URL pattern, and overall UI structure have all
  shifted >=2 times in the 12 months preceding 2026-05.
- xAI has occasionally rolled out major UI revisions to grok.com without prior
  notice (e.g. 2026-Q1 "Grok 4" launch reset the chat-history sidebar layout).
- Anti-bot detection on grok.com is more aggressive on free-tier accounts;
  premium accounts have a measurably smoother headless capture path.
- Login can occasionally bounce through x.com (formerly twitter.com) OAuth,
  which is sensitive to user-agent and TLS fingerprint quirks.

# Not-our-concern

- DeepSearch / search-augmented response surfaces beyond detection (P1+ per
  ADR-005).
- Image generation (Aurora / future xAI image model) beyond detection -- P1+.
- Voice / video / X (Twitter) integrated grok at x.com/i/grok -- the X
  integration is a different surface; this standard covers grok.com only.
- Premium-only features (Grok Heavy, etc.) beyond detection -- P1+.
