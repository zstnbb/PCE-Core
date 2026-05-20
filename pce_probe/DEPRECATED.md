# DEPRECATED — pce_probe moved to PCE-autonomy

**Date:** 2026-05-20
**Migrated to:** `PCE-autonomy/pce_probe/`
**First migrated:** 2026-05-18 (Phase 1 of web infra cut)
**Driven by:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

The Python WebSocket RPC bridge that talks to the browser extension
**no longer lives here**. The autonomy repo ships the live version.

## Where to find it

`F:/INVENTION/You.Inc/PCE-autonomy/pce_probe/`
or on GitHub:
`https://github.com/zstnbb/PCE-autonomy/tree/master/pce_probe`

## Runtime fact

The `pce-probe-server` NSSM service on the VPS at port :9888 currently
launches from `C:\pce-runtime\pce-autonomy\` (verified RUNBOOK §0).
PCE Core's copy is no longer used at runtime; it's preserved here for
release-grace only and will be removed in the next minor.

## Related

- `PCE-autonomy/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md`
- `PCE-autonomy/Docs/MIGRATION-2026-05-18-web-from-pce-core.md`
