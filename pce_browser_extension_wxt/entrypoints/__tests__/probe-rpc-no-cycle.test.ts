// SPDX-License-Identifier: Apache-2.0
/**
 * Regression test for the ``Cannot access 'captureVerbs' before
 * initialization`` TDZ bug.
 *
 * Background: the five ``probe-rpc-{tab,dom,page,system,capture}.ts``
 * verb modules used to import ``ProbeException`` / ``VerbHandler`` /
 * ``getExtensionVersion`` directly from ``probe-rpc.ts``, which itself
 * imports the verb registries from each verb module. Vitest tolerates
 * that cycle (each module is its own evaluator), but Rollup's chunker
 * (used by the sideload ``dist/`` build) merges all six files into a
 * single chunk. Depending on declaration order in the merged chunk,
 * ``probe-rpc.ts``'s ``const verbs = { ...captureVerbs }`` ran BEFORE
 * ``captureVerbs = {…}`` was initialised \u2014 producing a runtime
 * ``ReferenceError`` in the service-worker console on every extension
 * boot.
 *
 * The fix was to extract the three shared symbols to
 * ``probe-rpc-shared.ts`` and have the verb modules import only from
 * there, breaking the cycle.
 *
 * This test enforces the invariant going forward: no verb module may
 * import from ``./probe-rpc`` (the dispatcher). Importing from
 * ``./probe-rpc-shared`` is fine. If a future refactor reintroduces
 * the cycle, this test fails BEFORE the broken bundle ships.
 *
 * The check is purely textual \u2014 we read each verb-module source and
 * grep for the disallowed import. This is intentionally a string match
 * rather than a fancy AST walk so the assertion is trivially auditable.
 */

// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BG_DIR = resolve(__dirname, "../background");

// Modules that MUST NOT import from ``./probe-rpc`` because they are
// imported BY it. Adding a new verb module? Append it here.
const VERB_MODULES = [
  "probe-rpc-tab.ts",
  "probe-rpc-dom.ts",
  "probe-rpc-page.ts",
  "probe-rpc-system.ts",
  "probe-rpc-capture.ts",
];

describe("probe-rpc no-cycle invariant", () => {
  for (const file of VERB_MODULES) {
    it(`${file} must not import from ./probe-rpc (would re-introduce TDZ cycle)`, () => {
      const src = readFileSync(resolve(BG_DIR, file), "utf8");
      // Match any ``import ... from "./probe-rpc"`` (single or double
      // quotes) BUT NOT ``./probe-rpc-shared`` or other suffixes.
      const bad = /from\s+['"]\.\/probe-rpc['"]/.test(src);
      expect(
        bad,
        `${file} imports directly from ./probe-rpc; this re-introduces the ` +
          `5-way circular import that caused the captureVerbs TDZ regression. ` +
          `Import from ./probe-rpc-shared instead.`,
      ).toBe(false);
    });
  }

  it("probe-rpc-shared.ts does not import from any other probe-rpc-* module", () => {
    const src = readFileSync(resolve(BG_DIR, "probe-rpc-shared.ts"), "utf8");
    // Shared must be a sink, not a hub. It can import from
    // ../../utils/probe-protocol (the wire types) but not from any
    // sibling probe-rpc-* module.
    const sibling = /from\s+['"]\.\/probe-rpc/.test(src);
    expect(
      sibling,
      "probe-rpc-shared.ts must not import from any other probe-rpc-* file; " +
        "it is the cycle breaker, not another node in the cycle.",
    ).toBe(false);
  });
});
