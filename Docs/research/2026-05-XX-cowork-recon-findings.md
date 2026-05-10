# Cowork-region RECON findings — PARTIAL (Q0 ✅, Q1/Q6 substantial, +Q7/Q8 new)

> **Status**: **Q0 fully closed** as **Outcome B** (read-only registry;
> L3f-Cowork via official channel out of reach) by the 2026-05-11
> 06:55 Cowork MCP namespace probe — evidence at
> `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md`. Probe also
> substantially closes **Q1** (Skills picker is an inline interactive
> widget rendered descendantly, not a separate Win32 popup) and
> partially closes **Q6** (scheduled tasks carry "session tracking"
> at create-time → lifecycle leans eager) and reveals **two new
> architectural questions** Q7 (Live Artifacts mechanics) and Q8
> (file-share UX). Net effect: original 60-min RECON scope can
> shrink to ~30 min, focused on Q2 / Q3 / Q5 + N-axis empirical
> verification of Q6's eager-vs-lazy lean.
>
> **Cross-references** from `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
> §5.B.2 / §7.5 and `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-
> KICKOFF-2026-05-10.md` §4 resolve here.
>
> **Authority**: this is the real findings doc once filled in. All
> 9 questions (Q0–Q8) below MUST carry empirical evidence before
> P5.B.5 implementation may proceed (per the gating rule in
> `HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` §4.3).
> Q0 forces a reframing of C16 (originally "Cowork sees PCE tools")
> AND files L3f-Cowork as `KNOWN-PHASE6-DEFERRED` per Outcome B.
> Q7 and Q8 are NEW acceptance items not in the original kickoff and
> require driver helpers beyond the original 6.

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
| **Q0** | Cowork MCP isolation model; registration API? | **✅ CLOSED (Outcome B)**: read-only registry, no register verb | C16 reframe; L3f-Cowork → `KNOWN-PHASE6-DEFERRED` | `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md` |
| **Q1** | Skills picker UIA shape | **80% CLOSED**: it's an inline interactive widget in chat (NOT separate popup) per `mcp__skills__list_skills` description; descendant scope confirmed in spirit, UIA dump remaining for `aid`/`name` of the widget rows | `pick_skill()` (C08) | `mcp__skills__` description + `_uia_dump_open-skills.txt` (smaller deliverable now — just need row identifiers) |
| **Q2** | Async step semantics (single SSE / SSE-per-step / long-poll) | TBD | `wait_for_cowork_step()` (C02, C03) | `events.jsonl` timing analysis on `mark task-multistep` window |
| **Q3** | Dispatch (Beta) window class (in-app pane vs separate popup) | **TBD** — NOT in MCP namespace (no `mcp__dispatch__`), so likely UI-only abstraction over Task* built-ins or `mcp__cowork__` orchestration | `open_dispatch()` (C10) | `_uia_dump_open-dispatch.txt` |
| **Q4** | `/skills/list-skills` HTTP schema | **DEPRIORITIZED**: probe shows the picker rides `mcp__skills__list_skills` (stdio MCP, internal) NOT `/skills/list-skills` HTTP. The 4927 B HTTP endpoint may be unrelated to Cowork picker (could be Chat-surface skill enumeration). Verify by N-axis observation during RECON | `pick_skill(name)` field matching (C08) | DB row body when/if `/skills/list-skills` is hit during cowork `mark skill-picker-open` |
| **Q5** | `local-agent-mode-sessions/<uuid>/manifest.json` field schema | TBD | `local_persistence.py` v0 (C14) | `manifests/<uuid>.json` |
| **Q6** | Scheduled task lifecycle (eager vs lazy `conversations/<uuid>` creation) | **50% CLOSED**: probe shows `create_scheduled_task` output explicitly mentions "storage location and session tracking" — strongly suggests **eager** (session/conversation row created at scheduling time, not fire time). Need N-axis confirmation | C11 SKIP-vs-PASS decision | `events.jsonl` around `mark scheduled-create` + L3g `local-agent-mode-sessions/` directory diff |
| **Q7** *(NEW)* | Live Artifacts mechanics: are they MCP-driven, persistent across sessions, where stored? | **PARTIAL**: probe shows `mcp__cowork__create_artifact(id, html_path, mcp_tools?)` and `list_artifacts() → {id, name, path, createdAt, updatedAt}`; artifacts ARE persistent across sessions and stored at `path` (location TBD — likely under `LocalCache\Roaming\Claude\cowork-artifacts\` or similar). Distinct from `mcp__visualize__show_widget` which is ephemeral inline SVG/HTML | `view_live_artifacts()` (C04 supporting) + future PCE artifact normaliser | RECON `mark live-artifacts-open` + filesystem snapshot of `LocalCache\Roaming\Claude\` before/after artifact creation |
| **Q8** *(NEW)* | File-share UX: how does `request_cowork_directory(path?)` surface a native folder picker, and does the picker block in a separate top-level window? | TBD | C09 (`cowork_file_upload`) driver — needs to handle the folder picker if it's a separate Win32 dialog | UIA dump after triggering `request_cowork_directory` (RECON: ask Cowork "please request access to my Downloads folder") |

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

## Q1 — Skills picker UIA shape (80% closed by Q0 probe)

**Empirical answer (partial)**: per `mcp__skills__list_skills`
output description — *"Interactive widget showing installed skills
with 'Try it' buttons"* — the picker is a **chat-inline interactive
widget**, NOT a separate top-level Win32 popup. It renders
descendantly inside the Cowork main chat surface.

**Remaining 20%**: the exact UIA `control_type` / `aid` / `name`
shape of each skill row in the rendered widget — needed so
`pick_skill(name)` can match-and-click programmatically. Closed by
`_uia_dump_open-skills.txt` after RECON Step 3.

**Driver implication (locked)**: `pick_skill()` reuses the standard
`_find_uia_by_name_substr` (descendant-of-main-window scope) like
the chat-region attach/style/model pickers from sub-runs 1–3. It
does **NOT** need the cross-window `_find_uia_by_name_substr_all`
mode added in sub-run 4 for the model picker (which lived in a
separate top-level window). This saves implementation complexity.

**Evidence**: `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md`
§ `mcp__skills__` (closes the popup-vs-widget axis); pending
`_uia_dump_open-skills.txt` (closes the per-row UIA shape).

---

## Q2 — Async step semantics

**Empirical answer**: TBD.

**Driver implication**: TBD.

**Evidence**: TBD (`events.jsonl` window between `mark task-multistep` and next marker).

---

## Q3 — Dispatch (Beta) window class

**Empirical answer**: TBD.

**New constraint from Q0 probe**: Dispatch is **not** a dedicated MCP
namespace (no `mcp__dispatch__` was enumerated). It's therefore most
likely **either** (a) a UI-only abstraction over the built-in `Task*`
tools (`TaskCreate` / `TaskList` etc.) and `mcp__cowork__` orchestration,
rendering as an in-app sidebar pane, **or** (b) a UI label for the
same underlying mechanism that powers the regular Cowork tab. Less
likely it's a top-level Win32 popup; almost certainly it's a
descendant pane.

**Driver implication**: lean toward `open_dispatch()` as a sidebar
button click + main-window content-change wait. Confirm via UIA
dump.

**Evidence**: TBD (`_uia_dump_open-dispatch.txt`).

---

## Q4 — `/skills/list-skills` HTTP schema (DEPRIORITIZED by Q0 probe)

**New framing**: the Q0 probe shows the Cowork Skills picker rides
`mcp__skills__list_skills` (stdio MCP, internal Anthropic) NOT the
`/skills/list-skills` HTTP endpoint observed in ADR-018. The HTTP
endpoint may be:

- Used by the **Chat surface** for slash-menu skill enumeration
  (different code path).
- Used by the **Settings → Extensions** UI for listing installed
  MCP extensions (which DOES include PCE's `.mcpb` per Q0 evidence).
- Used at app-startup for global skill cache warm-up.

**Empirical answer**: TBD — confirm by observing whether
`/skills/list-skills` is hit during a `mark skill-picker-open` event
in Cowork (RECON N-axis). If NOT hit during cowork picker usage,
then the HTTP endpoint is Chat-only and `pick_skill(name)` does not
need to know its schema. If IT IS hit, then keep the original Q4
investigation plan (DB body inspection of the 4927 B response).

**Driver implication for `pick_skill(name)`**: if the picker rides
`mcp__skills__list_skills`, then `name` matching is against whatever
field `list_skills` returns in its widget rows (visible to UIA as
the row's `name` property). The `skill_names` input param to
`list_skills` suggests skill names are first-class strings; PCE
driver can match those literally.

**Evidence**: TBD — `events.jsonl` filter on `path: '/skills/list-
skills'` during the `mark skill-picker-open` window in RECON.

---

## Q5 — `local-agent-mode-sessions/<uuid>/manifest.json` field schema

**Empirical answer**: TBD.

**`local_persistence.py` v0 implication**: TBD.

**Evidence**: TBD (`manifests/<uuid>.json`).

---

## Q6 — Scheduled task lifecycle (50% closed by Q0 probe)

**Empirical lean**: probe shows `mcp__scheduled-tasks__create_
scheduled_task` output explicitly states *"Scheduled task created
with storage location and **session tracking**"*. The phrase
"session tracking" + the fact that the create verb returns a
*"storage location"* in the same response strongly suggests **eager
lifecycle**: the session/conversation row IS created at scheduling
time (not at fire-time). This matches Anthropic's general pattern
of eager resource creation with deferred execution.

**Remaining 50% — N-axis confirmation**: during RECON, the
`mark scheduled-create` marker must coincide with a `POST
/chat_conversations/<uuid>` capture in `events.jsonl`. If it does:
**confirmed eager**. If only a `/cowork_settings`-class metadata POST
fires: **lazy**, lifecycle resource creation deferred to fire-time.

**C11 acceptance implication**: 
- If confirmed eager: C11 should PASS in-sweep (immediate row
  visible via N-axis + L3g manifest, no waiting for scheduled fire).
- If lazy: C11 SKIP is acceptable per MATRIX §5.B (can't observe
  fire-time within sweep budget unless `>24h` test rig is built).

**Evidence**: pending RECON `events.jsonl` cross-referenced with
`mark scheduled-create` timestamp + L3g manifest snapshot diff
around that moment (the recon script's `dump-agent-session` REPL
command captures the manifest immediately after the marker).

---

## Q7 *(NEW)* — Live Artifacts mechanics

**Why this is new**: the Q0 probe revealed that Cowork's Live
Artifacts feature has dedicated MCP plumbing (`mcp__cowork__create_
artifact` / `update_artifact` / `list_artifacts`) and is distinct from
the ephemeral `mcp__visualize__show_widget` (which renders SVG/HTML
inline in chat without persistence). This was not in the original
6-question kickoff scope and surfaced organically from the probe.

**Empirical answer (partial from Q0 probe)**:

- Live Artifacts are **persistent across sessions** (vs. visualize
  widgets which are inline only).
- Each artifact has `{id, name, path, createdAt, updatedAt}` per
  `list_artifacts` output.
- Each artifact's `mcp_tools` field implies an artifact can declare
  which MCP tool servers it depends on (re-enabled when artifact is
  reopened).
- Storage path: TBD — most likely under `LocalCache\Roaming\Claude\
  cowork-artifacts\<id>\` or a sibling of `local-agent-mode-sessions\`.
  Closed by L3g snapshot diff before/after a `create_artifact` call
  during RECON.

**Remaining**:
- Exact filesystem storage path (RECON-closeable via filesystem diff).
- Whether artifacts surface to `pce_persistence_watcher` as a known
  install-rooted directory (probably needs adding a new "artifacts"
  root to `pce_persistence_watcher/discovery.py`).
- Whether `mcp__cowork__list_artifacts` is callable from non-Cowork
  surfaces (e.g. would Chat let PCE query it?).

**Driver implication**: `view_live_artifacts()` driver helper (C04
supporting) can verify presence by:
1. UIA-clicking the "Live Artifacts" sidebar entry, then
2. Reading the resulting pane's artifact list, OR
3. Tailing the (yet-to-be-discovered) filesystem path for a recent
   artifact directory mtime.

**Evidence**: `tests/manual/cowork_mcp_namespace_2026-05-11-0655.md`
§ `mcp__cowork__` (closes the MCP-side mechanics); pending L3g
filesystem diff during `mark live-artifacts-open` in RECON (closes
the storage-path question).

---

## Q8 *(NEW)* — File-share UX (`request_cowork_directory`)

**Why this is new**: Q0 probe revealed `mcp__cowork__request_cowork_
directory(path?)` with *"optional native folder picker"* in the
output. This is the entry point for Cowork to gain filesystem access
— critical for C09 (`cowork_file_upload`) driver work.

**Empirical answer**: TBD — closed by:
- RECON: ask Cowork *"please request access to my Downloads folder"*
  while running `dump_uia` in a separate terminal. The picker
  appearance + its window class will reveal whether it's a separate
  top-level Win32 dialog or an in-app pane.
- Possible outcomes:
  - **Native FolderBrowserDialog** (likely): a standard Windows
    Shell folder picker pops up. Driver `cowork_file_upload(path)`
    needs to handle this dialog (UIA-set the path field + click OK,
    or use Win32 shell APIs to dismiss preemptively).
  - **Custom in-app picker**: a Claude-Desktop-styled pane within
    the Cowork window. Driver can locate by name-substr like the
    other pickers.

**Driver implication**: `cowork_file_upload(path)` (C09 driver helper)
becomes significantly more complex if the picker is a separate Win32
dialog (needs a second UIA driver scope). If in-app, reuses standard
`_find_uia_by_name_substr`.

**Evidence**: TBD — RECON `mark file-share-open` + concurrent
`python -m tests.e2e_desktop_ui.scripts.dump_uia open-cowork`
(or a new dump_uia mode `open-folder-picker` if needed).

---

## Sign-off

Once all 9 questions above (Q0–Q8) carry concrete empirical answers +
driver implications + evidence references, this doc is the closure
artefact for `MATRIX §5.B.2` and unblocks P5.B.5 implementation
(P5.B.5.2 helpers → P5.B.5.3 normaliser → P5.B.5.5 C-case sweep).
**P5.B.5.4 (`.mcpb` pack) is already complete** — see commit
`73a83a1` and the `pce-mcp-0.1.0.mcpb` artifact.

**Status snapshot as of 2026-05-11 06:55**:
- Q0 ✅ fully closed (Outcome B, L3f-Cowork → `KNOWN-PHASE6-DEFERRED`)
- Q1 ☑️ 80% closed (descendant inline widget; UIA dump for row shape)
- Q2 ⬜ TBD (RECON multi-step task timing)
- Q3 ☑️ reframed (likely descendant pane, no MCP namespace evidence)
- Q4 ☑️ deprioritized (Cowork picker doesn't use this HTTP endpoint)
- Q5 ⬜ TBD (RECON `dump-agent-session`)
- Q6 ☑️ 50% closed (lean eager from probe; RECON N-axis confirms)
- Q7 ☑️ NEW, 30% closed (MCP mechanics from probe; L3g diff pending)
- Q8 ⬜ NEW, TBD (RECON folder-picker dump)

**Net RECON scope after Q0 probe**: shrunk from 60 min → ~30 min
because Q1/Q4/Q6 each lost substantial UIA / HTTP traversal work.
The focused RECON checklist now centers on Q2 (multi-step async),
Q3 (Dispatch UIA), Q5 (manifest schema dump), Q6 (N-axis eager
confirmation), Q7 (artifact storage path), Q8 (folder-picker class).

**This file MUST NOT be deleted before all 9 Q§ carry concrete
answers** — the cross-references in MATRIX and the kickoff handoff
resolve here.
