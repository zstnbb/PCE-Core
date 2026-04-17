/**
 * Tests for the pure helpers in `entrypoints/background/injector.ts`.
 *
 * Runtime probes (`getTabPipelineState`, `handleDynamicInjection`,
 * `proactiveInjectFallback`) touch `chrome.scripting` and are deferred
 * to integration tests in a later phase. The functions tested here
 * are pure data lookups — the exact surface that regressions in the
 * DOMAIN_CONTENT_SCRIPTS / DOMAIN_EXTRACTOR_FLAGS tables would show up
 * in immediately.
 */

// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  DOMAIN_CONTENT_SCRIPTS,
  DOMAIN_EXTRACTOR_FLAGS,
  getDynamicScriptsForDomain,
  getExtractorFlagForDomain,
  getScriptsForUrl,
  matchDomainConfig,
  toContentScriptPaths,
} from "../injector";

describe("matchDomainConfig", () => {
  it("returns null for empty / unknown input", () => {
    expect(matchDomainConfig(null)).toBeNull();
    expect(matchDomainConfig(undefined)).toBeNull();
    expect(matchDomainConfig("")).toBeNull();
    expect(matchDomainConfig("example.com")).toBeNull();
  });

  it("resolves direct hits", () => {
    const m = matchDomainConfig("chatgpt.com");
    expect(m).not.toBeNull();
    expect(m!.domain).toBe("chatgpt.com");
    expect(m!.scripts).toContain("chatgpt.js");
  });

  it("resolves subdomain suffix matches", () => {
    const m = matchDomainConfig("foo.claude.ai");
    expect(m).not.toBeNull();
    expect(m!.domain).toBe("claude.ai");
  });

  it("does not match substrings that aren't on a dot boundary", () => {
    expect(matchDomainConfig("notchatgpt.com")).toBeNull();
    expect(matchDomainConfig("fakeclaude.ai")).toBeNull();
  });
});

describe("toContentScriptPaths", () => {
  it("prefixes each file with content_scripts/", () => {
    expect(toContentScriptPaths(["a.js", "b.js"])).toEqual([
      "content_scripts/a.js",
      "content_scripts/b.js",
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(toContentScriptPaths([])).toEqual([]);
  });
});

describe("getExtractorFlagForDomain", () => {
  it("returns null for empty / unknown", () => {
    expect(getExtractorFlagForDomain(null)).toBeNull();
    expect(getExtractorFlagForDomain(undefined)).toBeNull();
    expect(getExtractorFlagForDomain("")).toBeNull();
    expect(getExtractorFlagForDomain("example.com")).toBeNull();
  });

  it("resolves direct matches", () => {
    expect(getExtractorFlagForDomain("chatgpt.com")).toBe(
      "__PCE_CHATGPT_ACTIVE",
    );
    expect(getExtractorFlagForDomain("claude.ai")).toBe("__PCE_CLAUDE_ACTIVE");
    expect(getExtractorFlagForDomain("grok.com")).toBe("__PCE_GROK_ACTIVE");
  });

  it("resolves subdomain suffix matches", () => {
    expect(getExtractorFlagForDomain("www.chatgpt.com")).toBe(
      "__PCE_CHATGPT_ACTIVE",
    );
    expect(getExtractorFlagForDomain("api.claude.ai")).toBe(
      "__PCE_CLAUDE_ACTIVE",
    );
  });
});

describe("getDynamicScriptsForDomain", () => {
  it("returns full pipeline minus detector.js for known domains", () => {
    const paths = getDynamicScriptsForDomain("chatgpt.com");
    expect(paths).not.toContain("content_scripts/detector.js");
    expect(paths).toContain("content_scripts/bridge.js");
    expect(paths).toContain("content_scripts/chatgpt.js");
  });

  it("falls back to universal scripts for unknown domains", () => {
    const paths = getDynamicScriptsForDomain("unknown.example.com");
    expect(paths).toContain("content_scripts/universal_extractor.js");
    expect(paths).not.toContain("content_scripts/detector.js");
  });

  it("returned paths all start with content_scripts/", () => {
    for (const p of getDynamicScriptsForDomain("chatgpt.com")) {
      expect(p.startsWith("content_scripts/")).toBe(true);
    }
  });
});

describe("getScriptsForUrl", () => {
  it("returns null for invalid URLs", () => {
    expect(getScriptsForUrl("not a url")).toBeNull();
  });

  it("returns null for unknown hosts", () => {
    expect(getScriptsForUrl("https://example.com/")).toBeNull();
  });

  it("includes detector.js by default", () => {
    const paths = getScriptsForUrl("https://chatgpt.com/c/abc");
    expect(paths).not.toBeNull();
    expect(paths!).toContain("content_scripts/detector.js");
    expect(paths!).toContain("content_scripts/chatgpt.js");
  });

  it("omits detector.js when opts.includeDetector=false", () => {
    const paths = getScriptsForUrl("https://chatgpt.com/c/abc", {
      includeDetector: false,
    });
    expect(paths).not.toBeNull();
    expect(paths!).not.toContain("content_scripts/detector.js");
  });

  it("handles subdomains via matchDomainConfig", () => {
    const paths = getScriptsForUrl("https://www.chatgpt.com/c/abc");
    expect(paths).not.toBeNull();
    expect(paths!).toContain("content_scripts/chatgpt.js");
  });
});

describe("DOMAIN_CONTENT_SCRIPTS / DOMAIN_EXTRACTOR_FLAGS tables", () => {
  it("every domain in CONTENT_SCRIPTS has a matching flag", () => {
    for (const domain of Object.keys(DOMAIN_CONTENT_SCRIPTS)) {
      expect(DOMAIN_EXTRACTOR_FLAGS[domain]).toBeDefined();
    }
  });

  it("every flag is a valid global-variable-looking string", () => {
    for (const flag of Object.values(DOMAIN_EXTRACTOR_FLAGS)) {
      expect(flag).toMatch(/^__PCE_[A-Z_]+_ACTIVE$/);
    }
  });

  it("ChatGPT aliases share the same scripts + flag", () => {
    expect(DOMAIN_CONTENT_SCRIPTS["chat.openai.com"]).toEqual(
      DOMAIN_CONTENT_SCRIPTS["chatgpt.com"],
    );
    expect(DOMAIN_EXTRACTOR_FLAGS["chat.openai.com"]).toBe(
      DOMAIN_EXTRACTOR_FLAGS["chatgpt.com"],
    );
  });

  it("Kimi aliases all use generic.js + __PCE_GENERIC_ACTIVE", () => {
    for (const host of [
      "kimi.com",
      "www.kimi.com",
      "kimi.moonshot.cn",
    ]) {
      expect(DOMAIN_CONTENT_SCRIPTS[host]).toContain("generic.js");
      expect(DOMAIN_EXTRACTOR_FLAGS[host]).toBe("__PCE_GENERIC_ACTIVE");
    }
  });
});
