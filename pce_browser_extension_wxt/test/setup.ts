// SPDX-License-Identifier: Apache-2.0
/**
 * Vitest global setup.
 *
 * Runs once per test worker before test files execute. Kept minimal
 * on purpose:
 *
 * - Silence ``console.debug`` so cache-invalidation / diagnostic
 *   logs from ``selector-engine.ts`` etc. don't drown out test
 *   output. Tests that explicitly verify logger behaviour pass a
 *   spy via the engine's ``logger`` option.
 *
 * - Do NOT install IndexedDB globally. ``capture-queue.test.ts``
 *   imports ``fake-indexeddb/auto`` at the top so the shim is only
 *   present when that test needs it.
 *
 * - Do NOT install a ``chrome`` stub globally. Tests that exercise
 *   ``background.ts`` / ``injector.ts`` set up per-test stubs via
 *   ``vi.stubGlobal("chrome", …)`` inside ``beforeEach`` — keeping
 *   each test's fixture explicit and preventing cross-file bleed.
 */

import { beforeEach, vi } from "vitest";

// ---------------------------------------------------------------------------
// WXT global stubs.
//
// `defineBackground` / `defineContentScript` / `defineUnlistedScript` are
// injected into entrypoint files by WXT's build step. Under Vitest they
// don't exist, so importing `entrypoints/background.ts` etc. would throw
// `ReferenceError`. We install no-op stubs up-front: they simply swallow
// the `main`/`def` argument and return it so the test can still exercise
// the module-level `__testing` exports that live outside the entrypoint
// closure.
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const g = globalThis as any;

if (typeof g.defineBackground !== "function") {
  g.defineBackground = (arg: unknown) => arg;
}
if (typeof g.defineContentScript !== "function") {
  g.defineContentScript = (arg: unknown) => arg;
}
if (typeof g.defineUnlistedScript !== "function") {
  g.defineUnlistedScript = (arg: unknown) => arg;
}

beforeEach(() => {
  vi.spyOn(console, "debug").mockImplementation(() => {});
});
