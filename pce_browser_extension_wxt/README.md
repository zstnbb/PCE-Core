# PCE Browser Extension — WXT Build Chain

P2 skeleton per [TASK-004 §5.1](../Docs/tasks/TASK-004-P2-capture-ux-upgrade.md)
and [ADR-006](../Docs/docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md).
P2.5 Phase 2 (bridge + interceptor TS port) landed on 2026-04-17.

## Status

| Area | Status |
|---|---|
| WXT build chain (vite + HMR + cross-browser zip) | ✅ landed (P2) |
| Single source of truth for the manifest (`wxt.config.ts`) | ✅ landed (P2) |
| Sideload vs webstore flavours via WXT `mode` | ✅ landed (P2) |
| Background entrypoint on TypeScript | ✅ landed (P2.5.1) |
| `CaptureQueue` (IndexedDB offline buffer) on TypeScript | ✅ landed (P2.5.1) |
| Dynamic / proactive injector on TypeScript | ✅ landed (P2.5.1) |
| `bridge.content.ts` (content-script) on TypeScript | ✅ **landed (P2.5.2)** |
| `interceptor-page-confirmed.ts` on TypeScript | ✅ **landed (P2.5.2)** |
| `interceptor-ai-patterns.ts` on TypeScript | ✅ **landed (P2.5.2)** |
| `interceptor-network.ts` (fetch/XHR/WS/SSE) on TypeScript | ✅ **landed (P2.5.2)** |
| Shared PCE message interfaces (`utils/pce-messages.ts`) | ✅ landed (P2) |
| Chrome API pre-install type shim (`types.d.ts`) | ✅ landed (P2.5.1+2) |
| Shared helpers (`pce_dom_utils`, `selector_engine`, `site_configs`) → TS | ⏳ P2.5 Phase 3 |
| 13 site-specific content scripts rewritten in TypeScript | ⏳ P2.5 Phase 3 (2-3 per PR) |
| Vitest harness + unit tests for pure helpers | ⏳ P2.5 Phase 3 |
| E2E verified against the new bundle | ⏳ P2.5 Phase 4 |

The step-by-step deferral honours ADR-006's guardrail:

> 迁移过程中不引入任何新功能，保持"结构替换、行为不变"。任何业务逻辑修改都单独成 PR，
> 不与框架迁移混合。

Every P2.5 phase is a pure 1-for-1 behaviour port. The legacy JS under
`../pce_browser_extension/` is the frozen source-of-truth for shared
helpers + site extractors until Phase 3 migrates them.

## What P2.5 Phase 2 shipped

All three interceptor page-context scripts + the content-script bridge
now live in TypeScript with typed interfaces and compile-time enforced
contracts between them:

`entrypoints/bridge.content.ts` (TypeScript, 125 lines) — content script
that registers itself via `defineContentScript` on the 17 AI-site match
patterns and does two things per page load:

1. Injects the three page-context interceptor scripts via
   `chrome.runtime.getURL("interceptor-*.js")` + `<script src>`
   (inline `<script>` is blocked by CSP on most AI sites).
2. Listens for `window.postMessage({ type: "PCE_NETWORK_CAPTURE", … })`
   events and forwards them to the background via
   `chrome.runtime.sendMessage`.

`entrypoints/interceptor-page-confirmed.ts` (TypeScript, 14 lines) —
WXT unlisted script. Sets `window.__PCE_AI_PAGE_CONFIRMED = true` so
`interceptor-network` can fall back to aggressive capture mode on
confirmed AI pages.

`entrypoints/interceptor-ai-patterns.ts` (TypeScript, 480 lines) —
WXT unlisted script. Pure pattern-matching module: 48-host allowlist,
27 path-regex patterns, 5 web-UI noise-path regexes, 5
request-body-field signatures, 42-entry HOST_TO_PROVIDER table.
Exports typed `AIPatternsAPI` (`matchUrl` / `matchRequestBody` /
`isStreamingResponse` / `isAIRequest`) and exposes itself at runtime as
`window.__PCE_AI_PATTERNS`.

`entrypoints/interceptor-network.ts` (TypeScript, 730 lines) — the big
one. WXT unlisted script. Monkey-patches `fetch` / `XMLHttpRequest` /
`WebSocket` / `EventSource`, captures AI-related traffic, and posts
back via `window.postMessage`. Preserves every tricky nuance from the
legacy JS:

- Request-body extraction from all body types (`string` / `URLSearchParams` /
  `FormData` / `Blob` / `ArrayBuffer` / cloned `Request`).
- Aggressive mode for confirmed AI pages: captures POSTs with AI-like
  JSON bodies on unknown URLs, and pending-stream-check for SSE/JSON
  responses.
- SSE/streaming response handler via pass-through `ReadableStream`
  that captures chunks without affecting the page's own reader, with
  120-second silence force-flush.
- ChatGPT WS-protocol extractor covering cumulative-parts
  deduplication, Deep Research `result`/`text` fields, DALL-E /
  browsing / code-interpreter tool calls with dedup by name+result.
- XHR interceptor catching both `load` and `error` events.
- EventSource interceptor with `close()` flush override.
- All four interceptors disguise their `toString()` output as native
  code to reduce fingerprinting surface against anti-automation checks.

`utils/pce-messages.ts` (updated in P2.5.1) and the coarse Chrome stub
in `types.d.ts` already cover everything Phase 2 needed — no new
runtime deps.

### Build-chain changes

- `wxt.config.ts` — removed `content_scripts/bridge.js` from
  `SITE_SCRIPT_COMMON` (it's now a `defineContentScript` entrypoint
  with its own matches list). `web_accessible_resources` now points at
  the new `interceptor-*.js` paths emitted by WXT at the output root.
- `scripts/sync-legacy-assets.mjs` — no longer copies the
  `interceptor/` directory; those scripts are TS entrypoints now.
- `.gitignore` — dropped the stale `public/interceptor/` line.
- `types.d.ts` — added typed stubs for `defineContentScript` and
  `defineUnlistedScript` globals + moved the `chrome` namespace stub
  inside `declare global { ... }` so `export {}` doesn't strip it
  from the global scope.

## How the skeleton works today

1. `scripts/sync-legacy-assets.mjs` runs as a `prebuild` / `predev`
   step. It copies the three still-legacy asset roots from
   `../pce_browser_extension/` into `public/`:
   ```
   ../pce_browser_extension/content_scripts  →  public/content_scripts
   ../pce_browser_extension/icons            →  public/icons
   ../pce_browser_extension/popup            →  public/popup
   ```
   Note: `interceptor/` is **not** copied anymore — it's TS now.
   The 13 site extractors + shared helpers (`pce_dom_utils.js`,
   `selector_engine.js`, `site_configs.js`, `detector.js`,
   `behavior_tracker.js`, `text_collector.js`) still ride through
   unchanged until Phase 3.
2. `wxt.config.ts` generates the MV3 manifest. Bridge content script
   is auto-registered from `bridge.content.ts`. Site bundles pull in
   the shared legacy helpers from `public/content_scripts/` (minus
   `bridge.js` — that's the TS entrypoint now).
3. Three TS entrypoints under `entrypoints/` (`bridge.content.ts`
   plus `interceptor-*.ts`) are typechecked, bundled, and sourcemapped
   by WXT. Legacy interceptor JS is gone.
4. `entrypoints/background.ts` is the full TS port of the legacy
   service worker. No imports from the legacy tree.

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
pnpm typecheck                  # strict tsc --noEmit (uses tsconfig.json)
```

To load the dev bundle in Chrome: visit `chrome://extensions` →
*Developer mode* → *Load unpacked* → point at `.output/chrome-mv3/`.

## Migration plan (remaining P2.5 phases)

Each phase is a separate PR, reviewable and revertable on its own:

### P2.5 Phase 1 ✅ (2026-04-17 — commit `cf02a3c`)
- `background/capture_queue.js` → `entrypoints/background/capture-queue.ts`
- `background/service_worker.js` → `entrypoints/background.ts`
- Extract injection logic into `entrypoints/background/injector.ts`
- Delete `entrypoints/legacy/` staging dir

### P2.5 Phase 2 ✅ (2026-04-17 — this PR)
- `content_scripts/bridge.js` → `entrypoints/bridge.content.ts`
- `interceptor/page_confirmed.js` → `entrypoints/interceptor-page-confirmed.ts`
- `interceptor/ai_patterns.js` → `entrypoints/interceptor-ai-patterns.ts`
- `interceptor/network_interceptor.js` → `entrypoints/interceptor-network.ts`
- Update WXT config + sync script + .gitignore + types shim.

### P2.5 Phase 3 — shared helpers + site extractors (next)
3a. Shared helpers + Vitest harness (one PR):
- `content_scripts/pce_dom_utils.js` → `utils/pce-dom.ts`
- `content_scripts/selector_engine.js` → `utils/selector-engine.ts`
- `content_scripts/site_configs.js` → `utils/site-configs.ts`
   (typed JSON literal so selectors get IntelliSense)
- Add Vitest + `fake-indexeddb` deps, unit tests for:
  - `injector.ts` pure helpers
  - `capture-queue.ts` IDB semantics
  - `background.ts` `__testing` hooks
  - `interceptor-ai-patterns.ts` URL/body matchers
  - `selector-engine.ts` pure matchers

3b–3f. Site extractors, 2–3 per PR (5 PRs):
1. `chatgpt.js` + `claude.js` + `gemini.js`
2. `deepseek.js` + `google_ai_studio.js` + `perplexity.js`
3. `copilot.js` + `poe.js` + `grok.js`
4. `huggingface.js` + `manus.js` + `zhipu.js`
5. `generic.js` + `universal_extractor.js` (covers Mistral / Kimi / ChatGLM)
   plus `detector.js` / `behavior_tracker.js` / `text_collector.js`

Each site extractor becomes an `entrypoints/<site>.content.ts`
entrypoint with `defineContentScript`.

### P2.5 Phase 4 — cleanup
- Delete `../pce_browser_extension/` (after Phase 3 is complete and
  e2e verified).
- Delete `scripts/sync-legacy-assets.mjs` (no more legacy to copy).
- Delete `public/{content_scripts,icons,popup}` staging.
- Update root README and install docs to point only at this directory.

## Testing

The existing Python Selenium e2e suite (`tests/e2e/`) drives a real
Chrome against the shipped extension. Once `pnpm build` is run,
pointing the test loader at
`pce_browser_extension_wxt/.output/chrome-mv3/` exercises the WXT
bundle verbatim. The swap is safe and reversible: pointing back at
`pce_browser_extension/` restores the original build path.

Vitest unit tests land with Phase 3 and will cover:
- `injector.ts` pure helpers (`matchDomainConfig`,
  `getScriptsForUrl`, `getExtractorFlagForDomain`).
- `capture-queue.ts` via `fake-indexeddb`.
- `background.ts` `__testing` hooks (`simpleHash`, `isDuplicate`,
  `isBlacklisted`, self-check entries lifecycle).
- `interceptor-ai-patterns.ts` matchers (`matchUrl`,
  `matchRequestBody`, `isAIRequest`, `isStreamingResponse`).

## File tree (after Phase 2)

```
pce_browser_extension_wxt/
├── .gitignore
├── README.md                              # this file
├── package.json                           # WXT + TypeScript + @types/chrome
├── tsconfig.json                          # strict; noEmit; allowJs
├── types.d.ts                             # pre-install shim for wxt + chrome
├── wxt.config.ts                          # MV3 manifest generator
├── scripts/
│   └── sync-legacy-assets.mjs             # dep-free Node sync (prebuild)
├── entrypoints/
│   ├── background.ts                      # ← P2.5 Phase 1
│   ├── bridge.content.ts                  # ← P2.5 Phase 2
│   ├── interceptor-page-confirmed.ts      # ← P2.5 Phase 2
│   ├── interceptor-ai-patterns.ts         # ← P2.5 Phase 2
│   ├── interceptor-network.ts             # ← P2.5 Phase 2
│   └── background/
│       ├── capture-queue.ts               # ← P2.5 Phase 1
│       └── injector.ts                    # ← P2.5 Phase 1
├── utils/
│   └── pce-messages.ts                    # typed PCE message shapes
└── public/                                # build-time staging (gitignored)
    ├── content_scripts/                   # synced from ../pce_browser_extension
    ├── icons/
    └── popup/
```
