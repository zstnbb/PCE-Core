# Cowork-region RECON findings — PLACEHOLDER

> **Status**: TBD — this file is a placeholder so cross-references
> from `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.B.2 / §7.5 and
> `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`
> §4 resolve while the RECON pass is pending.
>
> **Authority**: this is the real findings doc once filled in. The
> 6 questions below MUST be answered with empirical evidence before
> P5.B.5 implementation may proceed (per the gating rule in
> `HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` §4.3).

---

## How to fill this in

1. **Pre-flight** (manual, in this order):
   - Claude Desktop is running, logged in, and on the **Cowork** tab.
   - `pce_proxy/` mitmdump is up on `:8080`, system proxy points at it,
     CA installed (chat sub-runs 1–5 already proved this works on this
     machine — `Get-Item HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings`).
   - `pce_persistence_watcher` is running:
     ```powershell
     python -m pce_persistence_watcher watch --poll-interval 5
     ```
   - PCE Core is up at `127.0.0.1:9800` (uvicorn).

2. **Drive UIA dumps** to close Q1 and Q3 (each writes `_uia_dump_<mode>.txt`
   to cwd; commit them under `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_<mode>.txt`):
   ```powershell
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-cowork
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-skills
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-dispatch
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-scheduled
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-customize
   ```

3. **Run the cowork RECON** (60 min, MSIX-native — replaces the
   CDP-based `recon_claude_desktop.py` which ADR-018 §3.5 confirmed
   unreachable on MSIX):
   ```powershell
   python -m tests.manual.recon_claude_desktop_cowork --duration 3600
   ```

4. **Drive Claude Desktop's Cowork tab through the §4.1 RECON checklist**:
   - Switch to Cowork tab → REPL `> mark cowork-tab`
   - Single agent task ("List the files in C:\Users\Public") → REPL `> mark task-single`
   - Multi-step task ("Help me organize my screenshots and write a summary") → REPL `> mark task-multistep` (this answers Q2)
   - Type `/` in composer → REPL `> mark skill-picker-open`, dismiss
   - Select `/xlsx` → REPL `> mark skill-xlsx`, complete the task
   - After each task completes → REPL `> dump-agent-session` (this answers Q5 — dumps `local-agent-mode-sessions/<uuid>/manifest.json`)
   - Click Live Artifacts → REPL `> mark live-artifacts-open`
   - Click Dispatch → REPL `> mark dispatch-open` (Q3 supporting evidence)
   - Click Scheduled, create a schedule → REPL `> mark scheduled-create` (Q6)
   - Toggle a Customize setting (e.g. Web search) → REPL `> mark settings-toggle`
   - Idle 5 min → REPL `> mark idle-start`
   - REPL `> stop`

5. **Copy and complete the findings**: the recon script auto-generates
   `tests/manual/recon_cowork_<ts>/findings_skeleton.md` with
   auto-filled per-pattern event counts and marker timeline. Copy
   it OVER the contents of THIS file (rename this file to
   `2026-05-<DD>-cowork-recon-findings.md` when you do — match the
   recon end-time date). Then fill in the **TBD** answers in each
   Q section by examining:
   - `_uia_dump_open-skills.txt` for Q1
   - `events.jsonl` between `mark task-multistep` and the next marker for Q2
   - `_uia_dump_open-dispatch.txt` for Q3
   - The largest `/skills/list-skills` event body for Q4 (query DB directly
     by id; `events.jsonl` only carries metadata)
   - `manifests/<uuid>.json` for Q5
   - `events.jsonl` + `manifests/` cross-reference around `mark scheduled-create` and the scheduled-time for Q6

6. **Commit** the findings doc + the UIA dumps under
   `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_*.txt`. Then
   reference it from MATRIX §5.B.2 → flip "TBD" entries to the
   concrete answers.

---

## The 6 questions to close (mirror of MATRIX §5.B.2)

| # | Question | Affects | Closing evidence |
|---|---|---|---|
| **Q1** | Skills picker UIA shape (descendant vs separate top-level Win32 popup) | `pick_skill()` (C08) | `_uia_dump_open-skills.txt` |
| **Q2** | Async step semantics (single SSE / SSE-per-step / long-poll) | `wait_for_cowork_step()` (C02, C03) | `events.jsonl` timing analysis on `mark task-multistep` window |
| **Q3** | Dispatch (Beta) window class (in-app pane vs separate popup) | `open_dispatch()` (C10) | `_uia_dump_open-dispatch.txt` |
| **Q4** | `/skills/list-skills` schema (4927 B body shape) | `pick_skill(name)` matching field (C08) | DB row body for the largest matching event id |
| **Q5** | `local-agent-mode-sessions/<uuid>/manifest.json` field schema | `local_persistence.py` v0 (C14) | `manifests/<uuid>.json` |
| **Q6** | Scheduled task lifecycle (eager vs lazy `conversations/<uuid>` creation) | C11 SKIP-vs-PASS decision | `events.jsonl` + `manifests/` cross-ref around `mark scheduled-create` |

---

## Q1 — Skills picker UIA shape

**Empirical answer**: TBD.

**Driver implication**: TBD.

**Evidence**: TBD (`_uia_dump_open-skills.txt`).

---

## Q2 — Async step semantics

**Empirical answer**: TBD.

**Driver implication**: TBD.

**Evidence**: TBD (`events.jsonl` window between `mark task-multistep` and next marker).

---

## Q3 — Dispatch (Beta) window class

**Empirical answer**: TBD.

**Driver implication**: TBD.

**Evidence**: TBD (`_uia_dump_open-dispatch.txt`).

---

## Q4 — `/skills/list-skills` schema

**Empirical answer**: TBD.

**Driver implication for `pick_skill(name)`**: TBD.

**Evidence**: TBD (DB row body for the largest matching event id).

---

## Q5 — `local-agent-mode-sessions/<uuid>/manifest.json` field schema

**Empirical answer**: TBD.

**`local_persistence.py` v0 implication**: TBD.

**Evidence**: TBD (`manifests/<uuid>.json`).

---

## Q6 — Scheduled task lifecycle

**Empirical answer**: TBD.

**C11 acceptance implication**: TBD.

**Evidence**: TBD (`events.jsonl` + `manifests/` around `mark scheduled-create`).

---

## Sign-off

Once all 6 questions above carry concrete empirical answers + driver
implications + evidence references, this doc is the closure artefact
for `MATRIX §5.B.2` and unblocks P5.B.5 implementation
(P5.B.5.2 helpers → P5.B.5.3 normaliser → P5.B.5.4 `.mcpb` →
P5.B.5.5 C-case sweep).

**This placeholder file MUST NOT be deleted before being filled** —
the cross-references in MATRIX and the kickoff handoff resolve here.
