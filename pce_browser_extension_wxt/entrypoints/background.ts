/**
 * PCE background entrypoint — WXT version.
 *
 * Migration status (P2 — ADR-006):
 *   ✅ Build chain moved to WXT (this file is the TS entrypoint)
 *   ✅ Manifest generation moved to `wxt.config.ts`
 *   ⏳ Service-worker logic itself is still the battle-tested legacy JS
 *      under `../legacy/service_worker.js`; a file-by-file TS rewrite is
 *      tracked as a P2.5 follow-up.
 *
 * The rationale for the deferral is spelled out in ADR-006 §Guardrails:
 * "迁移过程中不引入任何新功能，保持'结构替换、行为不变'".  Porting 29 KB
 * of stable, e2e-tested capture-routing code in the same PR as the WXT
 * skeleton would violate that guardrail.
 *
 * The PCE message-shape interfaces are already exported from
 * `../utils/pce-messages.ts` so future TS content-script rewrites can
 * strictly type their `chrome.runtime.sendMessage` calls.
 */

import { defineBackground } from "wxt/sandbox";

export default defineBackground({
  type: "module",
  persistent: false,
  main() {
    console.log("[PCE] background entrypoint booted — loading legacy handlers");

    // Load the legacy service worker as a side-effect module. It registers
    // its own `chrome.runtime.onMessage` / `chrome.contextMenus` listeners
    // at module scope, just like it did under the hand-written manifest.
    //
    // A dynamic import is used (rather than a top-level `import "…"`) so
    // HMR picks up changes without restarting the whole WXT dev session.
    import("./legacy/service_worker.js").catch((err) => {
      console.error("[PCE] failed to load legacy service_worker.js:", err);
    });
  },
});
