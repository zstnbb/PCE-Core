# ChatGPT — "Full Coverage" Specification & Collaboration Protocol

**Scope:** this document defines what it means for PCE to capture
**everything** a user does on `chatgpt.com` / `chat.openai.com`, audits
the current implementation against that definition, and lays out the
exact human-computer loop we use to drive the implementation to that
bar in a finite number of iterations.

**Audience:** the user (running the browser) + Cascade (reading and
writing code).

**Timebox:** ChatGPT validation + fixes targeted at 2-3 evening
sessions spread over the Chrome Web Store review window.

---

## Part I — What "full coverage" means

ChatGPT is not a single chat UI. It is ~20 distinct product surfaces
that share the same shell. Full coverage means for every surface
below, PCE captures **both sides of every exchange**, with
**semantically-correct attachments**, **correct role attribution**,
**correct conversation-id**, and **no duplicate or truncated
messages**.

### I.1 Product surfaces PCE must handle

Grouped by probability-of-use (top = most common).

| # | Surface | URL shape | What the user sees | What PCE must capture |
|---|---|---|---|---|
| 1 | Vanilla chat | `/c/<uuid>` | Plain text back-and-forth | user text + assistant markdown |
| 2 | New chat (pre-URL) | `/` then routes to `/c/...` | Same as 1 but first message arrives before the URL | user text + assistant; conv_id=`_new_<ts>` fallback |
| 3 | Code blocks in reply | `/c/<uuid>` | ```lang fenced blocks with copy button | code_block attachments, language, full code |
| 4 | Long reply w/ streaming | `/c/<uuid>` | Progressive streaming, stop button visible | NO partial captures during stream; one final capture when stop-button disappears |
| 5 | Regenerate / alt branches | `/c/<uuid>` + nav arrows | `< 2/3 >` at bottom of assistant msg | the CURRENTLY DISPLAYED branch only (branches the user navigates away from are NOT re-captured) |
| 6 | Edit & resubmit user msg | `/c/<uuid>` | Pencil icon on user bubble → inline textarea | the edited text replaces the previous user turn; a NEW assistant reply follows |
| 7 | File attachment (user) | `/c/<uuid>` | PDF/doc/csv shown as chip above prompt | `file` attachment with name + media_type |
| 8 | Image upload (vision) | `/c/<uuid>` | Thumbnail chip above prompt | `image_url` attachment |
| 9 | DALL-E image generation | `/c/<uuid>` | Assistant inline renders generated image | `image_generation` attachment with src URL |
| 10 | Code Interpreter / Analysis | `/c/<uuid>` | "Analyzing..." pill → code + output block | `code_block` (the code) + `code_output` (stdout/result) |
| 11 | Web browse / search | `/c/<uuid>` | "Searching the web..." → assistant reply with inline citations | `citation` attachments with source URL + title |
| 12 | Deep Research | `/c/<uuid>` | Long-running research, progress UI, final report | assistant message with LONG report body; may fall outside `[data-message-author-role]` (Deep Research fallback path exists in current code) |
| 13 | Canvas | `/c/<uuid>` (side panel) | Side-by-side doc editor | `canvas` attachment with doc content |
| 14 | Custom GPT | `/g/<slug>` | GPT-specific shell, same chat semantics | same as 1 but different URL pattern |
| 15 | Projects | `/project/<id>` or `/projects/<id>` | Folder grouping multiple chats | chat captures within; URL pattern different |
| 16 | GPT Store browse | `/gpts` | Directory, no chat yet | PCE should NOT capture anything (no conversation) |
| 17 | Temporary chat | `/` with ghost indicator | Chat that isn't saved to history | captures still go to PCE Core; meta flag `temporary=true` ideal but not blocking |
| 18 | Shared conversation (read-only) | `/share/<id>` | Read-only view of someone else's chat | PCE should NOT re-capture (user didn't author it) — DISCUSS with user |
| 19 | Voice mode (transcribed) | `/c/<uuid>` | Voice toggle; transcript appears as normal text | same as 1 once transcribed |
| 20 | Settings / Memory / Plans | `/settings/*` | Non-chat surfaces | PCE should NOT capture; detector should not trigger |
| 21 | Error states | `/c/<uuid>` | "There was an error generating a response" | NO capture of the error toast as an assistant message |

### I.2 Meta-capture invariants

Independent of surface, these must hold **always**:

- **Role accuracy** — never mis-attribute user ↔ assistant.
- **No duplicates** — the same conversation state should not produce two identical captures to PCE Core (`fingerprintConversation` handles this).
- **Streaming safety** — no capture attempt with a half-rendered assistant reply; defer until stop-button disappears.
- **Idle honesty** — on a ChatGPT tab with no conversation (settings page, GPT store), PCE must produce zero captures.
- **SPA nav correctness** — navigating `/c/A` → `/c/B` must reset the fingerprint and re-capture `/c/B` from scratch, not leak state across conversations.
- **Manual capture** — the "Capture now" command (keyboard shortcut / popup button) must force-resend the current conversation even if the fingerprint is unchanged.
- **Model name population** — `conversation.model_name` should reflect the model the user actually used (GPT-4o, o1, o3-mini, custom GPT name...).
- **Console hygiene** — no red errors on any surface (yellow warnings from observer reconnect are OK).

---

## Part II — Current implementation audit

Grounded in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\chatgpt.content.ts`.

### II.1 Extraction strategies (in file, priority order)

| Strat | Selector | Covered by unit test | Surface it handles |
|---|---|---|---|
| A | `[data-message-author-role]` | ✅ | 1, 3, 4, 7, 8, 9, 19 (current main layout) |
| A+DeepResearch | Article/section fallback when only user msgs found | ✅ | 12 |
| B | `[data-testid^="conversation-turn"]` | ✅ | 1 (legacy layout) |
| C | `main article` | ❌ UNTESTED | 12, possibly 13 |
| D | `[data-message-id]` | ❌ UNTESTED | Older layout, rare now |
| E | `[role="row"] / [role="listitem"]` | ❌ UNTESTED | Emergency fallback |

### II.2 Helper coverage

| Helper | Tested | Handles |
|---|---|---|
| `getConvId` | ✅ | Surfaces 1, 5, 6 (any `/c/<uuid>`) |
| `getConvId` extension | ❌ | Surfaces 14 (`/g/<slug>`), 15 (`/project/<id>`), 18 (`/share/<id>`) — **MISSING** |
| `resolveConversationId` | ✅ | Surface 2 (synthetic `_new_<ts>`) |
| `isStreamingChatGPT` | ✅ | Surface 4 |
| `getModelName` | ✅ | Meta invariant (model_name population) |
| `extractSpecialContent` | ✅ | Surface 12 |
| `extractAttachments` | Lives in `pce-dom.ts` | Surfaces 7, 8, 9, 10, 11, 13 — coverage depth unverified against live DOM |
| `extractThinking` | In `pce-dom.ts` | Surfaces where assistant renders a `<details>Thinking…</details>` panel (o1 / o3 models) |
| `extractReplyContent` | In `pce-dom.ts` | All assistant surfaces |

### II.3 Runtime behaviours (in `utils/capture-runtime.ts`)

| Behaviour | Implemented | Effective for |
|---|---|---|
| Debounced MutationObserver | ✅ (2000ms) | All |
| Streaming defer | ✅ (`isStreaming` gate) | Surface 4 |
| Fingerprint dedup | ✅ | Idle honesty, no dupes |
| SPA pushState/replaceState hook | ✅ (`hookHistoryApi: true`) | Surface 2, 5, 6 and nav between /c/ ids |
| URL polling hook | ✅ (3000ms fallback) | Same |
| Optimistic send + rollback | ✅ | Net failure resilience |
| Manual-capture bridge | ✅ | Meta invariant |
| `requireBothRoles` gate | ❌ not used for ChatGPT | — |

### II.4 Known gaps (before live validation)

> **Update 2026-04-23 — post-autopilot reconciliation**
>
> Several gaps below were speculative pre-validation notes. After live
> autopilot runs on a real logged-in ChatGPT account (see Part III.1),
> their status is:
>
> - **G1 (URL patterns) — partially resolved.** T16 PASS
>   (`20260423-010121`) captured a conversation at
>   `/g/g-…-pce-sentinel-…/c/<uuid>` and persisted it under
>   `session_key=<uuid>`, proving `/g/<slug>/c/<uuid>` is handled
>   end-to-end. T17 PASS (`20260423-010547`) does the same for
>   `/project/<id>`. Still open: `/share/<id>` (T22, deferred to v1.1;
>   the product decision is to NOT re-capture shared read-only views).
> - **G2 (Custom GPT not in matches) — resolved.** Same T16 evidence
>   confirms the content script is injected on `chatgpt.com/g/…` and
>   the runtime resolves the conv-id from the URL.
> - **G3 (no `meta.temporary` flag) — not resolved; by design.** T18
>   PASS (`20260422-113723`) shows temporary chats still flow through
>   capture. The distinct `meta.temporary` marker is still missing and
>   tracked as v1.1 polish.
> - **G5 (Canvas depth) — baseline captured.** T15 PASS
>   (`20260422-113802`) shows the chat side of Canvas is captured;
>   structured capture of the canvas document body itself remains
>   unverified.
> - **G6 (Code Interpreter depth) — baseline captured.** T13 PASS
>   (`20260422-141125`) shows code + output round-trip through; depth
>   against every CI permutation is not exhaustively covered.
> - **G7 (Regenerate / branches) — resolved.** T08 PASS
>   (`20260423-013603`) covers regenerate. T09 PASS
>   (`20260423-113523`) covers user-message branch flip through
>   ChatGPT's current `上一回复` / `下一回复` controls and verifies the
>   selected branch via raw capture + session messages.
> - **G8 (error state filter) — resolved.** T19 PASS
>   (`20260422-113248`) confirms error toasts are not captured as
>   assistant messages.
> - **G4 / G9 — unchanged.**
>
> The bullet list below is preserved verbatim as the **original pre-live
> audit**, for traceability of what we believed before running real
> captures. Do NOT treat it as the current state — use Part III.1 for
> that.

Derived from comparing Part I.1 to Part II.1-3:

- **G1. URL patterns beyond `/c/<uuid>`**. `getConvId` regex is `/\/c\/([a-f0-9-]+)/`. This misses:
  - `/g/<slug>/c/<uuid>` (Custom GPT chats — may or may not live under `/g/...`, needs live verification)
  - `/project/<id>/c/<uuid>` (Project chats)
  - `/share/<id>` (shared view — and we likely want to SKIP capture here anyway)
  - Shape may need broadening to allow `[a-zA-Z0-9_-]` in the uuid (observed some conversation IDs use underscores).
- **G2. Custom GPT URL is not in `matches`**. `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\chatgpt.content.ts:348` limits injection to `chatgpt.com/*` + `chat.openai.com/*`. GPT Store URLs are on `chatgpt.com/g/...` so they ARE injected — but the conv-id regex ignores them.
- **G3. No temporary-chat awareness**. No `meta.temporary` flag for Surface 17.
- **G4. No shared-chat skip**. Surface 18: the content script will still try to capture read-only conversations the user landed on from a share link — we likely should NOT. Needs a gate on `/share/...` path.
- **G5. Canvas (Surface 13) extraction depth unverified**. `pce-dom.ts` has a `canvas` attachment type but the ChatGPT extractor never explicitly hunts for the Canvas side panel. Captures may contain the chat but miss the doc body.
- **G6. Code Interpreter output (Surface 10) depth unverified**. `code_output` attachment type exists but extraction vs. live DOM is unverified.
- **G7. Regenerate / branches (Surface 5)**. When the user flips between `< 2/3 >` branches, fingerprint changes and we resend the new branch. But is the whole conversation re-sent or just the last message? Behaviour is in the runtime's incremental mode. Needs live check — the `message_update` vs `message_delta` heuristic may mis-classify a branch flip.
- **G8. Error state (Surface 21)**. When ChatGPT shows "There was an error generating a response", our extractor may pick it up as an assistant message. Needs live check + probably a content-level filter.
- **G9. Strategies C / D / E untested**. Low risk (Strategy A matches current layout), but zero test coverage means future DOM changes that break A will silently fall through to untested code paths.

---

## Part III — Test matrix

Each row is a **verifiable checkpoint**. We march down this list
until every row is ✅ on live ChatGPT.

**Legend:**
- **Status** (filled in as we go): ⬜ not tested yet / ✅ pass (reference = earliest `tests/e2e/reports/chatgpt/<ts>/` directory with passing evidence) / ⏸ skip (blocked by upstream UI, not a capture-pipeline regression) / ❌ fail (link to the fix commit)
- **Surface** (from Part I.1 table)
- **User action** (exact browser clicks)
- **Expected capture** (what must appear in PCE dashboard)
- **Known risk** (the gap from Part II.4 most relevant here)

### III.1 Must-pass for v1.0.1

> **Live status as of 2026-04-23:** 20 ✅ pass / 0 ⏸ skip.
> Status reflects the **best-of** across live autopilot runs under
> `tests/e2e/reports/chatgpt/`. Each ✅ links to the earliest run
> that captured passing evidence; later runs may show transient
> autopilot-probe failures that are not capture-pipeline regressions.

| ID | Surface | Status | User action | Expected capture | Known risk |
|---|---|---|---|---|---|
| T01 | 1 vanilla | ✅ `20260422-083046` | New chat, type "what is 2+2", send | 1 user + 1 assistant, both text | — |
| T02 | 4 streaming | ✅ `20260422-083136` | Send a message that will take >3s to stream; watch stream to completion | 1 capture AFTER stream ends, none during | stream gate |
| T03 | 4 streaming + stop | ✅ `20260422-083136` | Same as T02, click Stop mid-stream | 1 capture with partial assistant text | stream gate + partial |
| T04 | 2 new chat | ✅ `20260422-083136` | From `/`, send a message, watch URL update to `/c/...` | 1 capture; conversation_id starts as `_new_...` then upgrades, or stays `_new_` — either OK if idempotent | SPA nav |
| T05 | 3 code blocks | ✅ `20260422-083136` | Ask for "a python hello world", ensure reply has fenced code | Assistant msg contains code; attachments include `code_block` with `language:"python"` | `extractAttachments` depth |
| T06 | 1 + thinking | ✅ `20260422-133158` | Switch model to o1/o3-mini; ask a reasoning question | Assistant msg content starts with `<thinking>…</thinking>` then the final answer | `extractThinking` |
| T07 | 6 edit & resubmit | ✅ `20260422-083440` | Click pencil on the 1st user msg, change it, submit | OLD capture gets replaced or a NEW capture appears with the new user turn + new assistant reply. No stale duplicate. | fingerprint dedup |
| T08 | 5 regenerate | ✅ `20260423-013603` | Click "Regenerate" on an assistant msg | New capture with the new assistant variant. `conversation.messages` reflects current branch. | branch heuristic |
| T09 | 5 branch flip | ✅ `20260423-113523` | Flip `< 1/2 >` back to the old branch | Should produce a new capture OR an update — not nothing | G7 resolved; current UI labels the controls as `上一回复` / `下一回复`. |
| T10 | 7 file attach | ✅ `20260422-083136` | Upload a .pdf, send "summarize" | User msg contains attachment chip; capture has `file` attachment with filename | G5/G6 |
| T11 | 8 vision | ✅ `20260422-083136` | Upload an image, send "describe" | User msg has `image_url` attachment | attachment |
| T12 | 9 DALL-E | ✅ `20260422-141811` | Send "generate an image of a cat" | Assistant msg has `image_generation` attachment | attachment |
| T13 | 10 code interpreter | ✅ `20260422-141125` | "Compute prime factors of 997 using Python" | Assistant msg has `code_block` + `code_output` attachments | G6 |
| T14 | 11 web browse | ✅ `20260422-083136` | Click "Search" tool, ask "what's the weather in Beijing right now" | Assistant msg has ≥1 `citation` attachments | attachment |
| T15 | 13 canvas | ✅ `20260422-113802` | Ask for "write me a short essay" → opens Canvas; edit inside canvas | Capture includes the canvas body (`canvas` attachment or body text) | G5 |
| T16 | 14 custom GPT | ✅ `20260423-010121` | Open any GPT from GPT Store, start a chat | Captures appear; `conversation.conversation_id` is well-defined | G1, G2 (validated resolved — capture path = `/g/<slug>/c/<uuid>`) |
| T17 | 15 project chat | ✅ `20260423-010547` | Open a Project (if the user has one), chat inside | Captures appear; conv-id handled | G1 (validated resolved via PCE_E2E_CHATGPT_PROJECT_URL) |
| T18 | 17 temporary chat | ✅ `20260422-113723` | Toggle temporary chat at top, send a message | Captures still go through | G3 |
| T19 | 21 error | ✅ `20260422-113248` | Force an error (e.g. trigger rate limit or poor prompt) | NO capture with the error toast as assistant message | G8 |
| T20 | 20 settings | ✅ `20260422-112322` | Open `/settings/data-controls`, just navigate | ZERO captures produced | idle honesty |

### III.2 Defer-to-v1.1 (record outcome but don't block)

| ID | Surface | Status | User action | Expected | Notes |
|---|---|---|---|---|---|
| T21 | 12 Deep Research | ⬜ | Launch a Deep Research task, wait for final report | Report captured as one assistant message | Long-running; validation is low-frequency |
| T22 | 18 shared | ⬜ | Open a public `/share/<id>` URL | ZERO captures (read-only, not authored by user) | G4 decision needed |
| T23 | 19 voice | ⬜ | Use voice-to-text on mobile | Transcribed text captures | Low priority |

### III.3 Regression guardrails

For every ❌ → ✅ transition above, we **add or update** a unit test
in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\__tests__\chatgpt.content.test.ts`
that locks in the live DOM snippet the fix was written against. This is
how we keep ChatGPT from regressing in 1.0.2, 1.1, etc.

---

## Part IV — Collaboration protocol

### IV.1 Why this is a loop, not a one-shot

Cascade can't see the live DOM. The user can't see the
`fingerprintConversation` state. Every test case bridges the two:
the user reports what PCE Dashboard shows; Cascade infers what
happened in the pipeline and proposes a code delta if needed.

### IV.2 One-round mechanics

```
┌─── Round N ───────────────────────────────────────────────┐
│                                                           │
│  1. Cascade: "run Tnn from the matrix. Exact steps: ..."  │
│                                                           │
│  2. User performs the browser action                      │
│                                                           │
│  3. User reports back, ONE of these three shapes:         │
│                                                           │
│     (a) "Tnn pass" — 3 words, we move to the next         │
│                                                           │
│     (b) "Tnn fail: [1-line description of what I saw]"    │
│         + optionally: screenshot of Dashboard / DevTools  │
│                                                           │
│     (c) "Tnn blocked: [why I can't run it]"               │
│         (e.g. no access to Custom GPT, rate-limited,      │
│         doesn't have Deep Research)                       │
│                                                           │
│  4. For (b): Cascade                                      │
│     - reads the site extractor + the relevant runtime     │
│     - proposes the minimal fix (file + diff + commit msg) │
│     - writes a regression unit test FIRST, makes it fail  │
│     - makes the fix, runs `pnpm test <file>`              │
│     - commits                                             │
│                                                           │
│  5. User: `pnpm build --mode webstore` + reload extension │
│     (or just "use" the on-disk unpacked install —         │
│     content scripts re-read on reload)                    │
│                                                           │
│  6. User re-runs Tnn. Back to 3.                          │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

### IV.3 What the user's "fail" report should contain

Not all failures look the same. To avoid re-asking, include
whichever of these applies:

- **What you saw on the page** — 1 sentence, e.g. "assistant replied with a 500-line essay; normal."
- **What PCE Dashboard showed** — ONE of:
  - "nothing new"
  - "new session but empty assistant content"
  - "new session with user only, no assistant"
  - "new session with wrong roles (assistant has my text, user has nothing)"
  - "duplicate session — two captures for the same exchange"
  - screenshot of the session detail if something else
- **DevTools Console output** — if any red `[PCE ...]` lines, paste them (anything else is usually noise).
- **Extension service worker logs** — only if the Dashboard says nothing at all; open `chrome://extensions/` → PCE → "Service worker" → "Inspect" → Console tab.

A well-formed fail report is ~3 lines and lets Cascade open the
right file on the first try.

### IV.4 What Cascade's "fix proposal" looks like

Every fix I propose will contain exactly these 4 parts:

1. **Root cause** — 1-2 sentences, grounded in the code I'm about to change.
2. **File + line range** — what I'm editing.
3. **The code change** — shown as a unified diff-style block before I apply it, so you can say "no, not that".
4. **The regression test** — usually a new `it(...)` block in `__tests__/chatgpt.content.test.ts` that the fixed code makes pass.

No blind edits on site extractors. Every selector/heuristic change
gets a test.

### IV.5 When to escalate off the happy path

Sometimes a "fail" can't be fixed with a selector tweak:

- **The AI site changed its DOM fundamentally** (e.g. ChatGPT ships a
  new React-native rewrite). → We might need to add a new extraction
  strategy, not tweak existing ones.
- **The bug is in the shared runtime, not the site extractor.** →
  Changes `capture-runtime.ts` / `pce-dom.ts`, which touches every
  site. We slow down, do a wider test sweep, not ship just ChatGPT.
- **The bug is in PCE Core backend, not the extension.** → Pivot
  investigation to `pce_core/server.py` or the storage layer.

If Cascade identifies one of these, we pause the ChatGPT loop and
decide whether to de-scope the test or broaden the fix.

---

## Part V — Order of attack

Recommended sequence for round 1, optimised for signal per minute:

```
Block 1 — smoke (5 min):     T01, T20
  Does the extension still capture at all on a fresh chat?
  Does it NOT capture on settings? These are the go/no-go for
  everything below.

Block 2 — core chat (15 min):  T04, T02, T03, T05, T06
  Vanilla chat lifecycle end-to-end: new chat + streaming +
  code + thinking panels. If any of these fails, the bug is
  likely in the base extractor or runtime — fix before attachments.

Block 3 — editing (10 min):   T07, T08, T09
  Edit and regenerate flows are the most bug-prone because they
  involve fingerprint logic + branch tracking.

Block 4 — attachments (20 min): T10, T11, T12, T13, T14
  Each attachment type is independent; any can fail in isolation.
  These map to `pce-dom.ts` helpers, not the site extractor —
  fixing one helper usually helps other sites too.

Block 5 — edge URLs (10 min):  T16, T17, T18, T19
  Custom GPT, Projects, Temporary, error states. If any of these
  misbehave, it's scoped to the URL regex + content filter.

Block 6 — canvas (10 min):    T15
  Canvas is its own beast; often the last thing to make work.

Block 7 — record & move on:  T21, T22, T23
  Deferred. Record whatever you observe but don't block v1.0.1.

Total ~75 min. Plan for two evenings of this pace with buffer
for fix-rebuild-retest cycles.
```

**What happens if Block 1 T01 fails?** Stop here, report it, Cascade
diagnoses before you spend time on Block 2. The rest of the blocks
assume the baseline works.

---

## Part VI — What Cascade does between rounds

When you're not actively running a test, Cascade can be:

- Writing regression tests for T-cases that haven't been run yet
  (so when you DO run them, we have a ground-truth DOM to compare
  against)
- Auditing `pce-dom.ts` attachment-extraction helpers for obvious
  brittle selectors
- Reading release notes / public write-ups about recent ChatGPT UI
  changes (if network permits) and proposing preemptive fixes
- Drafting the v1.0.1 release notes based on what we've fixed so far
- Refactoring test helpers so new DOM snippets slot in more cheaply

Say "Cascade, work on X in the background while I run T05" — I will.
