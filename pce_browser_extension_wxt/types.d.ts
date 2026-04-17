/**
 * Minimal pre-install type shim.
 *
 * WXT's own ``.wxt/tsconfig.json`` + its generated d.ts give us the real
 * types after ``pnpm install``. Until then (first clone, CI cache miss,
 * etc.) this stub keeps ``tsc --noEmit`` and editor lints quiet so
 * reviewers aren't distracted by "module not found" noise.
 *
 * Once ``pnpm install`` has run, the real types win via module resolution
 * precedence and this file becomes a no-op.
 */

declare module "wxt/sandbox" {
  export interface BackgroundDefinition {
    type?: "module" | "classic";
    persistent?: boolean;
    main: () => void | Promise<void>;
  }
  export function defineBackground(def: BackgroundDefinition): BackgroundDefinition;
  export function defineBackground(main: () => void | Promise<void>): BackgroundDefinition;
}

declare module "wxt/client" {
  // WXT's browser runtime helpers (stub; real types ship with the package).
  export const browser: typeof chrome;
}
