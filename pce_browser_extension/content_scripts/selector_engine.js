/**
 * PCE – Resilient Selector Engine
 *
 * Reads declarative site configs (from site_configs.js) and provides a
 * fault-tolerant DOM querying layer.  Each query tries multiple candidate
 * selectors in priority order, caches the last successful selector for
 * performance, and automatically invalidates the cache when it stops
 * matching (site redesign detected).
 *
 * Usage from site scripts:
 *   const engine = window.__PCE_SELECTOR_ENGINE;
 *   const container = engine.queryContainer();          // first matching container
 *   const userEls  = engine.queryUserMessages();        // all user message elements
 *   const assistEls = engine.queryAssistantMessages();  // all assistant elements
 *   const isStream = engine.isStreaming();               // streaming detection
 *   const role     = engine.detectRole(element);         // role from element
 *   const config   = engine.getConfig();                 // raw config object
 *
 * Depends on: site_configs.js (must be loaded first)
 */

(() => {
  "use strict";

  if (window.__PCE_SELECTOR_ENGINE) return;

  const TAG = "[PCE:engine]";

  // -----------------------------------------------------------------------
  // Selector cache — remembers which selector index worked last
  // -----------------------------------------------------------------------
  const _cache = {};       // key → { selectorIndex, hitCount, missCount }
  const CACHE_MISS_THRESHOLD = 3; // invalidate after N consecutive misses

  function _getCacheKey(category) {
    return `${location.hostname}:${category}`;
  }

  function _queryCandidates(candidates, mode, cacheKey) {
    if (!candidates || candidates.length === 0) return mode === "one" ? null : [];

    const key = cacheKey || "default";
    const cached = _cache[key];

    // Try cached selector first (fast path)
    if (cached && cached.selectorIndex < candidates.length) {
      const sel = candidates[cached.selectorIndex];
      try {
        if (mode === "one") {
          const el = document.querySelector(sel);
          if (el) {
            cached.hitCount++;
            cached.missCount = 0;
            return el;
          }
        } else {
          const els = document.querySelectorAll(sel);
          if (els.length > 0) {
            cached.hitCount++;
            cached.missCount = 0;
            return Array.from(els);
          }
        }
      } catch { /* invalid selector, fall through */ }

      // Cache miss
      cached.missCount++;
      if (cached.missCount >= CACHE_MISS_THRESHOLD) {
        // Invalidate — site may have redesigned
        delete _cache[key];
        console.debug(TAG, `Cache invalidated for ${key} (${cached.missCount} misses)`);
      }
    }

    // Scan all candidates (slow path)
    for (let i = 0; i < candidates.length; i++) {
      const sel = candidates[i];
      try {
        if (mode === "one") {
          const el = document.querySelector(sel);
          if (el) {
            _cache[key] = { selectorIndex: i, hitCount: 1, missCount: 0 };
            return el;
          }
        } else {
          const els = document.querySelectorAll(sel);
          if (els.length > 0) {
            _cache[key] = { selectorIndex: i, hitCount: 1, missCount: 0 };
            return Array.from(els);
          }
        }
      } catch {
        // Invalid selector for this site — skip
      }
    }

    return mode === "one" ? null : [];
  }

  // -----------------------------------------------------------------------
  // Config resolution
  // -----------------------------------------------------------------------

  function _getConfig() {
    const configs = window.__PCE_SITE_CONFIGS;
    if (!configs) return null;

    const hostname = location.hostname;

    // Exact match
    if (configs[hostname]) return configs[hostname];

    // Subdomain match (www.chatgpt.com → chatgpt.com)
    for (const domain of Object.keys(configs)) {
      if (hostname.endsWith("." + domain)) return configs[domain];
    }

    return null;
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  function queryContainer() {
    const config = _getConfig();
    if (!config) return document.querySelector("main") || document.body;
    return _queryCandidates(config.container, "one", _getCacheKey("container"))
      || document.querySelector("main")
      || document.body;
  }

  function queryUserMessages() {
    const config = _getConfig();
    if (!config) return [];
    return _queryCandidates(config.userMsg, "all", _getCacheKey("userMsg"));
  }

  function queryAssistantMessages() {
    const config = _getConfig();
    if (!config) return [];
    return _queryCandidates(config.assistantMsg, "all", _getCacheKey("assistantMsg"));
  }

  function queryTurnPairs() {
    const config = _getConfig();
    if (!config) return [];
    return _queryCandidates(config.turnPair, "all", _getCacheKey("turnPair"));
  }

  function queryAllMessages() {
    const config = _getConfig();
    if (!config) return [];

    // Try combined user+assistant selectors
    const combined = [
      ...(config.userMsg || []),
      ...(config.assistantMsg || []),
    ];

    // Build a combined selector string for querySelectorAll
    for (const userSel of (config.userMsg || [])) {
      for (const assistSel of (config.assistantMsg || [])) {
        try {
          const combinedSel = `${userSel}, ${assistSel}`;
          const els = document.querySelectorAll(combinedSel);
          if (els.length >= 2) {
            return Array.from(els);
          }
        } catch { /* skip invalid combo */ }
      }
    }

    // Fallback: try turn pairs
    return queryTurnPairs();
  }

  function isStreaming() {
    const config = _getConfig();
    if (!config) return false;
    const indicators = config.streamingIndicator || [];
    for (const sel of indicators) {
      try {
        if (document.querySelector(sel)) return true;
      } catch { /* skip */ }
    }
    return false;
  }

  function detectRole(el) {
    const config = _getConfig();

    // Strategy 1: check role attributes
    const roleAttrs = config ? (config.roleAttr || []) : [];
    // Always check common attributes
    const allAttrs = [...roleAttrs, "data-role", "data-message-role", "data-message-author-role", "data-testid"];
    for (const attr of allAttrs) {
      const val = el.getAttribute(attr);
      if (!val) continue;
      const lower = val.toLowerCase();
      if (lower.includes("user") || lower.includes("human") || lower.includes("query")) return "user";
      if (lower.includes("assistant") || lower.includes("bot") || lower.includes("model") || lower.includes("ai")) return "assistant";
    }

    // Strategy 2: check class name against keywords
    const className = (el.className || "").toLowerCase();
    const keywords = config ? (config.roleKeywords || {}) : {};
    const userKeywords = keywords.user || ["user", "human", "query"];
    const assistKeywords = keywords.assistant || ["assistant", "bot", "model", "ai", "response", "answer"];

    for (const kw of userKeywords) {
      if (className.includes(kw)) return "user";
    }
    for (const kw of assistKeywords) {
      if (className.includes(kw)) return "assistant";
    }

    // Strategy 3: check parent elements (one level up)
    if (el.parentElement) {
      const parentClass = (el.parentElement.className || "").toLowerCase();
      for (const kw of userKeywords) {
        if (parentClass.includes(kw)) return "user";
      }
      for (const kw of assistKeywords) {
        if (parentClass.includes(kw)) return "assistant";
      }
    }

    return "unknown";
  }

  function getSessionHint() {
    const config = _getConfig();
    if (!config || !config.sessionHintPattern) return null;
    const match = location.pathname.match(config.sessionHintPattern);
    return match ? match[1] : null;
  }

  function getModelName() {
    const config = _getConfig();
    if (!config) return null;
    const el = _queryCandidates(config.modelSelector || [], "one", _getCacheKey("model"));
    return el ? el.innerText.trim() : null;
  }

  function getConfig() {
    return _getConfig();
  }

  function getProvider() {
    const config = _getConfig();
    return config ? config.provider : null;
  }

  function getSourceName() {
    const config = _getConfig();
    return config ? config.sourceName : null;
  }

  // -----------------------------------------------------------------------
  // Diagnostics — useful for debugging selector failures
  // -----------------------------------------------------------------------

  function getDiagnostics() {
    const config = _getConfig();
    const hostname = location.hostname;
    const results = {
      hostname,
      configFound: !!config,
      provider: config?.provider || null,
      cache: {},
      selectorHits: {},
    };

    if (config) {
      for (const category of ["container", "userMsg", "assistantMsg", "turnPair", "streamingIndicator"]) {
        const candidates = config[category] || [];
        const key = _getCacheKey(category);
        results.cache[category] = _cache[key] || null;
        results.selectorHits[category] = [];
        for (const sel of candidates) {
          try {
            const count = document.querySelectorAll(sel).length;
            if (count > 0) {
              results.selectorHits[category].push({ selector: sel, count });
            }
          } catch { /* skip */ }
        }
      }
    }

    return results;
  }

  // -----------------------------------------------------------------------
  // Expose
  // -----------------------------------------------------------------------

  window.__PCE_SELECTOR_ENGINE = {
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
  };

  console.debug(TAG, "Selector engine initialized for", location.hostname);
})();
