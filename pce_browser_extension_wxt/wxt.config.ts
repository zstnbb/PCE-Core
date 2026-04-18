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
 * - **Nine site extractors are TypeScript** (P2.5 Phase 3b batches 1-3):
 *     - batch 1: `entrypoints/chatgpt.content.ts`,
 *       `entrypoints/claude.content.ts`, `entrypoints/gemini.content.ts`.
 *     - batch 2: `entrypoints/deepseek.content.ts`,
 *       `entrypoints/google-ai-studio.content.ts`,
 *       `entrypoints/perplexity.content.ts`.
 *     - batch 3: `entrypoints/copilot.content.ts`,
 *       `entrypoints/poe.content.ts`, `entrypoints/grok.content.ts`.
 *   They register themselves via `defineContentScript` and consume
 *   `utils/*.ts` directly (no window-global handoff). Their sites have
 *   been removed from the imperative `content_scripts` list below; a
 *   reduced `SITE_INDEPENDENT_HELPERS` entry still injects
 *   `behavior_tracker.js` + `text_collector.js` + `detector.js` so
 *   the `__PCE_BEHAVIOR` global + "Save snippet" floating button
 *   remain wired.
 * - The remaining 4 site-specific content scripts + shared helpers
 *   (`pce_dom_utils.js`, `selector_engine.js`, `site_configs.js`,
 *   `detector.js`, `behavior_tracker.js`, `text_collector.js`) remain
 *   legacy `.js` under `../pce_browser_extension/content_scripts/`. A
 *   prebuild step copies them into `public/content_scripts/` so WXT can
 *   register them without an in-flight rewrite. Phase 3b batches 4–5
 *   migrate the rest 2–3 per PR.
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

// Site-independent helpers — run on every covered site regardless of
// whether the site extractor is legacy JS or TS. These touch no site-
// specific DOM structure:
//   - behavior_tracker.js: writes `window.__PCE_BEHAVIOR` which the
//     capture runtime reads when assembling the `meta.behavior` field.
//   - text_collector.js: the floating "Save snippet" button.
//   - detector.js: unknown-domain AI detection. Harmless on covered
//     sites; carried over from the legacy bundle for parity.
const SITE_INDEPENDENT_HELPERS = [
  "content_scripts/behavior_tracker.js",
  "content_scripts/text_collector.js",
  "content_scripts/detector.js",
] as const;

// Legacy extractor dependencies — write the `window.__PCE_EXTRACT` /
// `window.__PCE_SITE_CONFIGS` / `window.__PCE_SELECTOR_ENGINE` globals
// that the *legacy* JS site extractors consume at runtime. TS
// extractors do NOT need these because they import from `utils/*.ts`
// directly; they therefore only receive `SITE_INDEPENDENT_HELPERS`.
const LEGACY_EXTRACTOR_DEPS = [
  "content_scripts/pce_dom_utils.js",
  "content_scripts/site_configs.js",
  "content_scripts/selector_engine.js",
] as const;

const LEGACY_SITE_SCRIPT_COMMON = [
  ...LEGACY_EXTRACTOR_DEPS,
  ...SITE_INDEPENDENT_HELPERS,
] as const;

// Sites whose extractor now ships as a TS `defineContentScript`
// entrypoint (P2.5 Phase 3b batches 1-3). These get ONLY
// `SITE_INDEPENDENT_HELPERS` via the shared entry below.
const TS_EXTRACTOR_SITES = [
  // Batch 1
  "https://chatgpt.com/*",
  "https://chat.openai.com/*",
  "https://claude.ai/*",
  "https://gemini.google.com/*",
  // Batch 2
  "https://chat.deepseek.com/*",
  "https://aistudio.google.com/*",
  "https://www.perplexity.ai/*",
  // Batch 3
  "https://copilot.microsoft.com/*",
  "https://poe.com/*",
  "https://grok.com/*",
] as const;

function legacySiteBundle(site: string, extractor: string) {
  return {
    matches: [site],
    js: [...LEGACY_SITE_SCRIPT_COMMON, `content_scripts/${extractor}`],
    run_at: "document_start" as const,
  };
}

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
      // Content scripts are defined imperatively for sites that still
      // use legacy JS extractors. TS extractors register themselves via
      // `defineContentScript` in their own entrypoint file and are NOT
      // listed here — only a reduced "site-independent helpers" entry
      // injects the shared `behavior_tracker` + `text_collector` +
      // `detector` bundle on those sites.
      content_scripts: [
        // --- Site-independent helpers for TS-extractor sites (P2.5 Phase 3b) ---
        {
          matches: [...TS_EXTRACTOR_SITES],
          js: [...SITE_INDEPENDENT_HELPERS],
          run_at: "document_start",
        },

        // --- Legacy JS extractors (Phase 3b batches 4-5 pending) ---
        legacySiteBundle("https://manus.im/*", "manus.js"),
        legacySiteBundle("https://chat.z.ai/*", "zhipu.js"),
        legacySiteBundle("https://huggingface.co/chat/*", "huggingface.js"),
        {
          matches: [
            "https://chat.mistral.ai/*",
            "https://kimi.moonshot.cn/*",
            "https://www.kimi.com/*",
            "https://kimi.com/*",
          ],
          js: [...LEGACY_SITE_SCRIPT_COMMON, "content_scripts/generic.js"],
          run_at: "document_start",
        },

        // --- Universal detector for uncovered pages ---
        {
          matches: ["<all_urls>"],
          js: ["content_scripts/detector.js"],
          run_at: "document_idle",
          exclude_matches: [...COVERED_SITES],
        },
      ],
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
