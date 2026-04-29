// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``page.*`` verb handlers.
 *
 *   - ``page.dump_state`` — snapshot DOM excerpt + a few cheap signals.
 *   - ``page.screenshot`` — visible-tab capture as base64 PNG.
 *   - ``page.network_log`` — best-effort tail of the
 *     ``interceptor-network.ts`` ring buffer (when present).
 */

import type {
  PageDumpStateParams,
  PageDumpStateResult,
  PageNetworkLogParams,
  PageNetworkLogResult,
  PageScreenshotParams,
  PageScreenshotResult,
} from "../../utils/probe-protocol";
import { ProbeException, type VerbHandler } from "./probe-rpc-shared";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function requireNumber(
  obj: Record<string, unknown>,
  field: string,
): number {
  const v = obj[field];
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new ProbeException("params_invalid", `field '${field}' must be a finite number`);
  }
  return v;
}

async function execInTab<TArgs extends unknown[], TRet>(
  tabId: number,
  func: (...args: TArgs) => TRet,
  args: TArgs,
): Promise<TRet> {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "ISOLATED",
    func: func as (...a: unknown[]) => unknown,
    args: args as unknown[],
  });
  if (!results || results.length === 0) {
    throw new ProbeException(
      "script_threw",
      "executeScript returned no results",
      { tab_id: tabId },
    );
  }
  const r = results[0] as unknown as { error?: { message?: string }; result: TRet };
  if (r.error) {
    throw new ProbeException(
      "script_threw",
      r.error.message ?? "unknown",
      { tab_id: tabId },
    );
  }
  return r.result;
}

// ---------------------------------------------------------------------------
// Injected snapshot helper (self-contained — copied into the page)
// ---------------------------------------------------------------------------

interface PageSnapshot {
  url: string;
  title: string;
  dom_excerpt: string;
  ready_state: DocumentReadyState;
  location_search: string;
  cookies_count: number;
}

function _injected_dump_state(domMaxChars: number): PageSnapshot {
  const html = document.documentElement?.outerHTML ?? "";
  const excerpt =
    html.length > domMaxChars ? html.slice(0, domMaxChars) + "..." : html;
  const cookies = (document.cookie ?? "").split(";").filter((c) => c.trim().length > 0);
  return {
    url: location.href,
    title: document.title,
    dom_excerpt: excerpt,
    ready_state: document.readyState,
    location_search: location.search,
    cookies_count: cookies.length,
  };
}

interface NetworkLogEntry {
  url: string;
  method: string;
  status: number | null;
  t: number;
}

interface NetworkLogReturn {
  requests: NetworkLogEntry[];
  interceptor_active: boolean;
}

function _injected_network_log(lastN: number): NetworkLogReturn {
  // ``interceptor-network.ts`` (when active in this tab) parks a
  // ring-buffer on ``window.__PCE_NET_LOG__``. If the global is
  // absent the interceptor isn't running on this tab — return an
  // empty list rather than throwing, so agents can detect the
  // condition via ``interceptor_active`` rather than via an error.
  const w = window as unknown as {
    __PCE_NET_LOG__?: NetworkLogEntry[];
  };
  const all = Array.isArray(w.__PCE_NET_LOG__) ? w.__PCE_NET_LOG__ : null;
  if (all === null) {
    return { requests: [], interceptor_active: false };
  }
  const tail = all.slice(Math.max(0, all.length - lastN));
  return { requests: tail, interceptor_active: true };
}

// ---------------------------------------------------------------------------
// Verb implementations
// ---------------------------------------------------------------------------

async function pageDumpState(rawParams: unknown): Promise<PageDumpStateResult> {
  const p = rawParams as Partial<PageDumpStateParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const domMaxChars =
    typeof p.dom_max_chars === "number" && p.dom_max_chars > 0
      ? p.dom_max_chars
      : 8_000;
  return execInTab<[number], PageDumpStateResult>(
    tabId,
    _injected_dump_state,
    [domMaxChars],
  );
}

async function pageScreenshot(
  rawParams: unknown,
): Promise<PageScreenshotResult> {
  const p = rawParams as Partial<PageScreenshotParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const format = p.format === "jpeg" ? "jpeg" : "png";

  // captureVisibleTab requires the tab to be active in its window.
  const tab = await chrome.tabs.get(tabId);
  if (!tab.windowId) {
    throw new ProbeException(
      "tab_not_found",
      `tab ${tabId} has no windowId`,
      { tab_id: tabId },
    );
  }
  if (!tab.active) {
    await chrome.tabs.update(tabId, { active: true });
    // small wait so the visible tab is actually rendered
    await new Promise((r) => setTimeout(r, 200));
  }

  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format,
    quality: 80,
  });
  if (!dataUrl || !dataUrl.startsWith("data:")) {
    throw new ProbeException(
      "extension_internal",
      "captureVisibleTab returned empty data URL",
      { tab_id: tabId },
    );
  }
  // strip the "data:image/png;base64," prefix
  const commaIdx = dataUrl.indexOf(",");
  const b64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl;

  // Width/height are not exposed by the API; agents that need them
  // should decode the base64. Returning 0/0 keeps the type stable.
  return { image_b64: b64, width: 0, height: 0 };
}

async function pageNetworkLog(
  rawParams: unknown,
): Promise<PageNetworkLogResult> {
  const p = rawParams as Partial<PageNetworkLogParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const lastN = typeof p.last_n === "number" && p.last_n > 0 ? p.last_n : 50;
  const out = await execInTab<[number], NetworkLogReturn>(
    tabId,
    _injected_network_log,
    [lastN],
  );
  return out;
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const pageVerbs: Record<string, VerbHandler> = {
  "page.dump_state": pageDumpState as VerbHandler,
  "page.screenshot": pageScreenshot as VerbHandler,
  "page.network_log": pageNetworkLog as VerbHandler,
};
