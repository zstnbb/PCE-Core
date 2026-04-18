/**
 * Tests for `entrypoints/background/injector.ts` pure helpers.
 *
 * Post-P2.5 Phase 4, the injector has shed its per-domain script
 * tables (`DOMAIN_CONTENT_SCRIPTS`, `DOMAIN_EXTRACTOR_FLAGS`,
 * `matchDomainConfig`, `toContentScriptPaths`,
 * `getExtractorFlagForDomain`). WXT's manifest owns the per-site
 * match list, and dynamic injection always ships a single file —
 * the TS universal extractor at `universal-extractor.js`.
 */

// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  getDynamicScriptsForDomain,
  getScriptsForUrl,
  UNIVERSAL_EXTRACTOR_TS_PATH,
} from "../injector";

describe("UNIVERSAL_EXTRACTOR_TS_PATH", () => {
  it("points at the WXT-emitted universal extractor", () => {
    expect(UNIVERSAL_EXTRACTOR_TS_PATH).toBe("universal-extractor.js");
  });
});

describe("getDynamicScriptsForDomain", () => {
  it("returns the universal extractor for any domain", () => {
    expect(getDynamicScriptsForDomain("chatgpt.com")).toEqual([
      UNIVERSAL_EXTRACTOR_TS_PATH,
    ]);
    expect(getDynamicScriptsForDomain("unknown.example.com")).toEqual([
      UNIVERSAL_EXTRACTOR_TS_PATH,
    ]);
  });

  it("does not include any legacy `content_scripts/` path", () => {
    for (const p of getDynamicScriptsForDomain("chatgpt.com")) {
      expect(p.startsWith("content_scripts/")).toBe(false);
    }
  });
});

describe("getScriptsForUrl", () => {
  it("returns null for non-HTTP(S) schemes", () => {
    expect(getScriptsForUrl("chrome://settings")).toBeNull();
    expect(getScriptsForUrl("about:blank")).toBeNull();
    expect(getScriptsForUrl("file:///home/user/page.html")).toBeNull();
  });

  it("returns null for unparseable input", () => {
    expect(getScriptsForUrl("not a url")).toBeNull();
    expect(getScriptsForUrl("")).toBeNull();
  });

  it("returns the universal extractor for any HTTP(S) URL", () => {
    expect(getScriptsForUrl("https://chatgpt.com/c/abc")).toEqual([
      UNIVERSAL_EXTRACTOR_TS_PATH,
    ]);
    expect(getScriptsForUrl("https://example.com/")).toEqual([
      UNIVERSAL_EXTRACTOR_TS_PATH,
    ]);
    expect(getScriptsForUrl("http://localhost:3000/")).toEqual([
      UNIVERSAL_EXTRACTOR_TS_PATH,
    ]);
  });

  it("accepts the legacy includeDetector opts object (no-op)", () => {
    expect(
      getScriptsForUrl("https://example.com/", { includeDetector: false }),
    ).toEqual([UNIVERSAL_EXTRACTOR_TS_PATH]);
    expect(
      getScriptsForUrl("https://example.com/", { includeDetector: true }),
    ).toEqual([UNIVERSAL_EXTRACTOR_TS_PATH]);
  });
});
