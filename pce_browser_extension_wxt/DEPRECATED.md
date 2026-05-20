# DEPRECATED — browser extension moved to PCE-autonomy

**Date:** 2026-05-20
**Migrated to:** `PCE-autonomy/pce_browser_extension_wxt/`
**First migrated:** 2026-05-18 (Phase 1 of web infra cut)
**Driven by:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

The Chrome MV3 browser extension (wxt + TypeScript) **no longer lives
here**. The autonomy repo ships the live version, including the L3a
DOM extractors (`<site>.content.ts`) and the L3g localStorage poller.

## Where to find it

`F:/INVENTION/You.Inc/PCE-autonomy/pce_browser_extension_wxt/`
or on GitHub:
`https://github.com/zstnbb/PCE-autonomy/tree/master/pce_browser_extension_wxt`

Build with:

```bash
cd /path/to/PCE-autonomy/pce_browser_extension_wxt
pnpm install
pnpm dev    # or  pnpm build  for production .output/chrome-mv3/
```

## Runtime fact

The Chrome session on the VPS loads the extension from the autonomy
checkout at `C:\pce-runtime\pce-autonomy\pce_browser_extension_wxt\
.output\chrome-mv3\`. PCE Core's copy is no longer used at runtime.

## Related

- `PCE-autonomy/Docs/RUNBOOK.md` §3 (Chrome bring-up)
- `PCE-autonomy/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md`

## Why the directory persists

Owner's in-progress branches (e.g. cold-start importer work as of
2026-05-19) may still touch this directory locally. Once those land
or are rebased onto the autonomy fork, this directory + its contents
get removed entirely.
