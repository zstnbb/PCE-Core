// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``system.*`` verb handlers (ping + version meta).
 */

import {
  PROBE_SCHEMA_VERSION,
  type SystemPingResult,
  type SystemReloadResult,
  type SystemVersionResult,
  type ProbeVerb,
} from "../../utils/probe-protocol";
import { getExtensionVersion, type VerbHandler } from "./probe-rpc-shared";

// Delay between sending the ``system.reload`` ack frame and actually
// calling ``chrome.runtime.reload()``. Long enough for the WebSocket
// send buffer to flush; short enough that the agent doesn't have to
// guess the timing on its side. The agent's ``reload_extension()``
// helper sleeps the same value before attempting reattach.
const SYSTEM_RELOAD_DELAY_MS = 100;

// We re-list known verbs here only for ``system.version`` so the
// returned ``capabilities`` array is self-describing without each
// verb-handler module having to leak its keys back to the dispatcher.
// This list is duplicated, but kept tiny and asserted by a unit test.
const ALL_KNOWN_VERBS: ProbeVerb[] = [
  "system.ping",
  "system.version",
  "system.reload",
  "tab.list",
  "tab.open",
  "tab.activate",
  "tab.close",
  "tab.navigate",
  "tab.wait_for_load",
  "tab.find_by_url",
  "dom.query",
  "dom.wait_for_selector",
  "dom.click",
  "dom.type",
  "dom.press_key",
  "dom.scroll_to",
  "dom.execute_js",
  "page.dump_state",
  "page.screenshot",
  "page.network_log",
  "capture.wait_for_token",
  "capture.recent_events",
  "capture.pipeline_state",
];

export const systemVerbs: Record<string, VerbHandler> = {
  "system.ping": async (): Promise<SystemPingResult> => ({
    pong: true,
    extension_version: getExtensionVersion(),
  }),

  "system.version": async (): Promise<SystemVersionResult> => ({
    schema_version: PROBE_SCHEMA_VERSION,
    extension_version: getExtensionVersion(),
    capabilities: ALL_KNOWN_VERBS,
  }),

  // ``system.reload`` — trigger ``chrome.runtime.reload()`` so the
  // agent can pick up a freshly-built bundle without the user clicking
  // "Reload" in chrome://extensions. We schedule the reload on a short
  // setTimeout so this handler can return cleanly first; the WS frame
  // flushes, the agent gets a clean ack, and ~100 ms later the SW
  // dies and the WS drops. The agent's ``reload_extension()`` helper
  // catches the drop and reattaches.
  "system.reload": async (): Promise<SystemReloadResult> => {
    setTimeout(() => {
      // ``chrome.runtime.reload()`` is the only well-supported way to
      // self-reload an MV3 SW. It re-reads the manifest + bundle from
      // the load path (i.e. ``.output/chrome-mv3`` for a sideload
      // build). No permissions required — it's part of the runtime
      // API surface, available unconditionally.
      try {
        chrome.runtime.reload();
      } catch (err) {
        console.warn(
          `[PCE Probe] system.reload failed to call chrome.runtime.reload(): ${
            (err as Error).message
          }`,
        );
      }
    }, SYSTEM_RELOAD_DELAY_MS);
    return { reloading: true, delay_ms: SYSTEM_RELOAD_DELAY_MS };
  },
};

export const __systemTesting = {
  ALL_KNOWN_VERBS,
};
