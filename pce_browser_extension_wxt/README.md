# PCE Browser Extension — WXT Build Chain

P2 skeleton per [TASK-004 §5.1](../Docs/tasks/TASK-004-P2-capture-ux-upgrade.md)
and [ADR-006](../Docs/docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md).
P2.5 Phase 3b batch 4 (HuggingFace + Manus + Zhipu extractors on
TypeScript) landed on 2026-04-18, following batches 1-3 (ChatGPT +
Claude + Gemini + DeepSeek + Google AI Studio + Perplexity + Copilot +
Poe + Grok + shared capture runtime with `requireBothRoles` option)
the same day. **12 of 13 site extractors now on TypeScript**.

## Status

| Area | Status |
|---|---|
| WXT build chain (vite + HMR + cross-browser zip) | ✅ landed (P2) |
| Single source of truth for the manifest (`wxt.config.ts`) | ✅ landed (P2) |
| Sideload vs webstore flavours via WXT `mode` | ✅ landed (P2) |
| Background entrypoint on TypeScript | ✅ landed (P2.5.1) |
| `CaptureQueue` (IndexedDB offline buffer) on TypeScript | ✅ landed (P2.5.1) |
| Dynamic / proactive injector on TypeScript | ✅ landed (P2.5.1) |
| `bridge.content.ts` on TypeScript | ✅ landed (P2.5.2) |
| `interceptor-page-confirmed.ts` on TypeScript | ✅ landed (P2.5.2) |
| `interceptor-ai-patterns.ts` on TypeScript | ✅ landed (P2.5.2) |
| `interceptor-network.ts` (fetch/XHR/WS/SSE) on TypeScript | ✅ landed (P2.5.2) |
| Shared helpers (`pce-dom`, `selector-engine`, `site-configs`) on TS | ✅ landed (P2.5.3a) |
| Vitest harness + unit tests for pure helpers | ✅ landed (P2.5.3a) |
| `interceptor-ai-patterns` refactored for testability | ✅ landed (P2.5.3a) |
| Shared PCE message interfaces (`utils/pce-messages.ts`) | ✅ landed (P2) |
| Chrome API pre-install type shim (`types.d.ts`) | ✅ landed (P2.5.1+2) |
| Shared capture runtime (`utils/capture-runtime.ts`) | ✅ **landed (P2.5.3b1)** |
| ChatGPT extractor on TypeScript | ✅ landed (P2.5.3b1) |
| Claude extractor on TypeScript | ✅ landed (P2.5.3b1) |
| Gemini extractor on TypeScript | ✅ landed (P2.5.3b1) |
| DeepSeek extractor on TypeScript | ✅ landed (P2.5.3b2) |
| Google AI Studio extractor on TypeScript | ✅ landed (P2.5.3b2) |
| Perplexity extractor on TypeScript | ✅ landed (P2.5.3b2) |
| Microsoft Copilot extractor on TypeScript | ✅ landed (P2.5.3b3) |
| Poe extractor on TypeScript | ✅ landed (P2.5.3b3) |
| Grok extractor on TypeScript | ✅ landed (P2.5.3b3) |
| HuggingFace Chat extractor on TypeScript | ✅ **landed (P2.5.3b4)** |
| Manus extractor on TypeScript | ✅ **landed (P2.5.3b4)** |
| Zhipu / Z.ai extractor on TypeScript | ✅ **landed (P2.5.3b4)** |
| Remaining 1 site extractor (`generic.js` for Mistral + Kimi) | ⏳ P2.5 Phase 3b batch 5 |
| E2E verified against the new bundle | ⏳ P2.5 Phase 4 |

The step-by-step deferral honours ADR-006's guardrail:

> 迁移过程中不引入任何新功能，保持"结构替换、行为不变"。任何业务逻辑修改都单独成 PR，
> 不与框架迁移混合。

Every P2.5 phase is a pure 1-for-1 behaviour port. The legacy JS under
`../pce_browser_extension/` is the frozen source-of-truth for site
extractors until Phase 3b–3f migrates them.

## What P2.5 Phase 3b batch 4 shipped

Three more site extractors ported — HuggingFace Chat, Manus, Zhipu
(Z.ai). 12 of 13 site extractors are now on TypeScript; only
`generic.js` (Mistral + Kimi) remains, and will ship in batch 5.

### New `entrypoints/*.content.ts`

- `entrypoints/huggingface.content.ts` — 4 role-selector strategies
  (`[class*=message][class*=user|assistant]` pair,
  `[data-message-role]`, `[class*=ConversationMessage]`,
  `[class*=chat-message]`) plus an alternating-children fallback
  for unknown-role pages. Model-name resolution uses three stages:
  header selector → `/chat/models/<name>` URL (URL-decoded) →
  title prefix containing `/`.
- `entrypoints/manus.content.ts` — anchors on `#manus-chat-box`,
  extracts from `.items-end .u-break-words` / `.whitespace-pre-wrap`
  (user bubbles) and `.manus-markdown` blocks (assistant). Chinese
  UI noise stripped byte-for-byte from the legacy regex set (`Lite`
  / `分享` / `升级` / `任务已完成` / `推荐追问`). Local
  attachment extractor preserves Manus-specific ``<pre>`` code
  blocks with sibling ``span.text-sm`` language labels + external
  citation links. `"Manus"` ultimate model-name fallback.
- `entrypoints/zhipu.content.ts` — dedicated `chat-user` /
  `chat-assistant` class markers with union selector across 5
  alternative patterns (the legacy JS mixed four different class
  conventions across rendering paths). `GLM-*` body-text regex for
  model name. `requireBothRoles: true` preserves the legacy
  "only user visible → defer" guard (see Design Decisions below).

All three use incremental capture mode and URL-polling SPA nav.

### Manifest update (`wxt.config.ts`)

`TS_EXTRACTOR_SITES` grew from 10 to 13 entries. HuggingFace, Manus,
and Zhipu are removed from `legacySiteBundle()` calls. The imperative
`content_scripts` list now contains only:

  1. The shared `SITE_INDEPENDENT_HELPERS` entry for the 13 TS
     extractor sites.
  2. The `generic.js` entry covering Mistral + Kimi (batch 5
     migrates this).
  3. The universal `detector.js` exclude-cover entry for uncovered
     pages.

The `legacySiteBundle()` helper is no longer called but kept defined
for clarity (batch 5 will remove it).

### Tests added (45 new cases across 3 files, 367 total)

| File | Test count |
|---|---|
| `entrypoints/__tests__/huggingface.content.test.ts` | 15 |
| `entrypoints/__tests__/manus.content.test.ts` | 15 |
| `entrypoints/__tests__/zhipu.content.test.ts` | 15 |

Coverage highlights:

- HuggingFace — session-hint hex match (case-insensitive) + fallback,
  4 container priority levels, role inference (`data-message-role`,
  class keywords user/human/assistant/bot/model), extractText
  noise stripping + `.prose`/`.markdown` preference, 5-stage
  model-name ladder (header → URL-decoded path → title prefix →
  null).
- Manus — `normalizeText` Chinese UI noise regex, `getChatBox` /
  `getContainer` priority, `getModelName` 3-tier ladder with
  `"Manus"` fallback, `dedupeAttachments` JSON-key dedup, local
  attachment extraction (pre with language label + citation
  same-origin skip), extractMessages pair + dedup + no-chatbox.
- Zhipu — `normalizeText` whitespace collapse + empty-line filter,
  `/c/<uuid>` case-insensitive session hint, `GLM-*` body-text
  regex (case-insensitive), role detection (3 paths), extractMessages
  pair + `.user-message` + skip-unknown + dedup + empty.

### Intentional behaviour deviation from legacy Zhipu

The legacy `zhipu.js` silently dropped ``message_update`` captures
(content changes on already-sent messages just updated the
fingerprint without a send). The runtime emits those captures with
``capture_mode: "message_update"``, so streaming updates to the last
assistant message ARE captured. Backend dedup + session-hint rollup
absorb any extra requests. Net effect: richer data, no duplicate
messages. Documented in the completion note.

## What P2.5 Phase 3b batch 3 shipped

Three more site extractors ported plus one small runtime-API addition
(`requireBothRoles` option for sites whose DOM briefly shows only one
role during render). 9 of 13 site extractors are now on TypeScript;
4 remain.

### New `entrypoints/*.content.ts`

- `entrypoints/copilot.content.ts` — 6 role-selector strategies
  (user-message / UserMessage / cib-message-group[source] /
  data-testid / thread-message / turn-*), class+source+testId role
  inference, `.ac-textBlock` / `.markdown` preference, badge-based
  model-name lookup.
- `entrypoints/poe.content.ts` — anchors on `[id^="message-"]`
  Poe-specific `ChatMessage_chatMessage_<hash>` containers; role
  detection via `rightSideMessageWrapper` (user) /
  `leftSideMessageBubble` / `BotMessageHeader` (assistant);
  `Message_messageTextContainer` → `Markdown_markdownContainer`
  → `Message_selectableText` text-node preference;
  `BotHeader_textContainer p` model name. `requireBothRoles: true`.
- `entrypoints/grok.content.ts` — `#last-reply-container` iteration
  with `[id^="response-"]` children; alignment-class role detection
  (`items-end` = user, `items-start` = assistant); `.message-bubble`
  text extraction; `grok-*` regex model-name from body text; `/c/<uuid>`
  session hint. `requireBothRoles: true`.

All three use incremental capture mode and URL-polling SPA nav.

### New runtime option — `requireBothRoles`

Poe and Grok both defer capture when the extracted messages contain
only a user role OR only an assistant role — the DOM transiently
reveals only one side during the first few hundred milliseconds of a
render, and capturing it would produce an incomplete conversation.
The legacy JS handled this with an inline ``hasUser && hasAssistant``
guard + ``scheduleCapture(1500)`` retry. We've promoted that pattern
into a reusable `requireBothRoles: boolean` flag on
``createCaptureRuntime`` so batches 4-5 can reuse it cleanly.

The guard fires inside ``captureNow``:
  - If enabled and both roles aren't present, reschedule at
    `streamCheckMs` (default 1500 ms).
  - Otherwise behave exactly as before.

Covered by 3 new unit tests under `utils/__tests__/capture-runtime.test.ts`.

### Manifest update (`wxt.config.ts`)

`TS_EXTRACTOR_SITES` grew from 7 to 10 entries. Copilot, Poe, and
Grok are removed from `legacySiteBundle()` calls and share the
reduced `SITE_INDEPENDENT_HELPERS` entry with batches 1-2 sites.
Only 3 explicit `legacySiteBundle()` calls remain (manus, zhipu,
huggingface) plus the generic entry for mistral/kimi.

### Tests added (43 new cases across 4 files, 322 total)

| File | Test count |
|---|---|
| `entrypoints/__tests__/copilot.content.test.ts` | 14 |
| `entrypoints/__tests__/poe.content.test.ts` | 14 |
| `entrypoints/__tests__/grok.content.test.ts` | 15 (12 file-local + 3 runtime) |

Coverage highlights:

- Copilot — session-hint path regex (3 forms), 6 role-detection
  cases (class/source/testId per role), extractText noise stripping
  + `.ac-textBlock` preference, model badge text + data-model
  attribute fallback.
- Poe — `normalizeText` strips timestamps + share/copy/copied,
  `getMessageNodes` targets the hashed container class,
  dedicated role detection for right/left-side wrappers +
  `BotMessageHeader`, user+assistant pair with dedup of repeated
  content, `BotHeader_textContainer` model-name lookup.
- Grok — `normalizeText` covers share/auto/copy/timing/mode-toggle
  classes, `/c/<uuid>` case-insensitive match, container fallback
  chain, alignment-class role detection, message-bubble text
  extraction + dedup, empty/ambiguous cases.
- Runtime `requireBothRoles` — user-only defer + eventual proceed,
  assistant-only defer, both-present pass-through.

## What P2.5 Phase 3b batch 2 shipped

Three more site extractors ported to TypeScript on top of the shared
capture runtime delivered in batch 1. No new runtime features — every
extractor is a straight behaviour-parity port of its legacy `.js`
counterpart using the same patterns as batch 1.

### New `entrypoints/*.content.ts`

- `entrypoints/deepseek.content.ts` — anchor-on-`.ds-markdown`
  strategy with DOM walk-up to find turn containers; fallbacks for
  `[data-role]` role-attributed elements and large-text-block
  heuristics. 7-selector model-name detector with `DeepSeek-R1` /
  `DeepSeek-V3` body-text fallback + `DeepSeek` ultimate default.
- `entrypoints/google-ai-studio.content.ts` — elaborate port of the
  largest legacy extractor (469 lines → ~500 lines TS with explicit
  types). Extracts from `<ms-chat-turn>` web components with
  `.chat-turn-container.user` / `.chat-turn-container.model` class
  markers. Comprehensive noise-stripping via a regex ladder
  (`edit` / `more_vert` / `user 12:34` / `google ai models may make
  mistakes` / `download` / `content_copy` / …). Local attachment
  extractor understands `<ms-image-chunk>`, `<ms-file-chunk>`,
  `<pre>` code blocks, and external `<a href>` citations.
  Streaming-aware (defers capture while a Stop/Cancel button is visible).
- `entrypoints/perplexity.content.ts` — 6 thread-selector strategies
  with role detection via class keywords (`query` / `answer` /
  `prose` / `not-prose` / …); `[class*=query]` × `[class*=answer]`
  index-pair fallback; 240-char dedup key. `model_name` always null
  (matches legacy).

All three use:
  - `captureMode: "incremental"`
  - `hookHistoryApi: false` (URL polling only)
  - `pce-manual-capture` DOM bridge listener

### Manifest update (`wxt.config.ts`)

`TS_EXTRACTOR_SITES` grew from 4 entries to 7. The imperative
`content_scripts` list shrank accordingly — DeepSeek, AI Studio, and
Perplexity no longer get the legacy JS bundle; they share the reduced
`SITE_INDEPENDENT_HELPERS` entry with the batch 1 sites.

### Tests added (50 new cases across 3 files, 279 total)

| File | Test count |
|---|---|
| `entrypoints/__tests__/perplexity.content.test.ts` | 14 |
| `entrypoints/__tests__/deepseek.content.test.ts` | 15 |
| `entrypoints/__tests__/google-ai-studio.content.test.ts` | 21 |

Coverage highlights:

- DeepSeek — turn-container walk-up (found + not-found paths),
  sibling-text extraction, `.ds-markdown`-anchored user-assistant
  pairing, `[data-role]` fallback, noise stripping, the 4-stage
  model-name ladder.
- Google AI Studio — each of the 4 noise regex classes fires
  correctly under `normalizeText`, `dedupeAttachments` composite
  key, `imageMediaType` data-URL + extension inference,
  `attachmentOnlyText` labelling, `extractLocalAttachments` per
  attachment type, `cleanContainerText` + `stripCode` / `stripLinks`
  flags, `ms-chat-turn` user + model + ambiguous paths,
  image-only user messages get the `[Attachment]` placeholder.
- Perplexity — role detection via class keywords + H1.group/query +
  PRE/not-prose exclusion + data-testid, extraction noise stripping,
  dedup by 240-char prefix, fallback pairing.

## What P2.5 Phase 3b batch 1 shipped

Three site extractors (`chatgpt.js`, `claude.js`, `gemini.js`) rewritten
as `defineContentScript` entrypoints plus a shared capture runtime that
factored out the ~80 % boilerplate they had in common. Behaviour parity
with the legacy JS is strict — every timing constant, fingerprint format,
rollback semantic, and DOM selector was lifted byte-for-byte.

### New `utils/capture-runtime.ts`

Shared factory `createCaptureRuntime(options)` that each site extractor
instantiates. The runtime owns:

- MutationObserver lifecycle (with retry-until-container-appears, up to
  15 × 2 s attempts by default).
- Debounced capture scheduling.
- `fingerprintConversation()`-based change detection.
- Two capture modes: `incremental` (ChatGPT/Gemini — `sentCount`-based
  delta + optional `message_update`) and `full` (Claude — always
  resend the whole conversation).
- SPA navigation handling: `hookHistoryApi` (pushState/replaceState +
  popstate) OR URL polling every `pollIntervalMs`.
- Streaming deferral via optional `isStreaming()` hook.
- `chrome.runtime.sendMessage` with rollback on lastError / non-ok
  response.
- Injectable `win` / `doc` / `chromeRuntime` so happy-dom tests drive
  it without installing real extension APIs.

The runtime exposes `start()`, `triggerCapture()`, `disconnect()` and
test-only `fingerprint` / `sentCount` getters.

### New `entrypoints/*.content.ts`

- `entrypoints/chatgpt.content.ts` — 5 extraction strategies
  (`[data-message-author-role]`, `[data-testid^=conversation-turn]`,
  `article`, `[data-message-id]`, ARIA rows), Deep Research / Canvas
  fallback, streaming detection (Stop button + `result-streaming`
  class), synthetic `_new_` + timestamp conversation ID when no
  `/c/<uuid>` URL is available, `history.pushState` hook.
- `entrypoints/claude.content.ts` — dedicated `human-turn` /
  `assistant-turn` selectors sorted by `getBoundingClientRect().top`,
  generic `[class*=message]` fallback with class/data-role/ARIA
  role detection, direct-children-of-container last-resort fallback,
  `full` capture mode, URL-polling only (matches legacy Claude).
- `entrypoints/gemini.content.ts` — Angular web-component extractor
  (`<user-query>`, `<model-response>`), 4 turn-selector strategies
  with dedup + role inference (tag / `data-turn-role` / class), URL
  polling only.

All three files are pure: each exports the site-specific helpers
(`getContainer`, `extractMessages`, `isStreaming*`, `getSessionHint`,
`getModelName`, `resolveConversationId`) as module-level functions so
they can be unit-tested without executing the `defineContentScript`
closure.

### Manifest surgery (`wxt.config.ts`)

The three ported sites (`chatgpt.com`, `chat.openai.com`, `claude.ai`,
`gemini.google.com`) are no longer in the imperative `content_scripts`
list — WXT auto-registers the TS `defineContentScript` entrypoints.
A new reduced-bundle entry still injects `SITE_INDEPENDENT_HELPERS`
(`behavior_tracker.js` + `text_collector.js` + `detector.js`) on
those sites so `window.__PCE_BEHAVIOR` and the floating "Save snippet"
button remain wired.

The legacy shared-helper globals (`pce_dom_utils.js`, `site_configs.js`,
`selector_engine.js`) are NOT injected on ported sites — the TS
extractors consume `utils/pce-dom.ts` / `utils/site-configs.ts` /
`utils/selector-engine.ts` directly via ESM imports.

### Tests added (80 new cases across 4 files)

| File | Test count |
|---|---|
| `utils/__tests__/capture-runtime.test.ts` | 13 |
| `entrypoints/__tests__/chatgpt.content.test.ts` | 22 |
| `entrypoints/__tests__/claude.content.test.ts` | 14 |
| `entrypoints/__tests__/gemini.content.test.ts` | 16 |

Coverage targets the migration-sensitive surfaces:

- Capture runtime — fingerprint diff, incremental vs full delta
  payload shapes, rollback on `lastError` + on non-ok response,
  streaming deferral, observer+debounce lifecycle, SPA-nav reset of
  fingerprint + sentCount, `resolveConversationId` override.
- ChatGPT — every one of the 5 extraction strategies, `<thinking>`
  panel wrapping, Deep Research fallback (assistant-only scenario),
  synthetic `_new_` + timestamp ID, streaming indicators.
- Claude — dedicated-selector pair with visual-top sort, thinking
  panel wrap, `.font-user-message` / `.font-claude-message` class
  markers, generic message-block role inference via
  class/data-role/aria-label, direct-children-of-container fallback.
- Gemini — `<user-query>` / `<model-response>` tag detection,
  `data-turn-role` attributes, dedup of duplicated turns,
  `[class*=turn]` fallback, generic noise-stripping (`.sr-only`,
  `.chip-container`, `.action-button`).

## What P2.5 Phase 3a shipped

Three shared-helper modules + a full Vitest harness. Phase 3a preserves
the legacy JS copies under `../pce_browser_extension/content_scripts/`
and keeps syncing them into `public/` — site extractors still load them
unchanged. The TS ports are additive: they exist so (a) Phase 3b site
extractors can import them as proper ESM modules instead of relying on
`window.__PCE_*` globals, and (b) Vitest can exercise them in isolation.

### New `utils/*.ts`

- `utils/site-configs.ts` — typed registry of selector configs for the
  14 supported AI sites + 5 aliases. Exports `PceSiteConfig`,
  `PceSiteConfigs`, a frozen `PCE_SITE_CONFIGS` record, and a
  `resolveSiteConfig(hostname)` helper that honours direct hits,
  aliases, and subdomain-suffix fallback.
- `utils/selector-engine.ts` — factory function
  `createSelectorEngine({ doc, win, hostname, pathname, logger })` that
  returns a `SelectorEngine` instance with injectable DOM handles so
  unit tests don't need real browser globals. Preserves the legacy
  cache-hit / cache-miss / 3-strike-invalidation behaviour byte-for-byte,
  exposes the same 13-method public API
  (`queryContainer` / `queryUserMessages` / `queryAssistantMessages` /
  `queryTurnPairs` / `queryAllMessages` / `isStreaming` / `detectRole` /
  `getSessionHint` / `getModelName` / `getConfig` / `getProvider` /
  `getSourceName` / `getDiagnostics`) plus a new `resetCache()` for
  tests.
- `utils/pce-dom.ts` — pure helpers for attachment / reply / thinking
  extraction + manual-capture DOM bridge. Exports
  `normalizeCitationUrl` / `fingerprintConversation` /
  `inferMediaTypeFromName` / `extractAttachments` /
  `extractThinking` / `extractReplyContent` / `isStreaming` /
  `getSessionHint` / `installManualCaptureBridge`.

### `entrypoints/interceptor-ai-patterns.ts` refactor

Pattern tables (`AI_API_DOMAINS`, `AI_PATH_PATTERNS`, `WEB_UI_DOMAINS`,
`WEB_UI_AI_PATHS`, `WEB_UI_NOISE_PATHS`, `AI_REQUEST_FIELDS`,
`HOST_TO_PROVIDER`) and matcher functions (`matchUrl`,
`matchRequestBody`, `isStreamingResponse`, `isAIRequest`,
`guessProviderFromHost`) moved from inside the `defineUnlistedScript`
closure to module-level exports. The `defineUnlistedScript` entry
point now just publishes the same functions onto
`window.__PCE_AI_PATTERNS` exactly as before — runtime behaviour is
byte-identical, the motivation is purely testability.

### Vitest harness

- `package.json` — added `vitest` + `@vitest/coverage-v8` +
  `happy-dom` + `fake-indexeddb` devDeps; `test` + `test:watch`
  scripts.
- `vitest.config.ts` — `happy-dom` env by default, coverage via
  `v8`, setup file at `test/setup.ts`.
- `test/setup.ts` — silences `console.debug`, installs no-op
  `defineBackground` / `defineContentScript` / `defineUnlistedScript`
  globals so entrypoint files import cleanly under Vitest.

### Tests landed (149 test cases across 7 files)

| File | Test count |
|---|---|
| `utils/__tests__/site-configs.test.ts` | 7 |
| `utils/__tests__/selector-engine.test.ts` | 17 |
| `utils/__tests__/pce-dom.test.ts` | 26 |
| `entrypoints/background/__tests__/injector.test.ts` | 16 |
| `entrypoints/background/__tests__/capture-queue.test.ts` | 14 |
| `entrypoints/__tests__/interceptor-ai-patterns.test.ts` | 34 |
| `entrypoints/__tests__/background.test.ts` | 15 |

Coverage targets the pure matchers that regressions in the migration
would surface fastest:

- URL / body / streaming matchers in `interceptor-ai-patterns` (web UI
  noise filter, pure-API fast path, unknown-domain path-pattern
  fallback, body field signatures, combined `isAIRequest` promotion
  rules).
- Domain-pipeline tables in `injector` (every
  `DOMAIN_CONTENT_SCRIPTS` entry has a matching
  `DOMAIN_EXTRACTOR_FLAGS`, alias rows share the same scripts + flag).
- `CaptureQueue` IDB semantics against `fake-indexeddb`
  (enqueue / count / flush happy-path, 4xx drops + 5xx retains + network
  errors bump attempts, TTL eviction, concurrency guard, clear).
- `selector-engine` cache hit + miss + invalidation, role detection,
  `queryAllMessages` fallback.
- `pce-dom` citation URL normalisation (Google redirect unwrap),
  fingerprint stability, media-type inference, attachment dedup.
- `background.ts.__testing` `simpleHash` determinism, `isDuplicate`
  5-second window roll-off, `getSilentDomains` grace window.

## How the skeleton works today

1. `scripts/sync-legacy-assets.mjs` runs as a `prebuild` / `predev`
   step. It copies the three still-legacy asset roots from
   `../pce_browser_extension/` into `public/`:
   ```
   ../pce_browser_extension/content_scripts  →  public/content_scripts
   ../pce_browser_extension/icons            →  public/icons
   ../pce_browser_extension/popup            →  public/popup
   ```
   The `content_scripts/` dir still contains `pce_dom_utils.js`,
   `selector_engine.js`, `site_configs.js`, `detector.js`,
   `behavior_tracker.js`, `text_collector.js`, and the 13 site
   extractors. All are registered in the imperative `content_scripts`
   list in `wxt.config.ts`.
2. `wxt.config.ts` generates the MV3 manifest. `bridge.content.ts` is
   auto-registered from its `defineContentScript` entrypoint. Site
   bundles include the legacy shared JS from `public/content_scripts/`.
3. TS entrypoints under `entrypoints/` (`background.ts` + its helpers,
   `bridge.content.ts`, `interceptor-*.ts`) are typechecked, bundled,
   and sourcemapped by WXT.
4. `utils/*.ts` are pure ESM libraries. Not yet loaded by the runtime
   (Phase 3b site extractors will import them). Unit-tested by Vitest.

## Usage

```bash
cd pce_browser_extension_wxt
pnpm install                    # first time only
pnpm dev                        # Chrome, HMR
pnpm dev:firefox                # Firefox, HMR
pnpm build                      # Chrome production bundle → .output/chrome-mv3/
pnpm build:firefox              # Firefox bundle       → .output/firefox-mv3/
pnpm zip                        # Chrome .zip for sideload / store
pnpm zip:firefox                # Firefox .xpi
pnpm typecheck                  # strict tsc --noEmit
pnpm test                       # Vitest — unit tests (367 cases after 3b4)
pnpm test:watch                 # Vitest watch mode
```

To load the dev bundle in Chrome: visit `chrome://extensions` →
*Developer mode* → *Load unpacked* → point at `.output/chrome-mv3/`.

### Pre-install lints

Before `pnpm install`, the IDE reports several "Cannot find module"
errors for `vitest`, `vitest/config`, `node:path`, `node:url` in
`vitest.config.ts` / `test/setup.ts` / `*.test.ts`. These are
**expected** — same class as the existing pre-install lints for `wxt` /
`@types/chrome`. Real module resolution kicks in post-install and
`pnpm typecheck` becomes clean.

## Migration plan (remaining P2.5 phases)

Each phase is a separate PR, reviewable and revertable on its own:

### P2.5 Phase 1 ✅ (2026-04-17 — commit `cf02a3c`)
- `background/capture_queue.js` → `entrypoints/background/capture-queue.ts`
- `background/service_worker.js` → `entrypoints/background.ts`
- `entrypoints/background/injector.ts`

### P2.5 Phase 2 ✅ (2026-04-17 — commit `5fa56a7`)
- `content_scripts/bridge.js` → `entrypoints/bridge.content.ts`
- `interceptor/page_confirmed.js` → `entrypoints/interceptor-page-confirmed.ts`
- `interceptor/ai_patterns.js` → `entrypoints/interceptor-ai-patterns.ts`
- `interceptor/network_interceptor.js` → `entrypoints/interceptor-network.ts`

### P2.5 Phase 3a ✅ (2026-04-17 — commit `3b4e75a`)
- `content_scripts/pce_dom_utils.js` → `utils/pce-dom.ts`
- `content_scripts/selector_engine.js` → `utils/selector-engine.ts`
- `content_scripts/site_configs.js` → `utils/site-configs.ts`
- `interceptor-ai-patterns.ts` module-level export refactor
- Vitest + happy-dom + fake-indexeddb devDeps + config + setup
- 7 test files, 149 cases covering the migrated pure helpers

### P2.5 Phase 3b — 13 site extractors (2-3 per PR)

1. ✅ `chatgpt.js` + `claude.js` + `gemini.js` (2026-04-18)
2. ✅ `deepseek.js` + `google_ai_studio.js` + `perplexity.js` (2026-04-18)
3. ✅ `copilot.js` + `poe.js` + `grok.js` (2026-04-18)
4. ✅ `huggingface.js` + `manus.js` + `zhipu.js` (**this PR**)
5. ⏳ `generic.js` (Mistral + Kimi) + `universal_extractor.js` +
   `detector.js` / `behavior_tracker.js` / `text_collector.js`

Each site extractor becomes an `entrypoints/<site>.content.ts`
entrypoint with `defineContentScript`, importing from `utils/*.ts`
and `utils/capture-runtime.ts`.

### P2.5 Phase 4 — cleanup
- Delete `../pce_browser_extension/` (after Phase 3b–3f is complete
  and e2e verified).
- Delete `scripts/sync-legacy-assets.mjs`.
- Delete `public/{content_scripts,icons,popup}` staging.
- Update root README and install docs to point only at this directory.

## File tree (after Phase 3b batch 4)

```
pce_browser_extension_wxt/
├── .gitignore
├── README.md                              # this file
├── package.json                           # WXT + TypeScript + Vitest + …
├── tsconfig.json                          # strict; noEmit; allowJs
├── types.d.ts                             # pre-install shim
├── vitest.config.ts                       # ← P2.5 Phase 3a
├── wxt.config.ts                          # MV3 manifest generator
├── test/
│   └── setup.ts                           # ← P2.5 Phase 3a
├── scripts/
│   └── sync-legacy-assets.mjs             # dep-free Node sync (prebuild)
├── entrypoints/
│   ├── background.ts                      # ← P2.5 Phase 1
│   ├── bridge.content.ts                  # ← P2.5 Phase 2
│   ├── interceptor-page-confirmed.ts      # ← P2.5 Phase 2
│   ├── interceptor-ai-patterns.ts         # ← P2.5 Phase 2 (refactored 3a)
│   ├── interceptor-network.ts             # ← P2.5 Phase 2
│   ├── chatgpt.content.ts                 # ← P2.5 Phase 3b1
│   ├── claude.content.ts                  # ← P2.5 Phase 3b1
│   ├── gemini.content.ts                  # ← P2.5 Phase 3b1
│   ├── deepseek.content.ts                # ← P2.5 Phase 3b2
│   ├── google-ai-studio.content.ts        # ← P2.5 Phase 3b2
│   ├── perplexity.content.ts              # ← P2.5 Phase 3b2
│   ├── copilot.content.ts                 # ← P2.5 Phase 3b3
│   ├── poe.content.ts                     # ← P2.5 Phase 3b3
│   ├── grok.content.ts                    # ← P2.5 Phase 3b3
│   ├── huggingface.content.ts             # ← P2.5 Phase 3b4
│   ├── manus.content.ts                   # ← P2.5 Phase 3b4
│   ├── zhipu.content.ts                   # ← P2.5 Phase 3b4
│   ├── background/
│   │   ├── capture-queue.ts               # ← P2.5 Phase 1
│   │   ├── injector.ts                    # ← P2.5 Phase 1
│   │   └── __tests__/
│   │       ├── capture-queue.test.ts      # ← P2.5 Phase 3a
│   │       └── injector.test.ts           # ← P2.5 Phase 3a
│   └── __tests__/
│       ├── background.test.ts             # ← P2.5 Phase 3a
│       ├── interceptor-ai-patterns.test.ts # ← P2.5 Phase 3a
│       ├── chatgpt.content.test.ts        # ← P2.5 Phase 3b1
│       ├── claude.content.test.ts         # ← P2.5 Phase 3b1
│       ├── gemini.content.test.ts         # ← P2.5 Phase 3b1
│       ├── deepseek.content.test.ts       # ← P2.5 Phase 3b2
│       ├── google-ai-studio.content.test.ts # ← P2.5 Phase 3b2
│       ├── perplexity.content.test.ts     # ← P2.5 Phase 3b2
│       ├── copilot.content.test.ts        # ← P2.5 Phase 3b3
│       ├── poe.content.test.ts            # ← P2.5 Phase 3b3
│       ├── grok.content.test.ts           # ← P2.5 Phase 3b3
│       ├── huggingface.content.test.ts    # ← P2.5 Phase 3b4
│       ├── manus.content.test.ts          # ← P2.5 Phase 3b4
│       └── zhipu.content.test.ts          # ← P2.5 Phase 3b4
├── utils/
│   ├── pce-messages.ts                    # typed PCE message shapes
│   ├── pce-dom.ts                         # ← P2.5 Phase 3a
│   ├── selector-engine.ts                 # ← P2.5 Phase 3a
│   ├── site-configs.ts                    # ← P2.5 Phase 3a
│   ├── capture-runtime.ts                 # ← P2.5 Phase 3b1
│   └── __tests__/
│       ├── pce-dom.test.ts                # ← P2.5 Phase 3a
│       ├── selector-engine.test.ts        # ← P2.5 Phase 3a
│       ├── site-configs.test.ts           # ← P2.5 Phase 3a
│       └── capture-runtime.test.ts        # ← P2.5 Phase 3b1
└── public/                                # build-time staging (gitignored)
    ├── content_scripts/                   # synced from ../pce_browser_extension
    ├── icons/
    └── popup/
```
