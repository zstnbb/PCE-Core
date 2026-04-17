/**
 * Minimal pre-install type shim.
 *
 * WXT's own ``.wxt/tsconfig.json`` + its generated d.ts give us the real
 * types after ``pnpm install`` (which also pulls in ``@types/chrome``).
 * Until then (first clone, CI cache miss, etc.) this stub keeps
 * ``tsc --noEmit`` and editor lints quiet so reviewers aren't distracted
 * by "module not found" / "cannot find name 'chrome'" noise.
 *
 * Once ``pnpm install`` has run, the real types win via module
 * resolution precedence and this file becomes a no-op.
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
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const browser: any;
}

// ---------------------------------------------------------------------------
// WXT globals + Chrome extension APIs — coarse `any`-typed stubs.
//
// We intentionally do not reproduce `@types/chrome`'s ~500 KB of
// declarations here. Post-install, those real types supersede this
// stub via TS module resolution precedence (union of declarations).
// Pre-install, the `any` relaxation keeps `tsc --noEmit` clean without
// encouraging us to depend on fields that don't exist at runtime —
// linting + `pnpm typecheck` after install will catch those.
//
// NOTE: everything belongs inside `declare global { ... }` plus a final
// `export {};` so TypeScript treats this file as an ambient module and
// the globals actually land in the global scope. A plain top-level
// `declare namespace chrome` will NOT leak out once the file also
// declares module augmentations like `declare module "wxt/sandbox"`.
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  // WXT auto-imported helpers
  interface ContentScriptMatchesDef {
    matches: string[];
    excludeMatches?: string[];
    runAt?: "document_start" | "document_end" | "document_idle";
    allFrames?: boolean;
    world?: "ISOLATED" | "MAIN";
    main: (ctx?: any) => void | Promise<void>;
  }
  interface UnlistedScriptDef {
    main: () => void | Promise<void>;
  }
  function defineBackground(main: () => void | Promise<void>): any;
  function defineBackground(def: any): any;
  function defineContentScript(def: ContentScriptMatchesDef): any;
  function defineUnlistedScript(main: () => void | Promise<void>): any;
  function defineUnlistedScript(def: UnlistedScriptDef): any;

  // Chrome extension APIs (coarse stubs)
  namespace chrome {
    const scripting: any;
    const runtime: any;
    const storage: any;
    const action: any;
    const contextMenus: any;
    const tabs: any;
    const windows: any;
    const alarms: any;
    const webRequest: any;
    const webNavigation: any;
  }
}
export {};
/* eslint-enable @typescript-eslint/no-explicit-any */
