---
title: "D-Day — Owner-at-keyboard mini-actions (close last 2 gaps)"
status: TODO
date: 2026-05-18
session: D-Day
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
canonical: Docs/stability/REDUNDANCY-AUDIT-MATRIX.md §4.1.0
estimated_owner_time: 5–10 min total
unlocks:
  - f5_p5_github_copilot: 2 → 3 ✅
  - f6_p7_codex_cli: 2 → 3 ✅
---

# D-Day Owner Actions — 5-10 min to close 7/8 desktop strict ≥3

> The aggregate evidence dump (commit `d741d83`) closed 5/8 desktop
> scenarios to STRICT ≥3 V-GREEN. Two scenarios need ~2 min of
> owner-at-keyboard work each to flip the last leg from "evidence
> aged out of window" to "fresh live row".

## Pre-flight (one-time, ~2 min)

```powershell
# 1. Confirm mitmproxy on 8080 (already running for B1 testing)
curl -x http://127.0.0.1:8080 https://api.anthropic.com/ -k 2>&1 | head -3
# Expect: "Anthropic" or 401 — anything that proves the proxy answered

# 2. Confirm SSLKEYLOG daemon active
python -m pce_sslkeylog stats --limit 3
# Expect: 3 recent rows OR empty (both fine; we'll add fresh ones)

# 3. Enable the B1 env triggers for this shell
$env:HTTPS_PROXY            = "http://127.0.0.1:8080"
$env:HTTP_PROXY             = "http://127.0.0.1:8080"
$env:PCE_CLI_WRAPPER_PROXY  = "http://127.0.0.1:8080"
$env:SSLKEYLOGFILE          = "$env:USERPROFILE\.pce\sslkeys.log"
```

## Action 1 — Close `f6_p7_codex_cli` L3g gap (~2 min)

Codex L3g was last written >43h ago. Just run codex once and the
scanner picks up the new session file.

```powershell
codex exec "say four"
# (or any 1-2 turn prompt — content doesn't matter)

# Rescan ~/.codex/sessions/*.jsonl
python -m pce_persistence_watcher.ide_scanner scan --emit-beacon

# Verify
python tools/dump_evidence.py --scenario f6_p7 --window-s 3600 --out /tmp/post_codex
# Expect: legs=[L1+L3g+L3h]
```

## Action 2 — Close `f5_p5_github_copilot` A2 gap (~3 min)

Copilot A2 leg needs the sslkeylog daemon to capture VS Code's TLS
session keys during a Copilot Chat. The daemon is already running;
just fire one Copilot Chat request.

```powershell
# 1. Open VS Code (any project)
code .

# 2. Open Copilot Chat (Ctrl+Shift+I or Ctrl+Alt+I)
#    Fire one short prompt: "say four"

# 3. Wait ~10s for daemon to flush + scanner pickup

# 4. Verify
python tools/dump_evidence.py --scenario f5_p5 --window-s 600 --out /tmp/post_copilot
# Expect: legs=[L1+L3f+A2]
```

If A2 still missing after the run, the keylog file may not have grown
during VS Code's session — VS Code spawns child processes for the
Copilot extension host. Confirm:

```powershell
# Check sslkeys.log grew
$before = (Get-Item $env:SSLKEYLOGFILE).Length
# … run Copilot prompt …
$after = (Get-Item $env:SSLKEYLOGFILE).Length
"keylog grew by $($after - $before) bytes"
```

If grew = 0, set the env for VS Code's child processes — close VS Code
fully, then relaunch from a shell that has the SSLKEYLOGFILE env set
(your current shell after the pre-flight does).

## Action 3 — Confirmation re-dump (~1 min, agent runs)

After Actions 1+2 complete, ping the agent to run:

```powershell
python tools/dump_evidence.py \
    --scenario f4_p1,f4_p2,f5_p3,f5_p4,f5_p5,f6_p6,f6_p7,f6_p8 \
    --window-s 3600 \
    --out Docs/handoff/_evidence_D_DAY_postowner \
    --block B11-postowner
```

Expected EOD state:

| Scenario | Pre-actions | After Actions 1+2 |
|---|:-:|:-:|
| f5_p5_github_copilot | 2/3 | **3/3** ✅ |
| f6_p7_codex_cli | 2/3 | **3/3** ✅ |
| (all others unchanged) | — | — |

## Out-of-scope today

- `f4_p2_chatgpt_desktop` 3rd leg = L4b UIA, **W7 ~140h** real
  engineering. Stays at 2/3 (Phase B target met).
- Web 5 scenarios — D-Day plan §0 explicit non-target.
- L3c VS Code extension (Copilot 4th leg) — **W6 ~80h**.
- L1 protobuf decoder (Cursor 4th leg) — **W8 ~200h**.

## Verification one-liner after each action

```powershell
curl -s http://127.0.0.1:9800/api/v1/supervisor/status | `
    python -c "import sys, json; d=json.load(sys.stdin); [print(f'{s[\"id\"]:30s} {s[\"color\"]:7s} {s[\"legs_active\"]}/{s[\"redundancy_target\"]}') for s in d['scenarios']]"
```

(Cards may stay grey for ~5 min after action — supervisor 24h beacon
window needs ingest tick; not an indicator of failure.)
