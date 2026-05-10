# Cowork-region RECON findings — PARTIAL (Q0 closed pre-RECON)

> **Status**: **Q0 closed** empirically 2026-05-11 06:48 during the
> P5.B.5.4 `.mcpb` smoke-test (see Q0 § below). The remaining
> Q1–Q6 are still gated on a 60-min RECON.
>
> **Cross-references** from `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
> §5.B.2 / §7.5 and `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-
> KICKOFF-2026-05-10.md` §4 resolve here.
>
> **Authority**: this is the real findings doc once filled in. All
> 7 questions (Q0 + Q1–Q6) below MUST carry empirical evidence before
> P5.B.5 implementation may proceed (per the gating rule in
> `HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` §4.3).
> Q0 is the only architectural blocker because it forces a reframing
> of C16 (originally "Cowork sees PCE tools") AND opens a new
> question about L3f-Cowork capture strategy.

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

## The 7 questions to close (Q0 + Q1–Q6, mirror of MATRIX §5.B.2)

| # | Question | Status | Affects | Closing evidence |
|---|---|---|---|---|
| **Q0** | Does Cowork load user-installed `.mcpb` extensions? Is there a registration API into Cowork's MCP namespace? | **PARTIAL** (isolation confirmed; registration API TBD) | C16 acceptance; L3f-Cowork strategy | Cowork's own Thought-process tool-list reveal 2026-05-11 06:48 (see Q0 §) + pending `mcp__mcp-registry__` probe |
| **Q1** | Skills picker UIA shape (descendant vs separate top-level Win32 popup) | TBD | `pick_skill()` (C08) | `_uia_dump_open-skills.txt` + `mcp__skills__` enumeration from Q0 probe |
| **Q2** | Async step semantics (single SSE / SSE-per-step / long-poll) | TBD | `wait_for_cowork_step()` (C02, C03) | `events.jsonl` timing analysis on `mark task-multistep` window |
| **Q3** | Dispatch (Beta) window class (in-app pane vs separate popup) | TBD | `open_dispatch()` (C10) | `_uia_dump_open-dispatch.txt` |
| **Q4** | `/skills/list-skills` schema (4927 B body shape) | TBD | `pick_skill(name)` matching field (C08) | DB row body for the largest matching event id + Q0 probe cross-check |
| **Q5** | `local-agent-mode-sessions/<uuid>/manifest.json` field schema | TBD | `local_persistence.py` v0 (C14) | `manifests/<uuid>.json` |
| **Q6** | Scheduled task lifecycle (eager vs lazy `conversations/<uuid>` creation) | TBD | C11 SKIP-vs-PASS decision | `events.jsonl` + `manifests/` cross-ref + `mcp__scheduled-tasks__` enumeration from Q0 probe |

---

## Q0 — Cowork's MCP isolation model (CLOSED with pending sub-question)

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

### Pending sub-question (the only TBD in Q0)

**Does `mcp__mcp-registry__` expose any verb that allows a local user
to register a custom MCP server into Cowork's runtime?** Closing
evidence will be the Q0 probe output saved at
`tests/manual/cowork_mcp_namespace_<ts>.md`. Three possible
outcomes:

- **A — Registration API exists**: e.g. a tool like
  `mcp__mcp-registry__add_server(name, command, args)`. **Action**:
  ship a `.mcpb`-style register call from PCE Core during install,
  inject the pce-mcp Node proxy into Cowork at runtime. L3f-Cowork
  becomes possible via official channel.
- **B — Read-only registry**: e.g. `mcp__mcp-registry__list_servers`
  but no add/remove verbs. **Action**: L3f-Cowork stays OPEN; rely
  on N + L3g axes. Document as KNOWN-LIMIT and consider OS-hook
  approach in Phase 6 (filing a deferral with explicit risk note).
- **C — Registry doesn't exist by that name in Cowork's actual
  toolset**: the namespace prefix may have been Cowork's hallucinated
  guess. **Action**: re-probe with broader question ("enumerate all
  MCP namespaces you have access to and any registration verbs").

### Driver implication (immediate)

No cowork helper in `tests/e2e_desktop_ui/drivers/claude_desktop.py`
needs to invoke PCE tools from inside Cowork. The driver only drives
UI elements — Cowork's own internal `mcp__skills__` orchestration
handles tool listing in the Skills picker. PCE's role inside Cowork
is purely passive (N+L3g observation), not active (tool invocation).
P5.B.5.2 helpers therefore remain in scope as originally specified.

### Evidence references

- This very chat log (PCE alpha.8, 2026-05-11 06:41–06:48 UTC+08:00).
- Memory `76de9e16-be1d-4609-860a-14c79d66b5de` in the Cascade memory
  store.
- `tests/manual/cowork_mcp_namespace_<ts>.md` (TODO: created by Q0
  probe step above).

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
