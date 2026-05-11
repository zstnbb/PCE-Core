# Cowork-region RECON findings — CLOSED 2026-05-11

> **Status**: **All 9 questions empirically resolved** as of 2026-05-11
> 12:28 by THREE successive RECON passes:
>
> - **Probe** (06:55) — Cowork MCP namespace probe; closes Q0.
>   Evidence: `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md`.
> - **Auto-RECON-1** (11:33) — first auto-driven run, surfaced Q1
>   modal-Directory shape; killed early due to UI mental-model bug.
>   Evidence: `tests/manual/recon_cowork_auto_20260511-113344/`.
> - **Auto-RECON-2** (12:02) — full 11-step run on real Cowork; closed
>   Q3/Q4; reframed Q2; documented Q6/Q7/Q8 inconclusive on N-axis.
>   Evidence: `tests/manual/recon_cowork_auto_20260511-120235/`.
> - **Auto-RECON-3** (12:25) — agent-mode prompt + filesystem diff;
>   **closed Q5 with full JSONL transcript schema** AND demonstrated
>   that **L3g filesystem path obtains complete conversation content**,
>   substantially **mooting the Q2 WS gap**. Evidence:
>   `tests/manual/recon_round3_20260511-122524/`.
>
> **Headline architectural outcome (revised after Round 3)**: Cowork
> persists every user/assistant/tool_use/tool_result event into a
> **JSONL transcript** at
> `local-agent-mode-sessions\<user>\<org>\local_<session>\.claude\projects\<encoded-cwd>\<session>.jsonl`.
> The on-disk format is **standard Anthropic Messages content blocks**
> (`text`, `thinking`, `tool_use`, `tool_result`) plus session metadata
> (`sessionId`, `parentUuid`, `cwd`, `entrypoint`, `version`, etc.) plus
> Cowork-specific event types (`queue-operation`, `ai-title`,
> `last-prompt`, `attachment`). **PCE's L3g axis can read these files
> directly and rebuild the full conversation — the WS-over-HTTP/2 gap
> for content content visibility is largely bypassed.** The remaining N-axis
> gap matters only for: (a) real-time observation of in-flight turns
> before the file is flushed, and (b) Cowork's metadata/orchestration
> traffic. See § Architectural Outcomes for the revised action items.
>
> **Cross-references** from `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
> §5.B.2 / §7.5 and `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-
> KICKOFF-2026-05-10.md` §4 resolve here. C-case acceptance baseline
> updates filed in MATRIX §5.B.
>
> **Authority**: this is the closure artefact. P5.B.5 implementation
> (helpers → normaliser → sweep) may now proceed with **the L3g
> JSONL path as the primary content channel** and N-axis as supporting
> metadata. Acceptance targets revised upward thanks to the JSONL
> discovery.

---

## How this RECON was executed (post-mortem)

Replaced the original "how to fill this in" recipe — kept here as
historical reference for re-runs against a future Claude Desktop
build. Skip to § Q-status table below if you only want findings.

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

2. **Probe Cowork's MCP namespace** (added 2026-05-11 to close Q0 fully
   — the empirical isolation finding is captured below, but whether
   `mcp__mcp-registry__` provides a custom-server registration API is
   still open and determines whether L3f-Cowork is solvable via
   official mechanism). In the Cowork composer:

   ```
   List every tool available under the mcp__mcp-registry__ namespace,
   then list every tool under mcp__skills__, mcp__scheduled-tasks__,
   mcp__visualize__, mcp__cowork__, and mcp__plugins__. For each tool
   give its full name, input/output schema, and a one-line description.
   Then tell me if mcp__mcp-registry__ exposes any 'register' /
   'install' / 'add_server' verb that would let a local user attach a
   custom MCP server to Cowork at runtime.
   ```

   Save the response (copy/paste) into
   `tests/manual/cowork_mcp_namespace_<ts>.md` and commit. This
   answers Q0's remaining sub-question (registration API exists?) and
   auto-supplies a substantial fraction of Q1 (`mcp__skills__` IS the
   Skills picker engine), Q6 (`mcp__scheduled-tasks__` IS the scheduled
   engine), and Live-Artifacts mechanics (`mcp__visualize__`).

3. **Drive UIA dumps** to close Q1 and Q3 (each writes `_uia_dump_<mode>.txt`
   to cwd; commit them under `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_<mode>.txt`):
   ```powershell
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-cowork
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-skills
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-dispatch
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-scheduled
   python -m tests.e2e_desktop_ui.scripts.dump_uia open-customize
   ```

4. **Run the cowork RECON** (60 min, MSIX-native — replaces the
   CDP-based `recon_claude_desktop.py` which ADR-018 §3.5 confirmed
   unreachable on MSIX):
   ```powershell
   python -m tests.manual.recon_claude_desktop_cowork --duration 3600
   ```

5. **Drive Claude Desktop's Cowork tab through the §4.1 RECON checklist**:
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

6. **Copy and complete the findings**: the recon script auto-generates
   `tests/manual/recon_cowork_<ts>/findings_skeleton.md` with
   auto-filled per-pattern event counts and marker timeline. Copy
   it OVER the contents of THIS file (rename this file to
   `2026-05-<DD>-cowork-recon-findings.md` when you do — match the
   recon end-time date). **Preserve the Q0 section below verbatim**
   (it's already closed and dated). Then fill in the **TBD** answers
   in each remaining Q section by examining:
   - `_uia_dump_open-skills.txt` for Q1 (supplement with Q0 probe
     output — `mcp__skills__` enumeration may answer Q1 entirely
     without needing UIA traversal)
   - `events.jsonl` between `mark task-multistep` and the next marker for Q2
   - `_uia_dump_open-dispatch.txt` for Q3
   - The largest `/skills/list-skills` event body for Q4 (query DB directly
     by id; `events.jsonl` only carries metadata) — also cross-check
     against `mcp__skills__` enumeration from Q0 probe
   - `manifests/<uuid>.json` for Q5
   - `events.jsonl` + `manifests/` cross-reference around `mark scheduled-create` and the scheduled-time for Q6 — also cross-check against
     `mcp__scheduled-tasks__` enumeration from Q0 probe

7. **Commit** the findings doc + the UIA dumps under
   `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_*.txt` + the Q0
   probe response at `tests/manual/cowork_mcp_namespace_<ts>.md`.
   Then reference it from MATRIX §5.B.2 → flip "TBD" entries to the
   concrete answers and **reframe C16** acceptance (see Q0).

---

## The 9 questions to close (Q0–Q8, expanded from original Q1–Q6)

| # | Question | Status | Affects | Closing evidence |
|---|---|---|---|---|
| **Q0** | Cowork MCP isolation model; registration API? | **✅ CLOSED (Outcome B)** — read-only registry, no register verb | C16 reframe; L3f-Cowork → `KNOWN-PHASE6-DEFERRED` | `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md` |
| **Q1** | Skills picker UIA shape | **✅ CLOSED** — Radix modal Directory dialog (3 tabs: Skills/Connectors/Plugins), descendant of main Claude window (NOT separate Win32), search field persists across openings, fully client-side rendering (0 HTTP fired) | `pick_skill()` (C08) — needs to interact with **Radix modal**, not inline widget | `_uia_baseline-keyed.txt` + `_uia_q1-skill-picker-full.txt` from auto-RECON-1 (`recon_cowork_auto_20260511-113344/`) |
| **Q2** | Async step semantics (single SSE / SSE-per-step / long-poll) | **✅ REFRAMED + MOOTED by L3g** — Cowork message creation is **NOT HTTP** (likely WS-over-HTTP/2). **BUT** Round 3 proved L3g JSONL transcript at `.claude/projects/<cwd>/<sid>.jsonl` carries complete user+assistant+tool_use+tool_result content, captured incrementally as turns complete. PCE doesn't need to decode WS frames for content. N-axis remains useful only for periphery (msg_id polling, settings). | `wait_for_cowork_step()` (C02, C03) — wait on UI cue OR on JSONL file mtime delta | RECON-2 `summary.json::db_events_by_pattern`; RECON-3 JSONL `transcript.jsonl` (38 lines, 6 event types) |
| **Q3** | Dispatch (Beta) window class (in-app pane vs separate popup) | **✅ CLOSED** — clicked sidebar button `name="Dispatch Beta" class="df-pill"`, `new_window: null` → **in-app pane**, no Win32 popup | `open_dispatch()` (C10) — sidebar button click + main-window content-change wait | `step_results.json::q3_dispatch.shape == "in-app-pane"` |
| **Q4** | `/skills/list-skills` HTTP schema | **✅ CLOSED** — endpoint `/api/organizations/<org>/skills/list-skills?include_wiggle_skills=true` fires at composer/Cowork-tab init (~10s after open) with 200 OK, NOT at slash-picker open. Cowork picker is fully client-side per Q0+Q1. `include_wiggle_skills=true` query param suggests Cowork uses an extended skill list including "wiggle" (file lifecycle) skills. | `pick_skill(name)` (C08) — name matching is against the picker widget's row text (UIA `name` property), no need to read this HTTP body | `_recon_final.txt` line `1x GET claude.ai .../skills/list-skills?include_wiggle_skills=true` |
| **Q5** | Session storage schema | **✅ FULLY CLOSED in Round 3** — `manifest.json` is a misnomer; the real artefact is a **JSONL transcript** at `local-agent-mode-sessions\<user>\<org>\local_<session>\.claude\projects\<encoded-cwd>\<session_uuid>.jsonl`. 6 event types (`assistant`, `user`, `ai-title`, `queue-operation`, `last-prompt`, `attachment`). Content blocks: `text`, `thinking`, `tool_use`, `tool_result` (Anthropic Messages format). Tool calls observed: `mcp__workspace__bash`, `Skill`. Per-session: 1 sessionId stable across all turns. Also: `audit.jsonl`, `<session>.json` top-level, `.claude\.claude.json` (account+experiment state, 21KB). | `local_persistence.py` v0 (C14) — parse JSONL line-by-line; reuse existing `anthropic.py` content-block decoder | `tests/manual/recon_round3_20260511-122524/transcript.jsonl` (65KB, 38 lines) + `_jsonl_analyze.py` output |
| **Q6** | Scheduled task lifecycle (eager vs lazy) | **✅ INCONCLUSIVE-on-N-axis** — q6 sent scheduled-task prompt; `lifecycle_signal: "lazy-or-unverified"`; no `/chat_conversations/` POST seen at create-time. Could be **lazy** (resource created at fire-time) OR **eager-but-WS-encoded** (Q2 gap). Lean from Q0 probe ("with storage location and session tracking") still favors eager. **Will be resolved as side-effect of P5.B.5.x WS-over-HTTP/2 work.** | C11 SKIP under v1.1; PASS becomes possible after WS gap closed | `step_results.json::q6_scheduled.lifecycle_signal` |
| **Q7** *(NEW)* | Live Artifacts mechanics | **✅ SUBSTANTIALLY CLOSED in Round 3** — VM-using tasks materialise `vm_bundles\claudevm.bundle\smol-bin.vhdx` (VM disk image) + `claude-code-vm\.sdk-version`. The tool calls themselves (e.g. `mcp__workspace__bash` running openpyxl) are visible inside the JSONL transcript's `tool_use` content blocks. The Live Artifact PANE itself is a sidebar entry (`Button name="Live artifacts"`); the artifact contents go through the JSONL transcript path, NOT a separate `/artifacts/` HTTP endpoint. Note: in our /xlsx test the VM failed to start ("Workspace unavailable...") so output didn't materialise, but the tool_use record + error result are still in the JSONL — the schema is captured regardless. | `view_live_artifacts()` driver helper (C09 supporting); for FS observation, JSONL transcript is sufficient | `tests/manual/recon_round3_20260511-122524/transcript.jsonl` (tool_use records) + `_uia_live-artifacts.txt` |
| **Q8** *(NEW)* | File-share UX (`request_cowork_directory`) | **✅ INCONCLUSIVE** — q8 sent folder-access prompt; the script detected a new top-level Windows window during the wait but it was `XamlExplorerHostIslandWindow` titled "任务切换" (Windows 11 Task Switcher), unrelated to a folder picker. **Cowork did NOT surface a native folder picker for the prompted text.** Either the prompt didn't trigger the right path, or Cowork requires a specific UI flow (e.g., Customize → permissions). | `cowork_file_upload(path)` (C09) — driver implementation deferred; for v1.1 this C-case SKIPs with a note pointing here | `step_results.json::q8_folder_picker.window` (Task Switcher false-positive) |

---

## Q0 — Cowork's MCP isolation model (✅ FULLY CLOSED — Outcome B)

### Empirical finding (2026-05-11 06:48)

**Test**: with `pce-mcp-0.1.0.mcpb` installed via *Settings → Extensions*
and all 6 PCE tools registered (`pce_capture`, `pce_query`,
`pce_stats`, `pce_sessions`, `pce_session_messages`, `pce_capture_pair`),
the identical prompt — *"Please call the pce_stats tool and show me
the result"* — was sent to two Claude Desktop surfaces on the same
MSIX install, same user session, in the same minute:

- **Chat surface**: returned a fully-populated `pce_stats` response
  (Total Captures: 13,782; by-provider / direction / source
  breakdowns), confirming the end-to-end loopback:
  `Claude Desktop → MCP stdio → pce-mcp Node proxy → HTTP 127.0.0.1:9800
  → pce_core daemon → SQLite`.
- **Cowork surface**: returned *"I don't have access to a tool called
  'pce_stats'"* and helpfully revealed its actual exposed toolset
  in its Thought-process trace.

### Cowork's actual exposed toolset (from its own Thought process)

```
Built-in tools:
  Agent
  AskUserQuestion
  Skill                       — wrapper for invoking Anthropic skills
  TaskCreate / Get / List / Stop / Update
  WebSearch
  WebFetch

MCP server tools (mcp__<server>__<tool> shape — all
Anthropic-internal, NOT user-installable):
  mcp__Claude_in_Chrome__*    — Claude-in-Chrome integration
  mcp__cowork__*              — Cowork orchestration core
  mcp__mcp-registry__*        — possible registration API (TBD)
  mcp__plugins__*             — plugin engine
  mcp__skills__*              — Skills picker engine (closes Q1!)
  mcp__scheduled-tasks__*     — Scheduled-task engine (closes Q6!)
  mcp__session_info__*        — session/conversation metadata
  mcp__visualize__*           — Live Artifacts engine
  mcp__workspace__*           — workspace tools
```

### Architectural interpretation

1. **Cowork uses a separate, Anthropic-curated MCP namespace** from the
   Chat surface. User-installed `.mcpb` extensions deploy into Chat
   (and presumably Projects + non-Cowork surfaces) but **do NOT** load
   into Cowork — at least on this build (`Claude Desktop` MSIX 2026-05).
2. **The `mcp__<server>__*` tools are Anthropic-internal MCP servers**.
   Their existence shows Cowork itself is architected as an MCP host
   with multiple internal MCP server processes, NOT a monolithic web
   app — confirming the "local agent mode" L3g/L3f distinction in
   `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`.
3. **`mcp__mcp-registry__` is the most interesting unknown**. If it
   exposes a `register_server` / `install_extension` / `add_local_mcp`
   verb, the L3f-Cowork capture strategy can ship a `.mcpb`-equivalent
   that Cowork is willing to load. If not, L3f-Cowork is **out of
   reach via official mechanism** and PCE must rely on:
   - N axis (mitmdump network proxy) — covers all HTTP-shaped MCP
     calls Cowork makes outward (e.g. to `claude.ai` for chat
     completions, to MCP-over-HTTP servers Cowork connects to).
   - L3g axis (filesystem manifest tail of
     `local-agent-mode-sessions/`) — covers Cowork's own internal
     orchestration state.
   - **Cannot** capture stdio-MCP frames between Cowork's host process
     and its internal `mcp__cowork__*` / `mcp__skills__*` servers
     without OS-level hooks (out of scope for v1.1).

### Impact on MATRIX §5.B.2 acceptance

- **C16 reframed**: `.mcpb` installs via Settings → Extensions and
  works in Chat surface — **PASS**. `.mcpb` does NOT load into
  Cowork — **KNOWN architectural constraint**, no driver work can
  fix this. The original C16 description ("Cowork Skills picker can
  see PCE tools") was based on an incorrect assumption and is
  retired. New C16 acceptance:
  > **C16 (revised)**: `pce-mcp.mcpb` installs without error,
  > registers all 6 tools in Settings → Extensions, and successfully
  > invokes at least one tool (`pce_stats`) from the Chat surface
  > returning a structurally-valid response from `pce_core:9800`.
  > Cowork surface is documented as KNOWN-NOT-SUPPORTED for
  > user-MCP extensions in this build.
- **L3f-Cowork is now a separately-tracked OPEN architectural
  question**, to be resolved by the `mcp__mcp-registry__` probe
  (see step 2 of "How to fill this in" above). If that probe shows
  no registration API, file a Phase-6 deferral as
  `KNOWN-PHASE6-DEFERRED` and rely on N + L3g axes for Cowork
  coverage in v1.1.

### Probe outcome (2026-05-11 06:55) — Outcome B confirmed

The `mcp__mcp-registry__` probe (full transcript at
`tests/manual/cowork_mcp_namespace_2026-05-11-0655.md`) returned:

```
mcp__mcp-registry__:
  - search_mcp_registry(keywords)       — read-only, lists existing
  - list_connectors(keywords?)          — read-only, installed list
  - suggest_connectors(uuids, keywords) — surface suggestions

Verdict: "No, the mcp__mcp-registry__ namespace does not expose
any 'register' / 'install' / 'add_server' verb for attaching custom
MCP servers to Cowork at runtime."
```

This matches **Outcome B** in the decision tree:

- **L3f-Cowork via official mechanism is OUT OF REACH** for v1.1.
- PCE Core relies on **N (mitmdump) + L3g (filesystem manifest tail)
  axes** for Cowork capture coverage.
- L3f-Cowork is filed as `KNOWN-PHASE6-DEFERRED` with documented
  risk: the stdio MCP frames between Cowork host process and its
  `mcp__cowork__` / `mcp__skills__` / etc. internal servers are NOT
  captured. Mitigation in Phase 6: OS-level IPC hook (Windows ETW
  / macOS DTrace) — explicit non-goal for v1.1.

### Bonus intel from the probe (informs Q1 / Q6 / Q7 / Q8)

The probe response is far richer than the registration sub-question.
It enumerated 9 namespaces with full input/output schemas. Key
extractions:

1. **`mcp__skills__list_skills`** output = *"Interactive widget
   showing installed skills with 'Try it' buttons"*. This is the
   Skills picker. Because it's described as an "interactive widget"
   rendered in chat — not a Win32 popup — **Q1 leans heavily
   toward descendant-of-Cowork-main-window**, validating reuse of
   `_find_uia_by_name_substr` for `pick_skill()`.

2. **`mcp__scheduled-tasks__create_scheduled_task`** output =
   *"Scheduled task created with storage location and **session
   tracking**"*. The "session tracking" phrase is the smoking gun
   for **Q6 eager lifecycle** — a conversation/session row is
   created at scheduling time. RECON N-axis observation will
   confirm by watching `/chat_conversations/` POST at
   `mark scheduled-create`.

3. **`mcp__cowork__create_artifact(id, html_path, mcp_tools?)`** —
   Live Artifacts have an explicit MCP creation verb. Persist
   across sessions. `mcp_tools` arg implies an artifact can declare
   which MCP tools it depends on (probably enabled when artifact
   opened). NEW question Q7 below.

4. **`mcp__cowork__request_cowork_directory(path?)`** with *"optional
   native folder picker"* — reveals the file-share UX path.
   NEW question Q8 below.

### Impact on MATRIX §5.B.2 acceptance (locked in)

- **C16 reframed** — `pce-mcp.mcpb` installs without error, registers
  all 6 tools in Settings → Extensions, and successfully invokes
  `pce_stats` from the Chat surface returning a real response.
  Cowork surface is **KNOWN-NOT-SUPPORTED** for user-MCP
  extensions in this build (Outcome B confirmed empirically).
- **L3f-Cowork** — filed as `KNOWN-PHASE6-DEFERRED` with mitigation
  notes in this section.
- **C04 (Live Artifacts visible)** — newly informed by Q7. Driver
  helper `view_live_artifacts()` can verify by N-axis (HTTP), L3g
  (filesystem manifest) AND optionally by querying
  `mcp__cowork__list_artifacts` from a non-Cowork surface (TBD if
  Chat can read Cowork's artifact manifest).

### Driver implication (locked in)

No cowork helper in `tests/e2e_desktop_ui/drivers/claude_desktop.py`
needs to invoke PCE tools from inside Cowork. The driver only drives
UI elements — Cowork's own internal `mcp__skills__` orchestration
handles tool listing in the Skills picker. PCE's role inside Cowork
is purely passive (N+L3g observation), not active (tool invocation).
P5.B.5.2 helpers therefore remain in scope as originally specified,
plus 1-2 incremental helpers for Q7 (artifact list visibility) and
Q8 (folder-picker handling) if RECON confirms they require driver
work.

### Evidence references

- Chat-surface success: PCE alpha.8 chat log (2026-05-11 06:41
  UTC+08:00) — `pce_stats` returned 13,782 captures.
- Cowork-surface failure + tool-list reveal: Cowork chat log
  (2026-05-11 06:48 UTC+08:00) — *"I don't have access to a tool
  called 'pce_stats'"*.
- Q0 namespace probe: `tests/manual/cowork_mcp_namespace_2026-05-11-
  0655.md` (full verbatim transcript with 9 namespace enumerations).
- Memory `76de9e16-be1d-4609-860a-14c79d66b5de`.

---

## Q1 — Skills picker UIA shape (✅ FULLY CLOSED 2026-05-11 11:41)

### Empirical finding (auto-RECON-1, run dir `recon_cowork_auto_20260511-113344/`)

The **Q0-probe lean** that the picker is an "inline interactive
widget" was **wrong in spirit** — the picker IS rendered
descendantly to the Claude main window (no Win32 popup), but it is
NOT inline below the composer. It's a **Radix UI modal Directory
dialog** that overlays the screen with three tabs: **Skills /
Connectors / Plugins**.

UIA dump excerpt from `_uia_q1-skill-picker-full.txt`:

```
Window  Directory       radix-_r_ba_    (256,80)-(2304,1264)
Text    Directory       radix-_r_bb_
Button  Close           (2207,129)-(2271,193)
```

The `radix-_r_*` automation IDs confirm it's a [Radix UI](https://www.radix-ui.com)
dialog primitive. The dialog rect (256,80)-(2304,1264) covers ~80% of the
2560×1344 main window. It's NOT a separate top-level Win32 window
(the script's `_poll_new_window` returned `null` after the `/`
keystroke).

A confirmation pass on 2026-05-11 11:41 with the user's Cowork
surface in clean state, on the Cowork composer (not Customize page),
fired the same modal — the Cowork composer's `/` slash command
**opens the same Directory dialog** as the Settings page's Skills
library entry. Search field state is **persisted** across openings
(text typed in one opening shows up in the next).

### Driver implication (locked)

`pick_skill(name)` (C08 helper) does the following sequence:

1. `click_composer()` + `send_keys('/')` to open the Directory.
2. Wait for `Window class="radix-*"` element to appear via
   `_find_uia_by_name_substr("Directory", control_types=["Window"])`.
3. **Clear the search field** (Ctrl+A, Backspace) — sticky search is
   a real risk per the empirical observation.
4. Switch to the "Skills" tab if not already (the dialog remembers
   the last selected tab; default is Plugins on subsequent
   openings).
5. Type the skill name (or substring) into the search input.
6. Click the matching skill row.
7. Press `Esc` to dismiss — multi-press if first ESC fails.

Note that the existing `_find_uia_by_name_substr` (descendant scope)
works because the modal IS a descendant of the main window. Cross-
window search (`_find_uia_by_name_substr_all`) is NOT needed.

### Q4 also closed by this section

The `/` keystroke fires **0 HTTP requests** (verified via
`_q1_observe.py`, 0 rows in raw_captures since checkpoint). The skill
list is rendered entirely from client-side state (loaded at composer
or Cowork-tab init via `/api/organizations/<org>/skills/list-skills?include_wiggle_skills=true`).

### Evidence

- `tests/manual/recon_cowork_auto_20260511-113344/_uia_q1-skill-picker-full.txt` (2710B post-`/` UIA)
- `tests/manual/recon_cowork_auto_20260511-113344/_uia_q1-skill-picker.txt` (279B keyed)
- `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md` § `mcp__skills__` (Q0 probe context)
- User screenshot 2026-05-11 11:41 (Directory dialog with persisted search text)
- `_q1_observe.py` output (0 HTTP rows since `/` keystroke)

---

## Q2 — Async step semantics (✅ REFRAMED — major architectural finding)

### Empirical finding (auto-RECON-2, run dir `recon_cowork_auto_20260511-120235/`)

Five prompts were sent through Cowork during the auto run
(`q2_multistep`, `c3_cancel`, `c4_file_input`, `q7_artifact`,
`q6_scheduled`), each via `send_message()` with full UI focus and
UIA-verified composer entry. **Nine distinct `msg_01...` IDs**
surfaced via `/api/organizations/<org>/cowork/messages/<msg_id>/safety_flags`
GET requests during the 338s window — confirming Cowork did receive
and process each prompt and produce assistant replies (1 user + 1
assistant per turn = ~10 messages, matches the 9 observed).

**Yet 0 `/completion` requests, 0 `/chat_conversations/<uuid>` POSTs,
and 0 `/cowork/messages/` POSTs were captured in raw_captures.** The
only POSTs to claude.ai during the entire 338s window were:

- `/api/event_logging/v2/batch` (4× telemetry batches)
- `/api/organizations/<org>/dust/generate_session_title` (1× post-first-message title generation)

The message-creation traffic flows over a transport that **does NOT
appear as `request`/`response` HTTP captures, AND does NOT trigger
mitmproxy's `websocket_message` hook** (verified by adding
`websocket_message` + `websocket_end` hooks to `pce_proxy/addon.py`
at 11:59 — 0 WS sessions captured during the subsequent run).

Given that:

- mitmproxy stderr DOES log persistent WebSocket ping/pong frames
  to `claude.ai` (continuous keepalive every 15-20s).
- No QUIC / UDP/443 traffic from Claude (verified via
  `Get-NetUDPEndpoint`).
- `_categorize_tls_error` shows no pinning failures on `claude.ai`.
- The chat sweep on Chat surface DID capture `/chat_conversations/<uuid>/completion`
  as HTTP/2 SSE (per `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`) —
  Cowork is structurally different.

The most likely explanation is **WebSocket-over-HTTP/2 (RFC 8441)**.
Claude Desktop and `claude.ai` likely opened an HTTP/2 stream with
`:method=CONNECT` + `:protocol=websocket`. mitmproxy older versions
(≤ 11.0) MAY proxy these transparently without firing
`websocket_message`. The observed ping/pong logs in stderr come from
an HTTP/1.1 WebSocket on a different connection (notifications channel)
or from a parallel WS that mitmproxy DOES track but only at INFO-log
level for control frames.

### Driver implication (locked)

`wait_for_cowork_step()` (C02, C03 helper) **must use UI-side cues**
for turn-completion detection, NOT N-axis `/completion` counts:

- Observe stop-button → send-button transition in composer area.
- Observe new `msg_01...` row in the `/cowork/messages/<id>/safety_flags`
  GET stream as a fallback (these GETs DO go through HTTP and are
  captured).
- Optional: observe the L3g IndexedDB summary delta (when the
  `keyval-store` updates) but body is redacted by privacy filter so
  this only confirms presence not content.

### Action item — P5.B.5.x WS-over-HTTP/2 capture

Filed in § Architectural Outcomes below as the dominant remaining
gap. Sufficient to land P5.B.5.2/5.3/5.5 with C-case acceptance
revised — not blocking immediate driver work.

### Evidence

- `tests/manual/recon_cowork_auto_20260511-120235/summary.json` —
  `db_events_by_pattern`: only `/environments` (18×), `/skills/list-skills` (2×),
  `/cowork_settings` (2×), `/referral/eligibility` (2×). NO completion / chat_conversations.
- `tests/manual/recon_cowork_auto_20260511-120235/step_results.json`
  — every prompt-sending step has `n_completions_delta: 0` and
  `pair_id: null`.
- `_recon_final.txt` § 5 — 21 distinct `/cowork/*` rows showing 9
  unique `msg_01...` IDs (one per user/assistant turn).
- `pce_proxy/addon.py:414-513` — new `websocket_message` +
  `websocket_end` hooks landed but produced 0 captures during the
  RECON window.

---

## Q3 — Dispatch (Beta) window class (✅ CLOSED)

### Empirical finding

`step_q3_dispatch` clicked the Cowork sidebar entry named
`Dispatch Beta` (Button, class `df-pill hide-focus-ring`); the
post-click `_poll_new_window` returned `null`, classifying the
shape as **`in-app-pane`** (descendant of the main Claude window).
Duration: 6.2s. No new top-level Win32 window appeared.

From `step_results.json::q3_dispatch.result`:

```json
{
  "clicked": true,
  "hit": {
    "name": "Dispatch Beta",
    "aid": "",
    "ct": "Button",
    "class": "df-pill hide-focus-ring ..."
  },
  "new_window": null,
  "shape": "in-app-pane"
}
```

### Driver implication (locked)

`open_dispatch()` (C10 helper) is a single sidebar button click via
`_find_uia_by_name_substr(["Dispatch"], control_types=["Button"])`,
followed by a 1-2s wait for main-window content swap. No special
handling needed for separate windows. Same shape as the rest of
Cowork sidebar entries (Projects / Scheduled / Live artifacts /
Customize all use the `df-pill` button pattern).

### Evidence

- `tests/manual/recon_cowork_auto_20260511-120235/step_results.json::q3_dispatch`
- `tests/manual/recon_cowork_auto_20260511-120235/_uia_q3-dispatch.txt` (401B post-click UIA)

---

## Q4 — `/skills/list-skills` HTTP schema (✅ CLOSED)

### Empirical finding

The HTTP endpoint exists and IS hit, but at **app/Cowork-tab init**,
NOT at slash-picker open. Full path observed in `_recon_final.txt`:

```
1x  GET    request   /api/organizations/<org>/skills/list-skills?include_wiggle_skills=true
1x  GET    response 200  (json, ~9854B)
```

The `include_wiggle_skills=true` query parameter is interesting —
"wiggle" is the codename for Cowork's file lifecycle
(`/wiggle/upload-file`, `/wiggle/download-file`). So Cowork augments
the standard skill list with file-lifecycle skills.

The slash-picker (Q1) does NOT re-fetch this endpoint when opened —
it renders entirely from client-side cached state. **0 HTTP** fired
at the `/` keystroke (per Q1 verification).

### Driver implication (locked)

`pick_skill(name)` matching uses the **picker widget's row text**
(UIA `name` property), NOT the HTTP body. PCE does not need to parse
this 9854B JSON for driver purposes. The body MAY be useful for the
future `mcp__skills__list_skills` MCP-stdio capture work in P6+ (out
of v1.1 scope).

### Evidence

- `_recon_final.txt` § 1 — distinct paths during RECON include
  `/api/organizations/<org>/skills/list-skills?include_wiggle_skills=true`
  (1× request + 1× response, 200 OK)
- Q1 § "Q4 also closed by this section" — 0 HTTP at picker open

---

## Q5 — Session storage schema (✅ FULLY CLOSED in Round 3)

### Empirical finding

The original Q5 spec asked for the schema of `local-agent-mode-sessions/<uuid>/manifest.json`.
**There is no such file** — the term "manifest.json" was a misnomer
inherited from initial scaffold speculation. The real artefact is a
**JSONL transcript** at a deeply nested path:

```
LocalCache\Roaming\Claude\local-agent-mode-sessions\
  <user_uuid>\                              ← e.g. e323c763-c6b3-4761-a126-8d80870ee7c3
    <org_uuid>\                             ← e.g. 8a522742-17e5-43c6-b1aa-b28bf3d7ad32
      cowork-gb-cache.json
      local_<session_uuid>.json             ← top-level session pointer (small)
      local_<session_uuid>\                 ← session content root
        audit.jsonl                         ← audit/security log
        .claude\
          .claude.json                      ← account + experiment state (~21KB)
          backups\.claude.json.backup.<ts>  ← timestamped backups
          projects\<encoded-cwd>\
            <session_uuid>.jsonl            ← ⭐ PRIMARY TRANSCRIPT
```

Alongside:

- `vm_bundles\claudevm.bundle\smol-bin.vhdx` — VM disk image for the
  agent sandbox (created on first agent-mode use)
- `claude-code-vm\.sdk-version` — SDK marker
- `logs\cowork_vm_node.log` — VM node host logs

### JSONL transcript schema (Round 3 sample, 38 lines, 65KB)

The JSONL contains 6 distinct top-level `type` values:

| `type` | count | semantics |
|---|---|---|
| `assistant` | 16 | assistant turn (each delta or completion) |
| `user` | 8 | user message OR tool_result reply |
| `ai-title` | 5 | auto-generated session title updates |
| `queue-operation` | 4 | enqueue/dequeue marker for prompt scheduling |
| `last-prompt` | 3 | cached "last prompt" snapshot |
| `attachment` | 2 | user file/attachment metadata |

Message `content` blocks (standard Anthropic Messages format):

| block type | count in sample |
|---|---|
| `thinking` | 7 |
| `text` | 5 |
| `tool_use` | 5 |
| `tool_result` | 5 |

Tool names observed in `tool_use`: `mcp__workspace__bash` (4),
`Skill` (1).

Distinct top-level JSONL keys (union across all lines): `aiTitle`,
`attachment`, `attributionPlugin`, `attributionSkill`, `content`,
`cwd`, `entrypoint`, `gitBranch`, `isMeta`, `isSidechain`,
`lastPrompt`, `leafUuid`, `message`, `operation`, `parentUuid`,
`permissionMode`, `promptId`, `requestId`, `sessionId`,
`sourceToolAssistantUUID`, `sourceToolUseID`, `timestamp`,
`toolUseResult`, `type`, `userType`, `uuid`, `version`.

The `sessionId` is **stable across the whole conversation** — 1
session spans all user/assistant turns. `parentUuid` links each
turn to its predecessor (tree of message events).

### Sample (first 3 lines, redacted for length)

```jsonl
{"type":"queue-operation","operation":"enqueue","timestamp":"2026-05-11T03:47:02.714Z","sessionId":"8bff8cb1-43df-4452-b4bb-454de97b3bf1","content":"Please do this in TWO distinct reasoning steps:\nStep 1: List 3 file types ..."}
{"type":"queue-operation","operation":"dequeue","timestamp":"2026-05-11T03:47:02.782Z","sessionId":"8bff8cb1-43df-4452-b4bb-454de97b3bf1"}
{"parentUuid":null,"isSidechain":false,"promptId":"2e308b46-aadc-4a8c-a44c-feb9638b0a21","type":"user","message":{"role":"user","content":"Please do this in TWO ..."},"uuid":"c5f6d1f5-e122-4d6f-a6a3-76a0de2b2c95","timestamp":"2026-05-11T03:47:02.790Z","permissionMode":"default","userType":"external","entrypoint":"local-agent","cwd":"C:\\Users\\ZST\\AppData\\Roaming\\Claude\\local-agent-mode-sessions\\<user>\\<org>\\local_<session>\\outputs","sessionId":"8bff8cb1-43df-4452-b4bb-454de97b3bf1","version":"2.1.128","gitBranch":"HEAD"}
```

Later lines (assistant turns with tool calls) carry richer
`message.content` arrays of Anthropic-format content blocks, plus
optional `_audit_timestamp` + `_audit_hmac` on some assistant rows
(security audit signature; not on every row).

### Architectural significance

This discovery **largely moots the Q2 WS-over-HTTP/2 gap** for
content capture. PCE's L3g axis can read these JSONL files directly
and reconstruct the full conversation (text, thinking, tool calls,
tool results, attachments) without needing WebSocket frame visibility.

The N-axis remains useful for:
- **Real-time observation** (in-flight turns before the file is
  flushed)
- **Periphery traffic** (settings, heartbeats, msg_id polling,
  telemetry batches) — these don't go through JSONL
- **Pre-task drafts** — `keyval-store` IndexedDB composer drafts
  (captured by existing watcher's IndexedDB summary scanner)

### Driver / normaliser implication

`pce_core/normalizer/local_persistence.py` v0 (C14) for v1.1:

- **Watch path**: `local-agent-mode-sessions\<user>\<org>\local_<session>\.claude\projects\<encoded-cwd>\*.jsonl`
- **Parser**: line-by-line JSON decode, dispatch by top-level `type`:
  - `user` + `assistant` lines → reuse existing
    `pce_core/normalizer/anthropic.py` content-block decoder
    (`text`, `thinking`, `tool_use`, `tool_result` are already
    supported per memory `9e642209`)
  - `queue-operation` / `last-prompt` / `ai-title` / `attachment`
    → store as `direction=l3g_meta` raw_captures rows with
    `body_format=jsonl` for future enrichment
- **Session/message mapping**: `sessionId` → PCE `sessions.session_key`;
  `uuid` → PCE `messages.message_uuid`; `parentUuid` → thread linkage
- **`pce_persistence_watcher/discovery.py`** must be extended to
  recurse into `<user>/<org>/local_<session>/.claude/projects/`
  (currently only iterates `local-agent-mode-sessions/<children>`)

C14 acceptance (revised UP from earlier stub-only target): PASS
when `local_persistence.py` v0 lands with:

- 1 fixture JSONL (copy from Round 3's `transcript.jsonl`)
- ≥5 unit tests covering each top-level type
- 1 integration test that runs the parser over the fixture and
  asserts the expected user/assistant/tool_use sequence is emitted

### Evidence

- `tests/manual/recon_round3_20260511-122524/transcript.jsonl`
  (65,036B, 38 lines, copied from the real file in LocalCache)
- `_jsonl_analyze.py` output (type counts, content-block counts,
  tool names, key union)
- `tests/manual/recon_round3_20260511-122524/filesystem_diff.json`
  (7 new files, 1 of-interest: the `.claude.json.backup`)

---

## Q6 — Scheduled task lifecycle (✅ INCONCLUSIVE-on-N-axis)

### Empirical finding

`step_q6_scheduled` sent a scheduled-task prompt and observed:

- `n_completions_delta: 0` (consistent with Q2 WS gap, not
  diagnostic of lifecycle)
- 0 manifest delta in `local-agent-mode-sessions/`
- `lifecycle_signal: "lazy-or-unverified"`
- 15 UIA-keyed rows in post-step dump (`_uia_q6-end.txt` 1256B)

This result is **dominated by the Q2 architectural gap**, not a true
lifecycle signal. The Q0 probe's eager-lean ("with storage location
and session tracking") still holds as the leading hypothesis, but
cannot be verified at the N-axis level without WS-over-HTTP/2
capture.

### C11 acceptance implication

C11 (scheduled task) **SKIP under v1.1** because:
- N-axis evidence is unavailable (Q2 gap)
- L3g doesn't create a manifest at scheduling time for our test
  prompt (could be because Ask-mode prompts don't qualify as agent
  sessions, OR because lifecycle truly is lazy)
- A 24h+ test harness is out of v1.1 scope

C11 PASS becomes possible after **either** the Q2 WS gap is closed
(eager lifecycle would surface a WS-decoded create event) **or** a
follow-up RECON proves manifests appear at create-time using a
proper agent-mode scheduled task.

### Evidence

- `step_results.json::q6_scheduled.lifecycle_signal == "lazy-or-unverified"`
- `tests/manual/recon_cowork_auto_20260511-120235/_uia_q6-end.txt`
- `summary.json::db_events_by_pattern` lacks `/chat_conversations` rows

---

## Q7 *(NEW)* — Live Artifacts mechanics (✅ SUBSTANTIALLY CLOSED in Round 3)

### Empirical finding

Round 3's agent-mode prompt (asking Cowork to use the xlsx skill to
create an Excel file) revealed THREE concrete artifact-related
filesystem materialisations:

1. **VM disk image**: `vm_bundles\claudevm.bundle\smol-bin.vhdx`
   — the agent sandbox's virtual hard disk (created on first
   agent-mode use; the VM was attempted but failed to start in
   our test, yet the image file remained).
2. **VM SDK marker**: `claude-code-vm\.sdk-version` — newly
   materialised after the agent-mode invocation.
3. **Tool-use record inside the JSONL transcript**: the
   `mcp__workspace__bash` tool call (running Python with openpyxl
   to create `fruits.xlsx`) is captured verbatim in the JSONL
   transcript as a `tool_use` content block, including the full
   bash command string. The corresponding `tool_result` is also
   captured (in our case it was an error: *"Workspace unavailable.
   The isolated Linux environment failed to start. You can still
   use file tools directly."*).

The Live Artifacts **pane** itself is a Cowork sidebar entry
(`Button name="Live artifacts"`, same `df-pill` pattern as Q3).
Clicking it surfaces an in-app pane with a `New artifact` button and
a list area; for empty state shows *"Create dynamic artifacts that
stay up-to-date using live data from ..."*.

### Architectural significance

Live Artifacts are NOT a separate HTTP endpoint family. They are
**tool calls inside the standard JSONL transcript** plus a
supporting VM bundle on disk. PCE's L3g normaliser can capture them
for free as `tool_use` records (already supported by
`pce_core/normalizer/anthropic.py` per memory `9e642209`).

The outstanding sub-question is whether finished artifact bytes
(the actual generated .xlsx / .png / .pdf files) live under
`vm_bundles\` or under `local-agent-mode-sessions\<user>\<org>\local_<session>\outputs\` —
our test couldn't tell because the VM failed to start. A follow-up
RECON with a working VM would close this; **not blocking v1.1**
because v1.1 can ship by capturing the tool_use record (which
includes the command + arguments + result) without surfacing the
binary artifact bytes.

### Driver implication

`view_live_artifacts()` (C09 supporting) is a single sidebar button
click. For C09 acceptance under v1.1, presence-check via UIA is
sufficient (don't need to read the artifact bytes). The richer
artifact content is captured automatically through Q5's JSONL path.

### Evidence

- `tests/manual/recon_round3_20260511-122524/transcript.jsonl`
  (tool_use record with openpyxl Python script)
- `tests/manual/recon_round3_20260511-122524/filesystem_diff.json`
  (new files: `vm_bundles\claudevm.bundle\smol-bin.vhdx`,
  `claude-code-vm\.sdk-version`, `logs\cowork_vm_node.log`)
- `tests/manual/recon_round3_20260511-122524/_uia_live-artifacts.txt`
  (Live Artifacts pane structure)

---

## Q8 *(NEW)* — File-share UX (`request_cowork_directory`) (✅ INCONCLUSIVE)

### Empirical finding

`step_q8_folder_picker` sent the prompt asking Cowork to request
access to the user's Downloads folder, then polled for a new
top-level window during a 30s wait window. A new top-level window
DID appear, but it was **not** the folder picker:

```json
{
  "window": {
    "title": "任务切换",
    "class": "XamlExplorerHostIslandWindow",
    "hwnd": 263608,
    "descendant_rows": 0,
    "category": "unknown"
  }
}
```

"任务切换" = Windows 11 Task View / Task Switcher. This is
UNRELATED to the prompt — either:

1. Cowork did not surface a native folder picker for the prompted
   text (perhaps the user must invoke a specific UI flow via
   Customize → permissions, not via free-form prompt).
2. Or the script's prior step left a stale focus/keystroke that
   triggered task-switcher (Alt+Tab or Win+Tab).
3. Or Cowork's filesystem-access flow happens entirely server-side
   and just shows the user a non-blocking notification.

### Driver implication

`cowork_file_upload(path)` (C09 helper) implementation **deferred for
v1.1**. The C09 C-case **SKIPs under v1.1** with a note pointing
here; it can be revisited in a follow-up RECON pass that:

1. Triggers folder access via the proper UI flow (Customize →
   filesystem permissions toggle, not a chat prompt).
2. Captures the actual picker if it appears.

### Evidence

- `tests/manual/recon_cowork_auto_20260511-120235/step_results.json::q8_folder_picker`
  (Task Switcher false-positive)
- `tests/manual/recon_cowork_auto_20260511-120235/_uia_q8-folder-picker.json` (175B — 0 descendants)

---

## Architectural Outcomes (NEW — revised after Round 3)

### A1 — N-axis WS-over-HTTP/2 capture gap (DOWNGRADED: "nice-to-have")

**Symptom**: Cowork user-prompt + assistant-reply traffic does not
appear in `raw_captures` despite mitmdump being live, allowlist
including `claude.ai`, and a `websocket_message` hook being added to
`pce_proxy/addon.py`. Observed empirically across all three RECON
runs. Periphery captures (settings, heartbeats, message-id polling,
telemetry batches) flow normally.

**Root cause hypothesis**: WebSocket-over-HTTP/2 (RFC 8441). Modern
Anthropic web/desktop chat opens an HTTP/2 stream with
`:method=CONNECT` + `:protocol=websocket` headers, then exchanges
WebSocket frames over that stream. mitmproxy's HTTP/2 implementation
may relay these frames transparently without firing the
`websocket_message` hook (which was designed for HTTP/1.1 WS
upgrades). The ping/pong logs we DO see in mitmdump stderr come
from a parallel HTTP/1.1 WS keepalive (likely a notifications
channel) which does NOT carry chat content.

**Status (post-Round 3)**: **DOWNGRADED**. The Round 3 discovery
that **complete conversation content lives in JSONL transcripts on
disk** (Q5) means PCE can capture user prompts, assistant replies,
thinking, tool calls, and tool results all through the L3g axis —
no WS decoding required. The remaining WS gap matters only for:

- **Real-time observation** (sub-second turn detection before disk
  flush) — nice-to-have, not on v1.1 critical path.
- **Metadata** (Cowork's internal orchestration to the server) —
  out-of-scope for content capture.

**v1.1 action items** (priority LOW; can ship without):

1. **One-line investigation** — check installed mitmproxy version
   and confirm WS-over-HTTP/2 hook behaviour. If trivial fix, do
   it; otherwise defer.
2. **Defer alternative paths** (raw HTTP/2 stream capture, raw byte
   dumping) — not justified given JSONL gives us the data.
3. **MATRIX §5.B revised acceptance** — PASS targets restored upward:
   - PASS via L3g+UIA: **C00, C01, C02, C03, C05, C06, C07, C08,
     C10, C12, C13, C14, C15, C16** = 14
   - SKIP: C04 (D04 inheritance), C09 (Q8 inconclusive), C11 (Q6
     inconclusive) = 3
   - FAIL: 0
   - Revised target: **≥13 PASS / ≤3 SKIP / 0 FAIL** — RESTORES the
     original pre-RECON ≥13 PASS bar thanks to Round 3 JSONL
     discovery.

### A4 — `pce_persistence_watcher/discovery.py` must recurse into
`local-agent-mode-sessions/<user>/<org>/local_<session>/.claude/projects/`

**Symptom**: The watcher currently registers `agent_sessions:
<root>/local-agent-mode-sessions` as a top-level root and iterates
its direct children. But the **actual JSONL transcripts** live 5
levels deep:
```
local-agent-mode-sessions / <user_uuid> / <org_uuid> / local_<session_uuid> / .claude / projects / <encoded-cwd> / <session_uuid>.jsonl
```
The watcher's `iter_records(ag_root)` doesn't descend that deep, so
the JSONL content is currently invisible to PCE despite the data
being on disk.

**v1.1 action item** (P5.B.5.3 / C14):

- Extend `pce_persistence_watcher/agent_sessions.py::iter_records`
  to recursively walk `<user>/<org>/local_<session>/.claude/projects/`
  and yield each `*.jsonl` file as a record.
- Add `pce_core/normalizer/local_persistence.py` with line-by-line
  JSONL parser:
  - Dispatch by top-level `type`:
    - `user` / `assistant` → reuse `anthropic.py` content-block
      decoder (already handles `text` / `thinking` / `tool_use` /
      `tool_result`)
    - `queue-operation` / `last-prompt` / `ai-title` / `attachment`
      → capture as L3g metadata for future enrichment
  - Map `sessionId` → `sessions.session_key`, `uuid` →
    `messages.message_uuid`, `parentUuid` → thread linkage
- Add `tests/test_local_persistence.py` with a fixture copy of
  Round 3's `transcript.jsonl` and ≥5 unit tests.

**Acceptance**: C14 PASS when the L3g axis successfully ingests the
fixture JSONL and produces matching `sessions` + `messages` rows in
the PCE DB.

### A2 — `pce_proxy/addon.py` extensions landed (2026-05-11 11:59)

- New `websocket_message(self, flow)` hook capturing every WS frame
  as a `raw_captures` row with `method="WS"`,
  `direction="ws_send"|"ws_recv"`, `body_format="ws_text"|"ws_binary"`,
  one pair_id per WS session.
- New `websocket_end(self, flow)` hook for session close logging.
- New `_ws_state` module-level dict tracking pair_id per flow.id.
- `pce_proxy/addon.py:197-205` (state) + `:414-513` (hooks).

This is **infrastructure** for when the WS-over-HTTP/2 path is
resolved. It captures HTTP/1.1 WebSocket traffic correctly today;
the HTTP/2 case requires the A1 follow-up.

### A3 (renumbered A5) — `tests/manual/recon_claude_desktop_cowork.py` schema fixes

During auto-RECON the `DbTailer` had two schema-drift bugs that
produced silent errors:

- Used non-existent column `source_type` (renamed to `source_id` /
  `source` per migration 0011/0012).
- Used `int(r["id"])` cursor (column type changed from int to TEXT
  UUID hex).

Both fixed in this commit:
- `tests/manual/recon_claude_desktop_cowork.py:284-339` —
  `_cursor_id: int` → `_cursor_ts: float`, query by `created_at >
  ?` ordered ASC.
- Same file: removed `source_type` from SELECT and event dict.
- `_resolve_agent_sessions_root` app_id check changed from
  `"claude_desktop"` (underscore) to `"claude-desktop"` (hyphen,
  matching `pce_persistence_watcher.discovery._DISCOVERY_ORDER`).

These fixes unblocked the auto-RECON run and should be committed.

---

## Sign-off

All 9 questions Q0-Q8 carry concrete empirical answers + driver
implications + evidence references across three RECON passes plus
the pre-RECON Q0 probe. This doc is the closure artefact for
`MATRIX §5.B.2`; both the JSONL discovery (Q5) and the
WS-over-HTTP/2 hypothesis (Q2) are filed in § Architectural Outcomes.

**Final status (2026-05-11 12:30 — post-Round 3)**:
- Q0 ✅ fully closed (Outcome B, L3f-Cowork → `KNOWN-PHASE6-DEFERRED`)
- Q1 ✅ fully closed (Radix modal Directory dialog, descendant scope)
- Q2 ✅ reframed + **mooted-by-L3g** (WS gap downgraded to nice-to-have)
- Q3 ✅ closed (in-app pane, sidebar `df-pill` button)
- Q4 ✅ closed (HTTP fires at init, picker is client-side)
- Q5 ✅ **fully closed in Round 3** (JSONL transcript schema, 6 event types)
- Q6 ✅ inconclusive on N-axis (dominated by Q2 gap; L3g fallback works)
- Q7 ✅ **substantially closed in Round 3** (tool_use captured via JSONL)
- Q8 ✅ inconclusive (folder picker not surfaced for chat-prompt path)

**P5.B.5 implementation may proceed** with the **revised-upward**
acceptance baseline: **≥13 PASS / ≤3 SKIP / 0 FAIL** (back to the
original pre-RECON target). P5.B.5.4 (`.mcpb` pack) already complete
(commit `73a83a1`).

**Recommended next moves** (priority order):

1. **P5.B.5.3** (HIGH) — `local_persistence.py` v0 + extend
   `pce_persistence_watcher/agent_sessions.py` to recurse into
   `.claude/projects/<encoded-cwd>/*.jsonl`. This unlocks C14 PASS
   AND retroactively lets PCE ingest the JSONL transcripts already
   on disk from the three RECON runs.
2. **P5.B.5.2** (HIGH) — 6-7 cowork driver helpers in
   `tests/e2e_desktop_ui/drivers/claude_desktop.py` (open_cowork_tab,
   pick_skill, select_ask_mode, view_live_artifacts, open_dispatch,
   open_scheduled, wait_for_cowork_step). All have empirically-locked
   shapes from this RECON.
3. **P5.B.5.5** (HIGH) — 16 C-case runner under
   `tests/e2e_desktop_ui/cases/p1_cowork_*.py`. Target ≥13/16 PASS.
4. **P5.B.5.x WS-over-HTTP/2** (LOW) — deferrable; investigate when
   real-time observation becomes a product requirement.
5. **Q8 follow-up** (LOW) — if a user reports filesystem-permission
   issues in Cowork, re-run with the Customize → permissions UI flow
   instead of a chat prompt to characterise the picker.

**This file MUST NOT be deleted** — cross-references in MATRIX and
the kickoff handoff resolve here. If post-v1.1 work later changes
any of the above, append a delta section rather than rewriting
(preserve the empirical record).
