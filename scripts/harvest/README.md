# Harvest Scripts — One-shot data collection for P-region onboarding

Purpose: collect real wire fixtures from multiple target apps in a single
30-90 min session so subsequent normalizer / assembler / launcher work
can iterate fully offline. See `PCE-STANDARD-WORKFLOW.md` §7 (Stage 4
verification) for the methodology.

## Scripts

| File | Purpose |
|---|---|
| `setup_proxy_chain.ps1` | Start mitmdump on :8080 in upstream mode (upstream = existing system proxy, typically Clash on :7890) and switch HKCU system proxy to :8080. Saves the original ProxyServer value to a state file for rollback. |
| `teardown_proxy_chain.ps1` | Symmetric — restores original system proxy, stops mitmdump. Has an "emergency" mode for when state file is missing (manual fallback). |
| `harvest_cdp.py` | For CDP-capturable apps (Cursor / Windsurf / any Electron app with `--remote-debugging-port`). Detects install path → spawns with debug port → Playwright `connect_over_cdp` → saves request/response pairs direct to `~/.pce/data/pce.db` via `pce_core.db.insert_capture`. |
| `verify_harvest.py` | Scans DB for captures since a given ISO timestamp. Generates per-host coverage matrix (request/response counts, min/max body len, sample path). |

## Owner-facing checklist

See `Docs/harvest/HARVEST-SESSION-S1-CHECKLIST.md` for the numbered
action list to follow DURING the session.

## Output shape

- **mitmdump path** (P5 Copilot): captures land in `raw_captures` table,
  host filtered by `pce_core.config.ALLOWED_HOSTS`.
- **CDP path** (P3 Cursor, P4 Windsurf): captures also land in
  `raw_captures` with `source_id=SOURCE_CDP`, filtered by URL pattern
  passed at script invocation.
- Post-session, `verify_harvest.py` prints coverage summary. Owner
  shares the coverage block with Cascade for offline closure planning.

## Safety

- `setup_proxy_chain.ps1` always writes `_harvest_state.json` with the
  original `ProxyServer` + `ProxyEnable` values before mutating.
  `teardown_proxy_chain.ps1` reads this to restore.
- If state file is missing, `teardown_proxy_chain.ps1 -Emergency`
  forces `ProxyServer=127.0.0.1:7890` + `ProxyEnable=1` (Clash default).
  Override with `-FallbackProxy "..."` if your setup differs.
- `harvest_cdp.py` uses a SIGINT handler — Ctrl+C closes the Playwright
  browser connection cleanly + prints capture stats.
- mitmdump runs in `--mode upstream:<current_proxy>` so apps that
  require the VPN/Clash exit still work during the harvest window.

## Scope-specific invocations

### S1 harvest (P3 + P4 + P5, ~30 min)

```powershell
# 0. setup
.\scripts\harvest\setup_proxy_chain.ps1

# 1. Cursor (in one terminal)
python scripts\harvest\harvest_cdp.py --app cursor --duration 600

# 2. Windsurf (in another terminal or sequentially)
python scripts\harvest\harvest_cdp.py --app windsurf --duration 600

# 3. Copilot: just use VS Code normally — mitmdump captures passively.

# 4. verify
python scripts\harvest\verify_harvest.py --since "2026-05-13T00:00:00"

# 5. teardown
.\scripts\harvest\teardown_proxy_chain.ps1
```

### Single-app harvest

```powershell
python scripts\harvest\harvest_cdp.py --app cursor --duration 300
```

No proxy chain setup needed if you only want CDP-based capture
(Cursor / Windsurf). The proxy chain is only needed for the mitmproxy
path (Copilot / ChatGPT Desktop / Claude Web, etc.).
