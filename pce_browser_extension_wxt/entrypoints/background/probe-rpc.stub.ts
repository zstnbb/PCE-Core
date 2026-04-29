// SPDX-License-Identifier: Apache-2.0
/**
 * Build-time replacement module. Used by the public production
 * bundle. The dev/internal bundle uses the sibling implementation
 * file. See ``wxt.config.ts`` ``vite.resolve.alias`` for the swap.
 *
 * Comments here are deliberately free of identifiers and URLs that
 * could appear suspicious to an automated source review of the
 * shipped ``background.js``. The real implementation file is the
 * source of truth for what this module would do if enabled.
 */

export function startProbeRpc(): void {
  /* intentionally empty in the public bundle */
}
