// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/generic.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  getContainer,
  HOST_PROVIDER_MAP,
  isDedicatedHost,
  resolveProvider,
  resolveSourceName,
} from "../generic.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("HOST_PROVIDER_MAP", () => {
  it("covers mistral + the three kimi hosts", () => {
    expect(HOST_PROVIDER_MAP["chat.mistral.ai"]).toBe("mistral");
    expect(HOST_PROVIDER_MAP["kimi.moonshot.cn"]).toBe("moonshot");
    expect(HOST_PROVIDER_MAP["www.kimi.com"]).toBe("moonshot");
    expect(HOST_PROVIDER_MAP["kimi.com"]).toBe("moonshot");
  });
});

describe("resolveProvider / resolveSourceName", () => {
  it("maps known hosts directly", () => {
    expect(resolveProvider("chat.mistral.ai")).toBe("mistral");
    expect(resolveProvider("kimi.moonshot.cn")).toBe("moonshot");
  });

  it("falls back to hostname.split('.')[0] for unknown hosts", () => {
    expect(resolveProvider("some.weird.host")).toBe("some");
  });

  it("sourceName appends -web", () => {
    expect(resolveSourceName("chat.mistral.ai")).toBe("mistral-web");
    expect(resolveSourceName("unknown.io")).toBe("unknown-web");
  });
});

describe("isDedicatedHost", () => {
  it("returns true for hosts with dedicated TS extractors", () => {
    expect(isDedicatedHost("chatgpt.com")).toBe(true);
    expect(isDedicatedHost("claude.ai")).toBe(true);
    expect(isDedicatedHost("aistudio.google.com")).toBe(true);
  });

  it("returns false for mistral / kimi (the generic's own targets)", () => {
    expect(isDedicatedHost("chat.mistral.ai")).toBe(false);
    expect(isDedicatedHost("kimi.moonshot.cn")).toBe(false);
  });
});

describe("getContainer", () => {
  it("prefers main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("detectRole", () => {
  it("detects user from class keywords", () => {
    document.body.innerHTML = `<div id="x" class="user-query">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("detects user from data-message-author", () => {
    document.body.innerHTML = `<div id="x" data-message-author="user">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("detects assistant from class keywords", () => {
    document.body.innerHTML = `<div id="x" class="assistant-answer">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("detects assistant from response / answer keywords", () => {
    document.body.innerHTML = `
      <div id="r" class="response">r</div>
      <div id="a" class="answer">a</div>`;
    expect(detectRole(document.getElementById("r")!)).toBe("assistant");
    expect(detectRole(document.getElementById("a")!)).toBe("assistant");
  });

  it("returns unknown when nothing matches", () => {
    document.body.innerHTML = `<div id="x">plain</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractMessages", () => {
  it("captures Kimi segment-based layout", () => {
    document.body.innerHTML = `
      <main>
        <div class="segment segment-user">user question</div>
        <div class="segment segment-assistant">assistant reply</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("captures [data-message-author] / [data-author-role] pairs", () => {
    document.body.innerHTML = `
      <main>
        <div data-message-author="user">user q</div>
        <div data-author-role="assistant">asst a</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("falls back to [class*=turn] containers", () => {
    document.body.innerHTML = `
      <main>
        <div class="turn user-turn">user turn with long enough text</div>
        <div class="turn model-turn">model turn with long enough text</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
  });

  it("returns [] when nothing matches", () => {
    document.body.innerHTML = `<main><p>x</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
