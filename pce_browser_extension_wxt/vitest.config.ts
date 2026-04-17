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
    },
  },
});
