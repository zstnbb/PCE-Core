# PCE Core ↔ PCE-autonomy boundary — 2026-05-20 full-independence cut

**Date:** 2026-05-20
**Scope:** PCE-autonomy now owns the entire maintenance pipeline. PCE Core
keeps the **capture engine** (capture_event, normalizer, models, db,
supervisor, test_conductor) and the **desktop product**
(`pce_app/`, `pce_app_launcher/`, `pce_cli_wrapper/`, `tests/e2e_desktop*/`).

**Authority:** [PCE-autonomy ADR-010](https://github.com/zstnbb/PCE-autonomy/blob/master/Docs/adr/ADR-010-autonomy-as-full-automation-repo.md)

## What moved out of PCE Core (with DEPRECATED.md banners now in place)

| Path here | Live home | Banner |
|---|---|---|
| `pce_core/adapters/*.yaml` (14 web yamls) | `PCE-autonomy/pce_autonomy/web_adapters/` | [`pce_core/adapters/DEPRECATED.md`](pce_core/adapters/DEPRECATED.md) |
| `pce_core/standards/f1_*.md` (5 web mds) | `PCE-autonomy/pce_autonomy/web_standards/` | [`pce_core/standards/DEPRECATED.md`](pce_core/standards/DEPRECATED.md) |
| `pce_core/adapter_loader.py` | `PCE-autonomy/pce_autonomy/web_adapters/adapter_loader.py` | (see adapters banner) |
| `pce_browser_extension_wxt/` | `PCE-autonomy/pce_browser_extension_wxt/` | [`pce_browser_extension_wxt/DEPRECATED.md`](pce_browser_extension_wxt/DEPRECATED.md) |
| `pce_probe/` | `PCE-autonomy/pce_probe/` | [`pce_probe/DEPRECATED.md`](pce_probe/DEPRECATED.md) |
| `tests/e2e_probe/` | `PCE-autonomy/tests/e2e_probe/` | [`tests/e2e_probe/DEPRECATED.md`](tests/e2e_probe/DEPRECATED.md) |

## What did NOT move (stays in PCE Core; do not assume autonomy owns)

- `pce_core/capture_event.py` — schema (autonomy mirrors enums)
- `pce_core/normalizer/*.py` — cross-platform capture normalization
- `pce_core/models.py`, `pce_core/db.py` — storage layer
- `pce_core/capture_supervisor/` — health probes + dedup
- `pce_core/server.py` — FastAPI :9800
- `pce_mcp/` / `pce_mcp_proxy/` — capture-side MCP
- `pce_test_conductor/` — test runner (autonomy calls via MCP)
- `pce_app/`, `pce_app_launcher/`, `pce_cli_wrapper/` — desktop product
- `tests/e2e_desktop/`, `tests/e2e_desktop_ui/` — product-surface tests

## Deletion timeline

The deprecated directories are kept on disk for the current release-grace
window. Removal scheduled for the next minor (v1.1+) of PCE Core.

## VPS implications

The pce-autonomy NSSM service on the VPS already uses its own checkout
of PCE-autonomy as the sandbox base (per ADR-010 §3.3). PCE Core's
runtime services (capture API :9800, pce-probe-server :9888) continue
running from `C:\pce-runtime\pce-core\` and are unchanged.
