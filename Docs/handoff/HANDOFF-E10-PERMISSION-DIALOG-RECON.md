# HANDOFF — E10 permission-dialog RECON (5-minute owner task)

> **Status**: open. Last of the 3 P5.B carry-forwards rolled into P6
> per `Docs/handoff/HANDOFF-P5C-COMPLETION-2026-05-12.md` §4.1.
> D04 + E04 closed by `71e9381`; only E10 remains.
>
> **Why this needs a human**: closure requires a real Claude Desktop
> session in `permissionMode=default` and a permission dialog visible
> on screen. No autopilot loop can synthesise that state.
>
> **Estimated effort**: 5 minutes operator + 1 minute follow-up commit.

---

## What E10 verifies

`Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.C E10:

> Fresh session with `permissionMode=default` + prompt that triggers a
> tool → UI dialog appears ≤ 5 s after prompt submit (UIA tree has a
> dialog element with "Allow" / "Deny" button children); driver's
> `accept_permission_dialog()` helper clicks "Allow once" → tool runs,
> `sessionPermissionUpdates[]` records `decision='allowOnce'`.

The driver helper at
`tests/e2e_desktop_ui/drivers/claude_desktop.py:1903` already exists
with a comprehensive candidate-name list:

```python
candidates = {
    "once":   ("Allow once", "Accept once", "Approve once",
               "Allow", "Accept", "Approve", "Yes"),
    "always": ("Allow always", "Always allow", "Accept always",
               "Allow this and future", "Always"),
    "deny":   ("Deny", "Reject", "Cancel", "No"),
}
```

The single open question (`§5.C.2 Q2`): **does at least one substring
from each row above match this build's actual button names?**

If yes → E10 immediately transitions from SKIP to PASS once we
remove the SKIP guard in `tests/e2e_desktop_ui/run_p1_code_sweep.py`
`case_E10_permission_dialog`.

If no → we add the missing string to the candidates list and ship a
1-line follow-up.

---

## 5-step operator procedure

### 1. Launch Claude Desktop with `permissionMode=default`

The default permission mode is **already the factory default** unless
you've set `acceptEdits` from a previous PCE sweep. To reset:

- Open Claude Desktop.
- Code tab → click ⚙ (top-right of Code tab) → "Permission mode" →
  select **"Default"** (NOT "Accept edits" / "Plan mode").
- Confirm with: `Get-Content "$env:APPDATA\Claude\claude-code-sessions\<user>\<org>\local_*.json" | Select-String permissionMode`
  — should print `"permissionMode": "default"`.

### 2. Send a prompt that will trigger a tool

Type into the Code tab composer:

```
Please run echo pce-e10-recon
```

…and press Enter.

### 3. WAIT for the permission dialog

Within ≤ 5 seconds, a modal-shaped UI appears asking "Allow Bash to run
this command?" (or similar wording — that's what we're dumping).

**Do NOT click anything.** Leave the dialog open.

### 4. Run the RECON dump

In a PowerShell window (separate from Claude Desktop, not stealing
focus):

```powershell
cd "F:\INVENTION\You.Inc\PCE Core"
python -m tests.e2e_desktop_ui.scripts.dump_uia recon-permission
```

The script:

- Does NOT click anything (no focus steal).
- Filters for control types `Button` / `MenuItem` / `Pane` / `Custom`
  / `Window` / `Dialog` / `Group`.
- Filters by keywords `allow / deny / approve / reject / accept /
  permission / always / once / yes / no / tool / trust`.
- Writes `_uia_dump_recon-permission.txt` in cwd.

### 5. Decide & commit

Look at the dump. For **each of the 3 rows below**, find one matching
substring from the candidate list:

| `which` | Candidate substrings (claude_desktop.py:1927-1939) | Found in dump? |
|---|---|---|
| `once`   | `Allow once`, `Accept once`, `Approve once`, `Allow`, `Accept`, `Approve`, `Yes` | ⬜ ⬜ |
| `always` | `Allow always`, `Always allow`, `Accept always`, `Allow this and future`, `Always` | ⬜ ⬜ |
| `deny`   | `Deny`, `Reject`, `Cancel`, `No` | ⬜ ⬜ |

#### Case A — all 3 rows have at least 1 match

The candidates already cover this build. The follow-up commit:

1. Update `tests/e2e_desktop_ui/run_p1_code_sweep.py`
   `case_E10_permission_dialog` — replace the SKIP body with a real
   live-mode case:
   ```python
   def case_E10_permission_dialog(ctx: CaseContext) -> dict:
       if ctx.mode != "live":
           return _verdict("E10", "skip", reason="static mode cannot drive UI")
       # Drive a tool prompt under permissionMode=default.
       ctx.driver.send_user_text("Please run echo pce-e10-live")
       if not ctx.driver.accept_permission_dialog(which="once", timeout=10.0):
           return _verdict("E10", "fail",
               reason="permission dialog never surfaced or accept_permission_dialog() click failed")
       # Wait for tool_result to land in the JSONL transcript.
       ok = ctx.driver.wait_for_tool_result("Bash", "pce-e10-live", timeout=20.0)
       return _verdict("E10", "pass" if ok else "fail",
           reason="dialog accepted; tool_result captured" if ok else "dialog accepted but tool_result missing")
   ```
2. Update `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.C.2 Q2 from
   "uncharted" to "closed (2026-05-XX): existing
   `accept_permission_dialog()` candidates cover MSIX build vN.M".
3. Run `python tests/e2e_desktop_ui/run_p1_code_sweep.py --mode live --only E10`
   to verify PASS.
4. Update §4.1.C release-gate row from `15 PASS / 1 SKIP` to
   `16 PASS / 0 SKIP`.

#### Case B — at least 1 row has zero matches

Paste the dump back to me. I add the missing substring(s) to the
candidates list in `claude_desktop.py:1927-1939` (1-line edit per
missing string), commit, and we're back at Case A.

---

## After RECON closes

E10 will be the third and final P5.B carry-forward to clear, fully
unblocking the **P6 Coverage Polish** phase. The next agent can
proceed straight to P2 ChatGPT Desktop kickoff with all P5.B legacy
items closed.

## References

- Existing driver helper: `tests/e2e_desktop_ui/drivers/claude_desktop.py:1903-1957`
- E10 case stub (live SKIP guard to remove): `tests/e2e_desktop_ui/run_p1_code_sweep.py:1019-1037`
- §5.C E10 spec: `Docs/stability/DESKTOP-PRODUCT-MATRIX.md:877`
- §5.C.2 Q2 (the open question this RECON closes): `Docs/stability/DESKTOP-PRODUCT-MATRIX.md:935`
- Originating handoff that listed E10 as a carry-forward:
  `Docs/handoff/HANDOFF-P5C-COMPLETION-2026-05-12.md` §4.1.

## Reproduction (if state is lost)

To reset to a clean default-mode session if a previous run left the
app in `acceptEdits`:

```powershell
# 1. Close Claude Desktop fully (system tray icon → Quit).
# 2. Inspect / delete the latest pointer:
Get-ChildItem "$env:APPDATA\Claude\claude-code-sessions\*\*\local_*.json" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
# 3. Open Claude Desktop fresh; Code tab opens with permissionMode=default.
```
