// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for ``probe-rpc-system.ts``.
 *
 * Most importantly: a consistency check that the hard-coded
 * ``ALL_KNOWN_VERBS`` list matches the actual verb registry exposed
 * to the dispatcher. If a future change adds a verb to one place but
 * not the other, ``system.version`` would advertise the wrong
 * capability set to agents — that would be a silent regression and
 * is exactly the kind of bug a contract test catches early.
 */

// @vitest-environment node
import { describe, expect, it } from "vitest";

import { __systemTesting, systemVerbs } from "../background/probe-rpc-system";
import { tabVerbs } from "../background/probe-rpc-tab";
import { domVerbs } from "../background/probe-rpc-dom";
import { pageVerbs } from "../background/probe-rpc-page";
import { captureVerbs } from "../background/probe-rpc-capture";

describe("system.* verbs", () => {
  it("system.ping returns pong + version", async () => {
    const result = await systemVerbs["system.ping"](
      {},
      new AbortController().signal,
    );
    expect((result as { pong: boolean }).pong).toBe(true);
    expect(typeof (result as { extension_version: string }).extension_version)
      .toBe("string");
  });

  it("system.version returns schema_version + capabilities", async () => {
    const result = (await systemVerbs["system.version"](
      {},
      new AbortController().signal,
    )) as { schema_version: number; capabilities: string[] };
    expect(result.schema_version).toBe(1);
    expect(Array.isArray(result.capabilities)).toBe(true);
    expect(result.capabilities.length).toBeGreaterThan(0);
  });
});

describe("verb registry consistency", () => {
  it("ALL_KNOWN_VERBS matches the union of namespace registries", () => {
    const actual = new Set<string>([
      ...Object.keys(systemVerbs),
      ...Object.keys(tabVerbs),
      ...Object.keys(domVerbs),
      ...Object.keys(pageVerbs),
      ...Object.keys(captureVerbs),
    ]);
    // Widen the advertised set to ``string`` so the symmetric-diff
    // ``.has`` calls below typecheck regardless of ``ProbeVerb`` being
    // a literal-union string type.
    const advertised = new Set<string>(__systemTesting.ALL_KNOWN_VERBS);

    // Two-way set equality, with descriptive diffs on failure.
    const missingFromAdvertised = [...actual].filter((v) => !advertised.has(v));
    const missingFromActual = [...advertised].filter((v) => !actual.has(v));

    expect(missingFromAdvertised, "verbs implemented but not advertised").toEqual([]);
    expect(missingFromActual, "verbs advertised but not implemented").toEqual([]);
  });

  it("contract is at v1 (smallest valid)", () => {
    // Future: when we ship v2, this test should be updated alongside
    // an ADR. Not a true regression check, just a tripwire.
    expect(__systemTesting.ALL_KNOWN_VERBS.length).toBeGreaterThanOrEqual(20);
  });
});
