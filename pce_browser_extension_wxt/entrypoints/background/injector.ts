/**
 * Dynamic / proactive content-script injection.
 *
 * Post-P2.5 Phase 3b complete: every covered site has a TS
 * ``defineContentScript`` entrypoint that auto-registers via the
 * manifest. The dynamic-injection path is only exercised when:
 *
 *   1. ``detector.content.ts`` fires ``PCE_AI_PAGE_DETECTED`` on an
 *      AI page whose domain isn't in the manifest match list (e.g.
 *      a new AI site the detector's heuristics caught).
 *   2. ``proactiveInjectFallback`` runs for covered sites where the
 *      static content script somehow didn't attach (SPA race, CDP /
 *      automation, tab came back from discard, etc.).
 *
 * For both cases the injected payload is the same: the TS universal
 * extractor emitted by WXT at ``universal-extractor.js``. On covered
 * sites, that universal extractor bails out instantly (its
 * ``SITE_EXTRACTOR_FLAGS`` guard checks ``__PCE_*_ACTIVE``); on
 * unknown AI sites, it drives the shared capture runtime.
 *
 * The per-domain script tables + alias matching that lived here
 * pre-Phase-4 (``DOMAIN_CONTENT_SCRIPTS`` + ``DOMAIN_EXTRACTOR_FLAGS``
 * + ``UNIVERSAL_FALLBACK_SCRIPTS``) are GONE — WXT now owns that
 * mapping via the manifest.
 */

// ---------------------------------------------------------------------------
// Injection payload — constant (Phase 4 cleanup)
// ---------------------------------------------------------------------------

/**
 * Path to the dynamically-injected universal extractor. Emitted at
 * the output root by the unlisted-script entrypoint
 * (``entrypoints/universal-extractor.ts``).
 */
export const UNIVERSAL_EXTRACTOR_TS_PATH = "universal-extractor.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PipelineState {
  /** Bridge content script active (forwards network captures). */
  bridge: boolean;
  /**
   * Any extractor loaded — a site-specific `__PCE_*_ACTIVE` flag OR
   * the universal-extractor's `__PCE_UNIVERSAL_EXTRACTOR_LOADED`.
   */
  extractor: boolean;
  /** Behavior tracker active (writes `window.__PCE_BEHAVIOR`). */
  behavior: boolean;
}

export interface InjectionResult {
  injected: boolean;
  domain?: string;
  confidence?: string;
  reason?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Returns the list of files the service worker should inject via
 * `chrome.scripting.executeScript`. Post-Phase-4 this is always the
 * TS universal extractor — the per-domain manifest handles matching
 * for covered sites at page-load time.
 */
export function getDynamicScriptsForDomain(_domain: string): string[] {
  return [UNIVERSAL_EXTRACTOR_TS_PATH];
}

/**
 * URL-keyed equivalent of `getDynamicScriptsForDomain`. Returns null
 * for non-HTTP(S) URLs (e.g. ``about:blank``, ``chrome://settings``)
 * so the service worker doesn't try to inject into privileged
 * surfaces.
 *
 * The `opts.includeDetector` flag is kept for legacy ABI compatibility
 * but is now a no-op: the detector is its own `defineContentScript`
 * entrypoint and is never part of a dynamic-injection payload.
 */
export function getScriptsForUrl(
  url: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _opts: { includeDetector?: boolean } = {},
): string[] | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    if (!parsed.hostname) return null;
  } catch {
    return null;
  }
  return [UNIVERSAL_EXTRACTOR_TS_PATH];
}

// ---------------------------------------------------------------------------
// Runtime probes (require chrome.scripting)
// ---------------------------------------------------------------------------

/**
 * Ask the tab whether the PCE pipeline is already attached. Used to
 * short-circuit re-injection when the static content scripts already
 * fired.
 */
export async function getTabPipelineState(
  tabId: number,
): Promise<PipelineState> {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const w = window as any;
        const siteFlagSet =
          Boolean(w.__PCE_CHATGPT_ACTIVE) ||
          Boolean(w.__PCE_CLAUDE_ACTIVE) ||
          Boolean(w.__PCE_DEEPSEEK_ACTIVE) ||
          Boolean(w.__PCE_GEMINI_ACTIVE) ||
          Boolean(w.__PCE_GOOGLE_AI_STUDIO_ACTIVE) ||
          Boolean(w.__PCE_MANUS_ACTIVE) ||
          Boolean(w.__PCE_PERPLEXITY_ACTIVE) ||
          Boolean(w.__PCE_GROK_ACTIVE) ||
          Boolean(w.__PCE_POE_ACTIVE) ||
          Boolean(w.__PCE_COPILOT_ACTIVE) ||
          Boolean(w.__PCE_HUGGINGFACE_ACTIVE) ||
          Boolean(w.__PCE_ZHIPU_ACTIVE) ||
          Boolean(w.__PCE_GENERIC_ACTIVE) ||
          Boolean(w.__PCE_UNIVERSAL_EXTRACTOR_LOADED);
        return {
          bridge: Boolean(w.__PCE_BRIDGE_ACTIVE || w.__PCE_BRIDGE_LOADED),
          behavior: Boolean(w.__PCE_BEHAVIOR_TRACKER_ACTIVE),
          extractor: siteFlagSet,
        };
      },
    });
    return (
      (results[0]?.result as PipelineState | undefined) ?? {
        bridge: false,
        behavior: false,
        extractor: false,
      }
    );
  } catch {
    return { bridge: false, behavior: false, extractor: false };
  }
}

export async function tabHasExpectedPipeline(
  tabId: number,
): Promise<boolean> {
  const state = await getTabPipelineState(tabId);
  return Boolean(state.bridge && state.behavior && state.extractor);
}

// ---------------------------------------------------------------------------
// Public injection entry points
// ---------------------------------------------------------------------------

export interface InjectionContext {
  /**
   * Called to short-circuit work. Caller supplies both `enabled` and
   * blacklist predicates so this module stays decoupled from the
   * state module in `background.ts`.
   */
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

  if (ctx.injectedTabs.has(tabId) && (await tabHasExpectedPipeline(tabId))) {
    console.debug(`[PCE] Tab ${tabId} already injected, skipping`);
    return { injected: false, reason: "already_injected" };
  }

  if (ctx.isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping injection for ${domain}`);
    return { injected: false, reason: "blacklisted" };
  }

  if (await tabHasExpectedPipeline(tabId)) {
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
    console.log(
      `[PCE] Successfully injected capture scripts into tab ${tabId} (${domain})`,
    );
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

  if (await tabHasExpectedPipeline(tabId)) {
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
    console.debug(
      `[PCE] Proactive injection skipped: ${(err as Error).message}`,
    );
  }
}
