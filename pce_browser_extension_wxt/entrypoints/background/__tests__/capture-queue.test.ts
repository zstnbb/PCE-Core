// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/background/capture-queue.ts`.
 *
 * Exercises the IndexedDB semantics against `fake-indexeddb` (shim
 * imported via `/auto`) and a stubbed `fetch`. Happy-dom ships
 * without IDB so we can't rely on the default environment here.
 *
 * The tests focus on observable behaviour:
 *   - enqueue → count
 *   - flush hits the target URL + deletes successful items
 *   - 4xx responses drop items (non-retriable)
 *   - 5xx / network errors bump `attempts` (retriable)
 *   - TTL eviction drops >7-day-old items
 *   - clear() empties the store
 */

// @vitest-environment node
import "fake-indexeddb/auto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureQueue, __testing } from "../capture-queue";

// --- Test helpers ------------------------------------------------------

/** Wipe IDB database + reset in-module state between tests. */
async function resetAll(): Promise<void> {
  await CaptureQueue.clear();
  __testing.resetModuleState();
  // fake-indexeddb does not require a fresh `deleteDatabase` here — the
  // `clear` + `resetModuleState` combo puts us back to a clean slate.
}

function mockFetchOk(): ReturnType<typeof vi.fn> {
  return vi.fn(async () =>
    new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

function mockFetchStatus(status: number): ReturnType<typeof vi.fn> {
  return vi.fn(async () =>
    new Response("{}", { status, headers: { "Content-Type": "application/json" } }),
  );
}

function mockFetchNetworkError(): ReturnType<typeof vi.fn> {
  return vi.fn(async () => {
    throw new TypeError("NetworkError");
  });
}

beforeEach(async () => {
  await resetAll();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// --- Tests -------------------------------------------------------------

describe("CaptureQueue.enqueue / count", () => {
  it("count starts at 0", async () => {
    expect(await CaptureQueue.count()).toBe(0);
  });

  it("enqueue accepts a string body", async () => {
    await CaptureQueue.enqueue(
      "http://localhost/api/v1/ingest",
      JSON.stringify({ a: 1 }),
    );
    expect(await CaptureQueue.count()).toBe(1);
  });

  it("enqueue accepts an object body and stringifies it", async () => {
    await CaptureQueue.enqueue("http://localhost/api/v1/ingest", { b: 2 });
    expect(await CaptureQueue.count()).toBe(1);
  });

  it("enqueue accumulates captures across calls", async () => {
    for (let i = 0; i < 5; i++) {
      await CaptureQueue.enqueue("http://localhost/x", { i });
    }
    expect(await CaptureQueue.count()).toBe(5);
  });
});

describe("CaptureQueue.flush — happy path", () => {
  it("flushes queued captures and deletes them on 2xx", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    await CaptureQueue.enqueue("http://localhost/ingest", { a: 1 });
    await CaptureQueue.enqueue("http://localhost/ingest", { b: 2 });
    expect(await CaptureQueue.count()).toBe(2);

    const result = await CaptureQueue.flush();
    expect(result.sent).toBe(2);
    expect(result.remaining).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(await CaptureQueue.count()).toBe(0);
  });

  it("returns {sent:0, remaining:0} when queue is empty", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const result = await CaptureQueue.flush();
    expect(result).toEqual({ sent: 0, remaining: 0 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("CaptureQueue.flush — error paths", () => {
  it("drops items on 4xx (non-retriable)", async () => {
    vi.stubGlobal("fetch", mockFetchStatus(400));
    await CaptureQueue.enqueue("http://localhost/x", { a: 1 });
    const result = await CaptureQueue.flush();
    expect(result.sent).toBe(0);
    expect(await CaptureQueue.count()).toBe(0);
  });

  it("keeps items on 5xx (retriable)", async () => {
    vi.stubGlobal("fetch", mockFetchStatus(503));
    await CaptureQueue.enqueue("http://localhost/x", { a: 1 });
    const result = await CaptureQueue.flush();
    expect(result.sent).toBe(0);
    expect(await CaptureQueue.count()).toBe(1);
  });

  it("stops the batch on a network error and keeps remaining items", async () => {
    vi.stubGlobal("fetch", mockFetchNetworkError());
    await CaptureQueue.enqueue("http://localhost/x", { a: 1 });
    await CaptureQueue.enqueue("http://localhost/x", { b: 2 });
    const result = await CaptureQueue.flush();
    expect(result.sent).toBe(0);
    // Both items still queued; attempts incremented for the one we
    // tried to send before bailing.
    expect(await CaptureQueue.count()).toBe(2);
  });
});

describe("CaptureQueue.flush — TTL eviction", () => {
  it("drops items older than MAX_AGE_MS without sending them", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    // Inject a stale record directly via a backdoor: set Date.now() to
    // the past, enqueue, restore.
    const realNow = Date.now;
    vi.stubGlobal(
      "Date",
      class extends Date {
        static now(): number {
          return realNow() - (__testing.constants.MAX_AGE_MS + 1_000);
        }
      },
    );
    await CaptureQueue.enqueue("http://localhost/x", { stale: true });
    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", fetchMock);

    // Fresh enqueue AFTER restoring clock — should be sent.
    await CaptureQueue.enqueue("http://localhost/x", { fresh: true });
    expect(await CaptureQueue.count()).toBe(2);

    const result = await CaptureQueue.flush();
    expect(result.sent).toBe(1);
    expect(await CaptureQueue.count()).toBe(0);
    // Only the fresh item hit the network; stale item was dropped.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("CaptureQueue.flush — concurrency guard", () => {
  it("second concurrent flush returns {sent:0, remaining:0} immediately", async () => {
    let resolveFetch: ((r: Response) => void) | null = null;
    const fetchMock = vi.fn(
      () => new Promise<Response>((r) => (resolveFetch = r)),
    );
    vi.stubGlobal("fetch", fetchMock);

    await CaptureQueue.enqueue("http://localhost/x", { a: 1 });

    // Kick off one flush, don't await it yet
    const first = CaptureQueue.flush();

    // Second one while the first is in-flight — must short-circuit
    const second = await CaptureQueue.flush();
    expect(second).toEqual({ sent: 0, remaining: 0 });

    // Let the first finish
    resolveFetch!(new Response("{}", { status: 200 }));
    const firstResult = await first;
    expect(firstResult.sent).toBe(1);
  });
});

describe("CaptureQueue.clear", () => {
  it("removes everything", async () => {
    for (let i = 0; i < 3; i++) {
      await CaptureQueue.enqueue("http://localhost/x", { i });
    }
    expect(await CaptureQueue.count()).toBe(3);
    await CaptureQueue.clear();
    expect(await CaptureQueue.count()).toBe(0);
  });
});

describe("CaptureQueue constants", () => {
  it("exposes the documented limits", () => {
    const { constants } = __testing;
    expect(constants.BASE_RETRY_MS).toBe(5_000);
    expect(constants.MAX_RETRY_MS).toBe(120_000);
    expect(constants.MAX_QUEUE_SIZE).toBe(2_000);
    expect(constants.BATCH_SIZE).toBe(20);
    expect(constants.MAX_AGE_MS).toBe(7 * 24 * 3_600 * 1_000);
  });
});
