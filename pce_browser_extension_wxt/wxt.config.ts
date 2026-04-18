import { defineConfig } from "wxt";

/**
 * WXT config for the PCE browser extension.
 *
 * See README.md for migration status. The short version (post-P2.5 Phase 3b1):
 * - Build chain, manifest generation, HMR and cross-browser zip are handled
 *   here.
 * - Background service worker + injector + capture queue are TypeScript
 *   (`entrypoints/background.ts`, `entrypoints/background/*.ts`).
 * - Bridge content script is TypeScript (`entrypoints/bridge.content.ts`)
 *   and registers itself via `defineContentScript`, so it's _not_ in the
 *   imperative `content_scripts` list below.
 * - Interceptor page-context scripts are TypeScript unlisted scripts
 *   (`entrypoints/interceptor-*.ts`). WXT emits them at the output root
 *   as `interceptor-<name>.js`, which `bridge.content.ts` injects via
 *   `chrome.runtime.getURL`. `web_accessible_resources` below lists those
 *   new paths.
 * - **All 13 site extractors + the shared helpers are TypeScript**
 *   (P2.5 Phase 3b batches 1-5 completed on 2026-04-18):
 *     - batch 1: `entrypoints/chatgpt.content.ts`,
 *       `entrypoints/claude.content.ts`, `entrypoints/gemini.content.ts`.
 *     - batch 2: `entrypoints/deepseek.content.ts`,
 *       `entrypoints/google-ai-studio.content.ts`,
 *       `entrypoints/perplexity.content.ts`.
 *     - batch 3: `entrypoints/copilot.content.ts`,
 *       `entrypoints/poe.content.ts`, `entrypoints/grok.content.ts`.
 *     - batch 4: `entrypoints/huggingface.content.ts`,
 *       `entrypoints/manus.content.ts`, `entrypoints/zhipu.content.ts`.
 *     - batch 5: `entrypoints/generic.content.ts` (Mistral + Kimi) +
 *       `entrypoints/detector.content.ts` (all_urls) +
 *       `entrypoints/behavior-tracker.content.ts` +
 *       `entrypoints/text-collector.content.ts` +
 *       `entrypoints/universal-extractor.ts` (unlisted; injected via
 *       `chrome.scripting.executeScript` by the background worker).
 *   All register themselves via `defineContentScript` /
 *   `defineUnlistedScript`, so the imperative `content_scripts` list
 *   below is now EMPTY. WXT auto-generates the manifest from the
 *   entrypoint files.
 * - The legacy JS under `../pce_browser_extension/content_scripts/`
 *   is still copied into `public/content_scripts/` by
 *   `scripts/sync-legacy-assets.mjs` — not because the manifest
 *   references it, but because `injector.ts`'s dynamic-injection
 *   fallback path still points at those filenames. Phase 4 removes
 *   the legacy dir and the sync script in one sweep.
 *
 * Two release flavours are produced via WXT env:
 *   - default sideload build (host_permissions: <all_urls>)
 *   - webstore build (explicit host_permissions list), invoked with
 *     `wxt build --mode webstore`.
 */

const COVERED_SITES = [
  "https://chatgpt.com/*",
  "https://chat.openai.com/*",
  "https://claude.ai/*",
  "https://gemini.google.com/*",
  "https://aistudio.google.com/*",
  "https://manus.im/*",
  "https://chat.deepseek.com/*",
  "https://chat.z.ai/*",
  "https://www.perplexity.ai/*",
  "https://copilot.microsoft.com/*",
  "https://poe.com/*",
  "https://huggingface.co/chat/*",
  "https://grok.com/*",
  "https://chat.mistral.ai/*",
  "https://kimi.moonshot.cn/*",
  "https://www.kimi.com/*",
  "https://kimi.com/*",
] as const;

// All site extractors + site-independent helpers now ship as TS
// `defineContentScript` entrypoints (P2.5 Phase 3b completed). The
// legacy `SITE_INDEPENDENT_HELPERS` / `LEGACY_EXTRACTOR_DEPS` /
// `legacySiteBundle` helper are gone — WXT auto-registers
// entrypoints. Phase 4 will remove the sync-legacy-assets.mjs copy
// path entirely once `injector.ts` switches to the TS unlisted
// universal-extractor output.

export default defineConfig({
  srcDir: ".",
  entrypointsDir: "entrypoints",
  publicDir: "public",
  outDir: ".output",
  manifestVersion: 3,
  manifest: ({ browser, mode }) => {
    const hostPermissions =
      mode === "webstore"
        ? [...COVERED_SITES]
        : ["<all_urls>"];

    return {
      name: "PCE - AI Interaction Capture",
      version: "0.4.0",
      description:
        "Captures AI conversations from web-based AI tools and sends them to your local PCE instance.",
      permissions: [
        "storage",
        "activeTab",
        "scripting",
        "tabs",
        "contextMenus",
      ],
      host_permissions: hostPermissions,
      action: {
        default_popup: "popup/popup.html",
        default_icon: {
          "16": "icons/icon16.png",
          "48": "icons/icon48.png",
          "128": "icons/icon128.png",
        },
      },
      // All content scripts now live in `entrypoints/*.content.ts` as
      // `defineContentScript` declarations. WXT auto-registers them
      // from those files — the imperative list is intentionally empty.
      content_scripts: [],
      // Page-context interceptor scripts emitted by the TS unlisted
      // entrypoints (`entrypoints/interceptor-*.ts`). The bridge
      // injects these via `chrome.runtime.getURL` so they must be
      // web-accessible.
      web_accessible_resources: [
        {
          resources: [
            "interceptor-page-confirmed.js",
            "interceptor-ai-patterns.js",
            "interceptor-network.js",
          ],
          matches: ["<all_urls>"],
        },
      ],
      icons: {
        "16": "icons/icon16.png",
        "48": "icons/icon48.png",
        "128": "icons/icon128.png",
      },
      browser_specific_settings:
        browser === "firefox"
          ? {
              gecko: {
                id: "pce@zstnbb.dev",
                strict_min_version: "109.0",
              },
            }
          : undefined,
    };
  },
  vite: () => ({
    build: {
      sourcemap: true,
      minify: false,
    },
  }),
});
