// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Page-context AI-page confirmation flag.
 *
 * TypeScript port of ``interceptor/page_confirmed.js`` (P2.5 Phase 2).
 *
 * Tiny script injected via ``<script src>`` (CSP-safe, because inline
 * ``<script>`` is blocked on most AI sites). Sets the window-level flag
 * that ``network-interceptor`` reads synchronously to enable aggressive
 * capture mode.
 */

export default defineUnlistedScript(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).__PCE_AI_PAGE_CONFIRMED = true;
});
