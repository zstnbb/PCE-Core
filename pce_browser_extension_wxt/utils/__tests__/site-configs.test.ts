/**
 * Tests for `utils/site-configs.ts`.
 *
 * Pure data module — these tests lock down the alias table and the
 * subdomain-fallback resolution so regressions in the migration
 * surface immediately rather than months later when a new domain
 * silently stops matching.
 */

// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  PCE_SITE_CONFIGS,
  resolveSiteConfig,
} from "../site-configs";

describe("PCE_SITE_CONFIGS", () => {
  it("covers every canonical provider", () => {
    expect(PCE_SITE_CONFIGS["chatgpt.com"].provider).toBe("openai");
    expect(PCE_SITE_CONFIGS["claude.ai"].provider).toBe("anthropic");
    expect(PCE_SITE_CONFIGS["gemini.google.com"].provider).toBe("google");
    expect(PCE_SITE_CONFIGS["chat.deepseek.com"].provider).toBe("deepseek");
    expect(PCE_SITE_CONFIGS["www.perplexity.ai"].provider).toBe("perplexity");
    expect(PCE_SITE_CONFIGS["grok.com"].provider).toBe("xai");
    expect(PCE_SITE_CONFIGS["copilot.microsoft.com"].provider).toBe(
      "microsoft",
    );
    expect(PCE_SITE_CONFIGS["poe.com"].provider).toBe("poe");
    expect(PCE_SITE_CONFIGS["huggingface.co"].provider).toBe("huggingface");
    expect(PCE_SITE_CONFIGS["kimi.com"].provider).toBe("moonshot");
    expect(PCE_SITE_CONFIGS["chat.mistral.ai"].provider).toBe("mistral");
    expect(PCE_SITE_CONFIGS["aistudio.google.com"].provider).toBe("google");
    expect(PCE_SITE_CONFIGS["manus.im"].provider).toBe("manus");
    expect(PCE_SITE_CONFIGS["chat.z.ai"].provider).toBe("zhipu");
  });

  it("exposes aliases with the same config object as their target", () => {
    expect(PCE_SITE_CONFIGS["chat.openai.com"]).toBe(
      PCE_SITE_CONFIGS["chatgpt.com"],
    );
    expect(PCE_SITE_CONFIGS["www.kimi.com"]).toBe(PCE_SITE_CONFIGS["kimi.com"]);
    expect(PCE_SITE_CONFIGS["kimi.moonshot.cn"]).toBe(
      PCE_SITE_CONFIGS["kimi.com"],
    );
    expect(PCE_SITE_CONFIGS["chatglm.cn"]).toBe(PCE_SITE_CONFIGS["chat.z.ai"]);
    expect(PCE_SITE_CONFIGS["chat.zhipuai.cn"]).toBe(
      PCE_SITE_CONFIGS["chat.z.ai"],
    );
  });

  it("registry is frozen (readonly at runtime)", () => {
    expect(Object.isFrozen(PCE_SITE_CONFIGS)).toBe(true);
  });
});

describe("resolveSiteConfig", () => {
  it("returns null for empty / unknown hostnames", () => {
    expect(resolveSiteConfig("")).toBeNull();
    expect(resolveSiteConfig("example.com")).toBeNull();
  });

  it("resolves direct hits", () => {
    expect(resolveSiteConfig("chatgpt.com")!.provider).toBe("openai");
  });

  it("resolves aliases", () => {
    expect(resolveSiteConfig("chat.openai.com")!.sourceName).toBe(
      "chatgpt-web",
    );
    expect(resolveSiteConfig("kimi.moonshot.cn")!.provider).toBe("moonshot");
  });

  it("falls back to subdomain-suffix match", () => {
    // A hypothetical `www.chatgpt.com` is not in the registry; engine
    // must still resolve it to `chatgpt.com` via suffix match.
    expect(resolveSiteConfig("www.chatgpt.com")!.provider).toBe("openai");
    expect(resolveSiteConfig("api.claude.ai")!.provider).toBe("anthropic");
  });

  it("does not subdomain-match substrings (must be dot-boundary)", () => {
    // `not-chatgpt.com` is a different TLD, not a subdomain of
    // `chatgpt.com`. Must not resolve.
    expect(resolveSiteConfig("not-chatgpt.com")).toBeNull();
    expect(resolveSiteConfig("fakeclaude.ai")).toBeNull();
  });
});
