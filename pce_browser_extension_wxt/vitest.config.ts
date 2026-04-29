// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vitest/config";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

/**
 * Vitest config for the PCE browser extension.
 *
 * Environments:
 *   - default: ``happy-dom`` so utilities that touch the DOM don't need
 *     the full ``jsdom`` surface. Individual tests may override via
 *     ``// @vitest-environment node`` when no DOM is required.
 *
 * IndexedDB: ``fake-indexeddb/auto`` is NOT imported globally because
 * ``happy-dom`` does not ship IndexedDB and we only want the shim in
 * tests that exercise ``capture-queue.ts``. Those tests import it
 * explicitly at the top of the file.
 *
 * ``chrome``: happy-dom doesn't provide the extension APIs. Tests that
 * exercise `background.ts`/`injector.ts` set up a `globalThis.chrome`
 * stub inside their own `beforeEach` — keeping the config minimal and
 * avoiding leakage between test files.
 */
export default defineConfig({
  // Compile-time constant injected by ``wxt.config.ts`` for the
  // production build. Vitest reuses the same identifier so any test
  // that imports a module gated by ``__PCE_PROBE_ENABLED__`` runs
  // against the real probe path. See ``types.d.ts`` for the global
  // declaration.
  define: {
    __PCE_PROBE_ENABLED__: JSON.stringify(true),
  },
  test: {
    environment: "happy-dom",
    include: [
      "utils/**/*.test.ts",
      "entrypoints/**/*.test.ts",
    ],
    exclude: [
      "node_modules/**",
      ".output/**",
      ".wxt/**",
      "public/**",
    ],
    globals: false,
    setupFiles: [resolve(__dirname, "test/setup.ts")],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: [
        "utils/**/*.ts",
        "entrypoints/**/*.ts",
      ],
      exclude: [
        "**/*.test.ts",
        "**/*.d.ts",
        "test/**",
      ],
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname),
      // Mirror the wxt.config.ts virtual aliases so vitest tests
      // resolve them to the real probe modules. Webstore-build
      // stubbing is a wxt-only concern; tests always run against the
      // real implementation.
      "$probe-rpc": resolve(
        __dirname,
        "entrypoints/background/probe-rpc.ts",
      ),
      "$probe-rpc-capture": resolve(
        __dirname,
        "entrypoints/background/probe-rpc-capture.ts",
      ),
    },
  },
});
