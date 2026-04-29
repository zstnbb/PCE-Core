// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — shared symbols for the verb-handler modules.
 *
 * The five ``probe-rpc-{tab,dom,page,system,capture}.ts`` verb modules
 * all need three pieces from the dispatcher:
 *
 *   * ``ProbeException`` — the typed error class they throw on input
 *     validation failures and Chrome runtime errors;
 *   * ``VerbHandler``    — the function signature each verb exports;
 *   * ``getExtensionVersion`` — used by ``system.*`` for the hello
 *     frame's ``extension_version`` field.
 *
 * Putting these in ``probe-rpc.ts`` (the dispatcher) creates a circular
 * import: the dispatcher imports each verb module's exported registry,
 * and each verb module imports these three symbols from the dispatcher.
 * Vitest tolerates the cycle because it loads modules independently;
 * Rollup's chunker (used by the sideload ``dist/`` build) merges the
 * cycle into a single chunk, and depending on declaration order, the
 * dispatcher's ``const verbs = { …captureVerbs }`` literal evaluates
 * BEFORE ``captureVerbs`` is initialised — producing the runtime
 * ``Cannot access 'captureVerbs' before initialization`` error you
 * see in the service worker console.
 *
 * Extracting the three shared symbols here breaks the cycle: the verb
 * modules import only from this file (which has no further imports
 * from the probe stack), and the dispatcher remains the sole consumer
 * of every verb module's registry. Result: the dependency graph is a
 * DAG, Rollup is happy, the TDZ disappears.
 *
 * For backward compatibility (vitest unit tests + any external code
 * that historically imported ``ProbeException`` from
 * ``./probe-rpc``), ``probe-rpc.ts`` re-exports these three symbols.
 * New code should import directly from this file.
 *
 * See also: ``Docs/docs/engineering/PCE-PROBE-API.md`` §10 for the
 * full file layout.
 */

import type { ProbeError, ProbeErrorCode } from "../../utils/probe-protocol";

// ---------------------------------------------------------------------------
// VerbHandler — function signature exported by each verb module.
// ---------------------------------------------------------------------------

export type VerbHandler = (
  params: unknown,
  signal: AbortSignal,
) => Promise<unknown>;

// ---------------------------------------------------------------------------
// ProbeException — typed error class.
// ---------------------------------------------------------------------------

/**
 * Custom error thrown by verb handlers. The dispatcher converts these
 * into typed ``ProbeErrorResponse`` envelopes via
 * ``instanceof ProbeException``. Anything else thrown becomes a
 * generic ``extension_internal`` error so the agent always gets a
 * structured envelope.
 */
export class ProbeException extends Error {
  readonly code: ProbeErrorCode;
  readonly context?: ProbeError["context"];
  readonly agentHint?: string;

  constructor(
    code: ProbeErrorCode,
    message: string,
    context?: ProbeError["context"],
    agentHint?: string,
  ) {
    super(message);
    this.name = "ProbeException";
    this.code = code;
    this.context = context;
    this.agentHint = agentHint;
  }
}

// ---------------------------------------------------------------------------
// getExtensionVersion — used by system.* verbs and the hello frame.
// ---------------------------------------------------------------------------

/**
 * Returns the running extension's manifest version, or ``"unknown"``
 * if ``chrome.runtime`` is unavailable (e.g. inside a vitest jsdom
 * environment that hasn't stubbed it).
 */
export function getExtensionVersion(): string {
  try {
    return chrome.runtime.getManifest().version;
  } catch {
    return "unknown";
  }
}
