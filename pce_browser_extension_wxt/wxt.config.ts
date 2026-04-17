import { defineConfig } from "wxt";

/**
 * WXT config for the PCE browser extension.
 *
 * See README.md for migration status. The short version (post-P2.5 Phase 2):
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
 * - The 13 site-specific content scripts + shared helpers
 *   (`pce_dom_utils.js`, `selector_engine.js`, `site_configs.js`,
 *   `detector.js`, `behavior_tracker.js`, `text_collector.js`) remain
 *   legacy `.js` under `../pce_browser_extension/content_scripts/`. A
 *   prebuild step copies them into `public/content_scripts/` so WXT can
 *   register them without an in-flight rewrite. Phase 3 migrates them
 *   2–3 per PR.
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

// Shared per-site content-script asset list (legacy layout, copied
// verbatim by `scripts/sync-legacy-assets.mjs`). `bridge.js` is
// intentionally absent: it's now a TS entrypoint (`bridge.content.ts`)
// that registers itself on the same matches via `defineContentScript`.
const SITE_SCRIPT_COMMON = [
  "content_scripts/pce_dom_utils.js",
  "content_scripts/site_configs.js",
  "content_scripts/selector_engine.js",
  "content_scripts/detector.js",
  "content_scripts/behavior_tracker.js",
  "content_scripts/text_collector.js",
] as const;

function siteBundle(site: string, extractor: string) {
  return {
    matches: [site],
    js: [...SITE_SCRIPT_COMMON, `content_scripts/${extractor}`],
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
      // Content scripts are defined imperatively instead of via
      // `entrypoints/*.content.ts` because the legacy JS files aren't yet
      // TS-ified. When a site extractor moves to TS, it becomes an entrypoint
      // and disappears from this list.
      content_scripts: [
        siteBundle("https://chatgpt.com/*", "chatgpt.js"),
        siteBundle("https://chat.openai.com/*", "chatgpt.js"),
        siteBundle("https://claude.ai/*", "claude.js"),
        siteBundle("https://gemini.google.com/*", "gemini.js"),
        siteBundle("https://aistudio.google.com/*", "google_ai_studio.js"),
        siteBundle("https://manus.im/*", "manus.js"),
        siteBundle("https://chat.deepseek.com/*", "deepseek.js"),
        siteBundle("https://chat.z.ai/*", "zhipu.js"),
        siteBundle("https://www.perplexity.ai/*", "perplexity.js"),
        siteBundle("https://copilot.microsoft.com/*", "copilot.js"),
        siteBundle("https://poe.com/*", "poe.js"),
        siteBundle("https://huggingface.co/chat/*", "huggingface.js"),
        siteBundle("https://grok.com/*", "grok.js"),
        {
          matches: [
            "https://chat.mistral.ai/*",
            "https://kimi.moonshot.cn/*",
            "https://www.kimi.com/*",
            "https://kimi.com/*",
          ],
          js: [...SITE_SCRIPT_COMMON, "content_scripts/generic.js"],
          run_at: "document_start",
        },
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
