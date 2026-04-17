// Copies legacy extension assets from ../pce_browser_extension into
// ./public so WXT can bundle them verbatim.  Runs as a prebuild / predev
// step. Idempotent: deletes the previous `public/*` staging tree before
// copying to prevent stale files from leaking into builds.
//
// This script is intentionally dependency-free (only Node stdlib) so the
// build works before any `pnpm install`.

import { cpSync, rmSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const LEGACY = resolve(ROOT, "..", "pce_browser_extension");
const PUBLIC = resolve(ROOT, "public");

const COPY_TARGETS = [
  "content_scripts",
  "interceptor",
  "icons",
  "popup",
];

function sync() {
  if (!existsSync(LEGACY)) {
    console.error(`[sync-legacy-assets] legacy extension not found: ${LEGACY}`);
    process.exit(1);
  }
  if (!existsSync(PUBLIC)) mkdirSync(PUBLIC, { recursive: true });

  for (const dir of COPY_TARGETS) {
    const src = resolve(LEGACY, dir);
    const dst = resolve(PUBLIC, dir);
    if (!existsSync(src)) {
      console.warn(`[sync-legacy-assets] skipping missing dir: ${src}`);
      continue;
    }
    // Wipe stale stage to avoid phantom files
    rmSync(dst, { recursive: true, force: true });
    cpSync(src, dst, { recursive: true });
    console.log(`[sync-legacy-assets] ${dir}/ synced`);
  }
}

try {
  sync();
} catch (err) {
  console.error("[sync-legacy-assets] failed:", err);
  process.exit(1);
}
