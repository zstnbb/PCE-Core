# PCE × Windsurf — Installation Guide

> **Capture method**: L1 TLS MITM with NODE_EXTRA_CA_CERTS injection
> **Coverage**: Full Cascade chat (user prompts, assistant thinking, responses, model names)
> **Requirement**: Windsurf must be launched from a terminal with the env vars set

---

## Prerequisites

1. PCE Core installed and working (`python -m pce_core` starts without errors)
2. mitmproxy CA certificate generated (`~/.mitmproxy/mitmproxy-ca-cert.pem` exists)
3. Windsurf installed (tested with v1.48.2+)
4. An upstream proxy for internet access (e.g. Clash on port 7890, or direct)

---

## Setup (one-time)

### 1. Note your mitmproxy CA cert path

```
Windows: C:\Users\<YOU>\.mitmproxy\mitmproxy-ca-cert.pem
macOS:   ~/.mitmproxy/mitmproxy-ca-cert.pem
Linux:   ~/.mitmproxy/mitmproxy-ca-cert.pem
```

If the file doesn't exist, run `mitmdump` once and it will auto-generate.

### 2. Create a Windsurf launch script

**Windows (PowerShell)** — save as `Start-Windsurf-PCE.ps1`:

```powershell
# PCE-aware Windsurf launcher
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
$env:https_proxy = "http://127.0.0.1:8080"
$env:http_proxy = "http://127.0.0.1:8080"

& "$env:LOCALAPPDATA\Programs\Windsurf\Windsurf.exe"
```

**macOS / Linux** — save as `start-windsurf-pce.sh`:

```bash
#!/bin/bash
export NODE_EXTRA_CA_CERTS="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
export https_proxy="http://127.0.0.1:8080"
export http_proxy="http://127.0.0.1:8080"

# macOS
open -a Windsurf
# Linux
windsurf
```

---

## Daily Usage

### Step 1: Start PCE proxy

```bash
cd /path/to/PCE-Core

# Direct internet (no upstream proxy):
mitmdump --listen-port 8080 -s run_proxy.py

# With upstream proxy (e.g. Clash on 7890):
mitmdump --mode upstream:http://127.0.0.1:7890 --listen-port 8080 -s run_proxy.py
```

### Step 2: Launch Windsurf via the PCE script

```powershell
.\Start-Windsurf-PCE.ps1
```

### Step 3: Use Cascade normally

All Cascade conversations are automatically captured. Check the PCE dashboard at `http://127.0.0.1:9800/` to see your conversations.

---

## What gets captured

| Data | Source endpoint | Example |
|---|---|---|
| User prompts | `RecordCortexGeneratorMetadata` | "What is 2+2?" |
| Assistant thinking | `RecordCortexTrajectoryStep` | "The user is asking..." |
| Model name | Multiple endpoints | `swe-1-6-slow` |
| Trajectory ID | Multiple endpoints | UUID (session grouping) |
| User identity | `GetUserStatus` | email, plan tier |
| Available models | `GetCliTeamSettings` | model list |

---

## Troubleshooting

### Windsurf shows "Failed to connect" or blank Cascade panel

- Verify mitmproxy is running on port 8080
- Verify the upstream proxy (if any) is accessible
- Check `NODE_EXTRA_CA_CERTS` points to an existing file

### Captures appear but no messages in dashboard

- The normalizer needs the `RecordCortexTrajectoryStep` or `RecordCortexGeneratorMetadata` endpoints
- Check PCE logs for normalizer errors: `logs/pce_core_stderr.log`

### Windsurf works but no captures at all

- Windsurf may not be picking up the env vars
- Ensure you launched Windsurf from the same terminal where env vars are set
- On Windows, `Start-Process` inherits the current env; double-clicking the .exe does NOT

### Rate limit errors in Cascade

- This is a Windsurf/Codeium issue, not PCE-related
- PCE is passive (record-not-intervention) — it never modifies requests

---

## Security Notes

- `NODE_EXTRA_CA_CERTS` adds the mitmproxy CA to Node.js's trust store
- This allows PCE to decrypt Windsurf's HTTPS traffic locally
- No data leaves your machine — everything stays in `~/.pce/data/pce.db`
- To disable capture, simply launch Windsurf without the env vars
