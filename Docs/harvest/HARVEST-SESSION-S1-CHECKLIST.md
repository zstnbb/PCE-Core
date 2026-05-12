---
title: "Harvest Session S1 — Owner Checklist"
scope: P3 Cursor + P4 Windsurf + P5 GitHub Copilot (VS Code)
duration_estimate: ~30 minutes
last_updated: 2026-05-13
---

# Harvest Session S1 — Owner Checklist

This is the numbered action list you follow DURING the harvest session.
Read once before starting so you know what's coming, then go top-to-bottom.

> **Goal**: capture 3-6 wire fixtures per app to ``raw_captures``,
> giving Cascade enough evidence to write proper normalizers + launchers
> for P3/P4/P5 offline (no further owner interaction needed).

---

## Pre-session — verify (5 min, done before timer starts)

- [ ] **Cursor** — open it once, confirm chat panel works (free tier ok)
- [ ] **Windsurf** — open it once, confirm Cascade panel works (free tier ok)
- [ ] **VS Code** — open it once, confirm Copilot icon is **active**
      (not "Sign in to Copilot")
- [ ] **No critical activity in flight** — payments / video calls /
      banking apps closed or paused. The harvest window will route ALL
      HTTPS traffic through mitmproxy for ~30 min.
- [ ] **PowerShell** — open a window at the repo root
      `F:\INVENTION\You.Inc\PCE Core`
- [ ] **Second terminal handy** — for the second harvester to run in parallel

---

## Phase 1: Setup (3 min)

### 1.1 Close all 3 target apps

Required for clean relaunch with `--remote-debugging-port`.

```powershell
Get-Process Cursor, Windsurf, Code -ErrorAction SilentlyContinue | Stop-Process -Force
```

(VS Code is "Code.exe" — kill if running, we'll restart later for Copilot harvest.)

### 1.2 Start the proxy chain

```powershell
.\scripts\harvest\setup_proxy_chain.ps1
```

**Expected output ends with:**

```
✔ Setup complete. State written to:
    F:\INVENTION\You.Inc\PCE Core\_harvest_state.json

System proxy now: 127.0.0.1:8080  (upstream: 127.0.0.1:7890)
```

If you see errors instead, **STOP** and ping Cascade — don't proceed.

### 1.3 Note the start time

```powershell
$harvestStart = (Get-Date).ToString("o")
$harvestStart  # save this — needed for verify
```

---

## Phase 2: Cursor harvest (8 min)

### 2.1 Launch the Cursor harvester (one PowerShell window)

```powershell
python scripts\harvest\harvest_cdp.py --app cursor --duration 480
```

The script will:
1. Kill any leftover Cursor.exe
2. Spawn Cursor with `--remote-debugging-port=9223`
3. Attach via Playwright
4. Start logging captures as you use Cursor

**Wait for the line:** `INFO: Attaching Playwright to http://127.0.0.1:9223`
before proceeding to 2.2.

### 2.2 In Cursor, do the following (in order)

These prompts are deliberately short to stay within free-tier quota.

- [ ] **Cursor chat** — open `Ctrl+L`, type **`What is 2+2?`** → wait for reply
- [ ] **Cursor chat** — type **`Write me a 1-line Python function that adds two numbers`** → wait for reply
- [ ] **Cursor tab completion** — open any code file, type `def hello():` then `<Enter>` and wait for the gray ghost-text suggestion to appear. Press `Tab` to accept.
- [ ] (optional) **Cursor composer** — `Ctrl+I` if you have it, type **`add a docstring`** → wait
- [ ] **Wait** ~10 seconds for any straggler network traffic to flush

### 2.3 End Cursor harvest

Back in the PowerShell window running `harvest_cdp.py --app cursor`:

- Press **Ctrl+C**

You should see:

```
INFO: HARVEST COMPLETE for Cursor
  matched:       X
  written:       Y (pairs)
  hosts:
    api2.cursor.sh                       N
    ...
```

**Target**: `written >= 3` pairs. If 0, something failed — note the
hosts seen and ping Cascade.

---

## Phase 3: Windsurf harvest (8 min)

### 3.1 Launch the Windsurf harvester (same or new PowerShell window)

```powershell
python scripts\harvest\harvest_cdp.py --app windsurf --duration 480
```

Wait for `INFO: Attaching Playwright to http://127.0.0.1:9224`.

### 3.2 In Windsurf, do the following

- [ ] **Cascade chat** — open Cascade panel, type **`What is 2+2?`** → wait
- [ ] **Cascade chat** — type **`List 3 sorting algorithms in 1 line each`** → wait
- [ ] **Tab completion** — same idea as Cursor: open a code file, type some code, accept a suggestion
- [ ] (optional) **Cascade @file** — if you have any open file, type **`@<filename> what does this do?`** → wait
- [ ] **Wait** ~10 seconds for flush

### 3.3 End Windsurf harvest

Press **Ctrl+C** in the harvester window.

**Target**: `written >= 3` pairs to `server.codeium.com` or similar.

---

## Phase 4: GitHub Copilot harvest (6 min)

P5 is **passive** — mitmdump captures Copilot traffic automatically
through the system proxy chain (Copilot doesn't use CDP, it just makes
HTTPS calls to `api.githubcopilot.com` and friends, which are already
on `ALLOWED_HOSTS`).

### 4.1 Start VS Code

```powershell
code   # or launch from start menu
```

### 4.2 In VS Code with Copilot

- [ ] **Copilot Chat** — `Ctrl+Alt+I` (or click Copilot icon), type **`What is 2+2?`** → wait
- [ ] **Copilot Chat** — type **`Explain Python list comprehensions in 1 line`** → wait
- [ ] **Inline completion** — open or create any `.py` file, type:
  ```python
  def fibonacci(n):
  ```
  Wait 1-2 seconds for the ghost suggestion. Press `Tab` to accept.
- [ ] (optional) **/explain** — select 3-5 lines of code in editor, right-click → "Copilot: Explain This"
- [ ] **Wait** ~10 seconds for flush

### 4.3 No script to stop — just close VS Code or move on

mitmdump is still capturing in the background through `setup_proxy_chain.ps1`.

---

## Phase 5: Verify (2 min)

```powershell
python scripts\harvest\verify_harvest.py --since $harvestStart
```

**Expected output** (rough shape — exact numbers will vary):

```
=== Harvest coverage report (since 2026-05-13T00:30:00) ===

Total captures: ~30-50

By source + app:
  cdp-embedded   cursor       12 captures (6 pairs)
  cdp-embedded   windsurf     10 captures (5 pairs)
  proxy          (none)       18 captures (9 pairs)

By host (top X):
  api2.cursor.sh              8  req=4 resp=4 ...
  server.codeium.com          6  req=3 resp=3 ...
  api.githubcopilot.com       4  req=2 resp=2 ...
  ...
```

**Acceptance criteria**:
- [ ] At least one `cdp-embedded` row with `app_name=cursor`
- [ ] At least one `cdp-embedded` row with `app_name=windsurf`
- [ ] At least one `proxy` row for `api.githubcopilot.com`
- [ ] All three have at least 1 paired req+resp (so `pairs >= 1`)

If any are 0, copy the verify output and ping Cascade.

---

## Phase 6: Teardown (1 min)

```powershell
.\scripts\harvest\teardown_proxy_chain.ps1
```

**Expected output ends with:** `✔ Teardown complete.`

Confirm system proxy is back to Clash:

```powershell
Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyServer
```

Should show `ProxyServer : 127.0.0.1:7890` (or whatever was there before).

---

## Phase 7: Hand back to Cascade

Just paste the `verify_harvest.py` output into chat. Cascade will:

1. Inspect what landed in `raw_captures`
2. Extract representative pairs to `tests/fixtures/cursor/`, `windsurf/`, `copilot/`
3. Build `pce_app_launcher/cursor/`, `pce_app_launcher/windsurf/` modules
4. Build `pce_core/normalizer/cursor.py`, `windsurf.py`, `copilot.py` assemblers (where needed)
5. Write regression tests
6. Update `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.3 / §4.4 / §4.5
7. Commit + push 3 commits (one per P-region)
8. Suggest a tag once all three are closed

You go to sleep. 😴

---

## Emergency teardown

If anything goes wrong (network breakage, can't access banking, etc.):

```powershell
.\scripts\harvest\teardown_proxy_chain.ps1 -Emergency
```

This force-restores `ProxyServer=127.0.0.1:7890` and kills all `mitmdump.exe`
processes, even if the state file is missing or stale.

If even THAT fails, manually:

```powershell
Set-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyServer -Value "127.0.0.1:7890"
Set-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyEnable -Value 1
Get-Process mitmdump -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## What can go wrong + how to recover

| Symptom | Likely cause | Fix |
|---|---|---|
| `setup_proxy_chain.ps1` says "Port 8080 already in use" | Stale mitmdump from previous session | `Get-Process mitmdump | Stop-Process -Force` then retry |
| `harvest_cdp.py` says "Could not find Cursor.exe" | Cursor installed in non-standard path | Edit `candidate_exe_paths` in `harvest_cdp.py` and add the real path |
| `harvest_cdp.py` says "CDP not ready" | App didn't open debug port, often because another instance was running | Run with `--no-kill-existing=false` (the default) — script will kill stragglers |
| Cursor/Windsurf says "Sign in expired" mid-session | mitmproxy intercepting auth cookies | Just sign in again — auth flows are part of what we want to capture |
| `verify_harvest.py` shows `Total: 0` | Either proxy chain not active OR apps didn't generate traffic | Re-check `setup_proxy_chain.ps1` ran successfully; ensure you actually pressed Enter in chat |
| Copilot gives "Quota exceeded" | You're near free-tier monthly limit | Use Cursor / Windsurf chats only; Copilot tab completions are usually under a separate quota |
