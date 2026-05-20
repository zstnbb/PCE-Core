# DEPRECATED — e2e_probe tests moved to PCE-autonomy

**Date:** 2026-05-20
**Migrated to:** `PCE-autonomy/tests/e2e_probe/`
**First migrated:** 2026-05-18
**Driven by:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

The web-side end-to-end probe tests (sites, cases, matrix, capture
verifier) **no longer live here**. Run them from autonomy:

```bash
cd /path/to/PCE-autonomy
python -m pytest tests/e2e_probe/
```

## What stays in PCE Core

`tests/e2e_desktop/` and `tests/e2e_desktop_ui/` — these test **PCE
Core's own desktop product** (`pce_app_launcher/` capture bridge,
detector, CLI wrapper, IDE drivers). They are *not* automation
maintenance tests; they're product-surface tests and continue to be
owned by PCE Core.

## Related

- `PCE-autonomy/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md`
- `PCE-autonomy/Docs/MIGRATION-2026-05-18-web-from-pce-core.md`
