# DEPRECATED — adapter YAMLs moved to PCE-autonomy

**Date:** 2026-05-20
**Migrated to:** `PCE-autonomy/pce_autonomy/web_adapters/*.yaml`
**Driven by:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

The 14 web adapter YAML files (`chatgpt.yaml`, `claude.yaml`,
`copilot.yaml`, `deepseek.yaml`, `gemini.yaml`, `googleaistudio.yaml`,
`grok.yaml`, `huggingface.yaml`, `kimi.yaml`, `manus.yaml`,
`mistral.yaml`, `perplexity.yaml`, `poe.yaml`, `zhipu.yaml`) **are no
longer maintained in this directory**.

## Where to find the live versions

`F:/INVENTION/You.Inc/PCE-autonomy/pce_autonomy/web_adapters/<name>.yaml`
or on GitHub:
`https://github.com/zstnbb/PCE-autonomy/tree/master/pce_autonomy/web_adapters`

## Why

The 2026-05-15 charter for PCE-autonomy had a "don't vendor" rule —
adapter data stayed in PCE Core, autonomy mutated it via the sandbox
worktree. By 2026-05-18 web migration that rule was partially relaxed
(data + extension copied across); by 2026-05-20 ADR-010 cut the
adapter loader and the sandbox base also moved. Result: PCE Core
no longer needs to own the maintenance pipeline at all; autonomy
does.

PCE Core continues to own the **capture engine** (capture_event,
normalizer, models, db, supervisor, test_conductor). That layer is
unaffected by this migration.

## What to do if you find this dir still here

The files have been left in place during the v1.0 transition window
to give downstream consumers time to switch their imports. After
the next minor release (v1.1+) this directory will be removed
entirely. If you're reading adapter YAMLs at runtime: switch to
the autonomy paths above.

## Related

- `PCE-autonomy/Docs/MIGRATION-2026-05-20-full-independence.md`
- `PCE-autonomy/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md`
- `PCE-autonomy/Docs/MIGRATION-2026-05-18-web-from-pce-core.md` (initial cut)
