---
title: "Harvest Session S2 — Retry Checklist (post-S1 fixes)"
scope: P3 Cursor + P4 Windsurf + P5 Copilot
duration_estimate: ~20 minutes
last_updated: 2026-05-13
predecessor: HARVEST-SESSION-S1-DISCOVERIES.md
---

# Harvest Session S2 — Retry Checklist

This is the second pass after S1 was aborted at 01:25. S1 surfaced
six architectural findings; tonight's code changes ship the fixes
for the three most critical ones:

| S1 finding | Fix shipped |
|---|---|
| F1 — missing 11 Cursor domains | Added to `pce_core/config.py` `ALLOWED_HOSTS` (commit pending) |
| F3 — mitmproxy buffer breaks SSE | Per-host streaming addon in `pce_proxy/addon.py` (12 tests) |
| F5 — extension host bypasses --proxy-server | Auto-patch `settings.json` with `http.proxy` (auto-restored on exit) |

S2 is therefore **much shorter than S1**: no manual settings clicks,
no HTTP/1.1 switch (the per-host streaming addon fixes the SSE issue
without forcing HTTP/1.1), and Windsurf is no longer at risk because
non-listed hosts default to buffered behaviour.

> **Pre-flight reminder**: Windsurf is the IDE this Cascade chat runs
> in. We do **Windsurf LAST** so the harvester killing it doesn't
> disrupt this session. Owner does Windsurf harvest themselves with a
> one-line command after Cursor + Copilot are confirmed working.

---

## Pre-session — verify (3 min)

- [ ] `git status` shows clean working tree (or only this checklist's commit pending)
- [ ] You can read this file from inside Cascade's chat panel (you're using Windsurf right now)
- [ ] Cursor is not currently open (kill if it is — we relaunch fresh below)
- [ ] VS Code is not currently open
- [ ] PowerShell at the repo root

---

## Phase 1 — Setup (~2 min)

### 1.1 Run setup_proxy_chain (only switches HKCU proxy + starts mitmdump)

```powershell
$harvestStart = (Get-Date).ToString("o")
$harvestStart  # save for verify
.\scripts\harvest\setup_proxy_chain.ps1
```

Expected end of output:

```
✔ Setup complete. State written to _harvest_state.json
System proxy now: 127.0.0.1:8080  (upstream: 127.0.0.1:7890)
```

Mitmdump now listens on :8080, forwards upstream to Clash 7890.
Per-host SSE streaming is built into the addon (no `stream_large_bodies`
flag needed).

### 1.2 Quick sanity

```powershell
.\scripts\harvest\verify_harvest.py --since-minutes 1
```

Should show 0 captures (nothing happened yet — this confirms the
pipeline runs cleanly with empty DB).

---

## Phase 2 — Cursor harvest (~5 min)

The harvester auto-patches `~/AppData/Roaming/Cursor/User/settings.json`
to set `http.proxy=http://127.0.0.1:8080` + `http.proxySupport=on`,
restores it on exit. You don't touch Cursor settings.

### 2.1 Launch

```powershell
python scripts\harvest\harvest_cdp.py --app cursor --duration 240
```

Wait for these log lines (~5 sec):

```
INFO: patched Cursor settings.json: prior={...} new={'http.proxy': 'http://127.0.0.1:8080', ...}
INFO: spawned PID=..., debug_port=9223, proxy_server=127.0.0.1:8080
INFO: CDP ready: Browser=Chrome/142.x
INFO: Attaching Playwright to http://127.0.0.1:9223 — patterns: ...
INFO: Now use Cursor normally. Press Ctrl+C when done, or wait 240s.
```

### 2.2 In Cursor

- [ ] **`Ctrl+L`** open chat panel
- [ ] Type **`What is 2+2?`** → wait for reply
- [ ] Type **`Write me a 1-line Python function that adds two numbers`** → wait
- [ ] (optional, free-tier safe) Tab completion: type `def hello():` in a code file, press Tab on the ghost suggestion

### 2.3 End harvest

Press **Ctrl+C** in the harvester window. You should see:

```
INFO: HARVEST COMPLETE for Cursor
  matched:       N
  written:       M (pairs)
  hosts:
    agent.api5.cursor.sh                 N    ← THIS IS THE CHAT — must be > 0
    api2.cursor.sh                       N
    api3.cursor.sh                       N (if you used tab completion)
INFO: settings restore for Cursor: restored
```

**Acceptance**: `written >= 2` AND `agent.api5.cursor.sh` appears with `N >= 1`.

If `agent.api5.cursor.sh` is 0 again, the extension-host bypass is
deeper than `http.proxy` reaches. STOP, run teardown (`Phase 4`),
and ping Cascade — we'll need to look at the L3d CDP approach.

### 2.4 Quick verify

```powershell
python scripts\harvest\verify_harvest.py --since $harvestStart --top-hosts 5
```

Confirm cursor agent rows landed.

---

## Phase 3 — VS Code Copilot harvest (~5 min)

Copilot is passive (no spawn), but **its Extension Host also needs the
http.proxy setting** or it'll bypass mitmdump same as Cursor's main process.

### 3.1 Patch VS Code settings (one-liner)

```powershell
python scripts\harvest\patch_ide_settings.py --app vscode --proxy http://127.0.0.1:8080
```

This backs up `%APPDATA%\Code\User\settings.json` and adds the proxy block.

### 3.2 Launch VS Code

```powershell
code
```

(Or click Start Menu shortcut. **Don't** open VS Code via "Reuse Window"
in an already-open instance — needs to read the new settings.json.)

### 3.3 In VS Code

- [ ] **`Ctrl+Alt+I`** (or Copilot icon) open Copilot Chat
- [ ] Type **`What is 2+2?`** → wait
- [ ] Type **`Explain Python list comprehensions in 1 line`** → wait
- [ ] (optional) Open any `.py` file, type `def fibonacci(n):` and wait 1-2 sec for ghost completion. Tab to accept.

### 3.4 Close VS Code

`File > Exit` or click ×. Doesn't matter — capture happens passively in mitmdump.

### 3.5 Restore VS Code settings

```powershell
python scripts\harvest\patch_ide_settings.py --app vscode --restore
```

### 3.6 Quick verify

```powershell
python scripts\harvest\verify_harvest.py --since $harvestStart --top-hosts 5
```

Should now see:
- `api.githubcopilot.com` rows (Copilot chat)
- `copilot-proxy.githubusercontent.com` rows (inline completions)

---

## Phase 4 — Windsurf harvest (~5 min, LAST)

> **⚠ Reading this aloud**: the harvester WILL kill the Windsurf
> instance you're currently chatting in. This chat session may pause
> or appear to disconnect. After Windsurf relaunches and you finish
> step 4.2, your Cascade chat history should reconnect from the
> persistent server-side state.

### 4.1 Save anything you've been editing

Just `Ctrl+S` on any open files in Windsurf. We're about to relaunch it.

### 4.2 Run harvester (ONE command, ~3 minutes)

Copy this into Windows Terminal / PowerShell **before** continuing
(once Windsurf is killed you won't be able to copy from this chat):

```powershell
python scripts\harvest\harvest_cdp.py --app windsurf --duration 180
```

Then quickly:
- Open the new Windsurf window's Cascade panel
- Type **`What is 2+2?`** → wait for reply
- Type **`List 3 sorting algorithms`** → wait
- (optional) Tab completion in a code file
- **Ctrl+C** in the PowerShell to end

If everything went right, the output ends with:

```
INFO: HARVEST COMPLETE for Windsurf
  hosts:
    server.codeium.com                   N    ← chat / completion
    inference.codeium.com                N    ← inference
    api.codeium.com                      N    ← REST
INFO: settings restore for Windsurf: restored
```

### 4.3 Verify (you can now resume this chat)

```powershell
python scripts\harvest\verify_harvest.py --since $harvestStart
```

All three apps should show in the output:

```
By source + app:
  cdp-embedded   cursor       N captures (M pairs)
  cdp-embedded   windsurf     N captures (M pairs)
  proxy          (none)       N captures (M pairs)
By host:
  agent.api5.cursor.sh          ✓
  api2.cursor.sh                ✓
  server.codeium.com            ✓
  api.githubcopilot.com         ✓
  ...
```

---

## Phase 5 — Teardown (~1 min)

```powershell
.\scripts\harvest\teardown_proxy_chain.ps1
```

Restores HKCU proxy to Clash 7890, stops mitmdump.

If anything went sideways and settings.json is stuck patched:

```powershell
python scripts\harvest\patch_ide_settings.py --app all --restore
```

This is idempotent — safe to run even if nothing's patched.

---

## Phase 6 — Hand off to Cascade

```powershell
python scripts\harvest\verify_harvest.py --since $harvestStart --json | clip
```

Paste into chat. Cascade will:
1. Extract fixtures per app
2. Build normalizers + launchers
3. Commit + push 3 commits (P3 P4 P5)
4. Suggest a tag

---

## Emergency teardown (if anything goes wrong)

```powershell
.\scripts\harvest\teardown_proxy_chain.ps1 -Emergency
python scripts\harvest\patch_ide_settings.py --app all --restore
```

This force-restores HKCU proxy + every IDE settings.json regardless
of state file presence.

---

## What changed since S1 (commit references)

- **Per-host streaming**: `pce_proxy/addon.py` — adds `responseheaders`
  hook + `_should_stream` policy. 12 new unit tests in
  `tests/test_proxy_layer.py`.
- **Settings auto-patch**: `scripts/harvest/patch_ide_settings.py` —
  patches/restores `~/AppData/Roaming/<App>/User/settings.json`.
  Handles JSONC (comments) gracefully. Atomic writes.
- **harvest_cdp.py integration**: calls patch_app before spawn,
  registers atexit restore. Add `--no-patch-settings` to skip.
- **Setup script bug fix** (from S1): paths-with-spaces quoting.

## Acceptance criteria for S2 (must all be true)

1. At least 1 pair to `agent.api5.cursor.sh` (Cursor chat)
2. At least 1 pair to `server.codeium.com` (Windsurf chat/completion)
3. At least 1 pair to `api.githubcopilot.com` (Copilot chat)
4. No "incomplete envelope" errors from Cascade during the session
5. All three IDE settings.json files restored to pre-S2 content
