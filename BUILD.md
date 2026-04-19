# PCE – Build & Run Guide

## Quick Start (Development)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PCE as desktop app (system tray + dashboard)
python pce.py

# Or start just the core server
python -m pce_core
```

On first launch the dashboard auto-redirects to `/onboarding` — a 5-step
wizard that walks you through the certificate install, extension
sideload, system proxy, and privacy preferences. You can re-run it any
time via the tray menu ("Run Setup Wizard") or `POST /api/v1/onboarding/reset`.

## Running Modes

### Desktop App (Recommended)
```bash
python pce.py                    # System tray + core server + auto-open dashboard
python -m pce_app                # Same as above
python -m pce_app --all          # Start all services (core + proxy + local hook)
python -m pce_app --proxy        # Core + network proxy
python -m pce_app --local-hook   # Core + local model hook
python -m pce_app --no-tray      # Headless (no tray icon, Ctrl+C to stop)
python -m pce_app --no-browser   # Don't auto-open dashboard
```

### Individual Services
```bash
python -m pce_core               # Core API server only (port 9800)
python -m pce_mcp                # MCP server (stdio, for Claude Desktop/Cursor)
python -m pce_mcp --sse          # MCP server (SSE transport)
python -m pce_core.local_hook    # Local model hook (Ollama proxy)
mitmdump -s run_proxy.py         # Network proxy (mitmproxy)
```

## Desktop App Features (P3)

The tray menu exposes every user-facing action:

- **Open Dashboard** — `http://127.0.0.1:9800/`
- **Run Setup Wizard** — re-opens the first-run flow at `/onboarding`
- **Phoenix → Start / Stop** — boots `arize-phoenix` and auto-wires PCE's
  OTLP exporter at it (only visible if `pip install arize-phoenix` succeeded).
  See `pce_core/phoenix_integration.py`.
- **Collect Diagnostics…** — writes a redacted zip with config, health,
  schema, recent logs, and pipeline errors to `~/.pce/data/diagnose/`.
  Same bundle is served at `GET /api/v1/diagnose` for remote collection.
- **Check for Updates…** — polls the release manifest
  (`PCE_UPDATE_MANIFEST_URL`, falls back to the GitHub-hosted default)
  and reports whether a newer PCE is available.
- **Service start/stop** — core, proxy, local-hook, clipboard, …

All of these are also available through the REST API under
`/api/v1/onboarding/*`, `/api/v1/diagnose`, `/api/v1/phoenix/*`, and
`/api/v1/updates/check`.

### `pce diagnose` CLI

```bash
python -m pce_core.diagnose                  # write ~/.pce/data/diagnose/pce-diag-<ts>.zip
python -m pce_core.diagnose -o out.zip       # custom output path
python -m pce_core.diagnose --no-logs        # skip log-file scraping
python -m pce_core.diagnose --stdout         # stream bytes to stdout (for pipes)
```

Bundle contents (all redacted of secrets before zipping):
`summary.json`, `config.json`, `env.json`, `health.json`, `stats.json`,
`migrations.json`, `schema.sql`, `packages.txt`,
`pipeline_errors.jsonl`, `logs/*.log`.

## Optional Observability & Analytics (P4)

Every P4 feature is **opt-in** — PCE still runs with only the
dependencies in `requirements.txt`. Install the extras only if you
want a specific capability.

### P4.1 — Semantic search over messages

Brute-force cosine over float32 BLOBs works out of the box with no new
deps (`EmbeddingsBackend.hash`). Optional, higher-quality backends:

```bash
pip install sentence-transformers   # local on-device embeddings
# or
export OPENAI_API_KEY=sk-...         # activates the openai backend
```

UI & API:

- Dashboard "Search" tab has a `🔀 Semantic` toggle.
- `GET /api/v1/search?q=...&mode=semantic` — semantic mode
- `GET /api/v1/embeddings/status` — backend + model + vector count
- `POST /api/v1/embeddings/backfill?limit=500` — embed missing rows

Storage lives in the additive `message_embeddings` table created by
migration 0004. Dropping it removes the feature cleanly.

### P4.2 — DuckDB analytics

```bash
pip install duckdb                   # ~20 MB
```

DuckDB reads the same SQLite file as PCE's writer via the
`sqlite_scanner` extension — **no ETL, no data duplication**. Six
endpoints ship out of the box:

- `GET /api/v1/analytics/status`
- `GET /api/v1/analytics/timeseries?by=day&days=30[&provider=...]`
- `GET /api/v1/analytics/top_models?limit=10&days=30`
- `GET /api/v1/analytics/top_hosts?limit=10&days=30`
- `GET /api/v1/analytics/token_usage?by=day&days=30`
- `GET /api/v1/analytics/export.parquet?table={raw_captures|sessions|messages|message_embeddings}[&days=N]`

Without `duckdb` the endpoints return **503** with a structured
`{"error": "duckdb_not_installed"}` envelope so the UI can nudge the
user.

### P4.3 — OpenLLMetry / OTel GenAI semconv

No extra dep — the attribute mapper in
`pce_core/normalizer/genai_semconv.py` runs every time a pair span is
emitted through the OTLP exporter. Behaviour is controlled by the
**already-honoured** OTLP env vars plus:

```bash
export PCE_OTEL_GENAI_SEMCONV=both   # default — OI + gen_ai (broadest)
export PCE_OTEL_GENAI_SEMCONV=only   # gen_ai only (OTel-native sinks)
export PCE_OTEL_GENAI_SEMCONV=off    # legacy OI-only
```

This makes PCE spans readable by Phoenix (OpenInference),
Traceloop / Grafana LLM panel / Datadog (`gen_ai.*`) and
OpenLLMetry (`llm.usage.prompt_tokens`) from the same capture without
reconfiguration.

### P4.4 — Embedded CDP browser

```bash
pip install playwright
python -m playwright install chromium  # one-time
```

CLI:

```bash
python -m pce_core.cdp                 # headful Chromium, Ctrl-C to stop
python -m pce_core.cdp --headless      # CI / kiosk mode
python -m pce_core.cdp --probe-only    # just report backend availability
```

HTTP:

- `GET /api/v1/cdp/status`
- `POST /api/v1/cdp/start?start_url=...&headless=false`
- `POST /api/v1/cdp/stop`

Matched responses go into `raw_captures` with `source_id = 'cdp-embedded'`
(registered by migration 0005, no schema change needed on existing
installs). Headers are redacted by the same `pce_core.redact.redact_headers`
pipeline mitmproxy uses.

### P4.5 — Mobile capture onboarding

See `Docs/docs/engineering/MOBILE-CAPTURE.md` for the full iOS / Android
walkthrough.

```bash
python -m pce_core.mobile_wizard                      # ASCII QR + instructions
python -m pce_core.mobile_wizard --json               # machine-readable
python -m pce_core.mobile_wizard --png ~/setup.png    # needs qrcode[pil]
```

Optional: `pip install qrcode[pil]` to unlock PNG rendering. The ASCII
path works with the zero-dep fallback.

HTTP:

- `GET /api/v1/mobile_wizard/info`
- `GET /api/v1/mobile_wizard/qr.txt`
- `GET /api/v1/mobile_wizard/qr.png`  *(503 without `qrcode[pil]`)*

## Building Executable (cross-platform, P5.A-11)

The `pce.spec` PyInstaller spec now produces a platform-correct bundle
from the same source on all three OSes — no manual tweaking required.

```bash
# One-time per machine
pip install -r requirements.txt
pip install pyinstaller>=6.0

# Build (picks the right flags based on sys.platform)
pyinstaller --clean pce.spec
```

Outputs:

| Platform | Artifact | Launch |
|---|---|---|
| Windows | `dist/PCE/PCE.exe` | Double-click (tray app, `console=False`) |
| macOS   | `dist/PCE.app`     | Drag to /Applications, tray-only via `LSUIElement` |
| Linux   | `dist/PCE/PCE`     | `./PCE` from terminal (console stays visible) |

The bundle ships `mitmproxy` (~90 MB) so L1 network capture works out
of the box. Icons are optional — drop `assets/icons/pce.ico` (Win) or
`assets/icons/pce.icns` (macOS) before building to embed them; a
missing file just yields a no-icon build instead of failing.

### Post-build self-check

```bash
python scripts/verify_build.py --platform Windows   # or macOS / Linux
```

Verifies: executable exists + is the right size, dashboard + migrations
are bundled under `_internal/`, and on Windows/Linux the binary can
launch without an import-error exit. Returns non-zero on any failure,
so CI gates artifact upload on it.

### Release pipeline

Tag-triggered GitHub Actions workflow at `.github/workflows/release.yml`:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

That produces a draft GitHub Release with five assets:

- `PCE-v1.0.0-windows-x64.zip`
- `PCE-v1.0.0-macos-universal.zip`
- `PCE-v1.0.0-linux-x64.tar.gz`
- `pce-extension-chrome-v1.0.0.zip` (webstore mode, explicit host_permissions)
- `pce-extension-firefox-v1.0.0.zip`

Pre-release naming (`v1.0.0-rc1`, `v1.0.0-beta`) automatically flips
the release to `prerelease: true`. Gate jobs (import-direction, SPDX,
full non-e2e pytest) run first so a tainted commit can't ship.

`workflow_dispatch` runs skip the release-creation step and just
upload artifacts to the workflow run — useful for validating the
pipeline between tags without burning a version bump.

## Building Native Installer (Tauri, cross-platform)

The P3 Tauri shell wraps the Python core in a native WebView with a
system tray, native updater, and single-instance guard. Target bundle
size is ~50 MB per platform.

```bash
# One-time: install Rust toolchain (https://rustup.rs) and Tauri CLI v2.
cargo install tauri-cli --version "^2"

# One-time: generate app/tray icons (see pce_app/tauri/icons/README.md).
cd pce_app/tauri
cargo tauri icon path/to/source-logo.png --output icons/

# Dev loop — launches the Python core + native WebView:
cargo tauri dev

# Release build:
cargo tauri build
# → target/release/bundle/{msi,nsis,dmg,deb,appimage}/
```

See `pce_app/tauri/README.md` for the full architecture, platform
prerequisites, Python sidecar bundling via PyInstaller, and updater
signing.

## Browser Extension

The extension is built by [WXT](https://wxt.dev/) from TypeScript
entrypoints under `pce_browser_extension_wxt/entrypoints/`. Build
first, then load the bundle:

```bash
cd pce_browser_extension_wxt
pnpm install       # first time only
pnpm build         # Chrome MV3 output → .output/chrome-mv3/
pnpm build:firefox # Firefox MV3 output → .output/firefox-mv3/
```

Then load in Chrome:

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked** → select
   `pce_browser_extension_wxt/.output/chrome-mv3/`

Firefox: use `about:debugging` → "Load Temporary Add-on" and point
at the `.output/firefox-mv3/manifest.json`.

## MCP Integration (Claude Desktop / Cursor)

Add to your MCP config (e.g. `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "/path/to/PCE Core"
    }
  }
}
```

## Running Tests

```bash
python tests/test_smoke.py
python tests/test_core_db.py
python tests/test_core_api.py
python tests/test_normalizer.py
python tests/test_mcp.py
python tests/test_local_hook.py
```

## Project Structure

```
PCE Core/
├── pce.py                     # One-click launcher
├── pce.spec                   # PyInstaller build spec
├── pce_app/                   # Desktop app (tray + service manager)
├── pce_core/                  # Core: config, db, API server, normalizer, dashboard
│   ├── dashboard/             # Web UI (served at http://127.0.0.1:9800)
│   ├── normalizer/            # OpenAI + Anthropic auto-normalizer
│   └── local_hook/            # Local model capture proxy
├── pce_proxy/                 # mitmproxy addon
├── pce_mcp/                   # MCP server (6 tools)
├── pce_browser_extension_wxt/ # Chrome/Firefox MV3 (WXT + TypeScript)
└── tests/                     # 7 test suites
```
