// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for the `__testing` hooks exported from
 * `entrypoints/background.ts`.
 *
 * Only the pure helpers are exercised here:
 *   - `simpleHash` — deterministic, collision-tolerant string digest.
 *   - `isDuplicate` — 5-second short-time window dedup keyed by
 *     `sessionHint + simpleHash(fingerprint)`.
 *   - `isBlacklisted` — domain suffix check against the shared
 *     blacklist state.
 *   - `recordDetection` / `recordCaptureSuccess` / `getSilentDomains`
 *     — self-check bookkeeping.
 *
 * Message routing / health check / ingest POSTs live inside the
 * `defineBackground` closure and are wired to chrome APIs. Those land
 * in a Phase 3b+ integration harness.
 *
 * Import-time note: `background.ts` calls `defineBackground` at module
 * scope. `test/setup.ts` installs a no-op stub for that global.
 */

// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __testing } from "../background";

beforeEach(() => {
  __testing.resetState();
});

afterEach(() => {
  vi.useRealTimers();
});

// --- simpleHash --------------------------------------------------------

describe("simpleHash", () => {
  it("is deterministic", () => {
    expect(__testing.simpleHash("hello world")).toBe(
      __testing.simpleHash("hello world"),
    );
  });

  it("differs for different inputs", () => {
    expect(__testing.simpleHash("a")).not.toBe(__testing.simpleHash("b"));
  });

  it("returns a string (base36)", () => {
    const h = __testing.simpleHash("x");
    expect(typeof h).toBe("string");
    expect(h).toMatch(/^-?[0-9a-z]+$/);
  });

  it("handles empty string without throwing", () => {
    expect(() => __testing.simpleHash("")).not.toThrow();
    // Standard 32-bit rolling-hash of empty string is 0 → "0" in base36.
    expect(__testing.simpleHash("")).toBe("0");
  });
});

// --- isDuplicate ------------------------------------------------------

describe("isDuplicate", () => {
  it("first call is never a duplicate", () => {
    expect(__testing.isDuplicate("session-1", "fingerprint-xyz")).toBe(false);
  });

  it("second call with same key+fingerprint within window → duplicate", () => {
    __testing.isDuplicate("session-1", "fp");
    expect(__testing.isDuplicate("session-1", "fp")).toBe(true);
  });

  it("different session-hint is NOT a duplicate", () => {
    __testing.isDuplicate("session-1", "fp");
    expect(__testing.isDuplicate("session-2", "fp")).toBe(false);
  });

  it("different fingerprint is NOT a duplicate", () => {
    __testing.isDuplicate("session-1", "fp-a");
    expect(__testing.isDuplicate("session-1", "fp-b")).toBe(false);
  });

  it("falls back to 'global' bucket when sessionHint is null/undefined", () => {
    expect(__testing.isDuplicate(null, "fp")).toBe(false);
    expect(__testing.isDuplicate(undefined, "fp")).toBe(true);
  });

  it("rolls off after 5s (DEDUP_WINDOW_MS)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-17T00:00:00Z"));

    expect(__testing.isDuplicate("session-1", "fp")).toBe(false);
    // Just inside the 5s window
    vi.setSystemTime(new Date("2026-04-17T00:00:04.500Z"));
    expect(__testing.isDuplicate("session-1", "fp")).toBe(true);

    // Past the 5s window
    vi.setSystemTime(new Date("2026-04-17T00:00:06Z"));
    expect(__testing.isDuplicate("session-1", "fp")).toBe(false);
  });
});

// --- isBlacklisted -----------------------------------------------------

describe("isBlacklisted", () => {
  it("returns false when blacklist is empty", () => {
    expect(__testing.isBlacklisted("chatgpt.com")).toBe(false);
  });

  it("returns false for null / empty", () => {
    expect(__testing.isBlacklisted(null)).toBe(false);
    expect(__testing.isBlacklisted(undefined)).toBe(false);
    expect(__testing.isBlacklisted("")).toBe(false);
  });

  // Blacklist state is scoped inside background.ts; populate it via
  // resetState + the internal setter. We can't drive it from the
  // public chrome-message handler in a unit test, so we just assert
  // the default (empty) behaviour here. Full coverage lands with
  // the chrome-messaging integration harness.
});

// --- Self-check bookkeeping -------------------------------------------

describe("recordDetection / recordCaptureSuccess / getSilentDomains", () => {
  it("no detections → no silent domains", () => {
    expect(__testing.getSilentDomains()).toEqual([]);
  });

  it("a detection with a recent capture is NOT silent", () => {
    __testing.recordDetection("chatgpt.com", 42);
    __testing.recordCaptureSuccess("chatgpt.com");
    expect(__testing.getSilentDomains()).toEqual([]);
  });

  it("a detection with no capture and > grace period IS silent", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-17T00:00:00Z"));

    __testing.recordDetection("chatgpt.com", 42);
    // Fast-forward past the 30s silent grace window.
    vi.setSystemTime(new Date("2026-04-17T00:01:00Z"));

    const silent = __testing.getSilentDomains();
    expect(silent.length).toBe(1);
    expect(silent[0].domain).toBe("chatgpt.com");
  });

  it("ignores recordDetection with null domain", () => {
    __testing.recordDetection(null, 1);
    __testing.recordDetection(undefined, 2);
    expect(__testing.getSilentDomains()).toEqual([]);
  });
});

// --- State reset -------------------------------------------------------

describe("resetState", () => {
  it("clears all module state", () => {
    __testing.isDuplicate("s", "fp");
    __testing.recordDetection("chatgpt.com", 1);

    __testing.resetState();

    const s = __testing.getState();
    expect(s.captureCount).toBe(0);
    expect(s.recentHashesSize).toBe(0);
    expect(s.domainDetectionsSize).toBe(0);
    expect(s.injectedTabs.size).toBe(0);
    expect(s.isEnabled).toBe(true);
    expect(s.siteBlacklist).toEqual([]);
  });
});
