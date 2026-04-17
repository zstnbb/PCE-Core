# PCE Browser Extension — WXT Build Chain

P2 deliverable per [TASK-004 §5.1](../Docs/tasks/TASK-004-P2-capture-ux-upgrade.md)
and [ADR-006](../Docs/docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md).

## Status

| Area | Status |
|---|---|
| WXT build chain (vite + HMR + cross-browser zip) | ✅ landed |
| Single source of truth for the manifest (`wxt.config.ts`) | ✅ landed |
| Sideload vs webstore flavours via WXT `mode` | ✅ landed |
| Background entrypoint on TypeScript | 🟡 thin TS wrapper; delegates to legacy JS (see below) |
| Shared PCE message interfaces (`utils/pce-messages.ts`) | ✅ landed |
| 13 site-specific content scripts rewritten in TypeScript | ⏳ deferred to P2.5 |
| E2E verified against the new bundle | ⏳ deferred to P2.5 |

The deferral follows ADR-006's own guardrail:

> 迁移过程中不引入任何新功能，保持"结构替换、行为不变"。任何业务逻辑修改都单独成 PR，
> 不与框架迁移混合。

Doing a full 13-extractor + interceptor TS rewrite in the same PR as the
WXT skeleton would mix "structure replacement" with "behaviour rewrite",
violating that guardrail. See the **Migration plan** below for the exact
cut line.

## How the skeleton works today

1. `scripts/sync-legacy-assets.mjs` runs as a `prebuild` / `predev` step.
   It copies the legacy directories from `../pce_browser_extension/`
   into `public/`:
   ```
   ../pce_browser_extension/content_scripts  →  public/content_scripts
   ../pce_browser_extension/interceptor      →  public/interceptor
   ../pce_browser_extension/icons            →  public/icons
   ../pce_browser_extension/popup            →  public/popup
   ```
2. `wxt.config.ts` generates the MV3 manifest and registers every site
   bundle using the legacy JS file names — so the bundle that ships is
   byte-equivalent to what the user loads today.
3. `entrypoints/background.ts` is the only entrypoint that's already on
   TypeScript. It uses `defineBackground` (WXT convention) and dynamically
   imports `./legacy/service_worker.js`, preserving all listener
   registration semantics.
4. `utils/pce-messages.ts` exports the PCE message-shape interfaces so
   the next content-script port gets `chrome.runtime.sendMessage` typed
   from day one.

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
```

To load the dev bundle in Chrome: visit `chrome://extensions` →
*Developer mode* → *Load unpacked* → point at `.output/chrome-mv3/`.

The legacy extension under `../pce_browser_extension/` is still the
source-of-truth for behaviour — do not delete it until P2.5 lands. The
legacy `manifest.json` and `manifest.store.json` will be removed in the
same PR that completes the content-script migration.

## Migration plan (P2.5)

The rewrite is intentionally decoupled from this skeleton so each step is
reviewable and rollbackable:

1. **`background/capture_queue.js` → `entrypoints/background/capture-queue.ts`.**
   Pure data structure, no chrome APIs touched — easy first step.
2. **`background/service_worker.js` → `entrypoints/background.ts` (full port).**
   Replace the dynamic `import("./legacy/...")` with the real handlers.
   Tag messages with the `PCEMessage` union.
3. **`content_scripts/bridge.js` + `network_interceptor.js` →
   `entrypoints/bridge.content.ts` + `entrypoints/interceptor/…`**.
   These are the shared plumbing every site extractor depends on.
4. **Site extractors, one PR per 2–3 sites.** Start with chatgpt.js /
   claude.js / gemini.js, keep `site_configs.js` as a JSON literal so
   type-safety is maximal.
5. **Final pass:** delete `../pce_browser_extension/` and the legacy
   sync script; update the root README to point only at the WXT project.

## Testing

The existing Python Selenium e2e suite
(`tests/e2e/`) drives a real Chrome against this extension. Once
`pnpm build` is run, pointing the test loader at
`pce_browser_extension_wxt/.output/chrome-mv3/` exercises the WXT bundle
verbatim.  That replacement is safe and reversible: pointing back at
`pce_browser_extension/` restores the original build path.
