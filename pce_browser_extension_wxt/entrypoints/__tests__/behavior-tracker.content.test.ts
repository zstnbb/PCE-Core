// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/behavior-tracker.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  computeScrollDepth,
  createBehaviorState,
  getBehaviorSnapshot,
  isSendButtonTarget,
  isSendKeyEvent,
  SEND_BUTTON_SELECTORS,
} from "../behavior-tracker.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("createBehaviorState", () => {
  it("returns a fresh zero-valued state", () => {
    const s = createBehaviorState();
    expect(s.lastRequestSentAt).toBeNull();
    expect(s.copyEvents).toEqual([]);
    expect(s.scrollDepthPct).toBe(0);
    expect(s.messageTimestamps).toEqual([]);
  });
});

describe("getBehaviorSnapshot", () => {
  it("assembles a snapshot from the current state", () => {
    const state = createBehaviorState();
    state.lastRequestSentAt = 1000;
    state.copyEvents = [{ at: 100, length: 42 }];
    state.scrollDepthPct = 55;
    const snap = getBehaviorSnapshot(state);
    expect(snap.request_sent_at).toBe(1000);
    expect(snap.copy_events).toHaveLength(1);
    expect(snap.scroll_depth_pct).toBe(55);
    expect(snap.think_interval_ms).toBeUndefined();
  });

  it("keeps only the last 10 copy events", () => {
    const state = createBehaviorState();
    for (let i = 0; i < 15; i++) {
      state.copyEvents.push({ at: i, length: 10 });
    }
    const snap = getBehaviorSnapshot(state);
    expect(snap.copy_events).toHaveLength(10);
    // Verify we got the LAST 10 (entries 5..14)
    expect(snap.copy_events[0].at).toBe(5);
    expect(snap.copy_events[9].at).toBe(14);
  });

  it("computes think_interval_ms from the last two message timestamps", () => {
    const state = createBehaviorState();
    state.messageTimestamps = [1000, 1500, 3000];
    const snap = getBehaviorSnapshot(state);
    expect(snap.think_interval_ms).toBe(1500);
  });

  it("reset=true clears copy_events + scroll_depth_pct", () => {
    const state = createBehaviorState();
    state.copyEvents = [{ at: 1, length: 2 }];
    state.scrollDepthPct = 80;
    getBehaviorSnapshot(state, true);
    expect(state.copyEvents).toHaveLength(0);
    expect(state.scrollDepthPct).toBe(0);
    // messageTimestamps + lastRequestSentAt are NOT reset
    expect(state.lastRequestSentAt).toBeNull();
  });
});

describe("SEND_BUTTON_SELECTORS", () => {
  it("includes the ChatGPT + Claude known testids", () => {
    expect(SEND_BUTTON_SELECTORS).toContain(
      'button[data-testid="send-button"]',
    );
    expect(SEND_BUTTON_SELECTORS).toContain(
      'button[data-testid="send-message-button"]',
    );
  });
});

describe("isSendButtonTarget", () => {
  it("true when the element matches a known send-button selector", () => {
    document.body.innerHTML = `
      <button id="b" data-testid="send-button">send</button>`;
    expect(isSendButtonTarget(document.getElementById("b"))).toBe(true);
  });

  it("true when a descendant of a send-button is clicked", () => {
    document.body.innerHTML = `
      <button data-testid="send-button">
        <svg id="icon"></svg>
      </button>`;
    expect(isSendButtonTarget(document.getElementById("icon"))).toBe(true);
  });

  it("true for a generic button inside a form with a textarea", () => {
    document.body.innerHTML = `
      <form>
        <textarea></textarea>
        <button id="send">Send</button>
      </form>`;
    expect(isSendButtonTarget(document.getElementById("send"))).toBe(true);
  });

  it("false for a random button", () => {
    document.body.innerHTML = `<button id="x">x</button>`;
    expect(isSendButtonTarget(document.getElementById("x"))).toBe(false);
  });

  it("false for null", () => {
    expect(isSendButtonTarget(null)).toBe(false);
  });
});

describe("isSendKeyEvent", () => {
  it("true for Enter in a textarea", () => {
    expect(
      isSendKeyEvent({
        key: "Enter",
        shiftKey: false,
        target: { tagName: "TEXTAREA" },
      }),
    ).toBe(true);
  });

  it("true for Enter in a contenteditable", () => {
    expect(
      isSendKeyEvent({
        key: "Enter",
        shiftKey: false,
        target: {
          tagName: "DIV",
          getAttribute: (name: string) =>
            name === "contenteditable" ? "true" : null,
        },
      }),
    ).toBe(true);
  });

  it("false for Shift+Enter", () => {
    expect(
      isSendKeyEvent({
        key: "Enter",
        shiftKey: true,
        target: { tagName: "TEXTAREA" },
      }),
    ).toBe(false);
  });

  it("false for other keys", () => {
    expect(
      isSendKeyEvent({
        key: "a",
        shiftKey: false,
        target: { tagName: "TEXTAREA" },
      }),
    ).toBe(false);
  });

  it("false for plain text input (not contenteditable)", () => {
    expect(
      isSendKeyEvent({
        key: "Enter",
        shiftKey: false,
        target: { tagName: "INPUT" },
      }),
    ).toBe(false);
  });
});

describe("computeScrollDepth", () => {
  it("returns 0 when content fits in viewport", () => {
    expect(computeScrollDepth(0, 500, 1000)).toBe(0);
  });

  it("returns percentage scrolled", () => {
    // scrollTop 0 + clientHeight 500 / scrollHeight 1000 = 50
    expect(computeScrollDepth(0, 1000, 500)).toBe(50);
    // scrollTop 500 + clientHeight 500 / scrollHeight 1000 = 100
    expect(computeScrollDepth(500, 1000, 500)).toBe(100);
  });

  it("rounds to integer", () => {
    expect(computeScrollDepth(123, 1000, 500)).toBe(Math.round(62.3));
  });
});
