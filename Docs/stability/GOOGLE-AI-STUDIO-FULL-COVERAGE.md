# Google AI Studio — "Full Coverage" Specification & Collaboration Protocol

**Scope:** this document defines what it means for PCE to capture
**everything** a user does on `aistudio.google.com`, audits the current
implementation against that definition, and lays out the exact
human-computer loop we use to drive the implementation to that bar
in a finite number of iterations.

**Audience:** the user (running the browser) + Cascade (reading and
writing code) + the AI Studio autopilot.

**Status:** **S1 tier** (developer-facing, heavy). ~20 must-pass
T-cases for v1.0.1.

**Timebox:** 1-2 evenings of autopilot runs once ChatGPT's
scaffolding is in place.

---

## Part I — What "full coverage" means

AI Studio is a **developer** front-end to the Gemini API. That
changes coverage vs. ChatGPT / Gemini:

- Developers change **system instructions**, **temperature**,
  **safety settings**, **model variant** between messages. PCE
  captures prompt text, not tuning knobs.
- Developers browse **Gallery / Library / Tune** without sending
  prompts. PCE must stay silent.
- Developers use **tools** (grounding, code exec, function calling,
  URL context). PCE captures the rendered output, not the JSON
  schema.
- Developers use **multi-modal** (image + video + audio + PDF)
  more heavily than consumer Gemini.
- Developers use **Stream Realtime** (bidi audio/video). Deferred
  to v1.1.

### I.1 Product surfaces

| # | Surface | URL shape | Must capture |
|---|---|---|---|
| 1 | Chat prompt | `/prompts/new_chat` → `/prompts/<id>` | user + assistant text; multi-turn |
| 2 | Create prompt (freeform) | `/prompts/new_freeform` → `/prompts/<id>` | single user + assistant; plus system text if any |
| 3 | Structured prompt | `/prompts/new_structured` → `/prompts/<id>` | example pairs + final input/output |
| 4 | Streaming | any prompt surface | final capture only, post-Stop |
| 5 | Code blocks | any prompt surface | `code_block` attachment |
| 6 | Thinking (2.5 Pro Thinking) | any prompt surface | `<thinking>…</thinking>` prefix |
| 7 | PDF upload | any prompt surface | `file` attachment |
| 8 | Image upload | any prompt surface | `image_url` + `media_type` |
| 9 | Video upload | any prompt surface | `file` / `video_url` + `media_type` |
| 10 | Audio upload | any prompt surface | `audio` + transcript |
| 11 | System instructions | any prompt surface | `layer_meta.system_instructions`, NOT messages |
| 12 | Grounding w/ Google Search | prompt + tools | `citation` attachments |
| 13 | URL context tool | prompt + tools | `citation` attachments |
| 14 | Code execution tool | prompt + tools | `code_block` + `code_output` |
| 15 | Function calling | prompt + tools | `tool_call` + `tool_result` |
| 16 | Imagen generation | `new_chat` w/ image model | `image_generation` attachment |
| 17 | Veo video gen | `new_chat` w/ Veo model | `video_generation` (new type) — DEFER |
| 18 | Edit / regenerate | any prompt surface | new capture with updated content |
| 19 | Get code modal | modal | NOT captured |
| 20 | Save to Drive | modal | NOT captured |
| 21 | Stream Realtime | `/stream` | **DEFER to v1.1** |
| 22 | Speech generation | `/speech/*` | DEFER |
| 23 | Media Gen browser | `/media/*` | DEFER |
| 24 | Tune a model | `/tune/*` | NOT captured |
| 25 | Prompt Gallery | `/gallery` | NOT captured |
| 26 | Library | `/library` | NOT captured |
| 27 | API Key management | `/apikey*` | NOT captured (sensitive) |
| 28 | Error states | any | NO capture of error banner as assistant |

### I.2 Meta invariants

- **Role accuracy** — ambiguous `<ms-chat-turn>` fallback ("try both") can produce ghost user turns — see A3.
- **No duplicates** — `fingerprintConversation` + `dedupeAttachments` must agree across heavy re-render.
- **Streaming safety** — `isStreaming` gate IS wired; validate Stop/Cancel regex stays current.
- **Idle honesty** — Gallery/Library/Tune/APIKey = zero captures.
- **SPA nav** — no pushState hook; 4s URL polling only (see A2).
- **Manual capture** — force-resend works.
- **Model name** — gemini-2.5-pro / 2.0-flash / imagen-3 / veo-2 (see A11).
- **UI noise stripped** — `normalizeText` drops `edit` / `more_vert` / `thumb_up` / `download` / `content_copy` / etc. New mat-icon → extend drop list.
- **Disclaimer stripped** — "Google AI models may make mistakes…" never in content.
- **System instructions separate** — in `layer_meta`, not messages.
- **Console hygiene** — no red errors.

---

## Part II — Current implementation audit

Grounded in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\google-ai-studio.content.ts` (521 lines).

### II.1 Extraction entry

**Single strategy:** iterate `<ms-chat-turn>`, classify by
`.chat-turn-container.user` / `.model`. Fallback: ambiguous turn →
try both extractions.

No selector ladder. If `<ms-chat-turn>` is renamed, capture = 0.

### II.2 Helpers (all tested in `google-ai-studio.content.test.ts`)

| Helper | Handles |
|---|---|
| `normalizeText` | CONTROL_LINE_RE + META_LINE_RE + DISCLAIMER_LINE_RE + AI_STUDIO_UI_LINE_RE; whitespace collapse; consecutive dedupe |
| `dedupeAttachments` | type+url+name+title+code composite key |
| `imageMediaType` | data: URL + filename extension |
| `attachmentOnlyText` | placeholder for attachment-only messages |
| `extractLocalAttachments` | `<ms-image-chunk>`, `<ms-file-chunk>`, `<pre>`, `<a href>` external citations |
| `cleanContainerText` | strip buttons/mat-icons/tooltips/feedback/author/timestamp; optional stripCode / stripLinks |
| `isStreaming` | shared helper + Stop/Cancel button text regex |
| `getContainer` | `.chat-view-container, ms-chat-session, main` |
| `getSessionHint` | `/prompts/<id>` only |
| `getModelName` | `ms-model-selector` + body-text `gemini-*` regex |

### II.3 Runtime (`utils/capture-runtime.ts`)

| Behaviour | State |
|---|---|
| Debounce (2500ms) | ✅ |
| streamCheckMs (1500ms) | ✅ |
| Poll (4000ms) | ✅ |
| `isStreaming` gate | ✅ WIRED (unlike Gemini) |
| `hookHistoryApi` | ❌ false (see A2) |
| Fingerprint dedup | ✅ |
| Manual-capture bridge | ✅ |
| Capture mode | incremental |

### II.4 Known gaps

**Status snapshot** — updated after P5.B static-analysis sweep:

| Gap | Status | Commit |
|---|---|---|
| **A3** ambiguous-turn ghost | ✅ CLOSED | `196a59b` — skip turns without `.user` / `.model` containers + 4 regression tests |
| A1, A2, A4, A5, A6, A7, A8, A9, A10, A11, A12, A13, A14 | ⬜ OPEN | A4 (structured prompts) + A1 (single-strategy) are highest-value; need live DOM probe |

- **A1. Single-strategy fragility.** If `<ms-chat-turn>` is renamed (common in Angular rewrites), capture drops to 0 with no fallback.
- **A2. No pushState hook.** `hookHistoryApi: false`; Angular router transitions detected only after 4s polling. Switching /prompts ↔ /tune ↔ /stream within 4s escapes notice.
- **A3. Ambiguous-turn "try both" produces ghost turns.** If `<ms-chat-turn>` has no `.user` / `.model` marker (observed on some empty/loading turns), both `extractUserText` AND `extractAssistantText` run. With the model container absent, `extractUserText` returns the whole turn HTML as user text — producing a ghost user turn with duplicated content.
- **A4. Structured prompt (Surface 3) not handled.** Input/Output example pairs render as `ms-structured-prompt-example` (or similar), NOT `<ms-chat-turn>`. Current extractor returns `[]` on these pages — zero capture.
- **A5. System instructions not captured.** Surface 11: the "System instructions" left rail is completely ignored. No `layer_meta` side-channel populated.
- **A6. URL regex too narrow.** `SESSION_HINT_RE = /\/prompts\/([a-zA-Z0-9_-]+)/` misses `/tune/*`, `/stream`, `/speech/*`, `/media/*`, `/gallery`, `/library`, `/apikey*`. These fall back to pathname — which for `/library` returns `/library` as session_hint, risking a "fake conversation" fingerprint if the `<ms-chat-turn>` extractor ever fires on library preview cards.
- **A7. No `hookHistoryApi` for `/gallery` → `/prompts/<id>` transition.** Clicking a gallery entry routes via Angular and takes 4s to register.
- **A8. No tool-call extraction.** Surfaces 14, 15: code-execution output and function-call JSON render inside `ms-code-block` / `ms-tool-call` custom elements — only `<pre>` matches today, so `code_output` and `tool_call` / `tool_result` are missed.
- **A9. Grounding citations may duplicate.** Surface 12: both `<a href>` citations (picked up by `extractLocalAttachments`) AND the shared `pce-dom.ts` extractor run. Without coordination, one citation may appear twice.
- **A10. Imagen / Veo attachment type wrong.** Surface 16/17: generated images render via `<ms-image-chunk>`, same path as uploaded images, producing `image_url` instead of `image_generation`. No way to tell them apart at extract time → both end up as user-visible images with same semantic type.
- **A11. Model name missing for Imagen/Veo.** `getModelName`'s `MODEL_NAME_RE = /\b(gemini-[\w.-]+)\b/i` won't match `imagen-3`, `veo-2`, `lyria-1`.
- **A12. `AI_STUDIO_UI_LINE_RE` drift.** The drop list (`download|content_copy|expand_less|expand_more|copy code|copy`) is hard-coded. When AI Studio adds a new glyph (e.g. `open_in_new`, `flag`), it leaks into content.
- **A13. No "Get code" / "Save to Drive" modal skip.** Surfaces 19/20: modal overlays may contain `<pre>` code (the SDK call snippet) which `extractLocalAttachments` happily captures — polluting the prompt's code_block list.
- **A14. Error banner leakage.** Surface 28: safety-block / quota banners render inside the model turn as plain text. `cleanContainerText` doesn't specifically remove them → end up as (tiny) assistant messages.

---

## Part III — Test matrix

**Legend:** Status ⬜/✅/❌; Auto 🟢 deterministic / 🟡 screenshot / 🟠 account-tier (Gemini Advanced or API quota).

### III.1 Must-pass for v1.0.1

| ID | Surface | Auto | User action | Expected capture | Known risk |
|---|---|---|---|---|---|
| A01 | 1 chat | 🟢 | New chat, "what is 2+2" | 1 user + 1 assistant | — |
| A02 | 2 freeform | 🟢 | New freeform, input "Summarize: Hello" | 1 user + 1 assistant | A4 |
| A03 | 3 structured | 🟢 | New structured, 2 example pairs + final input, Run | each example + final as user; outputs as assistant | **A4** |
| A04 | 4 streaming | 🟢 | Long prompt, watch stream | 1 capture AFTER Stop disappears | streaming |
| A05 | 5 code | 🟢 | "Write python hello world" | assistant has `code_block` with language | pce-dom depth |
| A06 | 6 thinking | 🟠 | Switch to 2.5 Pro Thinking, reasoning prompt | assistant content begins `<thinking>…</thinking>` | pce-dom |
| A07 | 7 PDF | 🟢 | Upload PDF, "summarize" | user has `file` attachment | upload |
| A08 | 8 image | 🟢 | Upload image, "describe" | user has `image_url` + `media_type` | extract |
| A09 | 9 video | 🟢 | Upload MP4, "describe" | user has video-typed attachment | A10 |
| A10 | 10 audio | 🟢 | Upload audio clip, "transcribe" | user has `audio` attachment | extract |
| A11 | 11 system instructions | 🟡 | Fill system instructions, send | prompt captured; system text in `layer_meta` | **A5** |
| A12 | 12 grounding search | 🟢 | Enable "Grounding with Google Search", ask current-events question | assistant has ≥1 `citation` with external URL | **A9** |
| A13 | 13 URL context | 🟢 | Enable URL context, give a public URL + "summarize" | assistant has `citation` with that URL | extract |
| A14 | 14 code execution | 🟢 | Enable Code execution, "compute primes of 997" | assistant has `code_block` AND `code_output` | **A8** |
| A15 | 15 function calling | 🟢 | Define a simple function, trigger it | `tool_call` + `tool_result` attachments | **A8** |
| A16 | 16 Imagen | 🟠 | Switch to Imagen model, "draw a cat" | assistant has `image_generation` (not `image_url`) | **A10** |
| A17 | 18 edit/regen | 🟢 | Click pencil, change input, resubmit | new capture reflects edit; no stale duplicate | fingerprint |
| A18 | 19/20 modals | 🟢 | Click "Get code" → open → close | ZERO new captures | **A13** |
| A19 | 24/25/26 browse | 🟢 | Navigate /tune, /gallery, /library, /apikey | ZERO new captures | **A6** |
| A20 | 28 error | 🟡 | Force a safety-block or quota error | NO capture with error banner as assistant | **A14** |

### III.2 Defer-to-v1.1

| ID | Surface | Notes |
|---|---|---|
| A21 | 17 Veo video gen | Needs `video_generation` attachment type; negotiate schema |
| A22 | 21 Stream Realtime | Bidi audio/video; fundamentally different capture model |
| A23 | 22 Speech gen | Needs `audio` attachment path; deferred |
| A24 | 23 Media Gen browser | Gallery of outputs; not chat |

### III.3 Regression guardrails

For every ❌ → ✅ transition above, add a unit test in
`@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\__tests__\google-ai-studio.content.test.ts`
locking in the live DOM snippet the fix was written against.

Three invariants must get dedicated regression tests before v1.0.1:

- **A3 ghost-turn defense**: `<ms-chat-turn>` with no `.user`/`.model`
  class + empty body → returns `[]`, not a ghost user turn.
- **A4 structured prompt extraction**: `<ms-structured-prompt-*>`
  pairs captured.
- **A12 glyph drift alarm**: when `normalizeText` drops a line
  matching `AI_STUDIO_UI_LINE_RE`, count it — if the count exceeds
  a threshold, log a warning (so we notice new glyphs before they
  leak).

---

## Part IV — Collaboration protocol

Inherits from `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\CHATGPT-FULL-COVERAGE.md` Part IV verbatim.

**Delta for AI Studio:**

- **Most tests need Gemini API quota.** AI Studio is free with a
  Google account but quotas apply. Autopilot throttles 30s between
  cases; if a quota error trips, pause and back-off 5 min.
- **Structured prompt + tools (A03, A12-A15)** are the highest-value
  tests because they exercise the attachment extractor's deepest
  branches. Budget extra time here.
- **A11 system instructions** requires a side-channel. Likely lands
  as a `pce_core` schema extension (CaptureEvent v2's `layer_meta`
  field). Escalate per ChatGPT spec IV.5 if we find the current
  CaptureEvent has no room.

---

## Part V — Order of attack

```
Block 1 — smoke (5 min):         A01, A19
  Does chat capture? Does /gallery / /library / /tune stay silent?

Block 2 — prompt variants (10 min): A02, A03, A04
  Freeform + structured + streaming. If A03 fails, fix A4
  (structured prompt extraction) before anything else — it's
  a zero-capture failure, not a partial one.

Block 3 — multi-modal (15 min):   A07, A08, A09, A10
  PDF / image / video / audio upload. Each independent.

Block 4 — tools (25 min):         A12, A13, A14, A15
  Grounding / URL / code-exec / function-calling. The deepest
  attachment test. Budget extra.

Block 5 — meta (10 min):          A11, A16, A17
  System instructions + Imagen + edit/regen.

Block 6 — quiet paths (10 min):   A18, A20
  Modals + error state.

Block 7 — 2.5 Pro Thinking (5 min): A06
  Only if user has access.
```

Total ~80 min for a first pass. Expect the tools block to surface
~2-3 shared-runtime bugs that, once fixed, unblock Claude and
ChatGPT function-calling too.

**What happens if Block 1 A01 fails?** Stop. Report. Fix before
Block 2.

---

## Part VI — What Cascade does between rounds

Same list as `CHATGPT-FULL-COVERAGE.md` Part VI, plus AI-Studio-specific:

- Auditing `AI_STUDIO_UI_LINE_RE` against the live probe file
  `@f:\INVENTION\You.Inc\PCE Core\tests\e2e\googleaistudio_global_media_probe_latest.json` — if any line matches a new glyph name, propose the regex extension preemptively.
- Drafting a selector ladder (Strategy 2 + 3) so A1's
  single-point-of-failure is gone. Candidates:
  Strategy 2: `[data-testid="chat-turn"]` if present.
  Strategy 3: `[class*="turn-container"]` with role-class inference.
- Preparing a `hookHistoryApi: true` patch for AI Studio runtime
  config (closing **A2**) with a dedicated test for
  `/prompts/A` → `/tune/B` transition.
- Writing the AI Studio sub-section of v1.0.1 release notes.

Say "Cascade, work on X in the background while I run A14" — I will.
