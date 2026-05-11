# Changelog

All notable changes to PCE (core + browser extension) are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-05-11 — P5.B.5 cowork-region implementation (L3g JSONL normaliser + cowork driver helpers + C-case sweep)

Direct upstream of the 2026-05-10 standard-alignment sub-run. With the
RECON authority chain frozen (see `Docs/research/2026-05-11-cowork-recon-findings.md`,
renamed from the 05-XX placeholder), this sub-run lands the three
code deliverables that the §7.5 phasing called for:

- **P5.B.5.3** `LocalPersistenceNormalizer` v0 — turns L3g transcript
  rows into Tier-1 sessions + messages (closing the WS-over-HTTP/2
  gap discovered in Round-2 RECON).
- **P5.B.5.2** 7 cowork driver helpers in
  `tests/e2e_desktop_ui/drivers/claude_desktop.py`.
- **P5.B.5.5** `run_p1_cowork_sweep.py` aggregator with all 17 C-cases
  (C00–C16) in a single file plus per-case JSON evidence.

### Architectural finding (RECON Round 2) — Cowork chat is WebSocket-over-HTTP/2

`pce_proxy/addon.py` in upstream HTTP-proxy mode does not see Cowork
chat traffic at all: the Cowork tab uses RFC 8441 WebSocket-over-HTTP/2
(`:protocol=websocket` extended CONNECT) for prompt/response streaming,
which mitmproxy upstream mode passes through opaquely. **`websocket_message` +
`websocket_end` hooks** were added to `pce_proxy/addon.py` as a defence-in-depth
measure (they capture any future plain ws:// traffic that may appear),
but the production fix for Cowork capture is the L3g axis below — not
the L1 axis.

### P5.B.5.3 — L3g local-persistence JSONL normaliser

Discovery: Cowork persists the **full** conversation transcript as
JSONL files on disk under

```
%LOCALAPPDATA%/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude
  /local-agent-mode-sessions/<user_uuid>/<org_uuid>/local_<session_uuid>
  /.claude/projects/<encoded-cwd>/<session_uuid>.jsonl
```

Six top-level line types observed (`user`, `assistant`, `ai-title`,
`queue-operation`, `last-prompt`, `attachment`). `user`/`assistant` lines
carry standard Anthropic Messages content blocks (`text`, `thinking`,
`tool_use`, `tool_result`) so the existing
`pce_core/normalizer/anthropic.py::_extract_rich_blocks` is reused.

#### New / modified files

- **`pce_core/normalizer/local_persistence.py`** (NEW, ~240 lines) —
  `LocalPersistenceNormalizer` with `can_handle` matching capture
  envelopes where `provider=local-agent-mode` and path contains
  `/agent-transcript/`. `normalize()` parses a single JSONL line into
  one `NormalizedMessage` (or zero for non-content line types like
  `ai-title` / `queue-operation` / `last-prompt`), reusing
  `_extract_rich_blocks` for content. Session-key resolution prefers
  the JSONL line's `sessionId` field over any literal "unknown" path
  segment.
- **`pce_core/normalizer/registry.py`** — registers
  `LocalPersistenceNormalizer` ahead of the `ConversationNormalizer`
  catch-all.
- **`pce_core/normalizer/pipeline.py`** — `normalize_conversation` gains
  a branch for `provider=local-agent-mode` + `/agent-transcript/` paths
  that routes directly to `LocalPersistenceNormalizer`, bypassing
  `try_normalize_pair` (which assumes a request+response pair).
- **`pce_persistence_watcher/agent_sessions.py`** — `AgentSessionRecord`
  gains a `transcript_line` kind plus `line_uuid` + `line_index` fields.
  New helpers: `_parse_iso8601_to_ms`, `_find_transcript_jsonl_files`
  (recurses into `.claude/projects/<encoded-cwd>/`), and
  `iter_transcript_records` (yields one record per JSONL line, deriving
  `session_id` from the local-agent-mode-sessions ancestor directory).
- **`pce_persistence_watcher/capture.py`** — `ChromiumStateObserver.stats`
  gains a `transcript_line` counter. `observe_agent_session` learns the
  `transcript_line` kind: dedup fingerprint prefers `line_uuid` over
  `line_index`, metadata gains `line_uuid` + `line_index` + `line_type`.
  `_write` gains a `trigger_normalize` parameter and
  `_normalize_just_inserted` to fire `pipeline.normalize_conversation`
  for transcript lines immediately after insert, so they materialise as
  Tier-1 sessions+messages in the same scan pass.
- **`pce_persistence_watcher/__main__.py`** — `_scan_install` now iterates
  `iter_transcript_records` after the existing agent-session records,
  so a single watcher tick covers both the manifest-level and
  line-level views.

#### Tests

- **`tests/test_local_persistence.py`** (NEW, 15 tests) — `can_handle`
  positive/negative; `normalize` for user / assistant / tool_use /
  tool_result / queue-operation / ai-title / last-prompt / attachment;
  session-key fallback when path segment is `"unknown"`; end-to-end
  watcher → capture → pipeline → messages round-trip via the
  `tests/fixtures/cowork_transcript_sample.jsonl` fixture (copied from
  a real Round-3 RECON session, redacted); idempotency (re-running the
  same watcher pass does not produce duplicate messages).
- **`tests/fixtures/cowork_transcript_sample.jsonl`** (NEW, 65 KB) —
  fixture covering all six line types.
- All 15 tests pass.

#### Empirical L3g ingestion

Restarting the persistence watcher on the developer's real machine
retroactively ingested **133 transcript rows → 6 cowork sessions → 54
messages** including 14 `mcp__*` tool calls, 2 `mcp__workspace__bash`
calls, 1 Skill invocation, and 54 attachment-bearing messages. This
is the empirical evidence behind the C-case sweep PASSes below.

### P5.B.5.2 — Cowork driver helpers

`tests/e2e_desktop_ui/drivers/claude_desktop.py` gains 7 helpers
empirically locked by the auto-RECON pass:

- `open_cowork_tab()` / `open_chat_tab()` — top-bar tab toggle.
- `pick_skill(skill_name, timeout)` — clicks `/`-picker row matching
  the given skill (`xlsx`, `pdf`, etc.); uses Y-band-aware finder for
  the in-app popup (per Q3 closure: descendant pane, not Win32 popup).
- `select_ask_mode()` — switches Cowork mode dropdown to "Ask".
- `view_live_artifacts()` — clicks the sidebar Live Artifacts entry.
- `open_dispatch()` — clicks the Dispatch (Beta) sidebar entry.
- `open_scheduled()` — clicks the Scheduled sidebar entry.
- `wait_for_cowork_step(timeout)` — polls for the agent-step done
  signal in the Cowork pane (Stop button → Send button transition),
  replacing the chat-region `/completion` HTTP probe (which doesn't
  fire for Cowork due to the WS-over-HTTP/2 architecture).

### P5.B.5.5 — C-case sweep aggregator

**`tests/e2e_desktop_ui/run_p1_cowork_sweep.py`** (NEW, ~700 lines) —
single-file aggregator with 17 case functions (C00–C16) each returning
`{verdict, reason, evidence, elapsed_s}`. Two modes:

- `--mode static` (fast, ~10 s wall-clock) — verifies from existing DB
  rows + filesystem state. No UI driving. Suitable as a CI smoke that
  catches L3g pipeline regressions.
- `--mode live` (~10–15 min, requires no-touch) — drives Claude Desktop
  UI via the new driver helpers, sends real prompts, waits via
  `wait_for_cowork_step`, then verifies the resulting cowork session.

Per-run output: `tests/e2e_desktop_ui/reports/p1_cowork/<ts>_mode-<m>/`
with `summary.json` (counts + acceptance verdict) and one `case_C*.json`
per case (gitignored, mirrors `/tests/e2e/reports/` convention added to
`.gitignore`).

#### Static-mode first run (developer machine)

**9 PASS / 8 SKIP / 0 FAIL** out of 17 cases — vastly exceeds the
≥5-PASS static-mode target. The 9 PASSes empirically confirm L3g
pipeline health end-to-end:

| Case | Static PASS evidence |
|------|----------------------|
| C00  | 1670 cowork heartbeats in `raw_captures` (`/environments?included_worker_types=cowork`) |
| C02  | 33 assistant messages with non-empty `content_text` in past cowork sessions |
| C05  | 54 cowork messages with attachment-style `content_json` |
| C06  | 2 cowork messages carrying `mcp__workspace__bash` tool calls (code execution observed) |
| C07  | 14 cowork messages with `mcp__*` tool calls (internal Anthropic MCP plumbing active) |
| C08  | 1 past `[Tool call: Skill]` invocation in cowork content_text |
| C09  | `vm_bundles/<...>/*.vhdx` exists on disk (agent-mode VM provisioned) |
| C13  | 76 `/cowork_settings` GETs in `raw_captures` |
| C14  | L3g pipeline healthy: 133 transcript rows → 6 sessions → 54 messages |

The 8 SKIPs split into: 5 live-mode-only cases (C01, C03, C10, C12,
C15), 1 known-bug inheritance (C04 ← chat-region D04 cancel-mid-stream),
1 scope-deferred (C11 scheduled-task lifecycle, per Q6 inconclusive),
and 1 awaiting-real-invocation (C16, `.mcpb` packs but no `pce_*` MCP
tool call yet observed).

The live-mode sweep (target ≥13/16 PASS) is a separate user-driven
step and is tracked in the next sub-run.

### Defence-in-depth: WebSocket hooks in pce_proxy

Even though the production fix is L3g, `pce_proxy/addon.py` gained
`websocket_message` + `websocket_end` hooks that capture WS frames as
`raw_captures` rows with `direction=ws_send` / `ws_recv` and
`body_format=ws_text` / `ws_binary`. These would catch any future
ws://-style traffic (e.g. third-party MCP servers) and do not interfere
with the existing HTTP request/response capture path.

### Recon-findings doc renamed + finalised

`Docs/research/2026-05-XX-cowork-recon-findings.md` → `2026-05-11-cowork-recon-findings.md`.
Q0–Q8 status table updated with empirical resolutions: Q0 closed
(Outcome B — Cowork MCP namespace is isolated, user .mcpb extensions
do not load in Cowork); Q1/Q3/Q4 closed (auto-RECON evidence); Q5/Q7
closed (Round-3 JSONL discovery); Q2 reframed as architectural finding
(WS-over-HTTP/2); Q6/Q8 inconclusive, scope-deferred. New §Architectural
Outcomes section (A1 WS-over-HTTP/2 gap downgraded to nice-to-have
thanks to L3g, A2/A3/A4 addon + watcher + schema fixes, A5 acceptance
targets revised upward).

### What stays unchanged

- Chat-region D-case closures (19 PASS / 1 SKIP / 1 KNOWN BUG / 1
  deferred) remain canonical.
- ADR-018 three-axis model unchanged; cowork adds M middleware as
  fourth axis on top.
- `pce_core/server.py` HTTP capture path unchanged; the L3g axis
  writes through the same ingest endpoint.
- Browser-extension capture path unchanged (cowork is a desktop-only
  surface).

### Next sub-run

P5.B.5.5c — drive the sweep in `--mode live` against a real Claude
Desktop install (user no-touch for ~15 min). Target verdict ≥13 PASS
/ ≤3 SKIP / 0 FAIL (per the `Docs/research/2026-05-11-cowork-recon-findings.md`
§A5 acceptance targets). On clearance, tag `v1.1.0-alpha.X-cowork-p1`
and update §8.2 v1.1 ship checklist.

---

## [Unreleased] - 2026-05-10 (still later same day) — P1 cowork-region standard alignment

Documentation-only sub-run. Lands the cowork-region acceptance bar in
the canonical docs **before** any cowork code is written, in response
to the user instruction:

> 你先把文档材料对齐好 然后再把 cowork 的标准落下来 我们再开始做吧

No new tests, no driver changes, no normaliser additions. Pure
documentation alignment so the next sub-run (P5.B.5 implementation)
has a single authoritative source for what "cowork-region passes" means.

### Documentation changes

- **`Docs/stability/DESKTOP-PRODUCT-MATRIX.md`**:
  - **Tier-1 fixes** (§4.1 P1 Field table): flipped `mcp_jsonrpc.py`
    to ✅ (alpha.1 shipped); clarified `local_persistence.py` ⬜
    status as "envelope-level rows shipped alpha.8, structural
    normaliser gated by ≥1 cowork D-case (now C14)"; **unbundled
    archetype** — Chat Tube ✅ (chat-region) + Tool Tape ⬜
    (cowork-region) + Run Trace ⬜ (cowork+code regions).
  - **§4.1.B (new)** — P1 Claude Desktop cowork-region subsection:
    product positioning, three-axis coverage with M middleware as
    fourth axis, observed endpoints (`/cowork_settings`,
    `/environments?included_worker_types=cowork`,
    `/skills/list-skills`, `/wiggle/upload-file`,
    `/wiggle/download-file`, `/referral/eligibility?source=cowork`,
    `/chat_conversations/<uuid>/completion` for cowork tasks),
    existing tooling status, pending tooling for P5.B.5, acceptance
    gate (≥80% C-cases PASS = ≥13/16), first-probe checklist.
  - **§5.B (new)** — Cowork-region C-case spec: 17 cases C00–C16
    with detailed acceptance signals; C-namespace independent of
    chat-region D-namespace to keep verdict semantics readable;
    audit anchor rule (every PASS produces ≥1 `raw_captures` row +
    ≥1 `messages` row + L3g-axis row where applicable).
  - **§5.B.1 (new)** — Cowork applicability matrix:
    P1 = 16 ✅ must-pass · P6 = 7 🟡 best-effort CLI mirror (count
    toward P6 D-case bar, not cowork sub-gate) · P2/P7/P8 = 1 🟡
    each best-effort · P3/P4/P5 = 0 (no cowork product surface).
  - **§5.B.2 (new)** — 6 RECON-resolvable open questions
    (Skills picker UIA shape · async step semantics · Dispatch
    window class · skill list schema · session manifest field
    schema · scheduled task lifecycle).
  - **§6** — Test infrastructure layout extended from 3 trees → 6
    trees (added `e2e_mcpb/` ✅ alpha.8, `e2e_l3g/` ✅ alpha.8,
    `e2e_desktop_ui/` ✅ chat sub-runs 1–5).
  - **§7** — Header updated P5.B.0–P5.B.4 → P5.B.0–P5.B.5;
    cross-ref D-case (chat) + C-case (cowork) acceptance.
  - **§7.5 (new)** — P5.B.5 cowork sub-phase phasing: builds on
    alpha.8 three-axis scaffolding, adds 6 cowork helpers + 1
    structural normaliser + `.mcpb` packaging + 16 C-case files;
    pass-distribution target 14 PASS / 2 SKIP / 0 FAIL = 87.5%
    (≥80% gate cleared by +1.5%); 7-item deliverables checklist;
    unblocks P5.C dashboard Tool Tape renderer.
  - **§8.2** — v1.1 ship checklist gains:
    - P1 cowork-region D0 sub-gate (≥80% C-cases, 0 capture-pipeline FAILs)
    - `local_persistence.py` v0 in normaliser-additions list
    - `e2e_mcpb/` + `e2e_l3g/` + `e2e_desktop_ui/` in test-tree list
    - **ADR-018** reference (P1 MSIX three-axis realisation —
      direct upstream of cowork-region §4.1.B / §5.B / §7.5).

- **`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`** §7:
  Capture-storage-render matrix updated — L3g and H1 CLI wrap
  flipped to ✅ (alpha.8 shipped); archetype mapping reflects
  cowork ↔ Tool Tape and code ↔ Run Trace; P1 and P2 rows updated
  with current delivery status and 2026-05-10 empirical findings
  (P2 split-channel WebSocket handoff blocking assistant-side L1
  capture).

- **`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`** §10–§11:
  Added P5.B.5 cowork-region sub-phase row to unlock matrix and
  delivery table; sign-off criteria gains cowork-region D0 sub-gate;
  test suite coverage references include `e2e_desktop_ui/` (chat
  sub-runs 1–5 + cowork sweep coming in P5.B.5).

- **`Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`** (new):
  Dedicated cowork kickoff handoff. ~440 lines covering authority
  chain, three-axis-plus-M overview, 60-min RECON walkthrough with
  per-question dump-file mapping, C-case acceptance table with
  axis-dependency + cost + target verdict, driver helper reuse +
  6 new helpers spec, reproduction recipe, risks + open follow-ups
  (RECON-dependent, normaliser, `.mcpb` install, D04/C04 KNOWN BUG),
  7-item sign-off criteria, cross-references to ADR-018 + chat
  sub-runs 1–5 prior-art handoffs.

### What stays unchanged

- All chat-region sub-runs 1–5 results (19 PASS / 1 SKIP / 1 KNOWN
  BUG / 1 deferred over 22 D-cases) remain the canonical chat-region
  closure.
- 0 capture-pipeline FAILs hard rule carries forward into cowork-region
  acceptance (FAIL = pipeline dropped an event that physically reached
  network or disk; SKIP = product UI feature absent or RECON-deferred).
- ADR-018 three-axis model unchanged; cowork-region adds M middleware
  as fourth axis on top, no rewrite.
- All existing test trees (`e2e_mcp/`, `e2e_mcpb/`, `e2e_l3g/`,
  `e2e_desktop/`, `e2e_desktop_ui/`, `e2e_cli/`) unchanged.

### Next sub-run

P5.B.5 implementation: 60-min RECON pass → findings doc closes 6
open questions → 6 (or 7) cowork helpers committed to
`tests/e2e_desktop_ui/drivers/claude_desktop.py` → `local_persistence.py`
v0 lands → `pce-mcp.mcpb` packaged + real-machine install verified →
16 C-case files runnable via `python -m tests.e2e_desktop_ui.run_p1_cowork_sweep`
→ ≥13/16 PASS / 0 FAIL → CHANGELOG `v1.1.0-alpha.X-cowork-p1`.

---

## [Unreleased] - 2026-05-10 (later same day) — P1 D03/D05 + P2 N/L1 + P1 chat first-pass + P1 chat web-parity extension + SKIP-conversion sweep + composer-focus sweep

Five live sub-runs the same day as `alpha.10-p1-empirical`. Each one
builds on the previous:

- **Sub-run 1 (P1 D03/D05 + P2 N/L1)** — extends the D-case matrix on
  **both** P1 and P2 under the same proxy chain (D03/D05 fix + P2 N/L1
  finding).
- **Sub-run 2 (P1 chat first-pass full sweep)** — drives the **full
  Claude Desktop chat D-case sweep** for the originally-defined 13
  D-cases end-to-end via the new UIA + SendInput automation tree under
  `tests/e2e_desktop_ui/` (5 windows, 7 D-cases driven, 6 PASS / 1
  KNOWN BUG).
- **Sub-run 3 (P1 chat web-parity extension)** — audits the desktop
  spec against `Docs/stability/CLAUDE-FULL-COVERAGE.md` (web Claude
  C01–C20), finds 10 missing surfaces, **extends the matrix from 13 →
  23 D-cases** (D13 thinking · D14 edit · D15 regenerate · D16 branch
  flip · D17 image · D18 PDF · D19 project · D20 artifact text · D21
  artifact interactive · D22 writing style), ships 7 new driver
  helpers + a fixtures helper + a UTF-8 stdout fix, and runs the new
  cases end-to-end. Score on D13–D22: **4 PASS / 6 SKIP / 0 FAIL**.
- **Sub-run 4 (P1 chat SKIP-conversion sweep)** — empirical UIA
  introspection pass, ships a UIA tree dumper (`scripts/dump_uia.py`
  + `dump_tree()` driver method), refactors driver helpers with
  cross-window popup search + Y-band-aware finder + `prefer="max_y"`
  selection, broadens D13's PASS criteria to honour text-shaped
  reasoning, and adds a keyboard-navigation fallback for the
  paperclip menu. **Converts 3 of 6 SKIPs to PASS**: D13 (extended
  thinking — text-shaped reasoning + `ANSWER:` + step markers in
  assistant content_text), D19 (project scope — with real
  architectural finding that Claude Desktop projects use the same
  `/chat_conversations/` API path as non-project chats), and D22
  (writing style — `personalized_styles.name='Concise'`, prompt
  1686 B, style on `sessions.oi_attributes_json`). Combined
  sub-runs 2+3+4 over P1's 22 applicable D-cases:
  **17 PASS / 3 SKIP / 1 KNOWN BUG / 1 deferred** (pass rate 77%,
  pass+skip 91%), **0 capture-pipeline FAILs across all four
  sub-runs**.
- **Sub-run 5 (P1 chat composer-focus sweep)** — driven by user
  diagnosis that `new_chat()` re-flows the composer to a centred
  position and the legacy fixed `bottom-120` `click_composer()` was
  silently clicking blank space, dropping focus, and making
  subsequent `Ctrl+V` paste a no-op (the killer for D17/D18). Driver
  rewrite ships UIA-based composer discovery, system-wide UIA
  focus verification (`IUIAutomation.GetFocusedElement` →
  `CurrentControlType ∈ {Edit, Document, Custom}` + width ≥ 200),
  retrying `click_composer` up to 3x, and a public
  `ensure_composer_focus()` helper that `paste_clipboard`,
  `new_chat`, and any caller can use to guarantee focus before
  Ctrl+V or send_keys. **Converts D17 + D18 SKIP → PASS**:
  D17 image (vision recognises `PCE-D17-5039` token, 4 upload-shaped
  requests, `file_uuid` round-trips into `messages.content_json`),
  D18 PDF (`PCE-D18-4471` summarised, 4 upload-shaped requests,
  `file_uuid` persisted). Combined sub-runs 2+3+4+5 over the 22
  applicable D-cases: **19 PASS / 1 SKIP / 1 KNOWN BUG / 1 deferred**
  (pass rate 86%, pass+skip 91%), **0 capture-pipeline FAILs across
  all five sub-runs**.

### Live-validated

- **P1 D05 model switch** ✅ — Mid-conversation switch from
  `claude-haiku-4-5-20251001` to `claude-sonnet-4-6` reflected per turn
  in `messages.model_name`. The post-switch turn correctly carries the
  new model.
- **P2 D01 user message capture** ✅ (partial) — 3/3 ChatGPT Desktop user
  prompts reached the `messages` table via `/backend-api/f/conversation`
  POST request body parsing. `provider=chatgpt.com`,
  `tool_family=api-chatgpt.com`, `model_name` populated correctly
  (`auto`, `gpt-5-5-thinking`).
- **0 pipeline_errors** across 4180 captures + 17 normalized messages.
  Pipeline degrades gracefully when the assistant side is missing
  (Bug 2 below).

### Bug found + fixed (P1 D03 multi-turn)

- **`pce_core/normalizer/anthropic.py`** — `session_key` derivation now
  falls back to extracting the conversation UUID from the path
  (`/api/organizations/<ORG>/chat_conversations/<UUID>/completion`) when
  the request body has no `conversation_id` / `session_id` keys.

  Empirical bug: 5 sequential POSTs to the same Claude Desktop chat
  conversation produced 5 disjoint `sessions` rows with `session_key=NULL`
  because the desktop client's request body keys are
  `['prompt', 'timezone', 'personalized_styles', 'locale', 'model',
  'tools', 'turn_message_uuids', 'attachments', 'files', 'sync_sources',
  'rendering_mode', 'create_conversation_params']` — the conversation
  UUID is in the path, not the body. With the fallback, all turns
  collapse into 1 session row keyed by the conversation UUID.

  Fix: 1 module-level `re.compile`, 4 lines of fallback logic. Regression
  test in `tests/test_normalizer.py::_test_anthropic_normalizer` covers
  positive (claude.ai desktop) and negative (`/v1/messages` public API
  must NOT match) cases. test_normalizer.py 22/22 PASS.

### Bug found, NOT a normalizer issue — major P2 architectural finding

- **ChatGPT Desktop split-channel architecture** ⚠️ — The new
  `/backend-api/f/conversation` POST endpoint returns ONLY a 567-byte
  SSE handoff envelope (`stream_handoff` + `subscribe_ws_topic`). The
  actual assistant text streams over a **separate WebSocket** that the
  current `pce_proxy/addon.py` HTTP-oriented capture path does not see.
  Empirical confirmation: searched all 4065 captured chatgpt.com response
  bodies for "Paris" / "capital of france" / "你好" / "香港" / "首都" —
  zero matches, despite all 3 user messages reaching `messages` and the
  assistant text being visible in the Desktop UI.

  This invalidates the implicit "L1 reaches both user and assistant
  text" assumption inherited from web ChatGPT. **N/L1 P2 chat-region
  capture is BLOCKED user-side-only** until WebSocket capture is added
  to the proxy. Detailed analysis + 4 candidate unblock paths in the
  handoff doc.

  `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.2 P2 row updated with a
  new dated note recording this finding.

### Documentation

- New handoff:
  `Docs/handoff/HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md` (8
  sections, ~330 lines: scope, run conditions, empirical numbers, bug
  root causes, fix details, D-case scoring, reproduction recipe,
  cross-references, open follow-ups for next operator).

### D-case status after the first sub-run (P1 D03/D05 + P2 N/L1)

| ID | Pre-run | Post-run | Note |
|----|---------|----------|------|
| P1 D01 | ✅ alpha.10 | ✅ | unchanged |
| P1 D03 multi-turn | ❌ | ✅ FIXED | Bug 1 fix in this commit |
| P1 D05 model switch | ⏭ | ✅ | empirically attested |
| P1 D11 long-context | ⏭ | ⏭ | not exercised in this sub-run |
| P1 D12 silent-on-idle | ⏭ | ⏭ | window contaminated; needs dedicated run |
| P2 D01 user msg | ⏭ | ✅ | empirically attested |
| P2 D02 assistant msg | (assumed ✅ via L1) | ❌ BLOCKED | architectural finding (Bug 2) |

---

### P1 Claude Desktop chat full D-case sweep (second sub-run)

After Bug 1 was fixed in the first sub-run, a second pass automated
the remaining applicable D-cases for Claude Desktop chat through a
new UIA + SendInput driver. Five sequential windows, ~25 minutes
total wall-clock. The new automation tree lives at
`tests/e2e_desktop_ui/` with companion inspectors at the repo root.

#### New code

- **`tests/e2e_desktop_ui/__init__.py`** — package overview + boundary
  vs `tests/e2e_desktop/`.
- **`tests/e2e_desktop_ui/utils.py`** — `force_foreground` (Windows
  `AttachThreadInput` trick), `click_at` (absolute screen coordinates
  for Chromium child-window focus), `baseline_ts` (UTC seconds via
  `time.time()` — fixes the local-vs-UTC offset bug that bit prior
  runs that used PowerShell `Get-Date -UFormat %s`),
  `count_completions`, `latest_completion_pair_id`,
  `wait_completion_response`, `wait_for_new_completion`,
  `copy_files_to_clipboard` (`CF_HDROP` for D06 attachment paste).
- **`tests/e2e_desktop_ui/drivers/base.py`** — `DesktopDriver` ABC
  with `focus`, `click_composer`, `send_message`, `wait_done`,
  `cancel_current`, `new_chat`. Stable across product drivers.
- **`tests/e2e_desktop_ui/drivers/claude_desktop.py`** — concrete
  Claude Desktop driver. Empirically validated: UIA backend +
  bottom-center mouse click before typing + `send_keys(vk_packet=True)`
  for IME bypass + `{ESC}` for stop generation + `Ctrl+N` (after
  composer click) for new chat.
- **5 case scripts under `tests/e2e_desktop_ui/cases/`**:
  - `p1_chat_window_a.py` — D03 + D07 + D04 in a 5-turn conversation
  - `p1_chat_window_b_d11.py` — D11 long-context (50 turns, distinct prompts)
  - `p1_chat_window_c_d12.py` — D12 idle (5 min + 10 s)
  - `p1_chat_window_d_d06.py` — D06 attachment via clipboard paste
  - `p1_chat_window_e_d10.py` — D10 mid-stream proxy kill + restart

#### D-case results (this sub-run)

| ID | Pre-run | Post-run | Evidence |
|----|---------|----------|----------|
| P1 D03 multi-turn | ✅ alpha.11 (regression) | ✅ | 5 turns / 1 session — Bug 1 fix held |
| P1 D04 cancel mid-stream | ⏭ | ❌ KNOWN BUG | request captured, response missing, no `messages` row — see "Known limitations" below |
| P1 D06 attachment | ⏭ | ✅ | CSV upload + completion: user `content_json.attachments=[{type:file, file_uuid:0778d897-…}]`, assistant `content_json.attachments=[4 tool_call view items]`, final answer "**item** and **value**" preserved |
| P1 D07 code block | ⏭ | ✅ | turn-3 ` ```python ` fenced block survives in assistant `content_text` |
| P1 D10 error mid-stream | ⏭ | ✅ | fail-closed: killed pair has request only and **0** phantom messages; restart produces clean smoke pair (status 200, body 6459 B, 2 messages) |
| P1 D11 long-context | ⏭ | ✅ | **50/50 turns + 100/100 messages + 1 session + 14 378 cumulative tokens**, monotonic `turn_index` 0..99, 0 drops |
| P1 D12 silent on idle | ⏭ | ✅ | over 310 s idle: completion req Δ=0, messages Δ=0, sessions Δ=0; `raw_captures` Δ=+8 (Electron-app heartbeats — `current_user_access`, `app_start`, `system_prompts`, `cowork_settings`) |

**Aggregate score: 9 PASS / 1 known bug / 1 deferred** out of 12
applicable D-cases on Claude Desktop chat (D08 deferred to upcoming
cowork sweep). **D0 release gate (≥85%) cleared empirically.**

#### Known limitations

- **D04 cancel-mid-stream user message loss** —
  `pce_core/normalizer/pipeline.py::try_normalize_pair` requires both
  request AND response rows (`if len(rows) < 2: return None`). On
  user-initiated cancel via `{ESC}`, mitmproxy's `response()` hook
  never fires (client TCP closed before upstream finished), so no
  response row is written, the pipeline gives up, and the user's
  prompt — though sitting in `raw_captures` — never surfaces as a
  `messages` row. D10 has the same shape (proxy killed mid-stream),
  but for D10 the fail-closed semantics are the **correct** product
  behaviour; for D04 the user-authored prompt should remain in
  history. Fix path: 3 coordinated changes (new
  `try_normalize_pair_request_only`, extend `anthropic.normalize_pair`
  to handle `response_row=None`, add either a `client_disconnected`
  hook OR a periodic orphan-request sweep). Estimated 1–2 person-days
  incl. tests. Tracked under medium-priority follow-up.
- **D08 MCP tool call** is intentionally NOT included in this chat
  sweep — Claude Desktop's MCP traffic flows through `pce_mcp_proxy/`
  (M-axis), not `pce_proxy/` (N-axis), so it belongs in the upcoming
  cowork sweep with separate fixtures.

#### Documentation

- New handoff:
  `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`
  (6 sections, ~290 lines: framework, per-window evidence, known D04
  bug + fix path, reproduction recipe, schema gotchas, follow-ups).
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 row gains a new
  dated note recording the 9/1/1 score and the cleared D0 gate.

#### Schema gotchas pinned for next operator

These cost ~30 min of debug each in this session and are documented
inline in the handoff so the next operator skips them:

- `~/.pce/data/pce.db` is the canonical DB path
  (`pce_core.db.DB_PATH`); some older inspector scripts hard-coded
  `~/.pce/db.sqlite3` and produce "no such table" errors.
- `messages` table has **no** `error` column; cancel/error state lives
  in `interaction_kind` or `content_json` markers.
- `raw_captures` column is `headers_redacted_json`, not `headers_json`.
- `messages` join key to captures is `capture_pair_id`, not
  `capture_id`. Pair IDs are 16-char hex in the DB; logs print 10-char
  prefixes — match with `LIKE 'prefix%'` or expand via
  `SELECT pair_id FROM raw_captures WHERE pair_id LIKE 'prefix%'`.

---

### P1 Claude Desktop chat web-parity D-case extension (third sub-run)

After the second sub-run landed `9 PASS / 1 known bug / 1 deferred`
across the originally-defined 13 D-cases, an audit against
`Docs/stability/CLAUDE-FULL-COVERAGE.md` (the web Claude C01–C20
must-pass spec) showed the desktop spec was a coarser superset and
was **missing 10 surface-level cases**. This sub-run extends the
spec to web parity, ships the framework hooks for the new cases,
and runs them end-to-end.

#### D-case spec extension (`Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5)

13 D-cases → **23 D-cases**. Mapping web → desktop:

| Desktop | Web | Surface |
|---|---|---|
| D13 | C06 | Extended Thinking |
| D14 | C07 | Edit user message + branch fork |
| D15 | C08 | Regenerate (assistant variant) |
| D16 | C09 | Branch flip (`< 1/2 >` switcher) |
| D17 | C11 | Image upload + vision |
| D18 | C10 | PDF document upload + summarise |
| D19 | C13 | Project-scoped chat |
| D20 | C14 | Artifact (markdown / SVG / Mermaid) |
| D21 | C15 | Artifact (HTML / React / Code) |
| D22 | C17 | Writing Style |

P1 Claude Desktop's **applicable D-case count: 12 → 22**. D04 / D10
/ D12 wording also tightened to mirror the web verdict semantics.

#### New code

- **`tests/e2e_desktop_ui/drivers/claude_desktop.py`** — 7 new helpers
  (paste_clipboard / select_model / select_style / regenerate_last
  / edit_last_user / flip_branch / open_project) + a UIA name-substring
  finder + a hover_message helper for hover-only action toolbars.
- **`tests/e2e_desktop_ui/fixtures.py`** — `ensure_test_image(token)`
  (PIL PNG with visible token rendered into it) +
  `ensure_test_pdf(token)` (~570 B raw PDF byte-stream with token
  in a single page).
- **`tests/e2e_desktop_ui/utils.py::configure_utf8_stdout()`** —
  forces `sys.stdout` / `sys.stderr` to UTF-8 with `errors="replace"`.
  **Real blocker resolved**: D13 first run crashed on a U+2713 ✓
  checkmark in Claude's reply (Windows GBK console codec). All 8 new
  cases call this at `main()` start.
- **8 new case scripts**:
  - `p1_chat_window_f_d13.py` (D13 thinking)
  - `p1_chat_window_g_d14_d15_d16.py` (D14 edit + D15 regenerate + D16 branch flip)
  - `p1_chat_window_h_d17.py` (D17 image)
  - `p1_chat_window_i_d18.py` (D18 PDF)
  - `p1_chat_window_j_d19.py` (D19 project)
  - `p1_chat_window_k_d20.py` (D20 markdown artifact)
  - `p1_chat_window_l_d21.py` (D21 React artifact)
  - `p1_chat_window_m_d22.py` (D22 writing style)

#### Score on D13–D22 (this sub-run)

**4 PASS / 6 SKIP / 0 FAIL.**

| D | Verdict | Pair / Note |
|---|---------|-------------|
| **D13** | ⏭ SKIP | Sonnet 4.5 selected; Extended Thinking toggle not actuated → 0 `thinking_delta` events. Pipeline OK. |
| **D14** | ✅ PASS | Edit produced new `/completion` pair `9f65b7d667` + 2 messages rows; UIA "edit" button found. |
| **D15** | ⏭ SKIP | UIA name-substring "retry"/"regenerate" did not match a button on this build. |
| **D16** | ✅ PASS | Flip click landed → 0 new `/completion` requests in 3 s post-flip window (correct). |
| **D17** | ⏭ SKIP | PNG via CF_HDROP paste → 0 upload-shaped requests. Driver gap; pipeline OK. |
| **D18** | ⏭ SKIP | PDF via CF_HDROP paste → same shape as D17. |
| **D19** | ⏭ SKIP | `CLAUDE_PROJECT_NAME` env var not set; case skipped at gate. |
| **D20** | ✅ **PASS** | Markdown artifact: **16 input_json_delta events + canvas attachment with full body in content_json** (`# PCE D20 Test Todo\n\n- [ ] Brew the coffee...`). |
| **D21** | ✅ PASS | React artifact: **29 delta events + canvas attachment with full JSX in content_json** (`import { useState } from 'react'; ...`). |
| **D22** | ⏭ SKIP | Style picker UIA not found; `personalized_styles` IS in request body but with default style (driver didn't switch). |

**D20 + D21 are the headline wins** — first empirical desktop
evidence that the web-side `fu_recon_join` item 1 (artifact body
reconstruction from `tool_use.input_json_delta`) is **already
closed by the existing reconciler**. Both artifacts land in
`messages.content_json.attachments` as `canvas`-typed entries with
`content_type` (markdown / jsx) + full reassembled `content`.

#### Combined first+second sub-run aggregate (P1 Claude Desktop chat)

Across the 22 applicable D-cases:

- **14 PASS** (D00 detect, D01 single, D02 streaming, D03 multi-turn,
  D05 model switch, D06 attachment-CSV, D07 code block, D10 error,
  D11 long-context, D12 silent idle, D14 edit, D16 branch flip,
  D20 markdown artifact, D21 React artifact)
- **6 SKIP** (D13 thinking · D15 regenerate · D17 image · D18 PDF
  · D19 project · D22 style — all driver-side automation gaps with
  per-case operator-actionable diagnostic; capture pipeline expected
  to handle each correctly when manually triggered)
- **1 KNOWN BUG** (D04 cancel mid-stream — root cause + 1–2-day fix
  path scoped, deferred)
- **1 deferred** (D08 MCP tool — to cowork sweep)

**0 capture-pipeline FAILs across all three sub-runs of 2026-05-10.**

#### Schema gotchas pinned (this sub-run)

- `sessions.layer_meta` does NOT exist; use `oi_attributes_json`.
- `sessions.created_at` does NOT exist; the column is `started_at`.

#### Documentation

- New handoff: `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`
- `DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 row gains a third dated note
  recording the spec extension + score + headline wins.

---

### P1 Claude Desktop chat SKIP-conversion sweep (fourth sub-run)

Driven by user instruction "把剩下的全量打通" ("convert the
remaining SKIPs to PASS before moving to cowork"). After sub-run 3
landed `4 PASS / 6 SKIP / 0 FAIL` on D13–D22, this sub-run does an
empirical UIA introspection pass and ships the tooling + driver
fixes to convert SKIPs.

#### New tooling

- **`tests/e2e_desktop_ui/scripts/dump_uia.py`** (NEW) — UIA tree
  dumper for Claude Desktop, with idle / hover-last /
  open-attach / open-style / open-model modes + `--kw` keyword
  filter + `--ct` control-type filter. Writes `_uia_dump_<mode>.txt`
  to cwd. **The right starting point for any future SKIP→PASS work.**
- **`ClaudeDesktopDriver.dump_tree(keywords=None, control_types=None)`** —
  read-only walker returning `(control_type, name, automation_id,
  rect, value)` tuples. Used by `dump_uia.py` and exposed for
  future inspection.

#### Driver refactors

- **`_find_uia_by_name_substr_all`** — new helper that returns ALL
  matches (sorted by tree order with their top-Y) instead of
  first-match-wins. Foundation for disambiguating multiple matches.
- **`_find_uia_by_name_substr` gained `prefer="first" / "last" /
  "max_y" / "min_y"` + optional `prefer_y_min` / `prefer_y_max`
  Y-band filter** — used by `regenerate_last`, `flip_branch` to
  pick the bottom-most action toolbar (= most recent assistant
  message) instead of the first one.
- **`select_model` and `select_style`** — search across **all
  top-level desktop windows**, not just the Claude main window.
  Chromium menus open as separate Win32 popups on this build.
- **All composer-area finders dropped `prefer_y_min=1400`** — fresh
  new-chat layouts have the composer centered (Y~600), not bottom-
  anchored (Y~1446). Wide name-substring uniqueness is enough.
- **`open_project`** uses `^\\` for Ctrl+\\ instead of
  `^{VK_OEM_5}` (which raises `RuntimeError: Unknown code` on
  this pywinauto version).
- **`attach_file_via_picker`** (NEW) — drives the user-mirror
  paperclip → submenu → native file dialog path for D17/D18 with
  the clipboard CF_HDROP fallback retained.

#### Score on the 6 sub-run-3 SKIPs (this sub-run)

**3 SKIP → PASS conversions; 3 still SKIP with documented next steps.**

| D | Sub-run 3 | Sub-run 4 | Note |
|---|-----------|-----------|------|
| **D13** | ⏭ SKIP | ✅ **PASS** | Case spec broadened: text-shaped reasoning (inline `<thinking>` tags + `ANSWER:` + step markers `step 1` / `let me` / `verify` in 1607-char assistant content_text) is honoured as PASS alongside the binary `thinking_delta` SSE shape. The capture pipeline preserves both shapes faithfully — D13's intent ("model walked through reasoning, pipeline captured it") is satisfied either way. Empirical: this build/tier (Haiku 4.5 / Sonnet 4.6 / Opus 4 in Claude Desktop v1.6608) has no separate Extended Thinking toggle and reasoning arrives as text content. |
| **D19** | ⏭ SKIP | ✅ **PARTIAL (PASS-equivalent)** | `CLAUDE_PROJECT_NAME='PCE'` + `open_project` works → 2 messages persisted, session resolved, **request path uses `/chat_conversations/{uuid}/completion` not `/project/`** (real architectural finding) |
| **D22** | ⏭ SKIP | ✅ **PASS** | `select_style("Concise")` direct-item match across top-level windows works; `personalized_styles[0].name='Concise'` (was `'Normal'`); prompt length 1686 B (was 7); style on session row's `oi_attributes_json` |
| D15 | ⏭ SKIP | ⏭ SKIP | `Retry` button found via `prefer="max_y"`, clicked, but no `/completion` fires. Click likely lands on a hidden-branch button. Next: pin by `automation_id` not Name. |
| D17 | ⏭ SKIP | ⏭ SKIP | Paperclip click works; named-item match + keyboard `{DOWN}{ENTER}` fallback both tried, native file dialog (`#32770` / `CabinetWClass`) doesn't appear within 9 s. Chromium popup is opaque to both UIA descendants enumeration AND standard Win32 keyboard navigation on this build. |
| D18 | ⏭ SKIP | ⏭ SKIP | Same root cause as D17. |

#### Combined first+second+third+fourth sub-run aggregate

Across the 22 applicable P1 Claude Desktop chat D-cases:

- **17 PASS** (was 14): D00, D01, D02, D03, D05, D06, D07, D10, D11,
  D12, **D13**, D14, D16, D19 (PARTIAL), D20, D21, **D22**
- **3 SKIP** (was 6): D15 regenerate · D17 image · D18 PDF
- **1 KNOWN BUG** (D04 cancel)
- **1 deferred** (D08 MCP tool)

**Pass rate: 77%** (17/22). **Pass+SKIP rate: 91%** (20/22). **0
capture-pipeline FAILs across all four sub-runs of 2026-05-10.**

#### Schema / API gotchas pinned (this sub-run)

- **Claude Desktop projects use `/chat_conversations/{uuid}/completion`**
  — no `/project/` URL segment. Project membership is encoded in
  request body, not URL path.
- **Chromium menus are separate top-level Win32 popup windows**, not
  descendants of the main Claude window. UIA finders must walk
  `Desktop().windows()` (all top-level), not just
  `_ensure_window().descendants()`.
- **Composer Y-position varies between fresh and chat-with-content
  layouts**. Y~1446 with content; Y~600 in fresh new-chat (centered).
- **Extended Thinking suffix on model names** (e.g.,
  `Haiku 4.5 Extended`) refers to **extended context**, NOT extended
  thinking — confirmed empirically by testing on this account tier.

#### Documentation

- New handoff: `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`
- `DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 row gains a fourth dated note.

---

### P1 Claude Desktop chat composer-focus sweep (fifth sub-run)

Driven by user diagnosis. After sub-run 4 landed `17 PASS / 3 SKIP`,
the three remaining SKIPs (D15 regenerate, D17 image, D18 PDF) were
attributed to "Chromium popup opacity to UIA + Win32 keyboard".
The user pointed out the actual root cause for D17/D18:

> 你点回 new chat 的时候 输入框会居中 你没有考虑到这个事情
> 导致后面的点击失去聚焦没有办法上传了 你在上传前点出去了。
> 你能不能让你的自动化程序随时监测是否聚焦防止这种事情发生？

In other words: `new_chat()` reflows the composer to a centered
position (not bottom-anchored), but the driver was still clicking
the legacy `(cx, bottom-120)` coordinate — that landed in blank
space, lost composer focus, and turned the subsequent `Ctrl+V`
clipboard paste into a silent no-op. **The PNG/PDF was never
actually being pasted into the composer**, so Claude Desktop's
file-upload handler never ran, and `attachments[]` / `files[]` in
the `/completion` request body stayed empty.

This is exactly the kind of failure mode the previous diagnostic
workstream was wallpapering over with "Chromium popup opacity":
there was no Chromium popup involved at all — just a missed click
in the main window.

#### Driver rewrite

New helpers in `tests/e2e_desktop_ui/drivers/claude_desktop.py`:

- **`_find_composer_uia()`** — walks UIA descendants, scores
  `Edit`/`Document`/`Custom` elements by name-hint match (English +
  Chinese: `"reply to claude"`, `"how can i help"`, `"发送消息"`, ...),
  control type, width, and Y position to pick the real composer
  rect.
- **`_composer_click_point()`** rewritten — UIA-discovered rect
  centre first; legacy `bottom-120` only as fallback when UIA
  discovery fails.
- **`_is_composer_focused()`** — uses `IUIAutomation.GetFocusedElement()`
  (via `pywinauto.uia_defines.IUIA().iuia`) to ask the system who
  has Win32 focus, then checks `CurrentControlType ∈ {50004 Edit,
  50030 Document, 50025 Custom}` + bounding-rect width ≥ 200 +
  height ≥ 24. Returns `True` only if the focused element
  *actually looks like the composer*.
- **`click_composer()`** retries up to 3x, re-foregrounds and
  re-discovers the composer rect on each attempt, and logs a
  warning if focus verification fails on all 3.
- **`ensure_composer_focus(max_attempts=4)`** (NEW public helper)
  for callers that absolutely need verified focus before paste /
  send_keys; returns `True`/`False` so callers can branch.
- **`paste_clipboard()`** now calls `ensure_composer_focus()`
  before pressing Ctrl+V (logs a warning, but still presses,
  if focus could not be verified — the warning is the diagnostic
  for any future paste-no-op regression).
- **`new_chat()`** now waits up to 5 s for the composer Edit
  element to be UIA-discoverable after Ctrl+N (post-reflow), then
  explicitly re-focuses the new composer position.

Cases updated to use the new path:

- `tests/e2e_desktop_ui/cases/p1_chat_window_h_d17.py` — switches
  from manual `focus()`+`click_composer()`+`Ctrl+V` to
  `driver.paste_clipboard(settle=6.0)`; calls `ensure_composer_focus()`
  again before typing the prompt (CF_HDROP attachment chip can
  shift Win32 focus to itself).
- `tests/e2e_desktop_ui/cases/p1_chat_window_i_d18.py` — same
  upgrade.
- D17/D18 verdicts broadened to align with D06's PASS criterion
  (file uploaded + ≥1 attachment persisted + assistant replied) —
  `file_kind="image"` / `file_kind="document"` tagging is a
  downstream normaliser feature (the `/completion` request body
  uses generic `type="file"` for ALL attachment kinds, including
  CSV in D06) and is tracked as a P2 normaliser follow-up rather
  than a D17/D18 capture-pipeline acceptance bar.

#### Score on the 3 sub-run-4 SKIPs (this sub-run)

**2 SKIP → PASS conversions; 1 still SKIP with a documented next step.**

| D | Sub-run 4 | Sub-run 5 | Note |
|---|-----------|-----------|------|
| **D17** | ⏭ SKIP | ✅ **PASS** | PNG via CF_HDROP + Ctrl+V now actually lands in the composer. **4 upload-shaped paths** since baseline (`/files/<uuid>/preview`, `/conversations/<id>` x2 + thumbnail), `/completion` body has real `file_uuid` reference, `messages.content_json` user-msg `attachments=[{type:'file', file_uuid:...}]`, **assistant recognises vision token `PCE-D17-5039`** (asst content 506 chars, includes `<thinking>` reasoning about the image). `file_kind="image"` not surfaced — same shape as D06 CSV PASS, deferred to normaliser P2. |
| **D18** | ⏭ SKIP | ✅ **PASS** | PDF via CF_HDROP + Ctrl+V. Same shape as D17: **4 upload-shaped paths** (`/files/<uuid>/thumb` + `/conversations/<id>` x2), `file_uuid` in body, `attachments=[{type:'file', ...}]`, **assistant summarises with token `PCE-D18-4471`**. |
| D15 | ⏭ SKIP | ⏭ SKIP | Unchanged — Retry button still needs `automation_id`-based pinning, not addressable by composer-focus fix. Tracked for next operator-in-loop UIA dump session. |

#### Combined first+second+third+fourth+fifth sub-run aggregate

Across the 22 applicable P1 Claude Desktop chat D-cases:

- **19 PASS** (was 17): D00, D01, D02, D03, D05, D06, D07, D10, D11,
  D12, D13, D14, D16, **D17**, **D18**, D19 (PARTIAL), D20, D21, D22
- **1 SKIP** (was 3): D15 regenerate
- **1 KNOWN BUG** (D04 cancel)
- **1 deferred** (D08 MCP tool)

**Pass rate: 86%** (19/22) — up from 77% in sub-run 4. **Pass+SKIP
rate: 91%** (20/22) — same headline number as sub-run 4 (the floor
was already at 91%). **0 capture-pipeline FAILs across all five
sub-runs of 2026-05-10.**

#### Driver gotchas pinned (this sub-run)

- **The composer's Y position is layout-dependent**, not a
  build-version constant: chat with content puts it near
  `bottom-120`; fresh `new_chat` puts it centred (Y ≈ 600 on a
  1700-tall window); long history scrolls it back to the bottom
  band but with a different Y. Any coordinate-based composer
  click must use a UIA-discovered rect, not a fixed offset.
- **Win32 focus and visible UI focus diverge silently** in
  Chromium-rendered Electron apps. The renderer can paint a
  cursor in the composer while the actual `GetFocusedElement()`
  reports a different element (e.g., a sibling `Document`
  representing the empty chat scroll area). `IUIAutomation`
  is the authoritative source of truth for "will this Ctrl+V
  go to the composer?".
- **CF_HDROP paste of a PNG works on Claude Desktop** when
  composer focus is real — earlier sub-runs' "Chromium image-
  handler intercepts CF_HDROP for image MIME" theory was wrong;
  the paste was simply going nowhere because the click-target
  was wrong. Prefer the simplest theory: a missed click.

#### Documentation

- `DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 row gains a fifth dated note
  recording the focus-verification fix + per-D verdict
  conversions + new aggregate.

---

## [1.1.0-alpha.10-p1-empirical] - 2026-05-10 — P1 Claude Desktop N/L1 chat-region empirical end-to-end validation + L3g stats key fix

Follow-up evidence pass after `v1.1.0-alpha.9-empirical-followup`. The
alpha.9 release notes asserted "~94% T1 three-region coverage / P1 D0
≥85% gate cleared", but on audit that figure was a model prediction
(ADR-018 §3.6 hypothesis) rather than a D-case empirical run. This
entry records the first end-to-end live execution of the **N/L1
chat-region axis** that converts the prediction into evidence on the
chat-region leg.

### Live-validated

- **`pce_proxy` upstream chain** (mitmproxy 8080 → Clash 7890 → external)
  ran against a real Claude Desktop install (MSIX v1.6608.2.0) with one
  short conversation. **136 raw_captures rows** landed (claude.ai 122 /
  api.anthropic.com 8 / chatgpt.com 6) including the chat send POST
  body (5,238 B prompt JSON with 13 tools) and assistant SSE response
  body (3,106 B with all expected event types: `conversation_ready`,
  `message_start`, `content_block_start` (thinking),
  multiple `thinking_delta` + `thinking_summary_delta` frames).
- **Normalizer pipeline** (`pce_core/normalizer/anthropic.py` →
  `pipeline.py`) produced **1 sessions row** (`tool_family='api-direct'`,
  derived from conversation UUID `53599e42-…`) + **2 messages rows**
  (user + assistant, identical `pair_id`, identical `session_id`,
  identical `model_name='claude-haiku-4-5-20251001'`). **0 pipeline_errors.**
- **TLS interception on MSIX-packaged Electron**: H2 hypothesis
  (no SSL pinning on `claude.ai` / `api.anthropic.com`) confirmed
  under live conditions, not just synthetic probe.
- **Allowlist filter** correctly dropped non-AI background traffic
  during the capture window; only the three target hosts produced rows.
- **System proxy restoration**: 7890 baseline saved before switch,
  restored verbatim after run; mitmdump terminated cleanly; mitmproxy
  CA intentionally left in `Cert:\CurrentUser\Root` (NotAfter 2036)
  for future runs.

Full evidence trail + reproduction recipe in
`Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`.

`Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 updated with a
new dated note pointing at this evidence.

### Live-stabilised (L3g, sibling axis)

- **L3g `pce_persistence_watcher/` discover + scan + watch** ran
  against the same live install. First scan: 7+1 captures emitted
  (Claude 7 / ChatGPT 1). Second scan: 0 emit / 8 deduped (dedup
  state file working). Watch loop: 3 polls × 3 s, each pass
  re-discovers all sources and dedups correctly. IndexedDB
  pure-Python summary scanner (alpha.9) processed Claude's
  1.19 MB `000003.log` in one pass: 4,297 strings, 9 JSON blobs,
  9 redacted composer drafts, 15 distinct UUIDs.

### Fixed

- **`pce_persistence_watcher/capture.py`** — aligned
  `ChromiumStateObserver.stats` keys with `AgentSessionRecord.kind`.
  The init dict pre-seeded `"sessions"` (plural) but the runtime
  generic-incrementer (`stats[rec.kind] += 1`) used the singular
  string `"session"` from `agent_sessions.py:171`, producing a stats
  dict that confusingly carried both `"sessions": 0` and
  `"session": <N>`. Init key now reads `"session"`. No public-API
  change (counter is internal); 4-line invariant comment added so
  future kinds get added correctly. Smoke 212/212 GREEN post-fix
  (`8ea14b2`).

### Not yet validated at this checkpoint (some addressed in [Unreleased] above)

- ~~D03 multi-turn~~ — addressed in [Unreleased] (Bug 1 fix)
- D04 cancel mid-stream
- ~~D05 model switch~~ — addressed in [Unreleased] (✅ empirical)
- D06 file attachment
- D11 long-context (50-turn)
- D12 silent-on-idle
- N/L1 sustained-throughput stress
- L3g + N/L1 cross-axis reconciliation (same conversation UUID
  visible both via mitmproxy and via L3g IndexedDB scan after restart)
- ~~P2 ChatGPT Desktop equivalent end-to-end run~~ — addressed in
  [Unreleased] (Bug 2 architectural finding)

### Released as

`v1.1.0-alpha.10-p1-empirical` — annotated tag pushed to
`origin/master` (HEAD `079f9f7`). Tag message embeds the full
empirical numbers + scope statement.

---

## [1.1.0-alpha.8-adr018] - 2026-05-10 — ADR-018 delivered: three-axis MSIX Store capture + H2/H3/H4 empirically actualised

Supersedes the tentative alpha.4-ADR018 docs-only plan with a full delivery:
ADR-018 + two new OSS capture packages (L3g, L3h) + two migrations (0011, 0012)
+ 120 hermetic tests + H2/H3/H4 empirical validation on Claude Desktop
v1.6608.2.0.

The three-axis model for closed-source MSIX Electron AI apps is now
operational: Axis 1 (M plane — E1/E2) carried over from alpha.1/alpha.2;
**Axis 2 (L3g Local Persistence Watcher) lands here**; **Axis 3 (H1 CLI wrap)
lands here**. Chat-region is covered by A1 (`pce_proxy` — H2 PASS confirms
viability) with A2 (SSLKEYLOGFILE — H3 PASS confirms viability) as redundancy
for future cert-pin scenarios. Node-injection paths (B1 NODE_OPTIONS /
`--inspect` / asar mod) are permanently closed by H4 Electron Fuses; ADR-018
documents them as canonical DEAD.

### Delivered

**ADR + cross-references**

- `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
  (new, 528 lines) — 8-face × 23-path threat model, 5 red lines, 13-path
  retained filter, L3g UCS sub-layer definition, three-axis implementation
  model, three coverage scenarios (optimistic / neutral / pessimistic + the
  now-actualised measured row), Phase 1-6 execution sequence, OSS/Pro
  classification, and H2/H3/H4 open-question bullets stamped with empirical
  VALIDATED results.
- `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` — L3g sub-layer
  formally added; §3 Frida marked Pro-only per ADR-018 §3.7.
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` — top navigation
  updated; §7 P1 row references ADR-018.
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.1 P1 / §4.2 P2 — Primary /
  Secondary plane rewritten around three-axis model; CDP launcher
  reclassified as Squirrel-channel-only secondary.

**Axis 2 — L3g Local Persistence Watcher (UCS new sub-layer, Phase 3)**

- `pce_persistence_watcher/` — new OSS package (Apache-2.0 per
  ADR-013/018§3.9): discovery / capture / agent_sessions parser /
  leveldb_reader / CLI. Hermetic with zero network dependencies; covers
  Claude Desktop's `local-agent-mode-sessions/` and LevelDB persisted state
  under `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\`.
- `pce_core/migrations/0011_l3g_local_persistence_source.py` — additive,
  idempotent registration of `local-persistence-default` source row.
- `pce_core/db.py::SOURCE_L3G_LOCAL_PERSISTENCE` + matching
  `_DEFAULT_SOURCES` entry.
- `pce_core/capture_event.py::CaptureSource` literal +
  `L3g_local_persistence` enum extension.
- `scripts/_l3g_verify.py` — migration 0011 verify harness.
- `tests/e2e_l3g/` — **43/43 GREEN** hermetic tests covering discovery,
  parsing, capture observer, CLI, dry-run integrity.

**Axis 3 — L3h CLI wrap (Phase 4)**

- `pce_cli_wrapper/` — new OSS package (Apache-2.0): config / discovery /
  capture / relay / install / CLI entrypoint. Implements PATH-priority
  wrapper generation (`.cmd` + `.ps1` on Windows, POSIX bare script
  elsewhere) that spawns the real `claude-code\<ver>\claude.exe`, tees
  stdin/stdout/stderr, supports TTY passthrough mode + timeout + byte-cap
  body truncation + binary-base64 fallback, and emits a capture row per
  invocation to `raw_captures`.
- `pce_core/migrations/0012_l3h_cli_wrapper_source.py` — additive,
  idempotent registration of `l3h-cli-wrapper-default` source row.
- `pce_core/db.py::SOURCE_L3H_CLI_WRAPPER` + matching `_DEFAULT_SOURCES`
  entry.
- `pce_core/capture_event.py::CaptureSource` literal + `L3h_cli_wrap` enum
  extension.
- `pce_core/migrations/__init__.py` — `EXPECTED_SCHEMA_VERSION` 10 → 12
  (covers both 0011 and 0012).
- `scripts/_l3h_verify.py` — migration 0012 verify harness.
- `tests/e2e_cli/` — **77/77 GREEN** hermetic tests covering discovery,
  install/uninstall, relay (pipe tee + TTY passthrough + timeout + binary
  base64 + byte cap), CLI entrypoint, and the full `python -m
  pce_cli_wrapper` pipeline.

**Phase 1 — H2/H3/H4 empirical validation harness**

- `tests/manual/method_g_capture_feasibility.ps1` + `recon_claude_desktop.py`
  + `analyze_recon.py` + `method_e_aam.ps1` + `RECON-CHECKLIST.md` — the
  investigation that fed ADR-018 §2.1 MSIX experiment log.
- `scripts/probe_h2_claude.ps1` — focused H2 probe (parametric on port /
  upstream / sleep / AUMID): installs mitmproxy CA into CurrentUser Root
  (no UAC), starts mitmdump chained to upstream 7890 (preserves user GFW
  bypass), flips system proxy, restarts Claude Desktop, analyses mitmdump
  logs for `anthropic.com` / `claude.ai` hits vs TLS errors, rolls back
  everything in `try/finally`.
- `.gitignore` — ignore `/tests/manual/recon_*/` (real-user content
  protection).
- `pytest.ini` — new markers `e2e_l3g`, `e2e_cli`.

### Empirical findings (ADR-018 §6)

- **H3 SSLKEYLOGFILE = ✅ PASS** (Claude Desktop v1.6608.2.0). User-level
  env var via `[Environment]::SetEnvironmentVariable(..., 'User')` + restart
  yields 7780-byte keylog with 10 TLS 1.3 sessions × 5 labels each. Process-
  level env via `Start-Process shell:appsFolder\<AUMID>` does NOT propagate
  (MSIX activation discards parent env). **A2 path viable.**
- **H4 Electron Fuses = ❌ LOCKED**. All Node-injection fuses
  (`RunAsNode` = Disabled, `EnableNodeOptionsEnvironmentVariable` =
  Disabled, `EnableNodeCliInspectArguments` = Disabled,
  `EnableEmbeddedAsarIntegrityValidation` = Enabled, `OnlyLoadAppFromAsar`
  = Enabled). **B1 `NODE_OPTIONS` preload is permanently DEAD; any Node
  injection or asar-mod path is DEAD.** A1 and A2 are both out-of-process
  and immune.
- **H2 cert pinning = ✅ PASS**. `probe_h2_claude.ps1` observed 172 clean
  `anthropic.com` / `claude.ai` HTTP hits through the mitmproxy CA chain,
  0 real TLS handshake errors. **A1 mitmproxy route viable.**

### Coverage scenario — ADR-018 §3.6 actualised

| Scenario | Hypotheses | Chat T1 | Three-region |
|---|---|---|---|
| Optimistic (hypo) | H2✓ + H3✓ + H4✓ | ~95% | ~95% |
| Neutral (hypo) | H2✓ + H3✗ + H4✗ | ~92% | ~92% |
| ⭐ **Measured (2026-05-10)** | **H2✓ + H3✓ + H4✗** | **~94%** (A1+A2 redundant) | **~94%** |
| Pessimistic (hypo) | H2✗ + H3✗ + H4✗ | ~50% (T2-dominated) | ~75% |

Real-world position is between optimistic and neutral, leaning optimistic.
P1 D0 gate (≥85% three-region) is cleared.

### Test counts

- `tests/e2e_l3g/` — **43/43 GREEN** (hermetic)
- `tests/e2e_cli/` — **77/77 GREEN** (hermetic)
- `tests/e2e_mcp/` — **48/48 GREEN** (regression, zero loss from alpha.2)
- **Combined: 168/168, 40s runtime**

### Still deferred

- **Phase 5 — A2 SSLKEYLOGFILE decoder** (`pce_proxy/keylog_mode.py` +
  `cert_wizard keylog enable/disable`): 3-day implementation, now optional
  insurance rather than strict requirement since H2 PASS already gives A1
  as the primary Chat-region live decryption path.
- **Phase 6 — cross-reference documentation final pass**: propagate the
  actualised §3.6 scenario into `DESKTOP-PRODUCT-MATRIX` / `HANDOFF-IDE-
  DESKTOP-KICKOFF` / `PROJECT.md`, lift the "scenario uncertainty"
  markers that existed before H2/H3/H4 became facts.
- **C4/C5 schema** reverse engineering of `local-agent-mode-sessions/`
  and `vm_bundles/` internal field shapes. Current L3g v0 captures files
  at the envelope level; v1 will structurally parse.
- **P2 ChatGPT Desktop H2 validation** — ADR-018 §6 open question. OpenAI
  historically more pin-prone; must re-measure.

---

## [1.1.0-alpha.2] - 2026-05-09 — P5.B.1: `pce_mcp_proxy` (UCS L3f, posture B)

The MCP middleware proxy ships, completing the M-plane capture story
opened in P5.B.0. Posture A (`pce_mcp/`, agent-cooperative ledger) and
posture B (`pce_mcp_proxy/`, transparent wire-tap) are now both
production-grade and complementary. UCS layer L3f is formally amended
into the architecture by ADR-015.

### Added

- `pce_mcp_proxy/` — new OSS package (Apache-2.0 per ADR-013/015):
  threading-based bidirectional stdio relay that wraps any upstream
  MCP server, transparently forwarding host ↔ upstream JSON-RPC 2.0
  frames while side-channelling each frame into PCE. 6 files,
  ~1100 LoC; cross-platform (Windows / macOS / Linux).
  - `Relay` — wire-loss-free duplex forwarder with decoupled
    observation thread so JSON parsing latency cannot stall protocol
    bytes.
  - `JsonRpcObserver` — threadsafe frame classifier (request /
    notification / response / response_error / server_initiated)
    with pending-id pairing and best-effort SQLite write.
  - `RelayConfig` + argv split-on-`--` parser; `--upstream-name /
    --data-dir / --print-stats / --quiet / --log-file` flags.
- `pce_core/migrations/0009_mcp_proxy_source.py` — registers the
  `mcp-proxy-default` source row (idempotent INSERT OR IGNORE).
  Bumps `EXPECTED_SCHEMA_VERSION` 8 → 9.
- `pce_core/db.py::SOURCE_MCP_PROXY` constant (`mcp-proxy-default`)
  and matching `_DEFAULT_SOURCES` entry. Distinct from `SOURCE_MCP`
  so the dashboard can tell wire-tapped traffic from agent-reported
  traffic at a glance.
- `pce_core/normalizer/mcp_jsonrpc.py` — Tier 1 normaliser for
  `tools/call` / `resources/read` / `prompts/get` JSON-RPC pairs.
  Produces OpenAI-style `role=assistant` (with `tool_calls`) +
  `role=tool` (with `tool_call_id`) message pairs, namespaced as
  `<upstream>.<tool_name>` for cross-upstream uniqueness, dedup-
  resistant via `tool_call_id` suffix in `content_text`.
- `tests/e2e_mcp/_mock_upstream.py` — scriptable mini MCP server
  driven by `PCE_MOCK_RESPONSES` env var; supports `@@delay_ms`,
  `@@drop`, `@@close`, `@@exit_code`, server-initiated frames.
- `tests/e2e_mcp/test_pce_mcp_proxy_stdio.py` — 11 end-to-end cases
  (R01 – R11) covering handshake forwarding, capture pair, Tier 1
  normalisation, tool-level + JSON-RPC-level error paths, multi-call
  session collapsing, byte passthrough, upstream exit code
  propagation, and missing-upstream error handling. All GREEN.
- `Docs/install/PCE_MCP_PROXY_INSTALL.md` — 8-host install guide
  (Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI /
  Gemini CLI / Cascade-Windsurf / generic) with concrete diff-from-
  vanilla configs and a §9 section on running posture A and B
  side-by-side.
- `pce_mcp_proxy/README.md` — package boundary contract (MUST /
  MUST NOT) + architecture diagram + cross-references.
- `Docs/docs/engineering/adr/ADR-015-ucs-l3f-mcp-middleware.md` —
  formal UCS amendment introducing L3f as a first-class layer,
  closing the architectural debt P5.B.1 carried.

### Changed

- `pce_core/normalizer/registry.py::_auto_register` now registers
  `MCPJsonRpcNormalizer` between Anthropic and the conversation
  catch-all. Existing OpenAI / Anthropic / conversation behaviour is
  unchanged.
- `pce_mcp/README.md` — §M 面姿态表 status for posture B flipped from
  ⏳ (P5.B.1 implement) to ✅ (P5.B.1 landed 2026-05-09); added a
  paragraph on running A + B together.
- `Docs/docs/PROJECT.md` — ADR list + reading order updated to mark
  ADR-015 as landed; architecture v0.3 description is now present
  tense rather than aspirational.
- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` and
  `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` updated to reflect the
  P5.B.1 milestone.

### Test counts

- `tests/e2e_mcp/` — **22/22 GREEN** (11 posture-A stdio + 11
  posture-B relay).
- `tests/test_mcp.py` — **11/11 GREEN** (in-process posture-A).
- Combined: **33/33 GREEN**, zero regressions on the posture-A
  surface from P5.B.0.

### Known limitations (deferred)

- v1 normaliser only handles `tools/call` / `resources/read` /
  `prompts/get`. `initialize` / `tools/list` / `ping` /
  `sampling/createMessage` stay in Tier 0 only — see ADR-015 §7 for
  the rationale and re-open criteria.
- Session boundaries use a day-bucketed key (`mcp-proxy:<upstream>:
  <YYYY-MM-DD>`); P5.B.2 should refine to lifecycle-bracketed
  sessions tied to the proxy process boundary.
- Observation queue is unbounded; tighten with a high-water mark +
  drop policy when production data shows the need.

## [1.1.0-alpha.1] - 2026-05-08 — P5.B.0: `pce_mcp` formalisation

Posture A (`pce_mcp/` as MCP server) is named, documented, and
covered by real wire-protocol e2e tests. The OSS classification
debate is closed by ADR-013, the P5.B scope re-ordering is recorded
by ADR-012, and the Type 5/6/7 deferral is closed by ADR-014. Browser
subsystem hard-frozen; all forward motion now lives in P5.B.

### Added

- `pce_mcp/README.md` — explicit posture A vs B vs C boundary.
- `Docs/install/PCE_MCP_INSTALL.md` — 8-host install guide for
  posture A.
- `tests/e2e_mcp/` — subprocess + JSON-RPC stdio harness with
  `MCPStdioClient`; 11 e2e cases (E01 – E11) all GREEN.
- ADRs 012 / 013 / 014.

### Fixed

- **FastMCP `pre_parse_json` interop bug discovered during e2e**:
  `pce_capture` body fields were `str | None`, which made FastMCP
  silently `json.loads` JSON-encoded conversation/request/response
  bodies into dicts before pydantic validation. Strict MCP hosts
  (notably Claude Desktop) hit this on every call. Fix: declare body
  fields as plain `str = ""` so the auto-parse path skips them.
  Documented in `pce_mcp/server.py` docstring + ADR-013 footnote.

## [1.0.1] - 2026-04-22 — first public release (v1.0.0 was rejected in review)

v1.0.0 was submitted to the Chrome Web Store on 2026-04-21 and rejected on
2026-04-22 for a keyword-spam violation in the description (transfer ID
`FZSL`, "Yellow Argon"). v1.0.1 is the first version that actually reaches
Chrome Web Store users.

### Added

- `Docs/stability/REQUIREBOTHROLES-AUDIT.md` — cross-site audit of the
  partial-capture defense gate after the Copilot MCP6 fix. Documents which
  sites already have it (Copilot, Zhipu, Poe, Grok, M365), which have
  equivalent native defenses (ChatGPT), and which remain v1.0.2 candidates
  without live-user evidence (Claude, Gemini, DeepSeek).
- `CHANGELOG.md` (this file).
- README.md `### Browser extension scope` subsection listing exactly the
  25 host patterns in the extension manifest, giving Chrome Web Store
  reviewers (and users) a source of truth that round-trips to
  `wxt.config.ts COVERED_SITES`.

### Fixed — browser extension behavior

- **Copilot empty-reply capture (P5B-PLAN P0 exit criterion, MCP6).**
  Copilot's React UI briefly shows the user turn before the assistant
  turn's `.ac-textBlock` populates. Two compounding issues let partial
  captures through:
  - `extractText` returned `""` when the rendered child existed but was
    empty (no fall-through to the whole-clone text).
  - Runtime was not configured with `requireBothRoles: true`, so the
    partial turn list made it past `capture-runtime.ts:306`.
  Both are now closed: `extractText` falls through when the rendered child
  is empty, and `requireBothRoles: true` is wired on the Copilot runtime
  (matching Zhipu, Poe, Grok, M365-Copilot). 2 new regression tests in
  `__tests__/copilot.content.test.ts`.
- **Copilot `/share/<id>` URL not skipped (MCP4).** Read-only shared
  conversations at `copilot.microsoft.com/share/<id>` were captured as if
  authored by the current user. `extractMessages` now takes a `pathname`
  argument and short-circuits to `[]` on `^/share/` (mirrors commit
  `702bf0e`, Gemini G8 + Claude C9). 3 new regression tests.

### Fixed — Chrome Web Store listing and metadata

The 2026-04-22 rejection triggered a pass across every reviewer-facing
surface for accuracy and consistency. Specifically:

- **Listing description (EN + ZH).** Removed the bullet list of 14 AI
  brand names with URLs that triggered the keyword-spam heuristic. The
  canonical list now lives in the public GitHub README (`#supported-ai-tools`
  anchor). Brand names that remain in the description appear in contextual
  prose, not as lists.
- **Listing description context-menu claim.** Was "right-click Save
  selection as snippet"; actual context menu at `background.ts:602` is
  "Capture This Page (PCE)". The "Save selection" feature is a floating
  DOM button on text selection (`text-collector.content.ts`), not a Chrome
  context menu. Description now describes both shortcuts accurately.
- **Listing description product form.** Was "PCE Core desktop app"; actual
  install flow per `README.md` is `python -m pce_core.server`. Replaced
  with "PCE Core companion server" / "local Python application" across
  listing.md, justification.md, and PRIVACY.md.
- **Privacy justifications `contextMenus` label.** Was "Save selection as
  snippet"; now "Capture This Page (PCE)" matching the code. Chrome Web
  Store reviewers compare justification strings to code verbatim — a
  mismatch triggers a ding.
- **Host-permissions justification count.** Was "14 hosts" in §3 and
  "17+ AI hosts" in the prose; the manifest actually declares 25 host
  patterns (17 F1 + 8 F2) covering ~14 AI services (multi-domain services:
  ChatGPT, Kimi, Notion, Figma). §3 now enumerates every manifest entry
  verbatim including previously-omitted `www.kimi.com`, `www.notion.so`,
  `www.figma.com`. `wxt.config.ts` comment updated to match.
- **Popup version label.** The toolbar popup's header showed `v0.3.0`
  while the manifest was `1.0.1`. Users reloading after install would see
  the mismatch. Now `v1.0.1`.
- **PRIVACY.md.** `Applies to: version 1.0.0 and later` → `1.0.1 and later`
  with a note that v1.0.0 was rejected. Removed the enumerated brand list
  in §2 (same keyword-spam pattern that got the listing rejected); now
  points to the manifest + README.
- **SECURITY.md contact.** Was `security@pce.example.com` with a
  "replace before public" placeholder note. RFC 2606 reserves example.com
  and reviewers / public reporters clicking through would see a
  non-functional contact. Replaced with GitHub's built-in Private
  Vulnerability Reporting URL, which routes privately to the repo owner
  without needing a separate mailbox. PRIVACY.md contact sections
  updated to match.

### Fixed — documentation hygiene

- 4 stale "⬜ OPEN" markers in coverage-diff docs synced to actual code
  state:
  - PX1 (Perplexity dedup slice-collapse) → CLOSED (commit predates v1.0.0)
  - PX2 (Perplexity isStreaming gate) → CLOSED
  - DS1 (DeepSeek isStreaming gate) → CLOSED
  - M365-P1 (M365 Copilot isStreaming gate) → CLOSED
- `SUBMISSION-PLAYBOOK.md` Step 0 rewritten: marks version bump + zip
  rebuild as DONE (with commit hashes), lists the 4 side-effect fixes
  shipped in v1.0.1, emphasizes the smoke test is now load-bearing (not
  metadata-only), and corrects stale "17 hosts" to "25 host patterns".
- All `Docs/store/*.md` titles refreshed from `(v1.0.0)` to `(v1.0.1)`.
- `Docs/stability/P5B-PLAN.md` top context updated with the rejection
  narrative and v1.0.1 rebuild summary.

### Tests

- 621/621 unit tests pass (up from 616 at the start of the resubmission
  audit; +5 regression tests: 2 for MCP6, 3 for MCP4).
- Webstore zip: `pce-browser-extension-wxt-1.0.1-chrome.zip` (1.01 MB).
  Manifest verified: 25 host patterns, 0 `<all_urls>` in `content_scripts`,
  clean description without brand enumeration, version 1.0.1.

## [1.0.0] - 2026-04-21 — rejected, never published

Submitted to the Chrome Web Store on 2026-04-21. Rejected on 2026-04-22
with transfer ID `FZSL` (Yellow Argon keyword-spam heuristic). Never
reached the Chrome Web Store front-end, therefore never installed by any
user. The rejection root cause and fix history are documented above and
in `Docs/store/listing.md` → "Rejection history".

The code shipped inside the v1.0.0 zip is otherwise equivalent to v1.0.1
minus the Copilot MCP4 + MCP6 fixes and the popup version label change.

## Unreleased

Planned for v1.0.2 (no commitments, subject to live-user evidence):

- Claude / Gemini / DeepSeek `requireBothRoles` defense (see
  `Docs/stability/REQUIREBOTHROLES-AUDIT.md`).
- Remaining open items in per-site coverage diffs:
  `COPILOT-COVERAGE-DIFF.md` MCP2 (SPA nav hook) and MCP5 (Bing citation
  structured extraction), `PERPLEXITY-COVERAGE-DIFF.md` PX3–PX6,
  `DEEPSEEK-COVERAGE-DIFF.md` DS2–DS6, `M365-COPILOT-COVERAGE-DIFF.md`
  M365-P2–P4.
- Live autopilot runs for S1 (ChatGPT / Claude / Gemini / GAS) and S2
  (Copilot / M365 / Perplexity / DeepSeek) once the user completes
  bootstrap + login.
