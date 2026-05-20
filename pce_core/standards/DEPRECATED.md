# DEPRECATED — standards moved to PCE-autonomy

**Date:** 2026-05-20
**Migrated to:** `PCE-autonomy/pce_autonomy/web_standards/*.md`
**Driven by:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

The 5 web standard markdowns (`f1_chatgpt_web.md`, `f1_claude_web.md`,
`f1_gas_web.md`, `f1_gemini_web.md`, `f1_grok_web.md`) **are no longer
maintained in this directory**.

## Where to find the live versions

`F:/INVENTION/You.Inc/PCE-autonomy/pce_autonomy/web_standards/<id>.md`
or on GitHub:
`https://github.com/zstnbb/PCE-autonomy/tree/master/pce_autonomy/web_standards`

Loader: `PCE-autonomy/pce_autonomy/standards/loader.py` walks both
`pce_autonomy/standards/` (desktop) and `pce_autonomy/web_standards/`
(web). PCE Core no longer ships a standards loader.

## Related

- `PCE-autonomy/Docs/MIGRATION-2026-05-20-full-independence.md`
- `PCE-autonomy/Docs/adr/ADR-007-standard-document-schema.md` — schema unchanged; only directory moved
- `PCE-autonomy/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md`
