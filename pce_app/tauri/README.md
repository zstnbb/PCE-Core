# PCE Desktop — Tauri Shell

A thin native shell (Windows / macOS / Linux) that wraps the existing
Python core (`pce_app`, `pce_core`) in a single installable app. Target
bundle size: **~50 MB** after strip + UPX.

## Architecture

```
  ┌───────────────────────────────┐
  │         Tauri (Rust)          │
  │  ─ native WebView window      │
  │  ─ native tray icon + menu    │
  │  ─ updater + single-instance  │
  │  ─ IPC commands → HTTP        │
  └─────────────┬─────────────────┘
                │ spawns at startup
                ▼
  ┌───────────────────────────────┐
  │  Python core (pce_app)        │
  │  python -m pce_app            │
  │    --no-tray --no-browser     │
  │                               │
  │  FastAPI on :9800             │
  │    /                 → redirect→/onboarding on first run
  │    /onboarding       → wizard HTML
  │    /dashboard        → dashboard HTML
  │    /api/v1/*         → ingest + query + control
  └───────────────────────────────┘
```

The Rust side is **intentionally tiny** — all business logic stays in
Python where it's been tested (174+ tests). Commands in
`src/commands.rs` forward to HTTP endpoints so adding a new user action
means touching `pce_core/server.py` + the WebView JS, **not** Rust.

## Prerequisites

1. **Rust toolchain** (≥ 1.70) — https://rustup.rs
2. **Node.js** is NOT required (our frontend is pure HTML/JS served by
   the Python core; Tauri only needs it if you add a JS build step).
3. **Tauri CLI v2**:

   ```bash
   cargo install tauri-cli --version "^2"
   ```

4. **Platform build deps** — see the [Tauri v2
   prerequisites](https://v2.tauri.app/start/prerequisites/). Short list:

   - **Windows**: MSVC toolchain (included with Visual Studio Build Tools),
     WebView2 runtime (pre-installed on Win11).
   - **macOS**: Xcode command-line tools.
   - **Linux**: `libwebkit2gtk-4.1-dev`, `libappindicator3-dev`,
     `librsvg2-dev`, `patchelf`.

## Quick start (dev)

```bash
# From the repo root:
cd pce_app/tauri

# Generate icon assets (one-time setup — see icons/README.md):
cargo tauri icon path/to/source-logo.png --output icons/

# Run the shell + auto-reload the Python core when code changes:
cargo tauri dev
```

`cargo tauri dev` will:

1. Run the `beforeDevCommand` from `tauri.conf.json`
   (`python -m pce_app --no-tray --no-browser`).
2. Wait for `/api/v1/health` to 200.
3. Open the native WebView pointing at `http://127.0.0.1:9800/`.

Press **F12** to open devtools inside the WebView just like Chrome.

## Release build

```bash
cd pce_app/tauri
cargo tauri build
```

Output paths:

| Platform  | Bundle                                              |
|-----------|-----------------------------------------------------|
| Windows   | `target/release/bundle/nsis/*.exe` + `msi/*.msi`    |
| macOS     | `target/release/bundle/dmg/*.dmg` + `macos/*.app`   |
| Linux     | `target/release/bundle/deb/*.deb` + `appimage/*.AppImage` |

### Bundling the Python core

The release build expects a standalone Python binary named
`binaries/pce-core` (or `.exe` on Windows) next to the Tauri output — see
`bundle.externalBin` in `tauri.conf.json`. Produce one with PyInstaller:

```bash
# From the repo root:
pyinstaller --name pce-core --onefile --console pce_app/__main__.py

# Then copy into the Tauri project:
cp dist/pce-core(.exe) pce_app/tauri/binaries/
```

Until you do that, Tauri's discovery falls back to `python -m pce_app`
(see `resolve_invocation` in `src/sidecar.rs`) which works for dev
machines that already have Python on PATH but is not suitable for a
shippable installer.

### Updater signing

`tauri.conf.json` points the updater at
`https://raw.githubusercontent.com/zstnbb/PCE-Core/main/releases/manifest.json`.
Before shipping a build, generate a keypair and replace `pubkey`:

```bash
cargo tauri signer generate -w ~/.tauri/pce.key
```

Keep the private key out of the repo. CI signs the payload; the shell
verifies.

## Testing

The Rust side has no unit tests yet — it's intentionally ~200 lines of
glue. Integration is exercised manually via `cargo tauri dev` plus the
Python test suite (`pytest tests/`).

## File map

| File                              | Purpose                               |
|-----------------------------------|---------------------------------------|
| `Cargo.toml`                      | Rust deps (tauri, reqwest, tokio, …)  |
| `tauri.conf.json`                 | Window / tray / bundle / updater config |
| `build.rs`                        | `tauri_build::build()`                |
| `src/main.rs`                     | CLI entry — calls `pce_desktop_lib::run()` |
| `src/lib.rs`                      | Tauri setup + sidecar boot + tray wiring |
| `src/sidecar.rs`                  | Python process supervision + health poll |
| `src/commands.rs`                 | IPC commands invoked from tray / UI   |
| `src/tray.rs`                     | Native tray menu + click handlers     |
| `capabilities/default.json`       | Tauri v2 permissions (main window)    |
| `icons/`                          | App / tray icons (generated, not in git) |
