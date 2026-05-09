# Desktop Product Matrix — P5.B Validation Targets

> **Status**: canonical — defines which desktop / IDE / CLI products
> P5.B (v1.1) ships against, what "stably captured" means for each,
> which tests prove it, and which products are **explicitly out of
> scope** for v1.1.
>
> **Adopted**: 2026-05-08
>
> **Sibling to**: `Docs/stability/SITE-TIER-MATRIX.md` (browser sites,
> now hard-frozen). This document does for desktop products what that
> one does for browser sites.
>
> **Authority**: derives validation targets from
> `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` (the strategy)
> and is referenced as the acceptance bar by
> `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` (the execution).
>
> **Audience**: Cascade, future agents, project owner.

---

## 1. Persona alignment — same as browser side

The `$50+/mo AI-native power user` persona defined in
`Docs/stability/SITE-TIER-MATRIX.md` §1 carries forward unchanged.
Nothing about that persona changes when their workflow moves from
browser tabs to desktop apps + IDE + CLI; if anything, the persona
spends **more** monthly $ and more daily minutes inside desktop AI
products than inside browser tabs.

The deliberate non-targets remain identical: mass-consumer AI apps
(豆包 / Yuanbao / Character.ai), entertainment-first apps, and
account-tier-locked enterprise surfaces.

---

## 2. The 8 in-scope products

P5.B v1.1 ships acceptance against these 8 products, organised by
the application typology in
`DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3.

| # | Product | Type | Why in-scope |
|---|---|---|---|
| **P1** | **Claude Desktop** (Win/Mac) | 1a Electron 聊天 + 1b MCP host | MCP rosetta stone — no pinning, MCP-native, clean Anthropic API |
| **P2** | **ChatGPT Desktop** (Win/Mac) | 1c pinned Electron 聊天 | Largest persona-overlap; pinning forces L3b path → real test of preload |
| **P3** | **Cursor** | 2a IDE-class MCP-aware | Most-used AI IDE in the persona; gRPC-web private protocol → preload-only path |
| **P4** | **Windsurf** | 2a IDE-class MCP-aware | Sister product to Cursor; reuses #P3 preload scaffolding to prove the design generalises |
| **P5** | **GitHub Copilot** | 3 IDE 寄生插件 | Highest enterprise-persona footprint; only realistic via L1 proxy on `*.githubcopilot.com` |
| **P6** | **Claude Code** | 4 CLI agent (Anthropic) | Sets the Run Trace archetype; densest tool/LLM ratio of any product on this list |
| **P7** | **Codex CLI** | 4 CLI agent (OpenAI) | Cross-validates Run Trace contract on a different provider |
| **P8** | **Gemini CLI** | 4 CLI agent (Google) | Cross-validates again on a third provider; closes the "big-3 model vendor" loop |

---

## 3. Tier-D — what each tier means operationally

Parallel to Tier-S in `SITE-TIER-MATRIX.md` §2, but for desktop
products. Tier governs **release gate**, not **build order** (build
order lives in the kickoff doc, organised by capture plane).

| Tier | Semantics | v1.1 release gate | Failure handling |
|---|---|---|---|
| **D0** | Indispensable — daily driver for ≥80% of persona; if any D0 product fails meaningfully, **v1.1 does not ship** | ≥85% applicable D-cases pass | Block release |
| **D1** | High-value — daily driver for ≥40% of persona | ≥70% applicable D-cases pass | Ship with documented gap |
| **D2** | Best-effort — high persona footprint but capture has structural risk (vendor pinning / closed extension API) | ≥50% applicable D-cases pass OR documented why not | Ship with explicit "best-effort" label in install docs |
| **D-OUT** | Explicit non-goal for v1.1 (see §10) | — | Honest "not supported" label in docs; reopen via ADR |

### 3.1 Per-product tier assignment

| Product | Tier | Rationale |
|---|---|---|
| P1 Claude Desktop | **D0** | The MCP reference platform; if PCE can't capture this, the framework doesn't work |
| P3 Cursor | **D0** | Largest IDE-class footprint; loss = persona walks |
| P6 Claude Code | **D0** | Most structurally rich agent capture; sets Run Trace archetype |
| P2 ChatGPT Desktop | **D2** | Pinning risk → may slip to P6/Frida; carry as best-effort |
| P4 Windsurf | **D1** | High value but reuses P3 scaffolding; lower marginal risk |
| P5 GitHub Copilot | **D2** | Vendor relay + extension API closure → realistic ceiling lower |
| P7 Codex CLI | **D1** | Verifies cross-provider Run Trace contract |
| P8 Gemini CLI | **D1** | Closes Google leg of big-3 |

---

## 4. Per-product validation profile

Each profile names: **OS**, **primary capture plane / UCS layer**,
**fallback**, **normalizer reuse vs new**, **archetype**, **known
risks**, **install path expected**, **first-probe verification list**.

### 4.1 P1 — Claude Desktop

| Field | Value |
|---|---|
| OS | Windows + macOS |
| Primary plane / layer | **M / L3f** (transparent MCP middleware) |
| Secondary plane / layer | H / L3b (Electron preload) |
| Tertiary plane / layer | N / L1 (system proxy fallback) |
| Normalizer | `pce_core/normalizer/anthropic.py` ✅ + `mcp_jsonrpc.py` ⬜ new |
| `source_type` | `mcp_proxy` (M path) + `desktop_electron` (H path) |
| Archetype | Chat Tube ✅ + Tool Tape ⬜ when MCP is in use |
| Risks | None known; clean reference target |
| Install assets | `Docs/install/PCE_MCP_PROXY_INSTALL.md` (M) + `Docs/install/PCE_CLAUDE_DESKTOP_INSTALL.md` (H) |
| First-probe checklist | **a)** read `claude_desktop_config.json` location per OS · **b)** verify `pce-mcp-proxy --upstream <fs>` round-trips a tools/list call · **c)** verify mitmproxy on `api.anthropic.com` is not blocked |

### 4.2 P2 — ChatGPT Desktop

| Field | Value |
|---|---|
| OS | Windows + macOS |
| Primary plane / layer | **H / L3b** (preload — pinning forces this) |
| Secondary plane / layer | M / L3f (姿态 A — agent-self-report only, since OpenAI MCP support is partial) |
| Tertiary plane / layer | N / L1 (best-effort; expected to fail on pinned endpoints) |
| Normalizer | `pce_core/normalizer/openai.py` ✅ |
| `source_type` | `desktop_electron` |
| Archetype | Chat Tube ✅ |
| Risks | 🟡 SSL pinning confirmed by community as of 2025; 🟡 Electron version uses a custom packaging (Mac native chrome wrapper vs Win Edge WebView2-style) — **first-probe must verify Electron actually applies** |
| Install assets | `Docs/install/PCE_CHATGPT_DESKTOP_INSTALL.md` |
| First-probe checklist | **a)** confirm Electron preload injection point exists per OS · **b)** mitmproxy attempt + capture the pinning failure mode · **c)** decide between L3b + best-effort vs hand-off to P6 (Frida) |

### 4.3 P3 — Cursor

| Field | Value |
|---|---|
| OS | Windows + macOS + Linux |
| Primary plane / layer | **H / L3b** (Electron preload — VS Code fork) |
| Secondary plane / layer | M / L3f (only when user has configured Cursor MCP servers) |
| Tertiary plane / layer | N / L1 (limited — Cursor uses gRPC-web + protobuf for upstream; mitmproxy sees binary bodies) |
| Normalizer | `openai.py` / `anthropic.py` ✅ + `ide_interactions.py` ⬜ new (for `interaction_kind ∈ {chat, completion, composer, inline_edit, lint_diff}`) |
| `source_type` | `desktop_electron` |
| Archetype | **Code Session** ⬜ new + Chat Tube fallback |
| Risks | 🟡 private gRPC-web protocol; preload sees **structured JS objects** before they hit the wire so risk is bounded; 🟡 frequent Cursor releases may break preload selectors |
| Install assets | `Docs/install/PCE_CURSOR_INSTALL.md` |
| First-probe checklist | **a)** confirm `app.asar` is unpacked or unpacking is permissible · **b)** locate the renderer-process IPC channel where `chat.send` / `composer.run` / `inline.complete` flow · **c)** verify IPC payloads carry `model`, `messages`, `interaction_kind` fields recoverable from JS-side hooks |

### 4.4 P4 — Windsurf

| Field | Value |
|---|---|
| OS | Windows + macOS + Linux |
| Primary plane / layer | **H / L3b** (Electron preload — reuses P3 scaffolding) |
| Secondary plane / layer | M / L3f |
| Normalizer | same as P3 (Codeium uses standard provider APIs underneath) |
| `source_type` | `desktop_electron` |
| Archetype | Code Session + Chat Tube |
| Risks | 🟢 lower than P3 (Codeium is more open about IPC shape); risk = Codeium tightens this as PCE gains visibility |
| Install assets | `Docs/install/PCE_WINDSURF_INSTALL.md` |
| First-probe checklist | mirrors P3; specifically verify the IPC channel is named distinctly from VS Code's |

### 4.5 P5 — GitHub Copilot

| Field | Value |
|---|---|
| OS | All (Copilot itself runs anywhere VS Code does) |
| Primary plane / layer | **N / L1** (system proxy on `*.githubcopilot.com` + `api.github.com`) |
| Secondary plane / layer | None viable for v1.1 (extension API does not expose chat content; chat panel is webview-rendered server-side) |
| Normalizer | `copilot_proxy.py` ⬜ new (Copilot's request schema is OpenAI-flavoured but not identical) |
| `source_type` | `proxy` (existing enum value) |
| Archetype | Chat Tube simplified (no branch UI; no role-tool turns) + Code Session for inline completions |
| Risks | 🟡 Microsoft may add SSL pinning to chat panel; 🟡 Copilot Workspace is a server-side product with limited visible request surface; 🟡 Microsoft IP / TOS concerns about traffic capture |
| Install assets | `Docs/install/PCE_COPILOT_INSTALL.md` |
| First-probe checklist | **a)** mitmproxy capture during a Copilot chat session — confirm endpoint list · **b)** check `/v1/engines/copilot-codex/completions`-style inline endpoint vs `/chat/completions`-style · **c)** validate request bodies remain plaintext JSON not binary |

### 4.6 P6 — Claude Code

| Field | Value |
|---|---|
| OS | All (Node CLI) |
| Primary plane / layer | **M / L3f** (transparent MCP middleware — Claude Code is heavily MCP-native) |
| Secondary plane / layer | N / L1 (Anthropic API direct calls) |
| Normalizer | `anthropic.py` ✅ + `mcp_jsonrpc.py` ⬜ |
| `source_type` | `mcp_proxy` (M) + `proxy` (N) |
| Archetype | **Run Trace** ⬜ new + Tool Tape ⬜ |
| Risks | 🟢 lowest of the 8; clean stdio + clean HTTPS, both transparent to PCE |
| Install assets | `Docs/install/PCE_CLAUDE_CODE_INSTALL.md` |
| First-probe checklist | **a)** confirm `~/.claude/` MCP config schema · **b)** verify a `claude code` invocation emits a recognisable tool-call burst pattern in PCE messages |

### 4.7 P7 — Codex CLI

| Field | Value |
|---|---|
| OS | All |
| Primary plane / layer | **M / L3f** if Codex CLI exposes MCP config (verify); else **N / L1** + **L3e gateway** |
| Secondary plane / layer | N / L1 |
| Normalizer | `openai.py` ✅ + `mcp_jsonrpc.py` ⬜ |
| `source_type` | `mcp_proxy` or `cli_gateway` |
| Archetype | Run Trace + Tool Tape |
| Risks | 🟡 Codex CLI's MCP support status as of 2026 needs first-probe verification |
| Install assets | `Docs/install/PCE_CODEX_CLI_INSTALL.md` |
| First-probe checklist | **a)** locate Codex CLI's MCP config (if any) · **b)** if no MCP, verify `OPENAI_BASE_URL` env-var redirect through L3e gateway works |

### 4.8 P8 — Gemini CLI

| Field | Value |
|---|---|
| OS | All |
| Primary plane / layer | **M / L3f** if Gemini CLI exposes MCP config (verify); else **N / L1** + **L3e gateway** (with Google-flavoured proto translation) |
| Secondary plane / layer | N / L1 |
| Normalizer | `genai_semconv.py` ✅ + `mcp_jsonrpc.py` ⬜ |
| `source_type` | `mcp_proxy` or `cli_gateway` |
| Archetype | Run Trace + Tool Tape |
| Risks | 🟡 Google API surface differs from OpenAI-style; gateway translation cost |
| Install assets | `Docs/install/PCE_GEMINI_CLI_INSTALL.md` |
| First-probe checklist | **a)** verify Gemini CLI's MCP support status · **b)** confirm Google API endpoint (`generativelanguage.googleapis.com`) is mitmproxy-able |

---

## 5. D-case definitions (the desktop-side T-cases)

Modelled on `tests/e2e_probe/cases/T**.py` but adapted for desktop /
CLI contexts. **13 D-cases** total.

| ID | Name | What it asserts |
|---|---|---|
| **D00** | product detection | PCE recognises the source (right `source_type`, right `provider`, right `model_name`) |
| **D01** | single-turn chat | One user message → one assistant message → `messages` table has 2 rows in same `session_id` |
| **D02** | streaming complete | The final stored text equals what the user sees in the product UI; no truncation, no partial frame |
| **D03** | multi-turn persistence | A 5-turn conversation preserves order + same `session_id` + correct `turn_index` |
| **D04** | stop / cancel | User-cancelled generation stores `error="cancelled"` and partial text up to cancel point |
| **D05** | model switch | After switching models mid-session, new turn's `model_name` reflects the change |
| **D06** | file attachment | Uploading a PDF/image stores `attachments[]` with mime + size; applicable only where the product supports file upload |
| **D07** | code block | A response containing fenced code stores it with language tag preserved |
| **D08** | MCP tool call | Single MCP tool invocation produces correlated `role=tool_call` + `role=tool_result` + `tool_call_id` link; applicable only to MCP-aware products |
| **D09** | inline completion | Accepting a ghost-text completion stores `interaction_kind=completion` (or equivalent); applicable only to IDE-class |
| **D10** | error state | A 4xx/5xx upstream response stores `status_code` and `error` text; pipeline does not crash |
| **D11** | long-context survival | A 50-turn conversation with cumulative ≥8K tokens captures every turn, no message dropped |
| **D12** | silent on idle | 5-min idle in the product yields **zero** capture writes (no false-positive heartbeat noise) |

### 5.1 Per-product applicability matrix

`✅` = must pass · `🟡` = best-effort · `—` = N/A · `?` = first-probe verification

| | P1 Claude Desktop | P2 ChatGPT Desktop | P3 Cursor | P4 Windsurf | P5 Copilot | P6 Claude Code | P7 Codex CLI | P8 Gemini CLI |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| D00 detection | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D01 single chat | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D02 streaming | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D03 multi-turn | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D04 cancel | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| D05 model switch | ✅ | ✅ | ✅ | ✅ | — | 🟡 | 🟡 | 🟡 |
| D06 attachment | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D07 code block | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D08 MCP tool | ✅ | 🟡? | 🟡 | 🟡 | — | ✅ | ✅? | ✅? |
| D09 inline completion | — | — | ✅ | ✅ | ✅ | — | — | — |
| D10 error state | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D11 long context | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| D12 silent idle | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **applicable** | 12 | 11 | 13 | 13 | 9 | 11 | 11 | 11 |

---

## 6. Test infrastructure layout

Three new test trees, parallel to the existing
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\` (which is the
browser-extension probe framework):

```
tests/
├── e2e_probe/                    # ✅ existing (browser-ext, frozen)
├── e2e_mcp/                      # ⬜ NEW — P5.B.0 + P5.B.1
│   ├── conftest.py
│   ├── cases/
│   │   ├── d00_product_detection.py
│   │   ├── d01_single_chat.py
│   │   ├── d08_tool_call.py
│   │   └── ...
│   └── products/
│       ├── claude_desktop_mcp.py
│       ├── claude_code.py
│       ├── codex_cli.py
│       └── gemini_cli.py
├── e2e_desktop/                  # ⬜ NEW — P5.B.2 + P5.B.3 + P5.B.4 part
│   ├── conftest.py
│   ├── cases/
│   │   ├── d01_single_chat.py    # shared with e2e_mcp via _shared_cases
│   │   └── ...
│   └── products/
│       ├── claude_desktop.py
│       ├── chatgpt_desktop.py
│       ├── cursor.py
│       └── windsurf.py
└── e2e_cli/                      # ⬜ NEW — P5.B.4 (gateway path)
    ├── conftest.py
    ├── cases/
    └── products/
        └── github_copilot.py     # tested via L1 proxy, lives here
```

### 6.1 Shared case helpers

`tests/e2e_desktop/_shared_cases.py` (and equivalents) define the
13 D-cases as parameterised functions; product adapters in
`products/<name>.py` provide:

- `launch()` — start the product (or attach to a running instance)
- `send(text, attachments=None)` — drive a chat input
- `receive(timeout)` — wait for a complete response
- `cancel()` — trigger D04
- `switch_model(name)` — drive D05
- `expected_capture_paths()` — declare what `storage_paths` and
  `render_paths` the test should find in PCE

This mirrors the `tests/e2e_probe/sites/<name>.py` adapter contract
that's been proven on browser sites.

### 6.2 Reports + best-of aggregator

`tests/e2e_desktop/reports/<timestamp>/summary.json` follows the
same shape as `tests/e2e_probe/reports/<timestamp>/summary.json`
so the freeze-doc-style "best-of" aggregator carries over verbatim.

---

## 7. Implementation phasing — mapped onto P5.B.0 to P5.B.4

The kickoff doc (`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §4)
defines five sub-phases. This section maps each sub-phase to **which
products gain which D-cases**.

### P5.B.0 — MCP server formalisation (姿态 A)

**Unlocks**: D00 + D01 (limited) + D08 for any agent willing to
self-report via `pce_capture` tool.

**Eligible products**: P1, P3, P4, P6, P7, P8 (all MCP hosts).

**Non-deliverables**: D02 streaming completeness — agent self-report
is a snapshot, not a stream observer.

**Acceptance**: Cascade itself (this very Windsurf session) can
exercise `pce_capture` and the resulting row appears in `messages`
with the right schema.

### P5.B.1 — MCP middleware proxy (姿态 B / L3f)

**Unlocks**: D00, D01, D02, D03, D08, D10 for all MCP-using products.

**Eligible products**: P1, P6, P7 (P3/P4/P8 if user opts MCP).

**Acceptance**: P6 Claude Code completes a real-world tool-heavy task
(e.g. "summarise this directory") and PCE has every `tools/call` +
`tools/result` frame correlated.

### P5.B.2 — L3b Electron preload — Claude Desktop only

**Unlocks**: D01-D07, D10-D12 for P1 (Claude Desktop's chat path,
filling the gap that L3f doesn't cover — chat text doesn't traverse
MCP frames).

**Eligible products**: P1.

**Acceptance**: P1 hits ≥85% of its 12 applicable D-cases. **D0
gate met.**

### P5.B.3 — L3b Electron preload — Cursor + Windsurf

**Unlocks**: D01-D11 for P3 + P4. Code Session archetype lands.

**Eligible products**: P3, P4.

**Acceptance**: P3 hits ≥85% of 13. P4 hits ≥70% of 13. **D0 gate
for P3 met.**

### P5.B.4 — L1 proxy + L3e gateway + ChatGPT Desktop attempt

Three parallel sub-tracks:

**Sub-track A — L1 proxy hardening for P5 (Copilot)**: ensure
mitmproxy + `copilot_proxy.py` normalizer cover the Copilot endpoint
list. Acceptance: ≥50% of 9 applicable D-cases (D2 gate).

**Sub-track B — L3e gateway for P7 + P8 fallback**: provide an
OpenAI-compatible local gateway that captures CLI agents not running
through MCP. Acceptance: P7 ≥70% of 11. P8 ≥70% of 11.

**Sub-track C — ChatGPT Desktop probe**: attempt L3b preload on P2.
**If pinning blocks fetch hooks**, accept partial coverage (D2 gate)
and log a P6 hand-off ADR. **If preload succeeds**, P2 D-case run
proceeds normally.

**Acceptance for whole P5.B.4**: D1 + D2 gates met for P5/P7/P8;
P2 either green at D2 gate or formally deferred.

---

## 8. Acceptance gates

### 8.1 Per sub-phase

Each sub-phase has its own acceptance bar (above). Sub-phase merging
into the next is gated on its own acceptance, **not** on later
sub-phase progress.

### 8.2 Whole P5.B (v1.1 ship)

`v1.1.0` ships when all of the following are simultaneously true:

- [ ] **D0 products** (P1 Claude Desktop, P3 Cursor, P6 Claude Code)
      each pass ≥85% of their applicable D-cases
- [ ] **D1 products** (P4 Windsurf, P7 Codex CLI, P8 Gemini CLI)
      each pass ≥70% of their applicable D-cases
- [ ] **D2 products** (P2 ChatGPT Desktop, P5 GitHub Copilot) each
      pass ≥50% OR have a formal P6 hand-off ADR
- [ ] All 4 normalizer additions exist and have unit tests:
      `mcp_jsonrpc.py`, `ide_interactions.py`, `copilot_proxy.py`,
      and the migration 0009 (`interaction_kind` column)
- [ ] All 8 install docs under `Docs/install/` exist and have been
      walk-through tested
- [ ] `tests/e2e_mcp/`, `tests/e2e_desktop/`, `tests/e2e_cli/` each
      have ≥3 passing cases
- [ ] ADR-012 (P5.B scope reorder), ADR-013 (`pce_mcp/` Open Core),
      ADR-014 (Type 5/6/7 deferral; this document's out-of-scope
      closure — see §10) landed (✅ 2026-05-08); ADR-015 (UCS L3f
      amendment) lands with P5.B.1
- [ ] `CHANGELOG.md` has v1.1 section
- [ ] `Docs/docs/PROJECT.md` updated to v1.1 phase pointer

---

## 9. Risk register

| ID | Risk | Owner product | Mitigation |
|---|---|---|---|
| **DR-01** | ChatGPT Desktop pinning blocks both N and H | P2 | Pre-stipulated D2 gate + P6 hand-off ADR |
| **DR-02** | Cursor's IPC shape changes between releases | P3 | Probe-style adapter with version detection; fail-soft on unknown shape |
| **DR-03** | Copilot adds SSL pinning to chat panel | P5 | Pre-stipulated D2 gate + monitor and hand off to P6 if needed |
| **DR-04** | Codex / Gemini CLI MCP support absent → forced to L3e gateway path | P7, P8 | Path documented (L3e CLI gateway in P5.B.4 sub-track B); no schedule impact |
| **DR-05** | Electron preload + app auto-update conflict | P1, P2, P3, P4 | Install doc explicitly documents "re-run installer after auto-update"; future P5.C may build a daemon-watcher |
| **DR-06** | MCP middleware adds latency that breaks user UX | All MCP-using | p95 < 50ms target; fail-open if proxy crashes (PROJECT.md §7.6 fail-open principle) |
| **DR-07** | normalizer divergence between desktop and web Claude/ChatGPT for the same `conversation_id` | P1, P2 | Migration 0009 includes `interaction_kind` (or its equivalent for desktop); reconciler in `pce_core/normalizer/conversation.py` extends to merge identical conversation_ids across sources |

---

## 10. Explicit out-of-scope — types 5, 6, 7

These three application types from
`DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3 are **deliberately
deferred** for v1.1. ADR-014 (planned, P5.B.0 timing) will record
this with full rationale.

### 10.1 Type 5 — Native (non-Electron) AI clients

**Examples**: Raycast AI (macOS), BoltAI / MacGPT / ChatX, Quicker
AI plugins (Windows).

**Why deferred**:

- Capture requires AX (macOS Accessibility) / UIA (Windows UI
  Automation) — these are L3d / L4b in UCS, scheduled for **P6** /
  **P7**.
- Persona analysis: native clients are a **secondary** entry point
  for the persona; primary daily AI exposure for them is browser
  + Electron + IDE + CLI, all of which are covered.
- Per ADR-010 (Open Core boundaries), L3d / L4b are Pro-tier;
  shipping them in v1.1 would cross the OSS / Pro line prematurely.

**Reopen criteria**:

- v1.1 ships and persona feedback identifies a concrete
  native-client gap.
- OR: an enterprise customer requests Raycast / BoltAI capture and
  is willing to fund Pro-tier development.

### 10.2 Type 6 — Local-LLM desktop apps

**Examples**: LM Studio, Ollama Desktop, Jan, GPT4All, Msty,
AnythingLLM Desktop, Open WebUI.

**Why deferred**:

- **Technically capturable** via L3e CLI gateway (which P5.B.4
  builds for CLI agents). Could be opportunistically extended.
- **But persona-fit is weak**: the $50+/mo persona overwhelmingly
  uses *cloud* models (Claude / GPT / Gemini); local-LLM users
  are a different persona slice (privacy-first / hobbyist).
- Including this in v1.1 risks **scope creep** and dilutes the
  desktop / IDE / MCP message.

**Reopen criteria**:

- Post-v1.1 persona research surfaces a measurable cohort that
  uses both cloud models AND local LLMs daily.
- OR: P5.B.4's L3e gateway lands cleanly enough that supporting
  Ollama/LM Studio is a ≤3-day extension; if so, it can ship as
  an undocumented bonus in v1.1.x without expanding the
  acceptance gate.

### 10.3 Type 7 — OS-integrated AI

**Examples**: Apple Intelligence (macOS / iOS), Windows AI
(Copilot+ PC), Galaxy AI, Pixel AI.

**Why deferred**:

- Capture requires either **kernel-level hooks (L0)** or
  **vendor-controlled API surfaces** that PCE has no leverage
  over.
- These are scheduled for **P7+** under L0 Kernel layer.
- Per Open Core boundary, L0 is Pro-tier and probably enterprise-
  only.

**Reopen criteria**:

- An enterprise customer requests this AND
- Apple / Microsoft / Google has published a non-vendor-locked
  API for capturing on-device model interactions.

(Realistically not before 2027.)

---

## 11. Cross-reference map

| You need to... | Read... |
|---|---|
| Understand why these 8 products and not others | `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3 + §8 |
| Know the build order (capture-plane axis, not product axis) | `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §4 |
| See per-D-case implementation pattern | (planned) `tests/e2e_desktop/cases/d**.py` modelled on `tests/e2e_probe/cases/t**.py` |
| Know what was sealed on the browser side | `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` |
| Know what UCS layers we touch | `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` (note: ADR-015 amends with L3f, lands with P5.B.1) |
| Know normalizer entry points | `pce_core/normalizer/__init__.py` |
| Re-evaluate tier of a product | follow §3 + §10 reopen criteria → ADR |

---

## 12. Working rules

1. **A product's tier is declared in §3.1 + repeated as a header
   line in its `Docs/install/PCE_<PRODUCT>_INSTALL.md`**. Drift =
   bug.
2. **Adding a new product to v1.1** requires updating §2 + §3.1 +
   §4 + §5.1 + an ADR. Do not silently extend the matrix.
3. **Promoting a product across tiers** (D2 → D1 → D0) requires
   evidence: best-of D-case pass rate over ≥3 runs.
4. **Out-of-scope products** (§10) cannot be silently revived in a
   side branch; they require ADR-014 supersession.
5. **D-case additions** require updating §5 + §5.1 + adding the
   case file under `tests/e2e_*/cases/`. Do not test something
   not enumerated here.
6. **First-probe verification** items (§4 each product) **must be
   completed and recorded** before that product's sub-phase begins.
   Treat as gating.

---

*End of matrix.*
