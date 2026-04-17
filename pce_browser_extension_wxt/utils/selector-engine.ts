/**
 * PCE — Resilient Selector Engine.
 *
 * TypeScript port of ``content_scripts/selector_engine.js`` (P2.5 Phase 3a).
 *
 * Reads declarative site configs from ``utils/site-configs.ts`` and
 * provides a fault-tolerant DOM-querying layer. Each query tries
 * multiple candidate selectors in priority order, caches the last
 * successful selector for performance, and automatically invalidates
 * the cache after ``CACHE_MISS_THRESHOLD`` consecutive misses (site
 * redesign detected).
 *
 * Design: the engine is instantiated with injectable ``window`` and
 * ``document`` handles so it can be unit-tested against ``happy-dom``
 * / ``jsdom`` without touching the real browser globals. The legacy JS
 * hard-coded ``window.location`` / ``document`` lookups; the TS port
 * keeps identical runtime behaviour when run in the browser (default
 * args resolve to the real globals) but also supports
 * ``createSelectorEngine({ window, document })`` for tests.
 */

import { resolveSiteConfig, type PceSiteConfig } from "./site-configs";

export type QueryMode = "one" | "all";

interface CacheEntry {
  selectorIndex: number;
  hitCount: number;
  missCount: number;
}

export interface SelectorDiagnostics {
  hostname: string;
  configFound: boolean;
  provider: string | null;
  cache: Record<string, CacheEntry | null>;
  selectorHits: Record<string, Array<{ selector: string; count: number }>>;
}

export interface SelectorEngine {
  queryContainer(): Element;
  queryUserMessages(): Element[];
  queryAssistantMessages(): Element[];
  queryTurnPairs(): Element[];
  queryAllMessages(): Element[];
  isStreaming(): boolean;
  detectRole(el: Element): "user" | "assistant" | "unknown";
  getSessionHint(): string | null;
  getModelName(): string | null;
  getConfig(): PceSiteConfig | null;
  getProvider(): string | null;
  getSourceName(): string | null;
  getDiagnostics(): SelectorDiagnostics;
  /** Clear all cached selector indices. Exposed for tests + debugging. */
  resetCache(): void;
}

export interface SelectorEngineOptions {
  /** Window handle (defaults to ``globalThis.window``). */
  win?: Window & typeof globalThis;
  /** Document handle (defaults to ``win.document``). */
  doc?: Document;
  /** Hostname override (defaults to ``win.location.hostname``). */
  hostname?: string;
  /** Pathname override (defaults to ``win.location.pathname``). */
  pathname?: string;
  /** Invalidate cache after this many consecutive misses. Default 3. */
  cacheMissThreshold?: number;
  /** Optional logger (defaults to ``console.debug``). */
  logger?: (...args: unknown[]) => void;
}

/**
 * Factory function that returns a fresh ``SelectorEngine`` instance.
 *
 * Separated from the module's top-level code so tests can create
 * multiple independent engines with different fixtures.
 */
export function createSelectorEngine(
  options: SelectorEngineOptions = {},
): SelectorEngine {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const win: any =
    options.win ??
    (typeof window !== "undefined"
      ? (window as unknown as Window & typeof globalThis)
      : undefined);
  const doc: Document | undefined =
    options.doc ?? (win ? win.document : undefined);
  if (!doc) {
    throw new Error("[PCE:engine] no document available");
  }

  const getHostname = (): string =>
    options.hostname ??
    (win && win.location ? win.location.hostname : "") ??
    "";
  const getPathname = (): string =>
    options.pathname ??
    (win && win.location ? win.location.pathname : "") ??
    "";

  const TAG = "[PCE:engine]";
  const CACHE_MISS_THRESHOLD = options.cacheMissThreshold ?? 3;
  const log = options.logger ?? console.debug.bind(console);

  const cache: Record<string, CacheEntry> = {};

  function cacheKey(category: string): string {
    return `${getHostname()}:${category}`;
  }

  function queryCandidates(
    candidates: string[] | undefined,
    mode: "one",
    cacheKeyStr?: string,
  ): Element | null;
  function queryCandidates(
    candidates: string[] | undefined,
    mode: "all",
    cacheKeyStr?: string,
  ): Element[];
  function queryCandidates(
    candidates: string[] | undefined,
    mode: QueryMode,
    cacheKeyStr?: string,
  ): Element | Element[] | null {
    if (!candidates || candidates.length === 0) {
      return mode === "one" ? null : [];
    }

    const key = cacheKeyStr || "default";
    const cached = cache[key];

    // Try cached selector first (fast path)
    if (cached && cached.selectorIndex < candidates.length) {
      const sel = candidates[cached.selectorIndex];
      try {
        if (mode === "one") {
          const el = doc!.querySelector(sel);
          if (el) {
            cached.hitCount++;
            cached.missCount = 0;
            return el;
          }
        } else {
          const els = doc!.querySelectorAll(sel);
          if (els.length > 0) {
            cached.hitCount++;
            cached.missCount = 0;
            return Array.from(els);
          }
        }
      } catch {
        /* invalid selector, fall through */
      }

      // Cache miss
      cached.missCount++;
      if (cached.missCount >= CACHE_MISS_THRESHOLD) {
        delete cache[key];
        log(TAG, `Cache invalidated for ${key} (${cached.missCount} misses)`);
      }
    }

    // Scan all candidates (slow path)
    for (let i = 0; i < candidates.length; i++) {
      const sel = candidates[i];
      try {
        if (mode === "one") {
          const el = doc!.querySelector(sel);
          if (el) {
            cache[key] = { selectorIndex: i, hitCount: 1, missCount: 0 };
            return el;
          }
        } else {
          const els = doc!.querySelectorAll(sel);
          if (els.length > 0) {
            cache[key] = { selectorIndex: i, hitCount: 1, missCount: 0 };
            return Array.from(els);
          }
        }
      } catch {
        // Invalid selector for this site — skip
      }
    }

    return mode === "one" ? null : [];
  }

  function getConfig(): PceSiteConfig | null {
    return resolveSiteConfig(getHostname());
  }

  function queryContainer(): Element {
    const config = getConfig();
    if (!config) {
      return doc!.querySelector("main") || doc!.body;
    }
    return (
      queryCandidates(config.container, "one", cacheKey("container")) ||
      doc!.querySelector("main") ||
      doc!.body
    );
  }

  function queryUserMessages(): Element[] {
    const config = getConfig();
    if (!config) return [];
    return queryCandidates(config.userMsg, "all", cacheKey("userMsg"));
  }

  function queryAssistantMessages(): Element[] {
    const config = getConfig();
    if (!config) return [];
    return queryCandidates(
      config.assistantMsg,
      "all",
      cacheKey("assistantMsg"),
    );
  }

  function queryTurnPairs(): Element[] {
    const config = getConfig();
    if (!config) return [];
    return queryCandidates(config.turnPair, "all", cacheKey("turnPair"));
  }

  function queryAllMessages(): Element[] {
    const config = getConfig();
    if (!config) return [];

    // Build a combined selector string for querySelectorAll
    for (const userSel of config.userMsg || []) {
      for (const assistSel of config.assistantMsg || []) {
        try {
          const combinedSel = `${userSel}, ${assistSel}`;
          const els = doc!.querySelectorAll(combinedSel);
          if (els.length >= 2) {
            return Array.from(els);
          }
        } catch {
          /* skip invalid combo */
        }
      }
    }

    // Fallback: try turn pairs
    return queryTurnPairs();
  }

  function isStreaming(): boolean {
    const config = getConfig();
    if (!config) return false;
    const indicators = config.streamingIndicator || [];
    for (const sel of indicators) {
      try {
        if (doc!.querySelector(sel)) return true;
      } catch {
        /* skip */
      }
    }
    return false;
  }

  function detectRole(el: Element): "user" | "assistant" | "unknown" {
    const config = getConfig();

    // Strategy 1: check role attributes
    const roleAttrs: string[] = config ? config.roleAttr || [] : [];
    const allAttrs = [
      ...roleAttrs,
      "data-role",
      "data-message-role",
      "data-message-author-role",
      "data-testid",
    ];
    for (const attr of allAttrs) {
      const val = el.getAttribute(attr);
      if (!val) continue;
      const lower = val.toLowerCase();
      if (
        lower.includes("user") ||
        lower.includes("human") ||
        lower.includes("query")
      ) {
        return "user";
      }
      if (
        lower.includes("assistant") ||
        lower.includes("bot") ||
        lower.includes("model") ||
        lower.includes("ai")
      ) {
        return "assistant";
      }
    }

    // Strategy 2: check class name against keywords
    const className = (el.className || "").toString().toLowerCase();
    const keywords = config ? config.roleKeywords || ({} as PceSiteConfig["roleKeywords"]) : ({} as PceSiteConfig["roleKeywords"]);
    const userKeywords = keywords.user || ["user", "human", "query"];
    const assistKeywords = keywords.assistant || [
      "assistant",
      "bot",
      "model",
      "ai",
      "response",
      "answer",
    ];

    for (const kw of userKeywords) {
      if (className.includes(kw)) return "user";
    }
    for (const kw of assistKeywords) {
      if (className.includes(kw)) return "assistant";
    }

    // Strategy 3: check parent elements (one level up)
    if (el.parentElement) {
      const parentClass = (el.parentElement.className || "")
        .toString()
        .toLowerCase();
      for (const kw of userKeywords) {
        if (parentClass.includes(kw)) return "user";
      }
      for (const kw of assistKeywords) {
        if (parentClass.includes(kw)) return "assistant";
      }
    }

    return "unknown";
  }

  function getSessionHint(): string | null {
    const config = getConfig();
    if (!config || !config.sessionHintPattern) return null;
    const match = getPathname().match(config.sessionHintPattern);
    return match ? match[1] : null;
  }

  function getModelName(): string | null {
    const config = getConfig();
    if (!config) return null;
    const el = queryCandidates(
      config.modelSelector || [],
      "one",
      cacheKey("model"),
    );
    if (!el) return null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const innerText: string | undefined = (el as any).innerText;
    if (typeof innerText !== "string") return null;
    const trimmed = innerText.trim();
    return trimmed.length > 0 ? trimmed : null;
  }

  function getProvider(): string | null {
    const config = getConfig();
    return config ? config.provider : null;
  }

  function getSourceName(): string | null {
    const config = getConfig();
    return config ? config.sourceName : null;
  }

  function getDiagnostics(): SelectorDiagnostics {
    const config = getConfig();
    const hostname = getHostname();
    const results: SelectorDiagnostics = {
      hostname,
      configFound: !!config,
      provider: config?.provider || null,
      cache: {},
      selectorHits: {},
    };

    if (config) {
      const categories = [
        "container",
        "userMsg",
        "assistantMsg",
        "turnPair",
        "streamingIndicator",
      ] as const;
      for (const category of categories) {
        const candidates = (config[category] as string[]) || [];
        const key = cacheKey(category);
        results.cache[category] = cache[key] || null;
        results.selectorHits[category] = [];
        for (const sel of candidates) {
          try {
            const count = doc!.querySelectorAll(sel).length;
            if (count > 0) {
              results.selectorHits[category].push({ selector: sel, count });
            }
          } catch {
            /* skip */
          }
        }
      }
    }

    return results;
  }

  function resetCache(): void {
    for (const k of Object.keys(cache)) delete cache[k];
  }

  return {
    queryContainer,
    queryUserMessages,
    queryAssistantMessages,
    queryTurnPairs,
    queryAllMessages,
    isStreaming,
    detectRole,
    getSessionHint,
    getModelName,
    getConfig,
    getProvider,
    getSourceName,
    getDiagnostics,
    resetCache,
  };
}
