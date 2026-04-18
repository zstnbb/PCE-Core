/**
 * Dynamic / proactive content-script injection.
 *
 * Mirrors the per-domain script tables from the original service worker.
 * Two injection flavours are supported:
 *
 * - **Dynamic injection** — driven by ``detector.js`` messaging
 *   ``PCE_AI_PAGE_DETECTED`` from a page that wasn't covered by static
 *   content_scripts. We inject the full per-domain pipeline.
 * - **Proactive fallback** — fires on ``chrome.tabs.onUpdated`` after a
 *   brief delay. Catches automation / CDP scenarios where static
 *   content_scripts don't fire.
 *
 * This module is deliberately self-contained: ``background.ts`` calls
 * ``handleDynamicInjection`` / ``proactiveInjectFallback`` and passes in
 * anything domain-specific (blacklist, isEnabled) explicitly so we don't
 * smuggle state between files.
 */

// ---------------------------------------------------------------------------
// Domain → content-script tables
// ---------------------------------------------------------------------------

const COMMON_PIPELINE = [
  "pce_dom_utils.js",
  "site_configs.js",
  "selector_engine.js",
  "detector.js",
  "behavior_tracker.js",
  "bridge.js",
] as const;

export const DOMAIN_CONTENT_SCRIPTS: Readonly<Record<string, readonly string[]>> = {
  "chatgpt.com":           [...COMMON_PIPELINE, "chatgpt.js"],
  "chat.openai.com":       [...COMMON_PIPELINE, "chatgpt.js"],
  "claude.ai":             [...COMMON_PIPELINE, "claude.js"],
  "gemini.google.com":     [...COMMON_PIPELINE, "gemini.js"],
  "aistudio.google.com":   [...COMMON_PIPELINE, "google_ai_studio.js"],
  "manus.im":              [...COMMON_PIPELINE, "manus.js"],
  "chat.deepseek.com":     [...COMMON_PIPELINE, "deepseek.js"],
  "chat.z.ai":             [...COMMON_PIPELINE, "zhipu.js"],
  "www.perplexity.ai":     [...COMMON_PIPELINE, "perplexity.js"],
  "copilot.microsoft.com": [...COMMON_PIPELINE, "copilot.js"],
  "poe.com":               [...COMMON_PIPELINE, "poe.js"],
  "huggingface.co":        [...COMMON_PIPELINE, "huggingface.js"],
  "grok.com":              [...COMMON_PIPELINE, "grok.js"],
  "chat.mistral.ai":       [...COMMON_PIPELINE, "generic.js"],
  "kimi.moonshot.cn":      [...COMMON_PIPELINE, "generic.js"],
  "www.kimi.com":          [...COMMON_PIPELINE, "generic.js"],
  "kimi.com":              [...COMMON_PIPELINE, "generic.js"],
  "chatglm.cn":            [...COMMON_PIPELINE, "generic.js"],
  "chat.zhipuai.cn":       [...COMMON_PIPELINE, "generic.js"],
} as const;

export const DOMAIN_EXTRACTOR_FLAGS: Readonly<Record<string, string>> = {
  "chatgpt.com": "__PCE_CHATGPT_ACTIVE",
  "chat.openai.com": "__PCE_CHATGPT_ACTIVE",
  "claude.ai": "__PCE_CLAUDE_ACTIVE",
  "gemini.google.com": "__PCE_GEMINI_ACTIVE",
  "aistudio.google.com": "__PCE_GOOGLE_AI_STUDIO_ACTIVE",
  "manus.im": "__PCE_MANUS_ACTIVE",
  "chat.deepseek.com": "__PCE_DEEPSEEK_ACTIVE",
  "chat.z.ai": "__PCE_ZHIPU_ACTIVE",
  "www.perplexity.ai": "__PCE_PERPLEXITY_ACTIVE",
  "copilot.microsoft.com": "__PCE_COPILOT_ACTIVE",
  "poe.com": "__PCE_POE_ACTIVE",
  "huggingface.co": "__PCE_HUGGINGFACE_ACTIVE",
  "grok.com": "__PCE_GROK_ACTIVE",
  "chat.mistral.ai": "__PCE_GENERIC_ACTIVE",
  "kimi.moonshot.cn": "__PCE_GENERIC_ACTIVE",
  "www.kimi.com": "__PCE_GENERIC_ACTIVE",
  "kimi.com": "__PCE_GENERIC_ACTIVE",
  "chatglm.cn": "__PCE_GENERIC_ACTIVE",
  "chat.zhipuai.cn": "__PCE_GENERIC_ACTIVE",
} as const;

// Legacy-JS fallback scripts (pre-P2.5.3b5 path names under
// `content_scripts/`). Used by `toContentScriptPaths` to build the
// `chrome.scripting.executeScript` file list for sites that still
// have legacy JS entries in `DOMAIN_CONTENT_SCRIPTS`.
const UNIVERSAL_FALLBACK_SCRIPTS = [
  "pce_dom_utils.js",
  "site_configs.js",
  "selector_engine.js",
  "behavior_tracker.js",
  "bridge.js",
  "universal_extractor.js",
] as const;

// TS-era fallback: the service worker injects
// `universal-extractor.js` (emitted by the WXT unlisted-script
// entrypoint `entrypoints/universal-extractor.ts`) at the output
// root, not under `content_scripts/`. Used by
// `getDynamicScriptsForDomain` when we've already run detector.js
// (detector is the thing that triggered the dynamic-injection path).
export const UNIVERSAL_EXTRACTOR_TS_PATH = "universal-extractor.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DomainMatch {
  domain: string;
  scripts: readonly string[];
}

export interface PipelineState {
  bridge: boolean;
  domUtils: boolean;
  behavior: boolean;
  extractor: boolean;
}

export interface InjectionResult {
  injected: boolean;
  domain?: string;
  confidence?: string;
  reason?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Pure helpers (easy to unit-test once a Vitest harness lands)
// ---------------------------------------------------------------------------

export function matchDomainConfig(hostname: string | null | undefined): DomainMatch | null {
  if (!hostname) return null;
  if (DOMAIN_CONTENT_SCRIPTS[hostname]) {
    return { domain: hostname, scripts: DOMAIN_CONTENT_SCRIPTS[hostname] };
  }
  for (const [domain, scripts] of Object.entries(DOMAIN_CONTENT_SCRIPTS)) {
    if (hostname.endsWith("." + domain)) {
      return { domain, scripts };
    }
  }
  return null;
}

export function toContentScriptPaths(files: readonly string[]): string[] {
  return files.map((file) => "content_scripts/" + file);
}

export function getExtractorFlagForDomain(domain: string | null | undefined): string | null {
  if (!domain) return null;
  if (DOMAIN_EXTRACTOR_FLAGS[domain]) return DOMAIN_EXTRACTOR_FLAGS[domain];
  for (const [knownDomain, flag] of Object.entries(DOMAIN_EXTRACTOR_FLAGS)) {
    if (domain.endsWith("." + knownDomain)) return flag;
  }
  return null;
}

export function getDynamicScriptsForDomain(domain: string): string[] {
  const match = matchDomainConfig(domain);
  if (match) {
    // Exclude detector.js when dynamically injecting — detector.js is
    // what triggered the injection, so it's already running.
    return toContentScriptPaths(match.scripts.filter((file) => file !== "detector.js"));
  }
  return toContentScriptPaths(UNIVERSAL_FALLBACK_SCRIPTS);
}

export function getScriptsForUrl(
  url: string,
  opts: { includeDetector?: boolean } = {},
): string[] | null {
  const { includeDetector = true } = opts;
  let hostname: string;
  try {
    hostname = new URL(url).hostname;
  } catch {
    return null;
  }
  const match = matchDomainConfig(hostname);
  if (match) {
    const files = includeDetector
      ? match.scripts
      : match.scripts.filter((file) => file !== "detector.js");
    return toContentScriptPaths(files);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Runtime probes (require chrome.scripting)
// ---------------------------------------------------------------------------

export async function getTabPipelineState(
  tabId: number,
  domain: string | null,
): Promise<PipelineState> {
  const extractorFlag = getExtractorFlagForDomain(domain);
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      args: [extractorFlag],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      func: (expectedExtractorFlag: string | null) => ({
        bridge: Boolean(
          (window as any).__PCE_BRIDGE_ACTIVE ||
            (window as any).__PCE_BRIDGE_LOADED,
        ),
        domUtils: Boolean((window as any).__PCE_EXTRACT),
        behavior: Boolean((window as any).__PCE_BEHAVIOR_TRACKER_ACTIVE),
        extractor: expectedExtractorFlag
          ? Boolean((window as any)[expectedExtractorFlag])
          : Boolean((window as any).__PCE_UNIVERSAL_EXTRACTOR_LOADED),
      }),
    });
    return (
      (results[0]?.result as PipelineState | undefined) ?? {
        bridge: false,
        domUtils: false,
        behavior: false,
        extractor: false,
      }
    );
  } catch {
    return { bridge: false, domUtils: false, behavior: false, extractor: false };
  }
}

export async function tabHasExpectedPipeline(
  tabId: number,
  domain: string | null,
): Promise<boolean> {
  const state = await getTabPipelineState(tabId, domain);
  return Boolean(state.bridge && state.domUtils && state.behavior && state.extractor);
}

// ---------------------------------------------------------------------------
// Public injection entry points
// ---------------------------------------------------------------------------

export interface InjectionContext {
  /** Used to gate work without the caller having to test both `enabled`
   * and the blacklist. Caller supplies the two functions so we can stay
   * decoupled from the `state` module. */
  isEnabled: () => boolean;
  isBlacklisted: (domain: string) => boolean;
  /** Shared set of tabs we've already injected into this session. */
  injectedTabs: Set<number>;
}

export async function handleDynamicInjection(
  ctx: InjectionContext,
  tabId: number,
  payload: { domain?: string; confidence?: string } | undefined,
): Promise<InjectionResult> {
  const domain = payload?.domain || "unknown";

  if (ctx.injectedTabs.has(tabId) && (await tabHasExpectedPipeline(tabId, domain))) {
    console.debug(`[PCE] Tab ${tabId} already injected, skipping`);
    return { injected: false, reason: "already_injected" };
  }

  if (ctx.isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping injection for ${domain}`);
    return { injected: false, reason: "blacklisted" };
  }

  if (await tabHasExpectedPipeline(tabId, domain)) {
    ctx.injectedTabs.add(tabId);
    return { injected: false, reason: "already_loaded" };
  }

  ctx.injectedTabs.add(tabId);
  const confidence = payload?.confidence || "unknown";

  console.log(
    `[PCE] Injecting capture scripts into tab ${tabId} (${domain}, confidence=${confidence})`,
  );

  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: getDynamicScriptsForDomain(domain),
    });
    console.log(`[PCE] Successfully injected capture scripts into tab ${tabId} (${domain})`);
    return { injected: true, domain, confidence };
  } catch (err) {
    ctx.injectedTabs.delete(tabId);
    const msg = (err as Error).message;
    console.error(`[PCE] Injection failed for tab ${tabId}:`, msg);
    return { injected: false, error: msg };
  }
}

export async function proactiveInjectFallback(
  ctx: InjectionContext,
  tabId: number,
  url: string,
): Promise<void> {
  // Brief delay: give static manifest content_scripts a chance to fire
  // first.
  await new Promise<void>((resolve) => setTimeout(resolve, 1_500));

  if (ctx.injectedTabs.has(tabId)) return;

  const scripts = getScriptsForUrl(url);
  if (!scripts) return;

  let domain: string;
  try {
    domain = new URL(url).hostname;
  } catch {
    return;
  }

  if (await tabHasExpectedPipeline(tabId, domain)) {
    ctx.injectedTabs.add(tabId);
    return;
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: scripts,
    });
    ctx.injectedTabs.add(tabId);
    console.log(`[PCE] Proactive fallback injection: ${domain} (tab ${tabId})`);
  } catch (err) {
    console.debug(`[PCE] Proactive injection skipped: ${(err as Error).message}`);
  }
}
