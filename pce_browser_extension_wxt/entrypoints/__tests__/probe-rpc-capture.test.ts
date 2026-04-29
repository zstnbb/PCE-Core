// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for ``probe-rpc-capture.ts`` — the capture-pipeline observer
 * and the ``capture.*`` verb handlers.
 *
 * The observer module has zero chrome.* dependencies (it just records
 * messages from the background router), so we exercise it directly
 * without mocking chrome at all.
 */

// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  __captureTesting,
  captureVerbs,
  observeCaptureMessage,
  setPipelineStateProvider,
} from "../background/probe-rpc-capture";
import { ProbeException } from "../background/probe-rpc";

beforeEach(() => {
  __captureTesting.reset();
});

afterEach(() => {
  __captureTesting.reset();
});

// ---------------------------------------------------------------------------
// observeCaptureMessage — ring buffer
// ---------------------------------------------------------------------------

describe("observeCaptureMessage", () => {
  it("appends a summary for a PCE_CAPTURE message", () => {
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: {
        provider: "openai",
        host: "chatgpt.com",
        session_hint: "abc-123",
        conversation: {
          messages: [{ role: "user", content: "hello world" }],
        },
      },
    });
    const ring = __captureTesting.ringRef();
    expect(ring.length).toBe(1);
    expect(ring[0].kind).toBe("PCE_CAPTURE");
    expect(ring[0].provider).toBe("openai");
    expect(ring[0].host).toBe("chatgpt.com");
    expect(ring[0].session_hint).toBe("abc-123");
    expect(ring[0].fingerprint).toContain("hello world");
  });

  it("flattens conversation.messages content into the fingerprint", () => {
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: {
        provider: "openai",
        host: "chatgpt.com",
        conversation: {
          messages: [
            { role: "user", content: "first" },
            { role: "assistant", content: "second" },
          ],
        },
      },
    });
    expect(__captureTesting.ringRef()[0].fingerprint).toContain("first");
    expect(__captureTesting.ringRef()[0].fingerprint).toContain("second");
  });

  it("handles PCE_NETWORK_CAPTURE shape (request_body + response_body)", () => {
    observeCaptureMessage({
      kind: "PCE_NETWORK_CAPTURE",
      payload: {
        provider: "openai",
        host: "chatgpt.com",
        request_body: '{"prompt":"hi"}',
        response_body: '{"reply":"yo"}',
      },
    });
    const fp = __captureTesting.ringRef()[0].fingerprint ?? "";
    expect(fp).toContain("prompt");
    expect(fp).toContain("reply");
  });

  it("handles PCE_SNIPPET shape (content_text)", () => {
    observeCaptureMessage({
      kind: "PCE_SNIPPET",
      payload: { content_text: "snippet body", source_domain: "x.com" },
    });
    expect(__captureTesting.ringRef()[0].fingerprint).toContain("snippet body");
    expect(__captureTesting.ringRef()[0].kind).toBe("PCE_SNIPPET");
  });

  it("trims the ring buffer to RING_MAX (100) entries", () => {
    for (let i = 0; i < 150; i++) {
      observeCaptureMessage({
        kind: "PCE_CAPTURE",
        payload: { conversation: { messages: [{ role: "user", content: `msg-${i}` }] } },
      });
    }
    const ring = __captureTesting.ringRef();
    expect(ring.length).toBe(100);
    // Oldest entry should be msg-50, not msg-0.
    expect(ring[0].fingerprint).toContain("msg-50");
    expect(ring[ring.length - 1].fingerprint).toContain("msg-149");
  });
});

// ---------------------------------------------------------------------------
// capture.wait_for_token — waiter resolution
// ---------------------------------------------------------------------------

describe("capture.wait_for_token", () => {
  const handler = captureVerbs["capture.wait_for_token"];

  it("returns immediately when token is already in the ring buffer", async () => {
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: {
        provider: "openai",
        conversation: { messages: [{ role: "user", content: "find FOO-123 here" }] },
      },
    });
    const ac = new AbortController();
    const result = await handler(
      { token: "FOO-123", timeout_ms: 1_000 },
      ac.signal,
    );
    expect((result as { matched: boolean }).matched).toBe(true);
  });

  it("resolves when a matching capture arrives later", async () => {
    const ac = new AbortController();
    const promise = handler(
      { token: "LATER-99", timeout_ms: 5_000 },
      ac.signal,
    );
    setTimeout(() => {
      observeCaptureMessage({
        kind: "PCE_CAPTURE",
        payload: {
          provider: "openai",
          conversation: { messages: [{ role: "user", content: "msg with LATER-99" }] },
        },
      });
    }, 50);
    const result = await promise;
    expect((result as { matched: boolean }).matched).toBe(true);
    expect((result as { provider?: string }).provider).toBe("openai");
  });

  it("filters by provider when provider is set", async () => {
    const ac = new AbortController();
    const promise = handler(
      { token: "PV-1", timeout_ms: 5_000, provider: "openai" },
      ac.signal,
    );
    // First arrival: wrong provider — should NOT resolve.
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: {
        provider: "anthropic",
        conversation: { messages: [{ role: "user", content: "PV-1 wrong provider" }] },
      },
    });
    // Second arrival: right provider — should resolve.
    setTimeout(() => {
      observeCaptureMessage({
        kind: "PCE_CAPTURE",
        payload: {
          provider: "openai",
          conversation: { messages: [{ role: "user", content: "PV-1 correct provider" }] },
        },
      });
    }, 50);
    const result = await promise;
    expect((result as { provider?: string }).provider).toBe("openai");
  });

  it("rejects with capture_not_seen when timeout fires", async () => {
    const ac = new AbortController();
    await expect(
      handler({ token: "NEVER-SEEN", timeout_ms: 50 }, ac.signal),
    ).rejects.toMatchObject({ code: "capture_not_seen" });
  });

  it("rejects with timeout when AbortSignal fires", async () => {
    const ac = new AbortController();
    const promise = handler({ token: "AB-1", timeout_ms: 5_000 }, ac.signal);
    setTimeout(() => ac.abort(), 30);
    await expect(promise).rejects.toBeInstanceOf(ProbeException);
  });

  it("includes recent capture events in the failure context", async () => {
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: {
        provider: "openai",
        conversation: { messages: [{ role: "user", content: "unrelated msg" }] },
      },
    });
    const ac = new AbortController();
    try {
      await handler({ token: "MISSING-TOKEN", timeout_ms: 50 }, ac.signal);
      throw new Error("should have thrown");
    } catch (e) {
      const err = e as ProbeException;
      expect(err.code).toBe("capture_not_seen");
      expect(err.context).toBeDefined();
      const events = (err.context as Record<string, unknown>)
        .last_capture_events as Array<unknown>;
      expect(events.length).toBeGreaterThan(0);
    }
  });

  it("rejects malformed params (non-string token)", async () => {
    const ac = new AbortController();
    await expect(
      handler({ token: 123 } as unknown as Record<string, unknown>, ac.signal),
    ).rejects.toMatchObject({ code: "params_invalid" });
  });
});

// ---------------------------------------------------------------------------
// capture.recent_events
// ---------------------------------------------------------------------------

describe("capture.recent_events", () => {
  const handler = captureVerbs["capture.recent_events"];

  it("returns the most recent events up to last_n", async () => {
    for (let i = 0; i < 5; i++) {
      observeCaptureMessage({
        kind: "PCE_CAPTURE",
        payload: {
          provider: "openai",
          conversation: { messages: [{ role: "user", content: `evt-${i}` }] },
        },
      });
    }
    const ac = new AbortController();
    const result = (await handler({ last_n: 3 }, ac.signal)) as {
      events: Array<{ fingerprint?: string }>;
    };
    expect(result.events.length).toBe(3);
    expect(result.events[0].fingerprint).toContain("evt-2");
    expect(result.events[2].fingerprint).toContain("evt-4");
  });

  it("filters by provider", async () => {
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: { provider: "openai", conversation: { messages: [{ content: "openai-1" }] } },
    });
    observeCaptureMessage({
      kind: "PCE_CAPTURE",
      payload: { provider: "anthropic", conversation: { messages: [{ content: "claude-1" }] } },
    });
    const ac = new AbortController();
    const result = (await handler({ provider: "openai" }, ac.signal)) as {
      events: Array<{ provider?: string }>;
    };
    expect(result.events.every((e) => e.provider === "openai")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// capture.pipeline_state — uses the injected provider
// ---------------------------------------------------------------------------

describe("capture.pipeline_state", () => {
  const handler = captureVerbs["capture.pipeline_state"];

  it("returns a fallback shape when no provider is wired", async () => {
    setPipelineStateProvider(null as unknown as () => never); // simulate "not wired"
    // Re-set to a known fallback shape; the implementation guards
    // against null and returns the not-wired payload.
    const ac = new AbortController();
    const result = (await handler({}, ac.signal)) as {
      enabled: boolean;
      last_error: string | null;
    };
    // We don't assert exact fallback values; just that it returned a
    // dict-shaped payload without throwing.
    expect(typeof result.enabled).toBe("boolean");
  });

  it("delegates to the wired provider when set", async () => {
    setPipelineStateProvider(() => ({
      enabled: true,
      capture_count: 42,
      last_error: null,
      server_online: true,
      queued_captures: 7,
    }));
    const ac = new AbortController();
    const result = (await handler({}, ac.signal)) as {
      capture_count: number;
      queued_captures: number;
    };
    expect(result.capture_count).toBe(42);
    expect(result.queued_captures).toBe(7);
  });
});
