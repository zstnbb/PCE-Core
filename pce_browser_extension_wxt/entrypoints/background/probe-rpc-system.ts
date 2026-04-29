// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``system.*`` verb handlers (ping + version meta).
 */

import {
  PROBE_SCHEMA_VERSION,
  type SystemPingResult,
  type SystemVersionResult,
  type ProbeVerb,
} from "../../utils/probe-protocol";
import { getExtensionVersion, type VerbHandler } from "./probe-rpc-shared";

// We re-list known verbs here only for ``system.version`` so the
// returned ``capabilities`` array is self-describing without each
// verb-handler module having to leak its keys back to the dispatcher.
// This list is duplicated, but kept tiny and asserted by a unit test.
const ALL_KNOWN_VERBS: ProbeVerb[] = [
  "system.ping",
  "system.version",
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
};

export const __systemTesting = {
  ALL_KNOWN_VERBS,
};
