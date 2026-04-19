// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/gmail.content.ts` (P5.A-9).
 *
 * Only the Help-me-write flow is captured — Smart Compose fires on
 * every keystroke and would be too noisy. These tests lock in:
 *
 *   1. Silent-by-default on normal inbox browsing + plain compose
 *      windows (no Help-me-write dialog → ``extractMessages === []``).
 *   2. Thread-id extraction from the URL hash shapes Gmail ships
 *      today (``#inbox/<id>``, ``#label/<label>/<id>``, nested
 *      views).
 *   3. Prompt + draft pairing inside an open Help-me-write dialog.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractMessages,
  getContainer,
  getSessionHint,
  isHelpMeWriteOpen,
  normalizeText,
} from "../gmail.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips Gmail compose-toolbar labels", () => {
    expect(normalizeText("Send\nReal draft")).toBe("Real draft");
    expect(normalizeText("Refine\nShorten\nReal draft")).toBe("Real draft");
    expect(normalizeText("1 / 3\nReal")).toBe("Real");
  });

  it("preserves multi-line content", () => {
    expect(normalizeText("Hi Sam,\nThanks for the update.")).toBe(
      "Hi Sam,\nThanks for the update.",
    );
  });

  it("handles null/empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts thread id from #inbox/<id>", () => {
    expect(getSessionHint("#inbox/FMfcgzQbgwLhBmvXPpqcRgRZ", "/")).toBe(
      "FMfcgzQbgwLhBmvXPpqcRgRZ",
    );
  });

  it("extracts thread id from nested #label/<label>/<id>", () => {
    expect(
      getSessionHint("#label/Work/FMfcgzQbgwLhBmvXPpqcRgRZ", "/"),
    ).toBe("FMfcgzQbgwLhBmvXPpqcRgRZ");
  });

  it("falls back to pathname when hash has no thread id", () => {
    expect(getSessionHint("#inbox", "/mail/u/0/")).toBe("/mail/u/0/");
  });

  it("returns null when both inputs empty", () => {
    expect(getSessionHint("", "")).toBeNull();
  });
});

describe("getContainer", () => {
  it("prefers the dialog enclosing Help-me-write when panel is open", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="New Message" id="compose">
        <button aria-label="Help me write">✨</button>
      </div>`;
    expect(getContainer(document)!.id).toBe("compose");
  });

  it("falls back to compose dialog when Help-me-write closed", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="New Message" id="compose">
        <div>body</div>
      </div>`;
    expect(getContainer(document)!.id).toBe("compose");
  });

  it("falls back to body on plain inbox view", () => {
    document.body.innerHTML = `<div id="inbox">messages</div>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("isHelpMeWriteOpen", () => {
  it("true when a Help-me-write marker is present", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="Help me write">x</div>`;
    expect(isHelpMeWriteOpen(document)).toBe(true);
  });

  it("false on ordinary compose dialog", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="New Message">x</div>`;
    expect(isHelpMeWriteOpen(document)).toBe(false);
  });

  it("false on inbox", () => {
    document.body.innerHTML = `<div>inbox</div>`;
    expect(isHelpMeWriteOpen(document)).toBe(false);
  });
});

describe("extractMessages", () => {
  it("returns [] when Help-me-write is not open", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="New Message">
        <textarea>ordinary reply</textarea>
      </div>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("pairs prompt + generated draft when dialog is open", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="Help me write">
        <textarea aria-label="Enter a prompt">
          write a polite decline to the meeting invite
        </textarea>
        <div role="region" aria-live="polite">
          Hi Jordan, thank you for the invite. Unfortunately I won't be able to join.
        </div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toContain("polite decline");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("Jordan");
  });

  it("stops after first non-empty draft region", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="Help me write">
        <div aria-label="Generated draft">first draft</div>
        <div aria-label="Draft preview">stale preview</div>
      </div>`;
    const msgs = extractMessages(document);
    const assistants = msgs.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0].content).toBe("first draft");
  });

  it("dedupes identical draft renders", () => {
    document.body.innerHTML = `
      <div role="dialog" aria-label="Help me write">
        <div aria-label="Generated draft">same text</div>
        <div aria-label="Generated draft">same text</div>
      </div>`;
    const msgs = extractMessages(document);
    const assistants = msgs.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(1);
  });
});
