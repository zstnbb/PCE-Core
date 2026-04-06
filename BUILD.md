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

## Building Executable (Windows)

```bash
# Install PyInstaller
pip install pyinstaller

# Build
pyinstaller pce.spec

# Output: dist/PCE/PCE.exe
```

The built `dist/PCE/` folder contains everything needed to run PCE
without Python installed. Double-click `PCE.exe` to start.

## Browser Extension

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked** → select `pce_browser_extension/`

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
├── pce_browser_extension/     # Chrome Manifest V3
└── tests/                     # 7 test suites
```
