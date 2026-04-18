# Icon assets

Tauri's bundler expects the following files in this directory before
`cargo tauri build` will succeed. They are intentionally **not** checked
in because (a) they're binary and bloat the repo, and (b) the project
identity may change.

## Required files

| File               | Purpose                                   | Size      |
|--------------------|-------------------------------------------|-----------|
| `32x32.png`        | Linux / generic tray icon                 | 32×32     |
| `128x128.png`      | Linux app icon                            | 128×128   |
| `128x128@2x.png`   | Linux HiDPI                               | 256×256   |
| `icon.icns`        | macOS bundle                              | multi     |
| `icon.ico`         | Windows bundle + NSIS installer           | multi     |
| `icon.png`         | Tray icon (used by `tauri.conf.json`)     | 512×512   |

## Generating them

The easiest path is the Tauri CLI's built-in icon generator:

```bash
# From a single 1024×1024 PNG source (white-on-transparent logo works best):
cd pce_app/tauri
cargo tauri icon path/to/source-logo.png --output icons/
```

Until someone runs that command the bundler will fail with a clear
"icon not found" error — that's intentional, not a bug.

## Fallback during dev

`cargo tauri dev` doesn't require the bundle icons. The WebView uses the
system default icon and the tray falls back to a blank square when
`tray_icon_image()` returns `None`.
