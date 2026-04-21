// SPDX-License-Identifier: Apache-2.0
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
 * - **P2.5 Phase 4 complete**: the legacy
 *   `../pce_browser_extension/` directory has been removed; icons +
 *   popup now live directly under `public/` as the canonical source
 *   of truth. There is no longer a sync step — WXT reads `public/`
 *   as-is.
 *
 * Two release flavours are produced via WXT env:
 *   - default sideload build (host_permissions: <all_urls>)
 *   - webstore build (explicit host_permissions list), invoked with
 *     `wxt build --mode webstore`.
 */

const COVERED_SITES = [
  // F1 — dedicated AI chat / coding tools (13 sites).
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
  // F2 — AI features embedded in productivity / collaboration tools
  // (P5.A-8/9 scaffolding; selectors need live-page validation).
  "https://www.notion.so/*",
  "https://notion.so/*",
  "https://m365.cloud.microsoft/*",
  "https://*.cloud.microsoft/*",
  "https://*.officeapps.live.com/*",
  "https://www.figma.com/*",
  "https://figma.com/*",
  "https://mail.google.com/*",
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
      version: "1.0.0",
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
  // In `webstore` mode, strip the `detector` content script from the
  // generated manifest. Its `matches: ["<all_urls>"]` (needed in the
  // sideload build to discover NEW AI sites the user browses to) would
  // otherwise force Chrome to request the "Read and change all your
  // data on all websites" permission at install time — which directly
  // contradicts the Chrome Web Store submission claim (see
  // Docs/store/justification.md §3) that the webstore build limits
  // itself to the explicit COVERED_SITES host list.
  //
  // The webstore build intentionally covers only the 17 pre-audited AI
  // hosts, so the detector's "discover new AI site" capability is out
  // of scope here. detector.js itself still ships in the zip (~10 KB,
  // negligible) but is never activated because it's not registered as
  // a content script.
  hooks: {
    "build:manifestGenerated": (wxt, manifest) => {
      if (wxt.config.mode !== "webstore") return;
      if (!manifest.content_scripts) return;
      manifest.content_scripts = manifest.content_scripts.filter((cs) => {
        const js = cs.js ?? [];
        return !js.includes("content-scripts/detector.js");
      });
    },
  },
});
