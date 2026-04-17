# PCE Browser Extension — WXT Build Chain

P2 skeleton per [TASK-004 §5.1](../Docs/tasks/TASK-004-P2-capture-ux-upgrade.md)
and [ADR-006](../Docs/docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md).
P2.5 Phase 1 (background TS port) landed on 2026-04-17.

## Status

| Area | Status |
|---|---|
| WXT build chain (vite + HMR + cross-browser zip) | ✅ landed (P2) |
| Single source of truth for the manifest (`wxt.config.ts`) | ✅ landed (P2) |
| Sideload vs webstore flavours via WXT `mode` | ✅ landed (P2) |
| Background entrypoint on TypeScript | ✅ **full port landed (P2.5.1)** |
| `CaptureQueue` (IndexedDB offline buffer) on TypeScript | ✅ **landed (P2.5.1)** |
| Dynamic / proactive injector on TypeScript | ✅ **landed (P2.5.1)** |
| Shared PCE message interfaces (`utils/pce-messages.ts`) | ✅ landed (P2) |
| Chrome API pre-install type shim (`types.d.ts`) | ✅ landed (P2.5.1) |
| `bridge.js` + `network_interceptor.js` → TS | ⏳ P2.5 Phase 2 |
| 13 site-specific content scripts rewritten in TypeScript | ⏳ P2.5 Phase 3 (2-3 per PR) |
| E2E verified against the new bundle | ⏳ P2.5 Phase 4 |

The step-by-step deferral honours ADR-006's guardrail:

> 迁移过程中不引入任何新功能，保持"结构替换、行为不变"。任何业务逻辑修改都单独成 PR，
> 不与框架迁移混合。

P2.5 Phase 1 (this PR) is a pure 1-for-1 behaviour port. The
legacy `../pce_browser_extension/background/service_worker.js` and
`capture_queue.js` are functionally frozen — every handler, every
timer, every dedup window, every state transition is reproduced in TS.

## What P2.5 Phase 1 shipped

`entrypoints/background.ts` (TypeScript, 620 lines) replaces the
`defineBackground` stub that previously re-loaded the legacy JS. It
orchestrates:

- **Message routing** — `PCE_CAPTURE`, `PCE_NETWORK_CAPTURE`,
  `PCE_SNIPPET`, `PCE_GET_STATUS`, `PCE_SET_ENABLED`,
  `PCE_BLACKLIST_UPDATED`, `PCE_AI_PAGE_DETECTED`.
- **Capture handlers** — `handleCapture` / `handleNetworkCapture` /
  `handleSnippet` build typed `IngestBody` records and POST to the
  local Ingest API.
- **Dedup** — 5-second short-time window keyed by `session_hint +
  simpleHash(fingerprint)`, with periodic cleanup of expired entries.
- **Self-check** — per-domain detection vs capture counter, badges
  the extension icon with a `!` when an AI page was detected but no
  capture arrived within 30 s.
- **Context menu** — "Capture This Page (PCE)" drives a manual
  capture even for non-detected pages.
- **Tab lifecycle** — cleans up `injectedTabs` on tab-removal /
  full-navigation, and triggers `proactiveInjectFallback` after
  `tabs.onUpdated status === "complete"` to cover CDP / automation
  scenarios where static content_scripts didn't fire.
- **Health loop** — polls `/api/v1/health` every 30 s, exposes
  `serverOnline` via `PCE_GET_STATUS`.
- **Offline queue** — routes captures through `CaptureQueue.enqueue`
  when ingest fails with 5xx / timeout, and starts the retry loop at
  boot.

`entrypoints/background/capture-queue.ts` (TypeScript, 290 lines) is a
full port of `capture_queue.js` — `enqueue / flush / count / clear /
startRetryLoop`, 7-day max age, 2 000-row max size with oldest-first
drop, 20-per-batch flush, exponential backoff from 5 s to 120 s.

`entrypoints/background/injector.ts` (TypeScript, 300 lines) hosts
the domain-to-scripts tables (`DOMAIN_CONTENT_SCRIPTS`,
`DOMAIN_EXTRACTOR_FLAGS`, `UNIVERSAL_FALLBACK_SCRIPTS`) plus the
pure helpers (`matchDomainConfig`, `getScriptsForUrl`,
`getExtractorFlagForDomain`) and the two public injection
entry points (`handleDynamicInjection`, `proactiveInjectFallback`).
The pure helpers are deliberately isolated so Vitest unit tests in
P2.5 Phase 2 can cover them without touching `chrome.scripting`.

`types.d.ts` was extended with a coarse `chrome` namespace stub so the
skeleton type-checks cleanly before `pnpm install`. After install,
`@types/chrome` supersedes the stub via TS declaration merging.

## How the skeleton works today

1. `scripts/sync-legacy-assets.mjs` runs as a `prebuild` / `predev`
   step. It copies the four asset roots from
   `../pce_browser_extension/` into `public/`:
   ```
   ../pce_browser_extension/content_scripts  →  public/content_scripts
   ../pce_browser_extension/interceptor      →  public/interceptor
   ../pce_browser_extension/icons            →  public/icons
   ../pce_browser_extension/popup            →  public/popup
   ```
   All 13 site extractors, the interceptor bundle, the popup UI and the
   icon assets ride through **unchanged** from today's extension.
2. `wxt.config.ts` generates the MV3 manifest and registers every
   site bundle using the legacy JS file names — so the bundle that
   ships is byte-equivalent to what the user loads today for every
   site-specific path.
3. `entrypoints/background.ts` is a full TypeScript port of the
   legacy `service_worker.js` and does **not** import anything from
   the legacy JS anymore. The `entrypoints/legacy/` staging dir has
   been removed.
4. `utils/pce-messages.ts` exports typed PCE message shapes so the
   next content-script port gets `chrome.runtime.sendMessage`
   type-safe from day one.

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

The legacy extension under `../pce_browser_extension/` is still the
source-of-truth for **content scripts**. It will stop being mirrored
into `public/` in P2.5 Phase 3/4 as site extractors move over.

## Migration plan (remaining P2.5 phases)

Each phase is a separate PR, reviewable and revertable on its own:

### P2.5 Phase 1 ✅ (this PR — 2026-04-17)
- `background/capture_queue.js` → `entrypoints/background/capture-queue.ts`
- `background/service_worker.js` → `entrypoints/background.ts`
- Extract injection logic into `entrypoints/background/injector.ts`
- Delete `entrypoints/legacy/` staging dir

### P2.5 Phase 2 — shared plumbing (next)
- `content_scripts/bridge.js` → `entrypoints/bridge.content.ts`
- `interceptor/network_interceptor.js` →
  `entrypoints/interceptor/network-interceptor.ts`
- `interceptor/ai_patterns.js` →
  `entrypoints/interceptor/ai-patterns.ts`
- `content_scripts/selector_engine.js` + `site_configs.js` +
  `pce_dom_utils.js` → `entrypoints/shared/*.ts`
  (these are used by every site extractor; port before extractors)
- Add Vitest harness + unit tests for the pure helpers in
  `injector.ts`, `capture-queue.ts`, `selector_engine.ts`

### P2.5 Phase 3 — site extractors (2-3 PRs per batch)
Order picked to front-load the highest-traffic providers:
1. `chatgpt.js` + `claude.js` + `gemini.js`
2. `deepseek.js` + `google_ai_studio.js` + `perplexity.js`
3. `copilot.js` + `poe.js` + `grok.js`
4. `huggingface.js` + `manus.js` + `zhipu.js`
5. `generic.js` + `universal_extractor.js` (covers Mistral / Kimi / ChatGLM)

Each site extractor becomes an `entrypoints/<site>.content.ts`
entrypoint. `site_configs.js` → a typed JSON literal so selectors get
IntelliSense.

### P2.5 Phase 4 — cleanup
- Delete `../pce_browser_extension/` (after Phase 3 is complete and
  e2e verified).
- Delete `scripts/sync-legacy-assets.mjs` (no more legacy to copy).
- Delete `public/{content_scripts,interceptor,icons,popup}` staging.
- Update root README and install docs to point only at this directory.

## Testing

The existing Python Selenium e2e suite (`tests/e2e/`) drives a real
Chrome against the shipped extension. Once `pnpm build` is run,
pointing the test loader at
`pce_browser_extension_wxt/.output/chrome-mv3/` exercises the WXT
bundle verbatim. The swap is safe and reversible: pointing back at
`pce_browser_extension/` restores the original build path.

For Phase 2 we will add Vitest unit tests for:
- `injector.ts` pure helpers (`matchDomainConfig`,
  `getScriptsForUrl`, `getExtractorFlagForDomain`).
- `capture-queue.ts` via `fake-indexeddb` so IDB semantics are
  exercised without spinning up Chrome.
- `background.ts` `__testing` hooks (`simpleHash`, `isDuplicate`,
  `isBlacklisted`, self-check entries lifecycle).

## File tree (after Phase 1)

```
pce_browser_extension_wxt/
├── .gitignore
├── README.md                     # this file
├── package.json                  # WXT + TypeScript + @types/chrome
├── tsconfig.json                 # strict; noEmit; allowJs for legacy copies
├── types.d.ts                    # pre-install shim for wxt + chrome
├── wxt.config.ts                 # MV3 manifest generator
├── scripts/
│   └── sync-legacy-assets.mjs    # dep-free Node sync (prebuild)
├── entrypoints/
│   ├── background.ts             # ← P2.5 Phase 1
│   └── background/
│       ├── capture-queue.ts      # ← P2.5 Phase 1
│       └── injector.ts           # ← P2.5 Phase 1
├── utils/
│   └── pce-messages.ts           # typed PCE message shapes
└── public/                       # build-time staging (gitignored)
    ├── content_scripts/          # synced from ../pce_browser_extension
    ├── interceptor/
    ├── icons/
    └── popup/
```
