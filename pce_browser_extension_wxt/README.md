# PCE Browser Extension — WXT Build Chain

P2 skeleton per [TASK-004 §5.1](../Docs/tasks/TASK-004-P2-capture-ux-upgrade.md)
and [ADR-006](../Docs/docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md).
P2.5 Phase 3a (shared helpers + Vitest harness) landed on 2026-04-17.

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
| Shared helpers (`pce-dom`, `selector-engine`, `site-configs`) on TS | ✅ **landed (P2.5.3a)** |
| Vitest harness + unit tests for pure helpers | ✅ **landed (P2.5.3a)** |
| `interceptor-ai-patterns` refactored for testability | ✅ **landed (P2.5.3a)** |
| Shared PCE message interfaces (`utils/pce-messages.ts`) | ✅ landed (P2) |
| Chrome API pre-install type shim (`types.d.ts`) | ✅ landed (P2.5.1+2) |
| 13 site-specific content scripts rewritten in TypeScript | ⏳ P2.5 Phase 3b-3f (2-3 per PR) |
| E2E verified against the new bundle | ⏳ P2.5 Phase 4 |

The step-by-step deferral honours ADR-006's guardrail:

> 迁移过程中不引入任何新功能，保持"结构替换、行为不变"。任何业务逻辑修改都单独成 PR，
> 不与框架迁移混合。

Every P2.5 phase is a pure 1-for-1 behaviour port. The legacy JS under
`../pce_browser_extension/` is the frozen source-of-truth for site
extractors until Phase 3b–3f migrates them.

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
pnpm test                       # Vitest — unit tests (149 cases)
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

### P2.5 Phase 3a ✅ (2026-04-17 — this PR)
- `content_scripts/pce_dom_utils.js` → `utils/pce-dom.ts`
- `content_scripts/selector_engine.js` → `utils/selector-engine.ts`
- `content_scripts/site_configs.js` → `utils/site-configs.ts`
- `interceptor-ai-patterns.ts` module-level export refactor
- Vitest + happy-dom + fake-indexeddb devDeps + config + setup
- 7 test files, 149 cases covering the migrated pure helpers

### P2.5 Phase 3b–3f — 13 site extractors (2-3 per PR)
1. `chatgpt.js` + `claude.js` + `gemini.js`
2. `deepseek.js` + `google_ai_studio.js` + `perplexity.js`
3. `copilot.js` + `poe.js` + `grok.js`
4. `huggingface.js` + `manus.js` + `zhipu.js`
5. `generic.js` + `universal_extractor.js` +
   `detector.js` / `behavior_tracker.js` / `text_collector.js`

Each site extractor becomes an `entrypoints/<site>.content.ts`
entrypoint with `defineContentScript`, importing from `utils/*.ts`.

### P2.5 Phase 4 — cleanup
- Delete `../pce_browser_extension/` (after Phase 3b–3f is complete
  and e2e verified).
- Delete `scripts/sync-legacy-assets.mjs`.
- Delete `public/{content_scripts,icons,popup}` staging.
- Update root README and install docs to point only at this directory.

## File tree (after Phase 3a)

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
│   ├── background/
│   │   ├── capture-queue.ts               # ← P2.5 Phase 1
│   │   ├── injector.ts                    # ← P2.5 Phase 1
│   │   └── __tests__/
│   │       ├── capture-queue.test.ts      # ← P2.5 Phase 3a
│   │       └── injector.test.ts           # ← P2.5 Phase 3a
│   └── __tests__/
│       ├── background.test.ts             # ← P2.5 Phase 3a
│       └── interceptor-ai-patterns.test.ts # ← P2.5 Phase 3a
├── utils/
│   ├── pce-messages.ts                    # typed PCE message shapes
│   ├── pce-dom.ts                         # ← P2.5 Phase 3a
│   ├── selector-engine.ts                 # ← P2.5 Phase 3a
│   ├── site-configs.ts                    # ← P2.5 Phase 3a
│   └── __tests__/
│       ├── pce-dom.test.ts                # ← P2.5 Phase 3a
│       ├── selector-engine.test.ts        # ← P2.5 Phase 3a
│       └── site-configs.test.ts           # ← P2.5 Phase 3a
└── public/                                # build-time staging (gitignored)
    ├── content_scripts/                   # synced from ../pce_browser_extension
    ├── icons/
    └── popup/
```
