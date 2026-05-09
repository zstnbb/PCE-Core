# HANDOFF: P5.B — IDE & Desktop & MCP Capture (v1.1)

> **Status**: active kickoff — effective 2026-05-08.
>
> **Supersedes**: `Docs/handoff/HANDOFF-P5A-KICKOFF.md` as the "current
> active handoff". P5.A is not cancelled — its browser-extension slice
> landed and is now hard-frozen per
> `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md`. The back-end
> deliverables of P5.A (CaptureEvent v2, migration 0006, Ingest API
> hardening) remain live and carry forward into P5.B unchanged.
>
> **Predecessor**: `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md`
> (same-day sibling). Read it before this one.
>
> **Framework authority**: every "why" question in this handoff resolves
> to `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`. Do not
> re-derive the 4-plane model or the attack sequence — it's canonical.
>
> **Validation authority**: every "what counts as done" question
> resolves to `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`. That doc lists
> the 8 in-scope products (P1-P8), the Tier-D classification, the 13
> D-cases, and the per-sub-phase unlock matrix referenced in §4 below.
>
> **Audience**: Cascade, future agents, project owner.

---

## 1. Phase transition summary

```
P5.A Subscription Capture (v1.0)                   → ✅ browser slice sealed
    browser-extension + S0/S1 site coverage              (freeze 2026-05-08)
    + back-end CaptureEvent v2 + normalizer             + back-end carries forward

P5.B IDE & Desktop & MCP (v1.1)                    → 🟢 ACTIVE (this doc)
    MCP middleware (L3f) + Electron preload (L3b)
    + CLI gateway (L3e)

P6 Pinning-Proof (v1.2)                            → ⏸ future
    L2 Frida + surviving pinned clients

P7+ Full Supervisor & force-capture                → ⏸ future
```

---

## 2. Your reading order (for new agents)

Do not skip; ~2 hours one-time investment. Priorities per file:

### Canonical framework & strategy (must read first)

1. `Docs/docs/PROJECT.md` §1-§11 — project boundaries (canonical phase
   division includes the 2026-05-08 P5.A → P5.B transition).
2. **`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`** ⭐ — the
   4-plane capture model + 6+1 application types + UCS×plane mapping
   + normalizer three-stage pipe + four archetype tubes + §8 attack
   sequence. **Every strategic question in P5.B routes through this
   document.**
3. `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` — what's
   frozen, what's carried forward, known issues NOT to touch.

### Existing architecture (skim, absorb terminology)

4. `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` — the UCS
   bible (1340 lines). P5.B introduces **L3f** as a new axis inside
   the UCS model; a follow-up ADR (see §7) will amend UCS DESIGN v0.3
   accordingly.
5. `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md` — the
   A+A release strategy (small-step releases, Open Core). P5.B
   preserves it but **reorders the P5.B scope**: L3f leads, L3b
   next, L3c postpones to P5.C.
6. `Docs/docs/decisions/2026-04-26-regenerate-edit-branch-semantics.md`
   — the branch/variant contract (migration 0008). Important because
   the IDE `interaction_kind` and MCP `tool_call` semantics build on
   top of the same storage schema.

### Existing PCE assets that P5.B will re-use

7. `@f:\INVENTION\You.Inc\PCE Core\pce_mcp\server.py:1-406` — **PCE is
   already an MCP server** (姿态 A). 6 working tools over stdio +
   SSE. P5.B.0 packages this as a distributable asset; P5.B.1 builds
   the middleware proxy (姿态 B) alongside it.
8. `@f:\INVENTION\You.Inc\PCE Core\pce_core\normalizer\` — 10 files,
   ~180 KB of normalization logic. Anthropic / OpenAI / Google
   adapters + conversation reconciler + branch/variant handling all
   exist. P5.B adds `mcp_jsonrpc.py` and `ide_interactions.py`;
   it does **not** rewrite what's there.
9. `@f:\INVENTION\You.Inc\PCE Core\pce_core\models.py:12-66` —
   `CaptureIn.source_type` already enumerates `mcp | ide_plugin |
   local_model`. P5.B adds `desktop_electron` / `mcp_proxy` /
   `cli_gateway` / `native_ax` as additional enum values.

---

## 3. Target mental model

From framework §8, the P5.B attack order is **capture-plane-first**,
not product-first:

```
Plane M (MCP)       → 9 products in one surface   → P5.B.0 + P5.B.1
Plane H (process)   → 4-5 Electron products       → P5.B.2 + P5.B.3
Plane N (local)     → 5-8 CLI/local products      → P5.B.4
Plane N (pinned)    → ChatGPT Desktop             → P6 (out of P5.B)
Plane U (AX/UIA)    → Raycast / native clients    → P6/P7 (out of P5.B)
```

**This means:** you are NOT supposed to ask "which product next?" at
the start of a work session. You are supposed to ask "which plane is
the current sub-phase on?", then the products drop out automatically.

---

## 4. Sub-phases

Each sub-phase is a **releasable slice** (preserving the A+A "small-
step releases" decision from ADR-009). After each sub-phase the user
should be able to capture something real they couldn't capture before.

### P5.B.0 — MCP asset formalisation (姿态 A) — 1 evening

**Goal**: `pce_mcp/server.py` becomes an installable, documented,
tested product. Zero new capture code; it's already written.

**Deliverables**

- `Docs/install/PCE_MCP_INSTALL.md` (new file) — one-page user guide
  covering **Claude Desktop**, **Cursor**, **Windsurf**, **Claude
  Code**, **Codex CLI** — each with the exact `mcp.json` / config
  block, verification steps, and uninstall instructions.
- `tests/e2e_mcp/test_pce_mcp_server.py` (new file) — end-to-end
  verification that each of the 6 tools in `pce_mcp/server.py`
  round-trips through the ingest path into SQLite.
- `pce_mcp/README.md` (new file or expand `__init__.py` docstring)
  — explain姿态 A vs 姿态 B distinction, point at the framework.
- (Optional) `.claude/CLAUDE.md` or `.cursor/.mcp-prompt.md` snippet
  — a system-prompt fragment that nudges the agent to call
  `pce_capture` when it finishes a user-visible action.

**Acceptance**

- [ ] User can install `pce_mcp` in Claude Desktop in ≤3 minutes
      following the install doc.
- [ ] `pce_capture` round-trips through normalizer into
      `sessions` + `messages` with the same schema as browser-
      extension captures (i.e. no new discovered divergence).
- [ ] `tests/e2e_mcp/` has at least 1 passing case per tool in
      `pce_mcp/server.py`.

**Non-goals**

- Do NOT build the middleware proxy yet. That's P5.B.1.
- Do NOT add new tools to `pce_mcp/server.py`. Six is enough for v1.1.

### P5.B.1 — MCP middleware proxy (姿态 B, L3f) — 3-5 days

**Goal**: `pce-mcp-proxy` — a transparent stdio wrapper around any
upstream MCP server that duplicates every JSON-RPC frame into PCE.

**Deliverables**

- `pce_mcp_proxy/` (new package) — a Python entrypoint that:
  - spawns an upstream MCP server as a subprocess
  - relays stdin ↔ stdout between host and upstream
  - asynchronously POSTs each frame to the Capture Ingest API
  - supports the MCP 2024-11-05 spec (initialize / tools/list /
    tools/call / resources/list / prompts/list / notifications)
- `pce_core/normalizer/mcp_jsonrpc.py` (new file) — segment ① provider
  adapter for JSON-RPC frames → provider-native dict. Should produce
  `role ∈ {tool, tool_result}` + `tool_call_id` correlations that
  segment ② (`message_processor.py`) already knows how to handle.
- `pce_core/models.py` — add `mcp_proxy` to `CaptureIn.source_type`.
- `tests/test_mcp_proxy/` — unit tests for transparent relay + ingest
  parity across 3 representative upstream servers (filesystem, git,
  puppeteer).
- `Docs/install/PCE_MCP_PROXY_INSTALL.md` — user guide with `mcp.json`
  examples that replace each upstream server with `pce-mcp-proxy
  --upstream <server>`.

**Acceptance**

- [ ] Tool-call-heavy workflow in Claude Desktop (e.g. "use
      filesystem MCP to list my desktop dir, then read one file")
      produces correlated user turn + tool call + tool result in
      PCE's messages table, with `tool_call_id` linking them.
- [ ] Proxy adds <50 ms latency per frame in p95.
- [ ] Proxy crashes (e.g. upstream dies mid-stream) are logged to
      `pipeline_errors` table but do not block the host UX
      (fail-open per PROJECT.md §7.6).

**Non-goals**

- Do NOT build a new `Tool Tape` dashboard view here. Ship data
  first; render archetype ships in a separate slice (§5).

### P5.B.2 — L3b Electron preload (Claude Desktop) — 1-2 weeks

**Goal**: demonstrate one end-to-end Electron preload capture path.
Claude Desktop is picked because it's **not SSL-pinned** and is
MCP-native (gives L3f cross-validation for free).

**Deliverables**

- `pce_preload/claude_desktop/` (new) — preload script injected via
  a user-run install command. Hooks:
  - `fetch` / `XMLHttpRequest` for chat N-plane traffic
  - `child_process.spawn` for child MCP subprocess visibility
    (姿态 C — 姿态 B proxy captures the same payload; 姿态 C adds
     the spawn context so we can reconcile which MCP call came
     from which chat turn)
- `pce_core/models.py` — add `desktop_electron` to source_type.
- `Docs/install/PCE_CLAUDE_DESKTOP_INSTALL.md` — install steps;
  call out the Electron preload caveat (app signing / auto-update).
- `tests/e2e_desktop/test_claude_desktop.py` — probe-style test.

**Acceptance**

- [ ] A live Claude Desktop conversation captures in PCE with the
      same `storage_paths` / `render_paths` contract as web Claude
      (re-uses the sealed browser-extension freeze bar).
- [ ] `interaction_kind=chat` rows from Claude Desktop and
      `interaction_kind=chat` rows from web Claude are **merged
      into the same user session** (if conversation_id matches), or
      at least linked by `session_key`.

**Non-goals**

- Do NOT build ChatGPT Desktop support here (pinning → P6).
- Do NOT generalise to Cursor/Windsurf yet — that's P5.B.3.

### P5.B.3 — L3b Electron preload (Cursor / Windsurf) — 1-2 weeks

**Goal**: reuse the P5.B.2 scaffolding for the IDE-class sub-type.
Deliver **Code Session** archetype as a new render tube.

**Deliverables**

- `pce_preload/cursor/` + `pce_preload/windsurf/` — adapter configs.
- `pce_core/normalizer/ide_interactions.py` — segment ① adapter for
  IDE-class products; distinguishes `interaction_kind ∈ {chat,
  completion, composer, inline_edit, lint_diff}`.
- Migration 0009 (proposed) — add `messages.interaction_kind
  TEXT NULL`. Idempotent guard + default NULL (treated as `chat`).
- `pce_core/dashboard/` — new **Code Session** archetype view: file
  tree + diff + chat side panel. storage/render contract per
  framework §6.1.
- `tests/e2e_desktop/test_cursor.py`, `test_windsurf.py`.

**Acceptance**

- [ ] A Cursor composer session produces distinct rows per
      `interaction_kind`, not smooshed into "chat".
- [ ] Dashboard Code Session view reconstructs the diff + the
      adjacent chat turn.

### P5.B.4 — L3e CLI gateway — 1 week

**Goal**: catch CLI agents that don't natively speak MCP (Aider,
raw LM Studio / Ollama users, Plandex) by redirecting their
`OPENAI_BASE_URL`-like env var to a PCE local gateway.

**Deliverables**

- `pce_gateway/` (new) — FastAPI server on `localhost:9700` that
  forwards OpenAI / Anthropic API calls to the real upstream while
  capturing into PCE. Compatible with the OpenAI REST API shape.
- `Docs/install/PCE_CLI_GATEWAY_INSTALL.md` — per-CLI env-var
  setup (Aider / LM Studio / Ollama / LiteLLM users).
- `pce_core/models.py` — add `cli_gateway` to source_type.
- `tests/e2e_cli/` — scripted CLI runs → PCE capture assertion.

**Acceptance**

- [ ] Aider run against gateway produces Run Trace rows with
      correct tool/llm span nesting.
- [ ] Local-LLM (Ollama) captures have `provider=local` and
      `model_name=<llama3|qwen|...>`.

---

## 5. Render tubes — scheduled not skipped

Framework §6 defines 4 archetype tubes. Their ship order:

| Archetype | Populates in sub-phase | Ships as render-UI in |
|---|---|---|
| **Chat Tube** | (already shipping) | done (2026-05-08) |
| **Tool Tape** | P5.B.1 (data) | **end of P5.B.1 or P5.B.3** — not blocking; can slip |
| **Code Session** | P5.B.3 (data) | **P5.B.3** |
| **Run Trace** | P5.B.4 (data) | **end of P5.B.4 or P5.C** |

**Rule**: the data schema lands with the capture sub-phase; the
dashboard view can land one sub-phase later without breaking
anything (the messages table accepts all four shapes today; the
renderers just fall back to Chat Tube if no specific archetype
matches).

---

## 6. Explicit non-goals for P5.B

Listing these so future agents don't drift:

- **L3c VS Code Extension API as primary path** — originally listed
  in the P5.B scope in `2026-04-18-ucs-and-release-strategy.md`;
  **reordered to P5.C** (see ADR-012 in §7). For Copilot in P5.B,
  we use L1 proxy on `*.githubcopilot.com`, not the extension API.
- **Frida / SSL-pinning bypass for ChatGPT Desktop** — if P5.B.4-C
  preload attempt fails on pinned endpoints, P2 ChatGPT Desktop
  formally hands off to P6 with an ADR. **Do not** start Frida work
  inside P5.B; the D2 gate accommodates partial coverage.
- **Native Mac/Windows clients** (Raycast, BoltAI, MacGPT etc.) —
  Type 5 in framework taxonomy → L3d / L4b → P6/P7. Closed by
  `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §10.1 + ADR-014.
- **Local-LLM desktop apps** (LM Studio, Ollama, Jan etc.) —
  Type 6 → deferred. Technically capturable via L3e gateway built
  in P5.B.4-B, but not part of the v1.1 acceptance gate. Closed by
  `DESKTOP-PRODUCT-MATRIX.md` §10.2 + ADR-014.
- **OS-integrated AI** (Apple Intelligence, Windows AI, Galaxy AI,
  Pixel AI) — Type 7 → P7+ Kernel layer. Closed by
  `DESKTOP-PRODUCT-MATRIX.md` §10.3 + ADR-014.
- **Reopening any S2/S3 browser site** — see
  `HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` §1 + §4.
- **Building new MCP server tools on top of `pce_mcp/server.py`**
  beyond the existing 6 — out of scope. The 6 tools cover the v1.1
  use cases.

---

## 7. ADRs that need to land alongside P5.B.0

Before/during P5.B.0, these ADRs should be authored (1 page each):

- **ADR-012** — P5.B scope reorder: L3f/L3b precede L3c; L3c moves
  to P5.C. References
  `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §8 as the
  evidence base. **✅ landed 2026-05-08.**
- **ADR-013** — `pce_mcp/server.py` Open Core classification. Must
  decide: OSS (Apache-2.0) or Pro? Per ADR-010, tools that capture
  user-visible data sit in OSS; this one does → OSS. **✅ landed
  2026-05-08.**
- **ADR-014** — Out-of-scope closure for application Types 5
  (native non-Electron), 6 (local-LLM desktop), and 7 (OS-integrated
  AI). Codifies the rationale and reopen criteria already drafted in
  `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §10. Prevents silent
  scope creep during P5.B execution. **✅ landed 2026-05-08.**
- **ADR-015** — UCS amendment: introduce **L3f (MCP middleware)** as
  a first-class UCS layer. Updates
  `UNIVERSAL-CAPTURE-STACK-DESIGN.md` §layer enumeration. (Numbered
  ADR-015 because ADR-011 is already taken by the probe-remote-agent
  decision.) **✅ landed 2026-05-09 alongside the `pce_mcp_proxy/`
  package shipping in P5.B.1.**

---

## 8. Immediate next action

**Start P5.B.0** (MCP asset formalisation). It's the lowest-risk,
highest-documented-value move and produces the foundation everything
else in P5.B leans on.

Concrete first steps for the agent picking up this handoff:

1. Create `Docs/install/` directory if it doesn't exist.
2. Write `Docs/install/PCE_MCP_INSTALL.md` covering Claude Desktop
   / Cursor / Windsurf / Claude Code / Codex CLI.
3. Write `tests/e2e_mcp/test_pce_mcp_server.py` covering the 6
   tools.
4. Verify end-to-end by installing `pce_mcp` inside this very
   Windsurf session (Cascade is an MCP host) and having Cascade
   call `pce_capture` as a self-test.
5. Open ADR-013 (OSS classification for `pce_mcp`) — 1 page.

Optional stretch (if time remains inside P5.B.0):

- Write `TASK-007-P5B-ide-desktop-mcp.md` capturing the full P5.B
  sub-phase list as an atomic-task execution doc (matches
  `TASK-006-P5A-subscription-capture.md` format).

---

## 9. Cross-reference map

| To answer... | Read... |
|---|---|
| Why we're reorganising P5.B | `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §0, §8 |
| Which 8 products v1.1 must cover and at what bar | `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §2-§8 |
| What's sealed from browser-ext | `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` §2, §4 |
| Whether a site is still in scope | `Docs/stability/SITE-TIER-MATRIX.md` §10 (2026-05-08 amendment) |
| Long-term vision | `Docs/docs/PROJECT.md` §3 (记录→看见→理解→干预) |
| UCS layer definitions | `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` (+ framework §4) |
| How existing normalizer handles branches | `Docs/docs/decisions/2026-04-26-regenerate-edit-branch-semantics.md` + migration 0008 |
| OSS vs Pro boundary for new code | `Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md` |

---

## 10. Product × Sub-phase unlock matrix

For each sub-phase, this table names exactly **which products gain
which D-cases**. D-cases and tier definitions are canonical in
`Docs/stability/DESKTOP-PRODUCT-MATRIX.md`.

### 10.1 Unlock matrix

Legend: `⏳` = enters scope this sub-phase · `➕` = augmented this
sub-phase · (blank) = not addressed by this sub-phase

| Sub-phase | P1 Claude Desktop | P2 ChatGPT Desktop | P3 Cursor | P4 Windsurf | P5 Copilot | P6 Claude Code | P7 Codex CLI | P8 Gemini CLI |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **P5.B.0** 姿态 A | ⏳ | | ⏳ | ⏳ | | ⏳ | ⏳ | ⏳ |
| **P5.B.1** L3f | ➕ | | ➕ | ➕ | | ➕ | ➕ | ➕ |
| **P5.B.2** L3b Claude | ➕ | | | | | | | |
| **P5.B.3** L3b IDE | | | ➕ | ➕ | | | | |
| **P5.B.4-A** L1 Copilot | | | | | ⏳ | | | |
| **P5.B.4-B** L3e gateway | | | | | | ➕ | ➕ | ➕ |
| **P5.B.4-C** ChatGPT probe | | ⏳ (best-effort) | | | | | | |

### 10.2 What each cell delivers

| Sub-phase | What lands across affected products |
|---|---|
| **P5.B.0** | D00 product detection + D01 (limited, agent-self-report only) + D08 single tool call. Acceptance: 报上可复现。 |
| **P5.B.1** | Adds D02 streaming + D03 multi-turn + full D08 tool-call correlation (L3f sees every JSON-RPC frame). Closes the M-plane axis for P1, P3, P4, P6, P7, P8 wherever each product uses MCP. |
| **P5.B.2** | Adds D01-D07 + D10-D12 for **P1 only** (Claude Desktop chat path that doesn't traverse MCP frames). **D0 gate met for P1.** |
| **P5.B.3** | Adds D01-D11 for **P3 + P4**. Code Session archetype lands. **D0 gate met for P3.** |
| **P5.B.4-A** | Adds D01-D08 + D10-D12 for **P5** via L1 proxy. **D2 gate met for P5.** |
| **P5.B.4-B** | Adds D01-D11 for **P7 + P8** (and any non-MCP fallback for P6). **D1 gate met for P7 + P8.** |
| **P5.B.4-C** | Attempts D01-D07 + D10-D12 for **P2 ChatGPT Desktop** via L3b. If pinning blocks, partial D2 acceptance + P6 hand-off ADR. |

### 10.3 Critical dependencies

- **P5.B.1 must land before P5.B.2 starts**, because Claude Desktop
  preload (P5.B.2) reuses the `mcp_jsonrpc.py` normalizer that L3f
  (P5.B.1) introduces.
- **P5.B.2 must land before P5.B.3 starts**, because Cursor /
  Windsurf preload (P5.B.3) reuses the install scaffolding pattern
  proven on Claude Desktop.
- **P5.B.4 sub-tracks A/B/C are parallelisable**; they share no
  code path beyond the unchanged Capture Ingest API.

### 10.4 Migration 0009 (proposed)

`messages.interaction_kind TEXT NULL DEFAULT NULL` lands in P5.B.3
but is consumed across P5.B.2 (where `chat` is the implicit
default), P5.B.4-A (where Copilot's `inline_completion` rows go),
and P5.B.4-B (where CLI agent rows are tagged `agent_loop`). This
is a single-column nullable migration; idempotent guard in
`pce_core/db.py` per existing pattern.

---

## 11. Sign-off criteria for P5.B as a whole

P5.B ships (i.e. tag `v1.1.0`) when:

- [ ] **D0 gate** met: P1, P3, P6 each ≥85% of applicable D-cases
      (per `DESKTOP-PRODUCT-MATRIX.md` §8.2).
- [ ] **D1 gate** met: P4, P7, P8 each ≥70%.
- [ ] **D2 gate** met (or formally deferred via ADR): P2 ≥50% OR
      P6 hand-off ADR landed; P5 ≥50% OR documented why not.
- [ ] Install docs under `Docs/install/` cover **all 8 target
      products** end-to-end.
- [ ] `tests/e2e_mcp/`, `tests/e2e_desktop/`, `tests/e2e_cli/` each
      have ≥3 passing D-cases.
- [ ] Migration 0009 (`interaction_kind`) landed and idempotent.
- [ ] 4 normalizer modules: `mcp_jsonrpc.py`, `ide_interactions.py`,
      `copilot_proxy.py`, plus extension to existing `anthropic.py`
      / `openai.py` / `genai_semconv.py` for desktop variants.
- [ ] The messages table, at v1.1, represents **chat / tool /
      code / run** archetypes without further migration.
- [ ] ADR-012, ADR-013, ADR-014 (✅ 2026-05-08) and ADR-015 (✅
      2026-05-09 alongside `pce_mcp_proxy/`) all landed.
- [ ] `Docs/docs/PROJECT.md` reflects the transition.
- [ ] `CHANGELOG.md` has a v1.1 section documenting what's new.

---

*End of kickoff.*
