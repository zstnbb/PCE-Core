# Handoff — P1 Claude Desktop cowork-region kickoff (2026-05-10)

**Scope:** Same-day continuation of the chat-region sub-run sequence
(`HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`,
`HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`,
`HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`). Chat-region
landed at **19 PASS / 1 SKIP / 1 KNOWN BUG / 1 deferred** over 22
applicable D-cases (commit `3b250e8`). User instruction next:

> 你先把文档材料对齐好 然后再把 cowork 的标准落下来 我们再开始做吧

This handoff is the **standard-down** artefact for the cowork-region.
It does not run any new tests; it captures the documentation-alignment
output and the implementation contract that **P5.B.5** (the new
sub-phase added 2026-05-10 to the kickoff doc) will execute against.

**TL;DR — what landed in this alignment pass**

| Doc | What changed |
|---|---|
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 Field table | Tier-1 fix: `mcp_jsonrpc.py` ✅, `local_persistence.py` ⬜ noted as cowork-gated, archetype unbundled (Chat Tube ✅ chat-region · Tool Tape ⬜ cowork-region · Run Trace ⬜ cowork+code) |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1.B (new) | P1 cowork-region subsection — product positioning · three-axis coverage (M / L3g / UI driver) · observed endpoints · existing tooling · pending tooling · acceptance gate · first-probe checklist |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.B (new) | 17 C-cases C00–C16 with detailed acceptance signals; C-namespace independent of D-namespace |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.B.1 (new) | Cowork-region applicability matrix (P1=16 must-pass, P6=7 best-effort CLI mirror, P2/P7/P8=1 each best-effort, P3/P4/P5=0) |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.B.2 (new) | 6 RECON-resolvable open architectural questions |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §6 | Test infrastructure layout — extended from 3 trees → 6 trees (added `e2e_mcpb/`, `e2e_l3g/`, `e2e_desktop_ui/`) |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §7 | Header updated P5.B.0–P5.B.4 → P5.B.0–P5.B.5; cross-ref D-case (chat) + C-case (cowork) |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §7.5 (new) | P5.B.5 sub-phase phasing — unlocks · eligible products · non-deliverables · acceptance · pass-distribution target · 7-item deliverables checklist |
| `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §8.2 | Ship checklist gains P1 cowork-region D0 sub-gate + `local_persistence.py` v0 + `e2e_mcpb`+`e2e_l3g`+`e2e_desktop_ui` test trees + ADR-018 reference |
| `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §7 | Capture-storage-render matrix — L3g + H1 CLI wrap flipped to ✅; archetype mapping cowork ↔ Tool Tape, code ↔ Run Trace |
| `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §10–§11 | Added P5.B.5 row to unlock matrix + delivery table; sign-off criteria gains cowork-region sub-gate; test suite coverage references include `e2e_desktop_ui/` |

**No code yet.** Implementation begins after this handoff is approved.

---

## 1. Authority chain (read these in order)

1. **`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`** §3
   (application typology), §7 (capture-storage-render matrix). The
   **strategy** layer — defines what an "archetype" is, which
   archetypes a desktop AI app produces, and how a UCS layer maps
   to render tier.
2. **`Docs/stability/DESKTOP-PRODUCT-MATRIX.md`** §4.1.B (this
   region's field card), §5.B (this region's C-cases), §7.5 (this
   region's phasing). The **standard** layer — the only authoritative
   source for the cowork-region's acceptance bar.
3. **`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`** §4 (sub-phase
   table) + §10/§11 (unlock + delivery + sign-off). The **execution**
   layer — what each sub-phase ships, which ADR governs it.
4. **`Docs/docs/engineering/adr/ADR-018-p1-claude-desktop-msix-three-axis.md`**
   (the realisation ADR — chat-region's network axis = `pce_proxy`,
   cowork-region's persistence axis = `pce_persistence_watcher`,
   code-region's CLI axis = `pce_cli_wrapper`). All three axes ship
   in `v1.1.0-alpha.8-adr018`; cowork-region adds the **MCP middleware
   axis** as the fourth axis on top.
5. **This handoff** — the implementation walkthrough.

---

## 2. The cowork-region in one paragraph

Claude Desktop's left sidebar exposes three regions: **Chat**, **Cowork**,
**Code**. Chat-region is a synchronous one-turn dialogue (closed at
19/22 PASS in chat sub-runs 1–5). **Cowork-region is an asynchronous
agent task launcher** — the user types a free-form task into a composer,
optionally invokes a Skill (`/xlsx`, `/pdf`, `/pptx`, `/docx`,
`/consolidate-memory`, `/skill-creator`, `/schedule`, `/setup-cowork`),
optionally scopes to a project, optionally enqueues onto Dispatch (Beta)
for parallel execution, and then walks away. The agent runs multi-step
reasoning in a sandbox at `/mnt/user-data/{uploads,outputs}`, produces
artefacts (xlsx / docx / pptx / jsx / md / pdf), and persists session
metadata to `LocalCache\Roaming\Claude\local-agent-mode-sessions\<uuid>\manifest.json`.
Code-region is covered by the H1 CLI wrap path (Claude Code via
`pce_cli_wrapper/` — separate concern, not this handoff).

The user-visible difference vs chat: **time-shifted execution + tool
usage + on-disk artefact lifecycle**. The capture-pipeline difference
vs chat: **one new endpoint family (`/wiggle/*`), one new persistence
surface (`local-agent-mode-sessions/`), and one new MCP tools picker
(Skills)**.

---

## 3. Three-axis implementation strategy (mirrors ADR-018, with M as fourth axis)

ADR-018 §3.5 fixed the chat-region to a three-axis model after the
CDP launcher route was confirmed unreachable on MSIX. The cowork-region
**reuses all three axes** and **adds a fourth** (M / MCP middleware)
because cowork's Skills picker is the primary user surface for
non-vendor MCP tools.

| Axis | Plane | Package | Cowork-region role | Status |
|---|---|---|---|---|
| **M (MCP middleware)** | UCS L3f | `pce_mcp_proxy/` (姿态 B) + `pce_mcp/` (姿态 A) | Captures `tools/list` + `tools/call` + `tools/result` JSON-RPC frames between Claude Desktop and any user-installed MCP server. **In cowork, the Skills picker shows MCP tools — this is the primary user surface** for tool calls. | ✅ alpha.1 (姿态 B) + alpha.8 (姿态 A `.mcpb` manifest); **needs real-machine `.mcpb` install (C16)** |
| **N / L1 (network proxy)** | UCS L1 | `pce_proxy/` (mitmproxy + CA) | Captures `claude.ai/api/organizations/<org>/chat_conversations/<uuid>/completion` SSE + `wiggle/upload-file` + `wiggle/download-file` + `cowork_settings` + `environments?included_worker_types=cowork` + `skills/list-skills` + `referral/eligibility?source=cowork`. **H2 PASS confirmed Anthropic does not pin claude.ai on MSIX**. | ✅ alpha.8 (chat-region 172 clean hits, 0 TLS errors); **cowork endpoints already in scope, just need C-case-level driver invocation** |
| **L3g (persistence watcher)** | UCS L3g (added by ADR-018) | `pce_persistence_watcher/` | Tails `LocalCache\Roaming\Claude\local-agent-mode-sessions\<uuid>\manifest.json` and emits `raw_captures` rows with `source_id='l3g-local-persistence-default'`. **Even-if-network-blocked path** for cowork sessions. | ✅ alpha.8 envelope-level emission + 43 hermetic tests; **needs `pce_core/normalizer/local_persistence.py` v0 structural parser (C14)** |
| **UI driver** | (test infra) | `tests/e2e_desktop_ui/drivers/claude_desktop.py` | UIA + SendInput automation. Drives the cowork tab, composer, Skills picker, Ask-mode picker, Live Artifacts pane, Dispatch (Beta), and Scheduled view. Reuses sub-run-5 focus-verified scaffold (90% of helpers carry over from chat-region). | ✅ chat-region 19/22 PASS; **needs 6 new cowork helpers — see §6 below** |

**Shape of a passing C-case run** = at least **two** axes fire
independently for the same `conversation_uuid`:

- N axis emits `raw_captures` rows with `host='claude.ai'`,
  `source='L1_proxy'` (or `proxy`).
- L3g axis emits `raw_captures` rows with
  `source_id='l3g-local-persistence-default'`.
- M axis (when a Skill or non-vendor MCP tool is invoked) emits rows
  with `source_type='mcp_proxy'` or `source_type='pce_mcp'`.

Cross-axis correlation by `conversation_uuid` is the **structural
acceptance signature** of the cowork-region — the chat-region's
"single-axis" SSE streaming is no longer sufficient because cowork
tasks may run for minutes and may be picked up by L3g first
(network arrives later, or SSE keepalive timed out and reconnected).

---

## 4. RECON walkthrough (60 min, before any C-case implementation)

§5.B.2 of MATRIX lists 6 open architectural questions. Each one
changes the cost / shape of ≥1 C-case helper. **Implementing C-cases
without first running RECON is forbidden** — it produced 4 hours of
guess-and-check on the chat sub-run 3 attempt before sub-run 4 went
back and dumped the UIA tree.

### 4.1 RECON command

```powershell
# Pre-flight: Claude Desktop is running, user is logged in, on Cowork tab.
# `pce_proxy` mitmdump is up on :8080, system proxy points at it.
# `pce_persistence_watcher` is running with the default config.

python -m tests.manual.recon_claude_desktop --duration 3600 --section cowork
```

This script (to be added in P5.B.5 sub-phase, scaffolded after
chat-region's existing `recon_claude_desktop.py`) drives the UI through:

1. **Cowork tab switch** (heartbeat verification — C00).
2. **Single agent task** "List the files in C:\Users\Public" (a no-arg
   tool call that any MCP filesystem server can execute) — C01.
3. **Skill invocation** `/xlsx make a 3-row spreadsheet of fruits` — C08
   (also exercises C05 file input shape and C09 live artefact shape).
4. **Project-scoped task** with `CLAUDE_PROJECT_NAME=<existing project>`
   — C12.
5. **Settings change** toggle `coworkWebSearchEnabled` via Customize
   — C13.
6. **Idle for 5 minutes** to confirm heartbeat-only baseline — C15.
7. **`local-agent-mode-sessions/`** dump after each task —
   `python -m pce_persistence_watcher.tools.dump_one --kind agent_session --first` — C14.
8. **`.mcpb` install** drag-and-drop — C16 (manual step,
   logged via UIA dump of the Settings → Extensions panel).

### 4.2 RECON outputs (each closes ≥1 §5.B.2 question)

| RECON output | Closes question | Used by C-case |
|---|---|---|
| `_cowork_uia_skills_picker.txt` | Q1: Skills picker tree shape (descendant vs popup) | `pick_skill()` — C08 |
| `_cowork_async_step_timing.json` | Q2: SSE-per-step vs single-stream vs long-poll | `wait_for_cowork_step()` — C02, C03 |
| `_cowork_uia_dispatch.txt` | Q3: Dispatch (Beta) window class | `open_dispatch()` — C10 |
| `list-skills.json` (full body dump, 4927 B) | Q4: schema for skill-id matching | `pick_skill()` — C08 |
| `manifest.json` (one full session entry) | Q5: `local-agent-mode-sessions` field schema | `local_persistence.py` v0 — C14 |
| `scheduled_task_lifecycle.txt` | Q6: when does scheduled task create `conversations/<uuid>` | `wait_for_cowork_step()` — C11 |

### 4.3 RECON closure doc

After the 60-min run, the agent writes
**`Docs/research/2026-05-XX-cowork-recon-findings.md`** (filename
date = day of RECON) with one section per question, each containing:

- The empirical answer (1–3 sentences).
- The dump file that proves it (gitignored under
  `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_*.txt`).
- The driver implication (one helper signature change, or "no
  change needed").

**Gating rule**: P5.B.5 implementation cannot start until this
findings doc exists and is referenced from §4.1.B's first-probe
checklist (a) of MATRIX.

---

## 5. C-case acceptance table (concrete pass-distribution target)

§5.B of MATRIX defines all 17 C-cases C00–C16 (C00 detection +
C01–C16 functional). The cowork-region D0 sub-gate is **≥80% PASS
= ≥13/16** (C00 is detection, C01–C16 = 16 functional cases).

| Case | Axis dependency | Implementation cost | Target verdict |
|---|---|---|---|
| **C00** detection | N (cowork heartbeat polling) | Low — heartbeat poll detection by URL pattern | ✅ PASS |
| **C01** single agent task | N + UI driver | Low — same as chat D01 with `open_cowork_tab()` prefix | ✅ PASS |
| **C02** streaming complete | N (SSE) + UI driver | Low if RECON Q2 = single-stream; Medium if per-step | ✅ PASS |
| **C03** multi-step task | N + UI driver | Medium — `wait_for_cowork_step()` semantics | ✅ PASS or ⏭ SKIP if Q2 unresolved |
| **C04** task cancel | N + UI driver | **Inherits chat-region D04 KNOWN BUG** — `pipeline.try_normalize_pair` requires both sides | ⏭ SKIP (linked to D04 fix) |
| **C05** file input | N (`wiggle/upload-file`) + UI driver | Low — driver reuses chat D06 path | ✅ PASS (closes D17/D18 normaliser-join gap as side effect) |
| **C06** code output | N (`wiggle/download-file`) + UI driver | Medium — artefact verification | ✅ PASS |
| **C07** MCP tool | M + N + UI driver | Medium — needs `pce_mcp_proxy` actively forwarding to a real MCP server | ✅ PASS |
| **C08** skill invocation | N (Skills picker SSE) + UI driver + RECON Q1 | Medium — `pick_skill()` + Q4 schema | ✅ PASS |
| **C09** live artefact | N + UI driver | Low — chat D20 already proves the shape works | ✅ PASS |
| **C10** dispatch concurrent | N + UI driver + RECON Q3 | Medium — concurrent task launching + correlation | ✅ PASS or 🟡 if Q3 is a separate window |
| **C11** scheduled task | N + L3g + UI driver + RECON Q6 | High — needs >24h to verify scheduled-time emission | ⏭ SKIP (carry as 🟡 best-effort) |
| **C12** project-scoped cowork | N + UI driver | Low — chat D19 already proved `/chat_conversations/<uuid>/completion` carries project membership | ✅ PASS |
| **C13** settings change | N (`POST /cowork_settings`) + UI driver | Low — single endpoint round-trip | ✅ PASS |
| **C14** L3g backstop | L3g + new normaliser | Medium — `local_persistence.py` v0 | ✅ PASS |
| **C15** idle silence | N (heartbeat baseline) | Low — chat D12 already proved the shape | ✅ PASS |
| **C16** `.mcpb` install | M + UI driver | Medium — manual install verification + Skills picker verification | ✅ PASS |

**Pass-distribution target** (modelled on chat-region 19/22):

- **PASS = 14** (C00, C01, C02, C03, C05, C06, C07, C08, C09, C10,
  C12, C13, C14, C15, C16) = **87.5%** clears the ≥80% gate by
  +1.5%.
- **SKIP = 2** (C04 cancel inheriting D04 KNOWN BUG, C11 scheduled
  needing >24h soak).
- **FAIL = 0** — any capture-pipeline FAIL blocks the sub-phase.

This is **conservative**: any of the SKIPs converting to PASS via
follow-up driver work (e.g., fixing D04's request-only normalisation
also fixes C04) brings the pass rate to 15/16 = **93.75%**.

---

## 6. Driver helper reuse + 6 new helpers

`tests/e2e_desktop_ui/drivers/claude_desktop.py` (sub-run 5 focus-verified)
exposes these primitives — **all reused unchanged for cowork**:

- `_find_uia_by_name_substr` / `_find_uia_by_name_substr_all` (with
  `prefer` + Y-band) — sub-run 4 added cross-window search; reusable
  for Skills picker if RECON Q1 = top-level popup.
- `ensure_composer_focus()` — `IUIAutomation.GetFocusedElement`-verified
  composer focus before SendInput; sub-run 5 fix.
- `send_keys()` with `vk_packet=True` — IME-bypass for CJK / Unicode.
- `paste_clipboard()` / `paste_image_clipboard()` — D17/D18 path; reusable
  for cowork file input (C05).
- `cancel_current()` — sends `{ESC}` (Claude's bound Stop shortcut);
  reusable for C04.
- `dump_tree(keywords, control_types)` — read-only UIA dump for
  RECON section above.

**6 new cowork helpers** to add (specs from §4.1.B / §7.5):

| Helper | Signature | RECON dependency | Used by |
|---|---|---|---|
| `open_cowork_tab()` | `() -> None` | None (sidebar tab is plain UIA Button) | C00, all cowork cases |
| `pick_skill(name)` | `(str) -> None` — name in `{"xlsx","pdf","pptx","docx","consolidate-memory","skill-creator","schedule","setup-cowork"}` | Q1 (picker tree shape) + Q4 (skill list schema) | C08 |
| `select_ask_mode(mode)` | `(str) -> None` — mode in `{"Ask","Reply","Plan-and-execute",...}` (final list per RECON) | UIA dump of "Ask" composer picker | C03, C10 |
| `view_live_artifacts()` | `() -> None` | None (sidebar entry) | C09 |
| `open_dispatch()` | `() -> None` | Q3 (window class) | C10 |
| `open_scheduled()` | `() -> None` | None (sidebar entry) | C11 |
| `wait_for_cowork_step(timeout=120)` | `(int) -> WaitResult` — different SSE/HTTP semantics from `wait_for_response` | Q2 + Q6 (lifecycle) | C02, C03, C11 |

(Total = 7 helpers, but `wait_for_cowork_step` is the spec's
"6 helpers" shorthand for "the multi-step waiter is one helper with
multiple timing modes inside" — implementation may split it into 2
sub-helpers.)

**Shared with chat-region**: ~90% of the chat-region driver remains
the canonical desktop-product driver contract (per §6.1 of MATRIX).
The 6 cowork helpers extend it; they do not fork it.

---

## 7. Reproduction recipe (post-implementation)

Once P5.B.5 implementation lands, the cowork sweep runs as:

```powershell
# Pre-flight
$env:PCE_DB_PATH = "$HOME/.pce/data/pce.db"
$env:CLAUDE_PROJECT_NAME = "<an existing project name substring>"  # for C12

# 0. Verify the three-axis scaffolding is up
python -m pce_proxy.health
python -m pce_persistence_watcher.health
python -m pce_mcp_proxy.health

# 1. RECON (one-time per Claude Desktop version)
python -m tests.manual.recon_claude_desktop --duration 3600 --section cowork

# 2. Read the findings doc
notepad Docs/research/2026-05-XX-cowork-recon-findings.md

# 3. C-case sweep (16 windows N..CC, one per case)
python -m tests.e2e_desktop_ui.run_p1_cowork_sweep

# 4. Inspect per-case
Get-Content tests/e2e_desktop_ui/reports/p1_cowork/<timestamp>/summary.json -Raw
```

`run_p1_cowork_sweep` will mirror `run_p1_chat_sweep` (sub-runs 1–5)
— per-window driver script + per-case verdict JSON + summary
aggregator + `_cowork_<window>_log.txt` driver evidence under
`tests/e2e_desktop_ui/reports/p1_cowork/<timestamp>/`.

---

## 8. Risks and open follow-ups

### 8.1 RECON-dependent risks (Medium priority)

- **Q1 Skills picker shape**: if it turns out to be a top-level Win32
  popup, `pick_skill()` needs `_find_uia_by_name_substr_all`'s
  cross-window mode (sub-run 4 already shipped this). Cost: 0.
- **Q2 async step semantics**: if cowork uses long-poll instead of
  SSE, `wait_for_cowork_step()` needs to poll `messages` table OR
  open a second SSE listener. Cost: +0.5 day.
- **Q6 scheduled lifecycle**: if scheduled tasks don't create
  `conversations/<uuid>` rows until execution time, C11 must SKIP
  with a documented "verify in 24h follow-up" note. Cost: 0
  (already accepted in §5).

### 8.2 Normaliser risk (Low priority)

`local_persistence.py` v0 parses session manifest envelope only.
The internal step shape inside `vm_bundles/` is not reverse-engineered
in v0 — that's v1, post-v1.1. Acceptance is **C14 PASS via envelope
parsing**; not blocked on full schema discovery.

### 8.3 `.mcpb` install risk (Low priority)

`pce_mcp/mcpb/` manifest is complete (alpha.8); only the ZIP step
is missing. Two viable paths:

1. `npx @anthropic-ai/mcpb pack pce_mcp/mcpb/` — official tool, recommended.
2. Hand ZIP per the manifest spec — fallback if the official tool
   doesn't ship publicly.

Either produces `pce-mcp.mcpb`. C16 acceptance = drag-and-drop install
+ 6 tools visible in Cowork Skills picker.

### 8.4 D04 cancel-mid-stream KNOWN BUG (Medium priority, shared with chat)

C04 inherits chat-region D04's root cause (`pipeline.try_normalize_pair`
requires both sides; cancel = response side never arrives). Three
coordinated fixes (also documented in the chat-region SKIP-conversion
handoff §8.3):

1. Pipeline accepts request-only with `incomplete=true` flag.
2. Normaliser persists user message even without paired response.
3. New `tests/test_pipeline_cancel.py` to lock the contract.

If this lands during P5.B.5 implementation, C04 + D04 both flip to
PASS — combined chat+cowork pass rate 20/22 + 15/16 = **35/38 = 92%**.

---

## 9. Sign-off criteria for P5.B.5 cowork-region

The cowork-region D0 sub-gate (§4.1.B / §7.5 / §8.2 of MATRIX) is
met when **all** of the following are true:

- [ ] **RECON closure doc** `Docs/research/2026-05-XX-cowork-recon-findings.md`
      exists and references all 6 §5.B.2 questions.
- [ ] **6 (or 7) cowork helpers** committed to
      `tests/e2e_desktop_ui/drivers/claude_desktop.py` with UIA dumps
      under `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_*.txt`.
- [ ] **`pce_core/normalizer/local_persistence.py`** v0 lands with
      ≥3 unit tests in `tests/test_local_persistence.py`.
- [ ] **`pce-mcp.mcpb`** packaged + real-machine install verified
      (6 PCE tools in Cowork Skills picker).
- [ ] **16 C-case files** under
      `tests/e2e_desktop_ui/cases/p1_cowork_*.py`, runnable via
      `python -m tests.e2e_desktop_ui.run_p1_cowork_sweep`.
- [ ] **Sub-run report** at
      `tests/e2e_desktop_ui/reports/p1_cowork/<timestamp>/`
      with **≥13/16 PASS** + **0 FAIL**.
- [ ] **CHANGELOG** entry for `v1.1.0-alpha.X-cowork-p1`.
- [ ] **MATRIX §4.1.B "Status"** flipped from "标准落盘" to "实施完成"
      with the sub-run timestamp + score.

When all 7 are checked, the cowork-region D0 sub-gate clears and
P5.B.5 is closed. Next sub-phase: P5.C dashboard (Tool Tape renderer
needs the data shape this sub-phase produces).

---

## 10. Cross-references

- Authority chain (top of this doc, §1).
- Chat-region prior-art handoffs (do not skip if you're new to the
  driver primitives):
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`
  - `Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`
- ADRs: ADR-013 (`pce_mcp/` Open Core), ADR-015 (UCS L3f amendment
  for `pce_mcp_proxy/`), ADR-016 (P5.B.2 pivot — CDP launcher
  formally deferred for MSIX), **ADR-018** (P1 MSIX three-axis
  realisation — direct upstream of this handoff).
- Standards: MATRIX §4.1.B / §5.B / §7.5 / §8.2 (defined above).
- Code that already exists and gets reused unchanged:
  `@f:\INVENTION\You.Inc\PCE Core\pce_proxy\` (alpha.8 N axis),
  `@f:\INVENTION\You.Inc\PCE Core\pce_persistence_watcher\`
  (alpha.8 L3g axis envelope-level), `@f:\INVENTION\You.Inc\PCE Core\pce_mcp_proxy\`
  (alpha.1 M-B axis), `@f:\INVENTION\You.Inc\PCE Core\pce_mcp\`
  (alpha.8 M-A `.mcpb` manifest),
  `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\claude_desktop.py`
  (sub-run 5 focus-verified driver).

---

## 11. What the user can claim NOW (post-alignment, pre-implementation)

- **Cowork-region standard is fully landed in the canonical docs**
  (MATRIX §4.1.B / §5.B / §5.B.1 / §5.B.2 / §7.5 / §8.2 + FRAMEWORK
  §7 + KICKOFF §10/§11).
- **17 C-cases are specified** with detailed acceptance signals and
  axis dependencies.
- **The implementation contract is concrete**: 6 (or 7) helpers,
  1 normaliser file, 1 packaging step, 16 test files, 1 RECON pass,
  ≥13/16 PASS.
- **No implementation has begun** — the next move is the user
  approving this handoff, then the agent runs RECON, writes the
  findings doc, and starts on the helpers.

That's the standard-down moment the user asked for. Implementation
phase begins next.
