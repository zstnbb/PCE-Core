---
target_id: f1_gas_web
domain: aistudio.google.com
tier: S0
last_validated: 2026-05-17
validator: manual-w4r-draft-2026-05-17
schema_version: 1
---

# Capability

User can send a free-form text prompt in Google AI Studio (aistudio.google.com)
and receive an AI-generated text response. AI Studio exposes raw model controls
(temperature, top-p, model picker) that Gemini Web does not, but the primary
capture target is the same: prompt-in, streamed-response-out, persisted to a
session.

# Expected user-visible behaviour

1. User opens aistudio.google.com/prompts/new_chat. A prompt editor is visible.
2. User types text into the prompt area.
3. User presses Ctrl+Enter or clicks the run/send button. The prompt is
   submitted.
4. Within ~10s, the model response begins streaming into a result panel below
   or beside the prompt (layout varies by viewport width).
5. The URL transitions to a `/prompts/<id>` path uniquely identifying the
   session.

# Critical DOM landmarks

- "prompt input area" -- larger textarea / code-mirror style editor (NOT a
  ProseMirror composer like ChatGPT/Claude).
- "run / send button" -- explicit Run button, often accompanied by a Ctrl+Enter
  hint.
- "response panel" -- distinct from the prompt panel; assistant output renders
  there.
- "model picker" -- top control to switch between Gemini variants.
- "temperature / top-p sliders" -- side panel; not part of the capture target
  but their presence is a structural marker for AI Studio vs Gemini Web.

# Verification scripts

- t00_smoke
- t01_basic_chat
- t02_streaming_complete
- t04_new_chat_url

t08_regenerate is NOT in the P0 set for AI Studio: re-running in AI Studio is
typically a full re-run of the cell rather than a regenerate-this-response
button, so the semantics differ. Included as a P1 leg later.

A leg is V-GREEN if its verification script passes on a real aistudio.google.com
session with the current adapter yaml.

# Redundancy plan

- L1 (extension capture): `background.js` intercepts the GenerateContent /
  StreamGenerateContent gRPC-over-HTTP responses that AI Studio uses. Endpoint
  pattern is determined at runtime from the adapter yaml (not hardcoded here
  since AI Studio has rotated endpoint prefixes).
- L2 (proxy capture): mitmproxy CA captures the same endpoint family at the
  network layer.
- L3 (DOM capture): `universal-extractor.js` reads the rendered response text
  from the AI Studio response panel.

# Known instability surfaces

- AI Studio uses gRPC-Web framing inside HTTP/2 streams. Frame parsing is
  fragile to layout changes from Google.
- Endpoint paths have rotated: `/v1beta/`, `/v1internal/`, etc. Adapter yaml's
  endpoint pattern must be kept current; this is a high-frequency drift surface.
- The session URL pattern `/prompts/<id>` has been stable, but AI Studio briefly
  used `/u/0/prompts/<id>` for multi-account users -- regex must tolerate that.
- AI Studio aggressively rotates its CSS class hashes, so any selector that
  relies on class names will rot within weeks. Adapter must lean on
  data-attribute and aria-role selectors only.
- AI Studio requires Google sign-in *and* a one-time T&C accept -- new profiles
  fail t00_smoke until both are completed.

# Not-our-concern

- Audio / image / video multimodal inputs beyond detection (P1+ per ADR-005).
- System instruction / safety-setting configuration surfaces (P1+).
- Cloud SDK integration / function-calling output capture (P1+).
- Tuning / fine-tuning UI (out of scope -- this is a developer surface, not a
  capture target).
