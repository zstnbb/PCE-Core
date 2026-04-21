# Gemini — "Full Coverage" Specification & Collaboration Protocol

**Scope:** this document defines what it means for PCE to capture
**everything** a user does on `gemini.google.com`, audits the current
implementation against that definition, and lays out the exact
human-computer loop we use to drive the implementation to that bar
in a finite number of iterations.

**Audience:** the user (running the browser) + Cascade (reading and
writing code) + the Gemini autopilot.

**Status:** **S1 tier** (heavy). ~15 must-pass T-cases for v1.0.1.
Parts IV–VI inherit from `CHATGPT-FULL-COVERAGE.md` (same protocol).

**Timebox:** Gemini validation + fixes targeted at **1 evening** of
autopilot runs once ChatGPT's scaffolding is in place.

---

## Part I — What "full coverage" means

Gemini is not one chat UI. It is ~20 distinct product surfaces that
share the same Angular shell. Full coverage means for every surface
below, PCE captures **both sides of every exchange**, with
**semantically-correct attachments**, **correct role attribution**,
**correct conversation-id**, and **no duplicate or truncated
messages**.

### I.1 Product surfaces PCE must handle

Grouped by probability-of-use (top = most common).

| # | Surface | URL shape | What the user sees | What PCE must capture |
|---|---|---|---|---|
| 1 | Vanilla chat | `/app` then `/app/<hex>` | Plain back-and-forth | user text + assistant markdown |
| 2 | New chat (pre-URL) | `/app` or `/` | First message before URL upgrades to `/app/<hex>` | user text + assistant; conv_id fallback to `_new_<ts>` |
| 3 | Code blocks in reply | `/app/<hex>` | ```lang fenced blocks with copy button | `code_block` attachments, language, full code |
| 4 | Long reply w/ streaming | `/app/<hex>` | Progressive streaming, Stop button visible | NO partial captures; one final capture when Stop disappears |
| 5 | Regenerate / alt drafts | `/app/<hex>` | "Show drafts" tab strip on assistant msg | currently displayed draft only; no re-capture when switching drafts |
| 6 | Edit & resubmit user msg | `/app/<hex>` | Pencil icon on user bubble | edited text replaces prior user turn; new assistant reply follows |
| 7 | File attachment (user) | `/app/<hex>` | PDF/doc/csv chip above prompt | `file` attachment with name + media_type |
| 8 | Image upload (vision) | `/app/<hex>` | Thumbnail chip above prompt | `image_url` attachment |
| 9 | Imagen image generation | `/app/<hex>` | Assistant inline renders 1-4 generated images | `image_generation` attachment per image |
| 10 | Deep Research | `/app/<hex>` | Long-running research, progress UI, final report | assistant message with LONG report body + `citation` attachments |
| 11 | Canvas (docs / code) | `/app/<hex>` (side panel) | Side-by-side doc editor | `canvas` attachment with doc content |
| 12 | Thinking (2.5 Pro Thinking) | `/app/<hex>` | `<details>Thinking…</details>` panel above reply | assistant content prefixed with `<thinking>…</thinking>` |
| 13 | Gems (custom personas) | `/gem/<id>` or `/gem/<id>/chat/<hex>` | Gem-specific shell; same chat semantics | same as 1 but `/gem/...` path |
| 14 | Extensions | `/app/<hex>` | "@Gmail find emails from X" → inline Gmail cards; same for Docs/Drive/Maps/YouTube | assistant message + `citation` or `file` attachments pointing to Google resources |
| 15 | Audio Overview | `/app/<hex>` | Generate-audio button → inline audio player | `audio` attachment with src URL or transcript |
| 16 | Gemini Live (voice) | dedicated route | Real-time voice; transcript in side panel | same as 1 once transcribed |
| 17 | Shared conversation | `/share/<hex>` | Read-only view of someone else's chat | PCE should NOT re-capture — DISCUSS |
| 18 | History list | `/app` sidebar | Non-chat surface | PCE should NOT capture (no conversation shown) |
| 19 | Settings / Activity | `/app/settings*`, `/app/activity*` | Non-chat surfaces | PCE should NOT capture |
| 20 | Error states | `/app/<hex>` | "Something went wrong, try again" | NO capture of the error banner as assistant message |
| 21 | Gemini Advanced upsell | various | Paywall screen | PCE should NOT capture |

### I.2 Meta-capture invariants

Independent of surface, these must hold **always**:

- **Role accuracy** — never mis-attribute user ↔ assistant.
- **No duplicates** — `fingerprintConversation` must dedupe correctly across Gemini's heavy re-render cycles.
- **Streaming safety** — no capture attempt with a half-rendered reply; defer until Stop button disappears.
- **Idle honesty** — on History / Settings / Activity pages, PCE must produce zero captures.
- **SPA nav correctness** — navigating `/app/A` → `/app/B` must reset the fingerprint. Gemini uses Angular router; we rely on 5s URL polling (no pushState hook — see G1).
- **Manual capture** — force-resend bypasses fingerprint.
- **Model name population** — `conversation.model_name` should reflect Gemini 2.5 Pro / 2.5 Flash / 2.0 Flash / Imagen / Gem name.
- **Console hygiene** — no red errors.

---

## Part II — Current implementation audit

Grounded in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\gemini.content.ts`.

### II.1 Extraction strategies (in file, priority order)

Gemini uses a 5-tier selector ladder with a class-keyword fallback.

| Strat | Selector | Tested | Surface it handles |
|---|---|---|---|
| 1 | `model-response, user-query` | ✅ | 1, 3, 4 (Angular web components — current main layout) |
| 2 | `[class*="query-text"], [class*="response-container"]` | ❌ UNTESTED | Legacy class layout |
| 3 | `[data-turn-role]` | ✅ | Attribute-based layout |
| 4 | `.conversation-container .turn-content` | ❌ UNTESTED | Older layout |
| 5 | `message-content` | ❌ UNTESTED | Shadow-DOM rendered turn |
| Fallback | `[class*="turn"], [class*="Turn"]` | ✅ | Emergency: class-keyword role detection |

### II.2 Helper coverage

| Helper | Tested | Handles |
|---|---|---|
| `getSessionHint` | ✅ | Surfaces 1, 2 (`/app/<hex>`, `/chat/<hex>`) |
| `getSessionHint` gap | ❌ | Surface 13 (`/gem/<id>`), 17 (`/share/<hex>`) — **MISSING** |
| `getContainer` | ✅ | All (main / body fallback) |
| `getModelName` | ✅ | Reads `.model-badge`, `[data-model-name]`, `[class*="model-selector"] [class*="selected"]` |
| `detectRole` | ✅ | Tag (`user-query`/`model-response`) + `data-turn-role` + class keyword |
| `extractText` | ✅ | `extractReplyContent` for model turns + chip/action strip |
| `extractAttachments` | Lives in `pce-dom.ts` | Surfaces 7, 8, 9 — depth unverified against live DOM |
| `extractThinking` | In `pce-dom.ts` | Surface 12 — **but not called from gemini.content.ts** — see G3 |
| `dedupeMessages` | ✅ | role+content[:200] — may over-dedupe on long identical prefixes (G10) |

### II.3 Runtime behaviours (in `utils/capture-runtime.ts`)

| Behaviour | Implemented | Effective for |
|---|---|---|
| Debounced MutationObserver (2500ms) | ✅ | All |
| Streaming defer | ✅ WIRED (c49d3de, closes **G2**) | Surface 4 |
| Fingerprint dedup | ✅ | Idle honesty, no dupes |
| SPA pushState/replaceState hook | ❌ `hookHistoryApi: false` | Surface 2, 5, 6 (**G1**) |
| URL polling (5000ms) | ✅ | SPA nav fallback |
| Optimistic send + rollback | ✅ | Net failure resilience |
| Manual-capture bridge | ✅ | Meta invariant |
| `requireBothRoles` gate | ❌ not used | — |

### II.4 Known gaps (before live validation)

**Status snapshot** — updated after P5.B static-analysis sweep:

| Gap | Status | Commit |
|---|---|---|
| **G2** streaming gate | ✅ CLOSED | `c49d3de` — `isStreaming` wired + regression tests |
| **G3** Thinking not extracted | ✅ CLOSED | `8fbba6d` — `extractText` calls `extractThinking` for assistant turns |
| **G6** Gems URL | 🔸 CLARIFIED | `11f4da0` — unanchored regex already matches `/gem/.../chat/<hex>` via substring; tests lock behaviour |
| **G9** Strategies 2/4/5 untested | ✅ CLOSED | `d34ff05` — dedicated regression tests for each fallback path |
| **G10** dedup too aggressive | ✅ CLOSED | `955cfef` — full content as dedupe key instead of `slice(0, 200)` |
| G1, G4, G5, G7, G8, G11, G12 | ⬜ OPEN | Need live DOM probe / autopilot |

Derived from comparing Part I.1 to Part II.1-3:

- **G1. No pushState hook.** `hookHistoryApi: false` — when Angular router transitions `/app/A` → `/app/B`, we only notice after up to 5s of polling. Short-lived visits to Gems / Canvas / Settings may escape detection entirely.
- **G2. No streaming gate.** Unlike ChatGPT, the Gemini content script does NOT pass `isStreaming` to `createCaptureRuntime`. Partial captures mid-stream are possible when the debounce (2.5s) fires before streaming ends. High priority.
- **G3. Thinking panel not extracted.** `extractThinking` exists in `pce-dom.ts` but `extractText` doesn't call it. Gemini 2.5 Pro Thinking's `<details>` panels will be either merged into reply body or dropped depending on layout.
- **G4. Canvas body not captured.** No canvas extractor. The chat body is captured but the Canvas doc pane (Surface 11) is missed entirely.
- **G5. Imagen attachment type unverified.** Generated images (Surface 9) may render as `image_url` instead of `image_generation`, or go as plain text with no attachment.
- **G6. Gems URL pattern missed.** `SESSION_HINT_RE = /\/(?:app|chat)\/([a-f0-9]+)/i` doesn't match `/gem/<id>` or `/gem/<id>/chat/<hex>` — conv-id falls back to pathname string, which is brittle for fingerprinting.
- **G7. Extensions / grounding cards not specially handled.** When Gemini answers via @Gmail / @Drive / @Docs, inline rich cards may render as empty-text divs. Needs extraction check.
- **G8. No `/share/<hex>` skip.** Read-only shared conversations will still be captured as if authored by the user.
- **G9. Strategies 2, 4, 5 untested.** Three of six extraction paths have zero unit test coverage. Future Angular redesigns that break Strategy 1 may silently fall through to paths that produce wrong output.
- **G10. Dedup too aggressive.** `key = role:content.slice(0, 200)` collapses two different user messages that share a long preamble (e.g. "You are a helpful assistant. Please answer the following…").
- **G11. No model-name for Imagen / Veo.** `getModelName` only matches selector classes; Imagen outputs don't have the usual model badge.
- **G12. Audio Overview (Surface 15) unhandled.** No `audio` attachment path in Gemini adapter.

---

## Part III — Test matrix

Each row is a **verifiable checkpoint**.

**Legend:**
- **Status**: ⬜ not tested / ✅ pass / ❌ fail (link to the fix commit)
- **Surface** (from I.1)
- **Autopilot-ready**: 🟢 fully deterministic / 🟡 screenshot-assisted / 🟠 requires Gemini Advanced subscription
- **Known risk**: gap from II.4

### III.1 Must-pass for v1.0.1

| ID | Surface | Auto | Status | User action | Expected capture | Known risk |
|---|---|---|---|---|---|---|
| G01 | 1 vanilla | 🟢 | ⬜ | New chat, send "what is 2+2" | 1 user + 1 assistant, both text | — |
| G02 | 4 streaming | 🟢 | ⬜ | Send prompt that streams >3s; wait for completion | 1 capture AFTER stream ends, none during | **G2** |
| G03 | 4 + stop | 🟢 | ⬜ | Click Stop mid-stream | 1 capture with partial assistant text | **G2** |
| G04 | 2 new chat | 🟢 | ⬜ | From `/app`, send message; watch URL upgrade | 1 capture; conv_id `_new_…` or upgraded `/app/<hex>` | **G1** |
| G05 | 3 code blocks | 🟢 | ⬜ | Ask "write a python hello world" | Assistant msg has `code_block` with `language:"python"` | pce-dom depth |
| G06 | 12 thinking (2.5 Pro Thinking) | 🟠 | ⬜ | Switch to "2.5 Pro with Thinking", ask a reasoning question | Assistant content begins with `<thinking>…</thinking>` then reply | **G3** |
| G07 | 6 edit | 🟢 | ⬜ | Click pencil on 1st user msg, change text, submit | OLD capture replaced or NEW capture with new user+assistant; no stale dup | fingerprint dedup |
| G08 | 5 regenerate | 🟢 | ⬜ | Click "Regenerate" on an assistant msg | New capture with new assistant variant | branch heuristic |
| G09 | 5 flip drafts | 🟢 | ⬜ | Switch "Show drafts" between 1/2/3 | Capture reflects currently-shown draft; no extra captures per flip | branch heuristic |
| G10 | 7 file attach | 🟢 | ⬜ | Upload PDF, send "summarize" | User msg has `file` attachment with filename | upload_via_paste |
| G11 | 8 vision | 🟢 | ⬜ | Upload image, send "describe" | User msg has `image_url` attachment | attachment |
| G12 | 9 Imagen | 🟠 | ⬜ | "Generate an image of a cat" (Advanced) | Assistant msg has ≥1 `image_generation` attachment | **G5** |
| G13 | 10 Deep Research | 🟠 | ⬜ | Launch Deep Research on a topic; wait for report | 1 assistant message with long body + `citation` attachments | long wait |
| G14 | 11 Canvas | 🟡 | ⬜ | Ask "write me a short essay" → Canvas opens; edit inside Canvas | Capture includes canvas body (`canvas` attachment or text) | **G4** |
| G15 | 13 Gems | 🟢 | ⬜ | Open a Gem, send a message | Captures appear; `conversation.conversation_id` well-defined for `/gem/<id>` path | **G6** |
| G16 | 14 Extensions | 🟡 | ⬜ | `@Gmail find recent emails` | Assistant msg has inline card content + `citation` or `file` attachments | **G7** |
| G17 | 17 shared | 🟢 | ⬜ | Open a public `/share/<hex>` URL | ZERO new captures (read-only) | **G8** |
| G18 | 19 settings | 🟢 | ⬜ | Navigate `/app/activity`, `/app/settings` | ZERO new captures | idle honesty |
| G19 | 20 error | 🟡 | ⬜ | Force error (bad prompt / rate limit) | NO capture with error banner as assistant | error filter |
| G20 | 18 history | 🟢 | ⬜ | Open history list from sidebar | ZERO new captures | idle honesty |

### III.2 Defer-to-v1.1

| ID | Surface | Status | Notes |
|---|---|---|---|
| G21 | 15 Audio Overview | ⬜ | Low priority; `audio` attachment path not implemented (**G12**) |
| G22 | 16 Live voice | ⬜ | Experimental; transcription-only path |
| G23 | 21 Advanced upsell | ⬜ | Should silently skip; low risk |

### III.3 Regression guardrails

For every ❌ → ✅ transition above, we **add or update** a unit test
in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\__tests__\gemini.content.test.ts`
that locks in the live DOM snippet the fix was written against.

Strategies 2, 4, 5 **must** get dedicated `describe` blocks before
v1.0.1 ships (close **G9**) — even if they don't fire in today's
layout, future Angular rewrites will cascade through them.

---

## Part IV — Collaboration protocol

Inherits from `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\CHATGPT-FULL-COVERAGE.md` Part IV verbatim.

**Delta for Gemini:**

- **Autopilot drives most cases.** G01-G05, G07-G11, G15, G17, G18, G20 = 13 cases fully automated via extended `GeminiAdapter` + `capture_verifier.wait_for_session_matching`.
- **G06, G12, G13 require Gemini Advanced account** — autopilot skips unless user declares subscription.
- **G14, G16, G19** use screenshot-assisted verification (Cascade `read_file`s the PNG + confirms Canvas pane visible / Extension card present / error banner visible).
- **Fingerprint under Angular re-render.** Gemini re-renders the turn list on nearly every interaction. If a real bug turns out to be a false-positive capture triggered by re-render, the fix is in `capture-runtime.ts`, not `gemini.content.ts` — escalate per IV.5 of the ChatGPT spec.

---

## Part V — Order of attack

Recommended sequence for round 1, optimised for signal per minute:

```
Block 1 — smoke (5 min):        G01, G18
  Can the extension capture at all on vanilla? Does it stay
  quiet on settings? Go/no-go for everything below.

Block 2 — streaming + structure (10 min):  G02, G03, G04, G05
  If G02 fails we add `isStreaming` to the runtime config
  (closing G2) before anything else — a partial capture
  corrupts all downstream T-cases.

Block 3 — editing (10 min):     G07, G08, G09
  Edit / regenerate / flip drafts — fingerprint churn.

Block 4 — attachments (15 min): G10, G11, G12
  Each attachment type independent; any can fail in isolation.
  G12 only if user has Advanced.

Block 5 — edge URLs (10 min):   G15, G17, G20
  Gems, shared, history. G15 closes G6 (URL regex).

Block 6 — heavy surfaces (20 min): G13, G14, G16, G19
  Deep Research (long wait), Canvas (visual), Extensions
  (rich cards), error state.

Block 7 — 2.5 Pro Thinking (5 min): G06
  Only if user has access to the 2.5 Pro Thinking toggle.
```

Total ~75 min for a first pass. Most should pass after the fixes
from ChatGPT's shared-runtime work (G2 — `isStreaming` wiring — is
cheap; G3 — thinking — adds a one-line call).

**What happens if Block 1 G01 fails?** Stop. Report. Fix before
touching Block 2.

---

## Part VI — What Cascade does between rounds

Same list as `CHATGPT-FULL-COVERAGE.md` Part VI, plus:

- Writing regression tests for Strategies 2, 4, 5 (close **G9**) so
  today's passing state is locked against future DOM churn.
- Auditing `pce-dom.ts` `extractAttachments` against the live
  `gemini_dom_probe.js` output in `@f:\INVENTION\You.Inc\PCE Core\tests\e2e\probe_gemini_dom.py` runs — if any attachment type is
  missed by the DOM probe, it's almost certainly missed by capture.
- Preparing a minimal `hookHistoryApi: true` patch for Gemini runtime
  config (closing **G1**) with a dedicated test that verifies
  `/app/A` → `/app/B` triggers fingerprint reset within 250ms, not 5s.
- Drafting the Gemini sub-section of v1.0.1 release notes.

Say "Cascade, work on X in the background while I run G14" — I will.
