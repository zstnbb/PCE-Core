// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``tab.*`` verb handlers.
 *
 * Thin wrappers over ``chrome.tabs.*``. The contract is:
 *   - validate input shape; throw ProbeException("params_invalid") on bad input.
 *   - translate Chrome runtime errors into typed ProbeException codes.
 *   - never leak Chrome-specific objects in the result; mirror the
 *     ``protocol.ts`` shapes exactly.
 */

import type {
  TabActivateParams,
  TabActivateResult,
  TabCloseParams,
  TabCloseResult,
  TabFindByUrlParams,
  TabFindByUrlResult,
  TabListResult,
  TabNavigateParams,
  TabNavigateResult,
  TabOpenParams,
  TabOpenResult,
  TabSummary,
  TabWaitForLoadParams,
  TabWaitForLoadResult,
} from "../../utils/probe-protocol";
import { ProbeException, type VerbHandler } from "./probe-rpc";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toTabSummary(t: chrome.tabs.Tab): TabSummary {
  return {
    id: t.id ?? -1,
    url: t.url ?? "",
    title: t.title ?? "",
    active: Boolean(t.active),
  };
}

async function getTab(tabId: number): Promise<chrome.tabs.Tab> {
  return new Promise((resolve, reject) => {
    chrome.tabs.get(tabId, (tab) => {
      const err = chrome.runtime.lastError;
      if (err || !tab) {
        reject(
          new ProbeException(
            "tab_not_found",
            `tab ${tabId} not found: ${err?.message ?? "unknown"}`,
            { tab_id: tabId },
          ),
        );
        return;
      }
      resolve(tab);
    });
  });
}

function requireString(
  obj: Record<string, unknown>,
  field: string,
): string {
  const v = obj[field];
  if (typeof v !== "string" || v.length === 0) {
    throw new ProbeException("params_invalid", `field '${field}' must be a non-empty string`);
  }
  return v;
}

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

// ---------------------------------------------------------------------------
// Verb implementations
// ---------------------------------------------------------------------------

async function tabList(): Promise<TabListResult> {
  const tabs = await chrome.tabs.query({});
  return { tabs: tabs.map(toTabSummary) };
}

async function tabOpen(rawParams: unknown): Promise<TabOpenResult> {
  const p = rawParams as Partial<TabOpenParams>;
  const url = requireString(p as Record<string, unknown>, "url");
  const active = p.active !== false; // default true
  const created = await chrome.tabs.create({ url, active });
  if (!created.id) {
    throw new ProbeException(
      "navigation_failed",
      `chrome.tabs.create returned no id (url=${url})`,
    );
  }
  return { tab_id: created.id, url: created.url ?? url };
}

async function tabActivate(rawParams: unknown): Promise<TabActivateResult> {
  const p = rawParams as Partial<TabActivateParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  await getTab(tabId); // throws tab_not_found if missing
  await chrome.tabs.update(tabId, { active: true });
  return { ok: true };
}

async function tabClose(rawParams: unknown): Promise<TabCloseResult> {
  const p = rawParams as Partial<TabCloseParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  await chrome.tabs.remove(tabId);
  return { ok: true };
}

async function tabNavigate(rawParams: unknown): Promise<TabNavigateResult> {
  const p = rawParams as Partial<TabNavigateParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const url = requireString(p as Record<string, unknown>, "url");
  await getTab(tabId);
  const updated = await chrome.tabs.update(tabId, { url });
  return { tab_id: tabId, url: updated?.url ?? url };
}

async function tabWaitForLoad(
  rawParams: unknown,
  signal: AbortSignal,
): Promise<TabWaitForLoadResult> {
  const p = rawParams as Partial<TabWaitForLoadParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const timeoutMs = typeof p.timeout_ms === "number" ? p.timeout_ms : 15_000;

  // Fast-path: already complete.
  const initial = await getTab(tabId);
  if (initial.status === "complete") {
    return {
      tab_id: tabId,
      url: initial.url ?? "",
      title: initial.title ?? "",
    };
  }

  return new Promise<TabWaitForLoadResult>((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new ProbeException(
          "timeout",
          `tab ${tabId} did not finish loading within ${timeoutMs}ms`,
          { tab_id: tabId },
        ),
      );
    }, timeoutMs);

    const onAbort = () => {
      cleanup();
      reject(
        new ProbeException(
          "timeout",
          `tab.wait_for_load aborted by dispatcher (request timeout)`,
          { tab_id: tabId },
        ),
      );
    };

    const listener = (
      changedId: number,
      changeInfo: chrome.tabs.TabChangeInfo,
      tab: chrome.tabs.Tab,
    ) => {
      if (changedId !== tabId) return;
      if (changeInfo.status === "complete") {
        cleanup();
        resolve({
          tab_id: tabId,
          url: tab.url ?? "",
          title: tab.title ?? "",
        });
      }
    };

    function cleanup() {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      try {
        chrome.tabs.onUpdated.removeListener(listener);
      } catch {
        /* ignore */
      }
    }

    signal.addEventListener("abort", onAbort);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function tabFindByUrl(rawParams: unknown): Promise<TabFindByUrlResult> {
  const p = rawParams as Partial<TabFindByUrlParams>;
  const pattern = requireString(p as Record<string, unknown>, "url_pattern");
  let re: RegExp;
  try {
    re = new RegExp(pattern);
  } catch (err) {
    throw new ProbeException(
      "params_invalid",
      `url_pattern is not a valid regex: ${(err as Error).message}`,
    );
  }
  const tabs = await chrome.tabs.query({});
  return {
    tabs: tabs.filter((t) => re.test(t.url ?? "")).map(toTabSummary),
  };
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const tabVerbs: Record<string, VerbHandler> = {
  "tab.list": tabList as VerbHandler,
  "tab.open": tabOpen as VerbHandler,
  "tab.activate": tabActivate as VerbHandler,
  "tab.close": tabClose as VerbHandler,
  "tab.navigate": tabNavigate as VerbHandler,
  "tab.wait_for_load": tabWaitForLoad as VerbHandler,
  "tab.find_by_url": tabFindByUrl as VerbHandler,
};

export const __tabTesting = {
  toTabSummary,
};
