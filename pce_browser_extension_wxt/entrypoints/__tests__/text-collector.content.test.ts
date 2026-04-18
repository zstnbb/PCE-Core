// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/text-collector.content.ts` pure helpers.
 *
 * The DOM-manipulation surface (floating UI, mouseup listener) is NOT
 * covered here — `defineContentScript.main()` is a no-op under Vitest.
 * We test the pure helpers that decide provider, category metadata,
 * and floating-button positioning.
 */

import { describe, expect, it } from "vitest";

import {
  CATEGORIES,
  clampFloatingPosition,
  DOMAIN_PROVIDER_MAP,
  detectProvider,
  MIN_SELECTION_LENGTH,
} from "../text-collector.content";

describe("constants", () => {
  it("exposes MIN_SELECTION_LENGTH > 0", () => {
    expect(MIN_SELECTION_LENGTH).toBeGreaterThan(0);
  });

  it("DOMAIN_PROVIDER_MAP covers all 13 supported sites", () => {
    for (const host of [
      "chatgpt.com",
      "chat.openai.com",
      "claude.ai",
      "gemini.google.com",
      "aistudio.google.com",
      "chat.deepseek.com",
      "chat.z.ai",
      "www.perplexity.ai",
      "copilot.microsoft.com",
      "poe.com",
      "huggingface.co",
      "grok.com",
      "manus.im",
      "chat.mistral.ai",
      "kimi.moonshot.cn",
      "www.kimi.com",
      "kimi.com",
    ]) {
      expect(
        DOMAIN_PROVIDER_MAP[host],
        `missing provider for ${host}`,
      ).toBeTruthy();
    }
  });

  it("CATEGORIES has the 5 expected ids", () => {
    const ids = CATEGORIES.map((c) => c.id);
    expect(ids).toEqual([
      "general",
      "code",
      "prompt",
      "output",
      "reference",
    ]);
  });
});

describe("detectProvider", () => {
  it("maps known hosts directly", () => {
    expect(detectProvider("chatgpt.com")).toBe("openai");
    expect(detectProvider("claude.ai")).toBe("anthropic");
    expect(detectProvider("kimi.moonshot.cn")).toBe("moonshot");
  });

  it("falls back to second-to-last hostname label for unknown hosts", () => {
    // foo.bar.example → example
    expect(detectProvider("foo.bar.example.ai")).toBe("example");
  });

  it("returns 'unknown' when hostname has no labels", () => {
    expect(detectProvider("")).toBe("unknown");
  });
});

describe("clampFloatingPosition", () => {
  it("clamps x to vw - 200 when overflowing right", () => {
    const { left } = clampFloatingPosition(1900, 400, 1920, 1080);
    expect(left).toBeLessThanOrEqual(1920 - 200);
  });

  it("flips y upward when overflowing bottom", () => {
    const { top } = clampFloatingPosition(400, 1050, 1920, 1080);
    // y + 8 + 40 = 1098 > 1080 → flip to y - 48 = 1002
    expect(top).toBe(1002);
  });

  it("keeps y below cursor when there's room", () => {
    const { top } = clampFloatingPosition(400, 200, 1920, 1080);
    expect(top).toBe(208);
  });

  it("clamps x to >= 8 when going off left edge", () => {
    const { left } = clampFloatingPosition(-50, 400, 1920, 1080);
    expect(left).toBe(8);
  });

  it("returns the requested position when nothing needs clamping", () => {
    const { left, top } = clampFloatingPosition(400, 400, 1920, 1080);
    expect(left).toBe(400);
    expect(top).toBe(408);
  });
});
