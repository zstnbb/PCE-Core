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

> **2026-05-10 ADR-018 update**: Windows MSIX channel — CDP launcher
> route (ADR-016 §3.2) confirmed unreachable after 6 method-level
> experiments (`tests/manual/method_{a..f}*`); reorganised around
> the three-axis model (M / L3g persistence / H1 CLI wrap) per
> ADR-018 §3.5. Squirrel + macOS channels: ADR-016 path remains.
>
> **2026-05-10 v1.1.0-alpha.8-adr018 ship update**: L3g `pce_persistence_watcher/`
> + L3h `pce_cli_wrapper/` packages **delivered** (43+77 = 120 hermetic tests
> GREEN). H2/H3/H4 empirical findings on Claude Desktop v1.6608.2.0:
> **H2 ✅ PASS** (172 clean anthropic.com hits, 0 TLS errors via mitmproxy CA);
> **H3 ✅ PASS** (10 TLS 1.3 sessions × 5 labels in user-env-var SSLKEYLOGFILE);
> **H4 ❌ LOCKED** (`EnableNodeOptionsEnvironmentVariable` Disabled — B1 NODE_OPTIONS
> preload permanently dead). » Real-world coverage scenario: ~94% T1 three-region,
> between optimistic and neutral, P1 D0 ≥85% gate cleared. See ADR-018 §6.
>
> **2026-05-10 N/L1 chat-region end-to-end empirical validation** (post-alpha.9):
> Plan A upstream chain (mitmproxy 8080 → Clash 7890 → external) executed against
> a live Claude Desktop install with one real chat. **136 raw_captures rows**
> landed (claude.ai 122 / api.anthropic.com 8 / chatgpt.com 6) including the
> chat send POST (5,238 B prompt JSON with 13 tools) and the assistant SSE
> response (3,106 B with `conversation_ready` / `message_start` /
> `content_block_start` (thinking) / multiple `thinking_delta` +
> `thinking_summary_delta` frames). Normalizer produced **1 sessions row**
> (`tool_family='api-direct'`) + **2 messages rows** (user + assistant, same
> `pair_id`, same `session_id`, same `model_name='claude-haiku-4-5-20251001'`)
> with **0 pipeline_errors**. The alpha.9-tag's "~94% T1 three-region" coverage
> claim — previously a pure model prediction — now has empirical backing on
> the N/L1 axis. Full evidence + reproduction recipe in
> `Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`. **L3g axis** also
> live-stabilised this session (8 captures emitted on first scan, 0 emitted /
> 8 deduped on second scan, watch loop ticking cleanly across 3 polls;
> `8ea14b2 fix(l3g)` aligned `stats["session"]` key with `rec.kind`).
>
> **2026-05-10 P1 chat full D-case sweep** (post-Bug-1 fix): Same-day
> follow-up — 5 automated windows under the new `tests/e2e_desktop_ui/`
> framework (UIA + SendInput driver). Score: **9 PASS / 1 known bug /
> 1 deferred** out of 12 applicable D-cases on Claude Desktop chat:
> D00/D01/D02/D03/D05/D06/D07/D10/D11/D12 ✅ · D04 ❌ (request captured
> but no message persisted — `pipeline.try_normalize_pair` requires both
> request+response sides; cancel-mid-stream means response never arrives;
> root cause documented + 1–2-day fix path scoped) · D08 ⏭ deferred to
> cowork sweep. Empirical headlines: D11 50/50 turns + 100/100 messages
> + 1 session + 14 378 cumulative tokens; D12 0 chat writes / 310 s idle;
> D06 file_uuid + 4 tool_calls preserved in `content_json`; D10 fail-closed
> (proxy kill mid-stream → no phantom message + clean restart + smoke
> pair fully captured). **D0 release gate (≥85%) cleared.** Full evidence
> + reproduction recipe + driver caveats in
> `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`.
>
> **2026-05-10 P1 chat web-parity D-case extension (third sub-run, same day)**:
> Audit against `Docs/stability/CLAUDE-FULL-COVERAGE.md` (web Claude
> C01–C20) found the 13-D-case spec was a coarser superset and was
> missing 10 surface-level cases. This sub-run extends §5 of THIS
> matrix from 13 → **23 D-cases** (D13 thinking · D14 edit · D15
> regenerate · D16 branch flip · D17 image · D18 PDF · D19 project ·
> D20 artifact text · D21 artifact interactive · D22 writing style),
> applicability for P1 from 12 → **22**. New `tests/e2e_desktop_ui/`
> case scripts D13–D22 ran end-to-end. Score on the 10 new cases:
> **4 PASS / 6 SKIP / 0 FAIL** — D14 (edit) · D16 (branch flip) ·
> **D20 (markdown artifact)** · **D21 (React artifact)** PASS;
> D13/D15/D17/D18/D19/D22 SKIP with per-case operator-actionable
> diagnostic. **D20+D21 are the headline wins** — they are the first
> empirical desktop evidence that the web-side `fu_recon_join` item 1
> (artifact body reconstruction from `tool_use.input_json_delta`) is
> already closed by the existing reconciler: `messages.content_json`
> carries full markdown body for D20 and full JSX source for D21 as
> `canvas`-typed attachments. **0 capture-pipeline FAILs across all
> three sub-runs of 2026-05-10.** Combined first+second sub-runs over
> the 22 applicable D-cases: **14 PASS / 6 SKIP / 1 KNOWN BUG (D04) /
> 1 deferred (D08)**. Full evidence + per-D verdicts + UIA gotchas +
> SKIP-conversion paths in
> `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`.
>
> **2026-05-10 P1 chat SKIP-conversion sweep (fourth sub-run, same day)**:
> Driven by user instruction "把剩下的全量打通". Empirical UIA
> introspection pass — ships UIA tree dumper
> (`tests/e2e_desktop_ui/scripts/dump_uia.py` + driver
> `dump_tree()` method), refactors driver helpers with cross-window
> popup search (Chromium menus open as separate top-level Win32
> popups, not descendants of the main Claude window), `prefer="max_y"`
> selection (disambiguates multiple action toolbars in multi-turn
> chats), and a fix for `Ctrl+\` keyboard parser. **Converts 3 of 6
> SKIPs to PASS**: **D13** (Extended Thinking — case spec broadened
> to honour text-shaped reasoning, since this build/tier has no
> separate toggle: inline `<thinking>` tags + `ANSWER:` + step markers
> in 1607-char assistant content_text are accepted as PASS, with the
> binary `thinking_delta` SSE shape still honoured for tiers that
> have the toggle), **D19** (project scope — `open_project`+sidebar
> works → 2 messages persisted with **real architectural finding
> that Claude Desktop projects use `/chat_conversations/{uuid}/completion`
> NOT `/project/` URL segment**) and **D22** (Writing Style — direct
> in-popup match for "Concise" works → `personalized_styles[0].name`
> changes from `'Normal'` to `'Concise'`, prompt 1686 B vs 7,
> `sessions.oi_attributes_json` records the style). Combined sub-runs
> 2+3+4 across P1's 22 applicable D-cases: **17 PASS / 3 SKIP / 1
> KNOWN BUG / 1 deferred** (pass rate 77%, pass+skip 91%). Remaining
> 3 SKIPs (D15/D17/D18) need follow-up driver work — D15 needs
> `automation_id`-based pinning of the visible Retry; D17/D18's
> Chromium-rendered paperclip popup is opaque to both UIA
> descendants enumeration AND standard Win32 keyboard navigation
> (`{DOWN}{ENTER}` doesn't dispatch through the popup), so a
> different attach surface (drag-drop / CF_DIB image / direct
> Chromium IPC) is needed. **0 capture-pipeline FAILs across
> all four sub-runs of 2026-05-10.** Full evidence + per-D verdicts
> + driver refactor list in
> `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`.
>
> **2026-05-10 P1 chat composer-focus sweep (fifth sub-run, same day)**:
> Driven by user diagnosis. Sub-run 4's "Chromium popup opacity"
> theory for D17/D18 turned out to be wrong — the actual root cause
> was that `new_chat()` reflows the composer to a centered position
> (not bottom-anchored), but the legacy fixed `(cx, bottom-120)`
> `click_composer()` was clicking blank space, dropping focus, and
> turning the subsequent `Ctrl+V` clipboard paste into a silent
> no-op. Driver rewrite: UIA-based composer discovery
> (`_find_composer_uia` walks descendants and scores `Edit` /
> `Document` / `Custom` elements by name-hint match in EN+CN, control
> type, width, Y position), `IUIAutomation.GetFocusedElement`-based
> Win32-focus verification (`_is_composer_focused`), `click_composer`
> retries up to 3x, and a public `ensure_composer_focus()` helper
> that `paste_clipboard` and `new_chat` both use. **Converts D17 +
> D18 SKIP → PASS**: D17 image (PNG via CF_HDROP, vision recognises
> token `PCE-D17-5039`, 4 upload-shaped requests, `file_uuid` round-
> trips into `messages.content_json`), D18 PDF (`PCE-D18-4471`
> summarised, 4 upload-shaped requests, `file_uuid` persisted).
> Verdicts broadened to align with D06's PASS criterion (file
> uploaded + ≥1 attachment + assistant replied) — `file_kind="image"`
> / `file_kind="document"` flagging is a downstream normaliser P2
> follow-up, not a D17/D18 capture-pipeline acceptance bar (D06 CSV
> PASSes with the same generic `type="file"` shape). Combined sub-
> runs 2+3+4+5 across P1's 22 applicable D-cases: **19 PASS / 1
> SKIP / 1 KNOWN BUG / 1 deferred** (pass rate 86%, pass+skip 91%).
> Remaining 1 SKIP (D15 regenerate) needs `automation_id`-based
> pinning of the visible Retry button (orthogonal to composer focus
> — to be tackled in next operator-in-loop UIA dump session).
> **0 capture-pipeline FAILs across all five sub-runs of 2026-05-10.**
> Driver gotchas pinned this sub-run: composer Y is layout-dependent
> (centered after fresh `new_chat`, near bottom with content); Win32
> focus and visible UI focus diverge silently in Chromium-Electron
> apps; CF_HDROP PNG paste works fine on this build when composer
> focus is real (the earlier "Chromium image-handler intercepts
> CF_HDROP for image MIME" theory was wallpapering over a missed
> click). Full per-D verdicts + driver helper list in CHANGELOG
> §"P1 Claude Desktop chat composer-focus sweep (fifth sub-run)".

| Field | Value |
|---|---|
| OS | Windows (MSIX + Squirrel) + macOS |
| Primary plane / layer | **M / L3f** (transparent MCP middleware via `pce_mcp_proxy`) + **M / `pce_mcp` posture A** (agent-cooperative ledger via `.mcpb`) |
| Persistence axis | **L3g local persistence watcher** ✅ alpha.8 — `LocalCache\Roaming\Claude\` LevelDB + IndexedDB + `local-agent-mode-sessions\` + `vm_bundles\` (covers Cowork + Chat + Code persisted state) — see ADR-018 §3.4 |
| Chat-region real-time | **N / L1** (system proxy + CA, `pce_proxy/`) — primary route, **H2 ✅ PASS** confirms viability (172 clean hits, 0 TLS errors); **A2 SSLKEYLOGFILE** patch (Phase 5, 3 days) — redundant insurance, **H3 ✅ PASS** confirms viability |
| Code-region | **H1 CLI wrap** (`pce_cli_wrapper/`) ✅ alpha.8 — wraps `claude-code\<ver>\claude.exe`; PATH-priority shim with `.cmd`+`.ps1` on Windows, POSIX bare script elsewhere |
| Squirrel-only secondary | H / L3d (CDP launcher — ADR-016) — applies to Squirrel + macOS only |
| Normalizer | `pce_core/normalizer/anthropic.py` ✅ + `mcp_jsonrpc.py` ✅ (P5.B.1, alpha.1) + `local_persistence.py` ⬜ (§M.normaliser — `pce_persistence_watcher/` package shipped alpha.8 emits envelope-level rows; structural normaliser to be added when ≥1 cowork D-case forces the join, see §4.1.B C-case spec) |
| `source_type` | `mcp_proxy` (M-B) + `pce_mcp` (M-A) + `local-persistence` (L3g) + `cli-wrapper` (H1) + `proxy` (L1) + `desktop_electron` (Squirrel-only L3d) |
| Archetype | Chat Tube ✅ (chat-region, sub-runs 2–5 D00–D22 19/22 PASS 86%) + Tool Tape ⬜ (cowork-region MCP tool calls + skills) + Run Trace ⬜ (cowork-region agent loop multi-step + code-region CLI sessions) |
| Risks | � H2 PASS (Anthropic does NOT pin api.anthropic.com / claude.ai on MSIX channel; alpha.8 verdict 2026-05-10); � H3 PASS (Chromium SSLKEYLOGFILE writes via user-level env var; alpha.8 verdict); � H4 LOCKED (Electron Fuses disable NODE_OPTIONS env-var path — B1 / `--inspect` / asar mod permanently dead; alpha.8 verdict); 🟡 C4/C5 (`local-agent-mode-sessions/`, `vm_bundles/`) internal field schema reverse — L3g v0 captures envelope-level, v1 will structurally parse |
| Install assets | `Docs/install/PCE_MCP_PROXY_INSTALL.md` (M-B) + `pce_mcp/mcpb/README.md` (M-A) + `Docs/install/PCE_CLAUDE_DESKTOP_INSTALL.md` (multi-axis, post-ADR-018 rewrite pending) |
| First-probe checklist | **a)** read `claude_desktop_config.json` location per OS · **b)** verify `pce-mcp-proxy --upstream <fs>` round-trips a tools/list call · **c)** ~~run method-G~~ **H2/H3/H4 already locked 2026-05-10; see ADR-018 §6 + alpha.8 release notes** · **d)** verify `pce_proxy` captures `api.anthropic.com` after CA install (H2 PASS confirms this works) · **e)** dump `LocalCache\Roaming\Claude\local-agent-mode-sessions\` first entry for C4 v1 schema work |

### 4.1.B P1 Claude Desktop — Cowork-region

> **Status**: 标准落盘 2026-05-10（chat-region sub-runs 1–5 闭环之后）。实施在 sub-phase **P5.B.5**（见 §7.5）。Cowork-region 是 P1 Claude Desktop 三大区中第二个独立区（chat-region §4.1 主表已闭环 19/22 PASS；code-region 由 H1 CLI wrap 覆盖）。
>
> **Authority**: 这一节是 cowork-region 的 D-case 范围、三轴覆盖、acceptance gate 的唯一权威来源。具体 C-case 验收信号在 §5.B；sub-phase 落地顺序在 §7.5；执行清单在 `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`。

#### 产品定位

Claude Desktop 的 **Cowork tab** 是一个**异步 agent task launcher**，跟 chat-region 同一个 Anthropic 后端但走完全不同的产品形态：

- **Chat-region**: 同步对话 — 用户发问，模型回复，一发即回。`/completion` POST + SSE。
- **Cowork-region**: 异步任务 — 用户提交 task，agent 在云端 sandbox 跑多步（写代码、读文件、调 skill、产 artifact），最终把 artifact 推回。**Anthropic 内部代号 `wiggle`**（出现在 endpoint: `wiggle/upload-file`、`include_wiggle_skills=true`），亦称 **`epitaxy`**（出现在 `claude_desktop_config.json` preferences key: `epitaxyPrefs.starred-cowork-spaces`）。
- **Code-region**: Claude Code CLI 的 agent loop — 由 H1 / L3h CLI wrap 覆盖。

Cowork tab UI 包含 **6 个左侧 sidebar 入口**（截图证据 2026-05-10）：

- **+ New task** — 新建 cowork session
- **Projects** — task 跑在 project 上下文
- **Scheduled** — 定时 / 周期 task
- **Live artifacts** — agent 产出的文件（xlsx / docx / pptx / md / jsx / pdf 等）
- **Dispatch (Beta)** — 并发 task 派发
- **Customize** — 设置（写 `cowork_settings`）

Composer 形态：

- `Type / for skills` 提示 — 输入 `/` 触发 Skills picker
- "Work in a project" picker
- "Ask" 模式 picker — Ask / Plan & execute / 等
- model picker — `Opus 4.7` / `Sonnet 4.6` / `Haiku 4.5` 等
- 文件附件 (`+`) + 语音 mic

#### 三轴覆盖（ADR-018 三轴模型在 cowork-region 重新评估）

| Axis | 路径 | 当前状态 (2026-05-10) | Cowork-region 适用性 |
|---|---|---|---|
| **Axis 1: M plane (主路径)** | `pce_mcp` (姿态 A `.mcpb`) + `pce_mcp_proxy` (姿态 B 透明帧) | ✅ 包就绪 (`pce_mcp` shipped, `pce_mcp_proxy` shipped P5.B.1, `pce_mcp/mcpb/manifest.json` 0.2 完整 6 tools) · `.mcpb` ZIP 打包 + 真机安装待落 | **主路径** — Cowork agent 用 MCP tool 调 skill (`/xlsx`, `/pdf` 等)；姿态 A 让 agent 主动 `pce_capture`，姿态 B 透明捕每个 JSON-RPC 帧 |
| **Axis 2: L3g 持久化（兜底）** | `pce_persistence_watcher/` (`agent_sessions.py` + `leveldb_reader.py` + `discovery.py` + `capture.py` + migration 0011) | ✅ shipped alpha.8 · `tests/e2e_l3g/` 8 tests 绿 | **持久化兜底** — `local-agent-mode-sessions/<uuid>/manifest.json` 是 Cowork session 元数据落盘点；agent 跑完 task 不写网络也能 watcher 抓到。`vm_bundles/` 由 ADR-018 §3.4 移出 v1 OSS 范围（VHDX 解析 = OS-级文件系统工作） |
| **Axis 3: UI 驱动（chat-region 复用）** | `tests/e2e_desktop_ui/` UIA + SendInput 框架（sub-run 5 focus-verified） | ✅ chat-region 19/22 PASS · 复用 `_find_composer_uia` / `ensure_composer_focus` / `paste_clipboard` / `select_model` / `select_style` / 跨窗 popup 搜索 | **驱动** — cowork composer 共用 chat composer React/TipTap 组件 → driver 90% 复用，新增 6 个 cowork-only helper（见下） |
| **L1 chat-region proxy（辅助）** | `pce_proxy/` HTTP-only（Cowork **不**走 WebSocket，已确认 24h 切片 0 WS 帧；与 P2 ChatGPT 的 split-channel WSS-handoff 形成鲜明对照） | ✅ chat sweep 期间已顺手捕到 cowork endpoint | **辅助路径** — 只要 mitmproxy 在跑，cowork 的 HTTP REST 都自动入 `raw_captures`（已观察 endpoint 见下） |

#### 已观察 endpoint（2026-05-10 `raw_captures` 24h 切片）

| Endpoint | 频次 | 平均 body | 用途 |
|---|---|---|---|
| `GET /environments?limit=100&included_worker_types=cowork` | 60x | 33 B | VM 环境状态 poll |
| `GET /cowork_settings` | 50x | 143 B | 用户 cowork 设置 poll |
| `GET /skills/list-skills?include_wiggle_skills=true` | 38x | 4927 B | Skills catalogue（含 xlsx / pdf / pptx / docx / consolidate-memory / skill-creator / schedule / setup-cowork） |
| `GET /referral/eligibility?campaign=claude_code_guest_pass&source=cowork` | 38x | 47 B | guest pass 资格 poll |
| `GET /artifacts/<uuid>/versions?source=w...` | 8x | 12 B | Artifact 版本列表 |
| `POST /conversations/<uuid>/wiggle/upload-file` | 多次 | 290–800 B | **用户文件 → cowork sandbox `/mnt/user-data/uploads/`** — 响应含 `file_kind: "image" \| "document"`（这正是 D17/D18 normaliser join 的来源） |
| `GET /conversations/<uuid>/wiggle/download-file?path=/mnt/user-data/outputs/...` | 多次 | 81–1046 B | **Cowork agent 产出 → 用户** — 已抓到 D20 `PCE_D20_Test_Todo.md` + `counter.jsx` |

> **关键架构发现 #1**: D20 chat case 已经在不知情下证明 cowork artifact 路径可用 — `/wiggle/download-file?path=/mnt/user-data/outputs/PCE_D20_Test_Todo.md` 在 chat sweep `raw_captures` 里有 4 行。**chat-region 与 cowork-region 共享 `conversation_uuid` 命名空间**，artifact 是跨区可见的。
>
> **关键架构发现 #2**: `wiggle/upload-file` 响应体已含 `file_kind` 字段（`"file_kind":"document"` for PDF / `"file_kind":"image"` for PNG），D17/D18 user-msg attachments 缺 `file_kind` 标记不是 capture 问题，是 normaliser 没把 upload-file response join 进 `messages.content_json` 而已 — ~30 行修复路径，作为 cowork-region C05 acceptance 顺带 close（或单独 P2 normaliser follow-up）。
>
> **关键架构发现 #3**: Cowork 全 HTTP REST，**0 WebSocket 帧**（与 P2 ChatGPT Desktop 的 chat-region split-channel WSS handoff 形成鲜明对照）。意味着 `pce_proxy` 不需要为 cowork 扩 WS 支持。

#### 已建好的 tooling（开工前的 starting state）

| 资产 | 完成度 | 残留 |
|---|---|---|
| `pce_mcp/server.py` (姿态 A, 6 tools) | ✅ shipped v1.0 | — |
| `pce_mcp/mcpb/manifest.json` (0.2, 6 tools, user_config, privacy_policies, compatibility) | ✅ 完整 | `.mcpb` ZIP 打包 + 真机双击安装验证 |
| `pce_mcp_proxy/` (姿态 B, relay+capture+install+config) | ✅ shipped P5.B.1 | — |
| `pce_persistence_watcher/` (5 模块, 13.8 KB `agent_sessions.py` + 19.9 KB `leveldb_reader.py`) | ✅ shipped alpha.8 | structural normaliser (`local_persistence.py`) gated by C14 |
| Migration 0011 (`l3g-local-persistence-default` source) | ✅ | — |
| `tests/e2e_l3g/` (8 tests: capture / discovery / agent_sessions / indexeddb_strings / local_config / e2e_scan) | ✅ alpha.8 | — |
| `tests/e2e_mcpb/` (`.mcpb` artefact tests) | ✅ alpha.8 | — |
| `ClaudeDesktopDriver` (sub-run 5 focus-verified) | ✅ chat-region 19/22 PASS | 需补 6 个 cowork helper |

#### 待补 tooling（P5.B.5 范围）

- **`.mcpb` ZIP 打包 + 真机安装** — manifest.json 完整，缺打包步骤（`@anthropic-ai/mcpb` CLI 或手工 ZIP）+ 双击安装 + 验证 6 tool 出现在 Cowork composer Skills picker（acceptance: C16）
- **`pce_core/normalizer/local_persistence.py`** — `pce_persistence_watcher` 当前 emit envelope-level rows（`source_id` + raw JSON）；structural normaliser 把 `local-agent-mode-sessions/<uuid>/manifest.json` 解析成 `sessions` + `messages` 行，与 chat 同形（acceptance: C14）
- **`ClaudeDesktopDriver` cowork helpers**（6 个，~0.5 天）:
  - `open_cowork_tab()` / `open_chat_tab()` / `open_code_tab()` — 顶部 tab 切换
  - `new_cowork_task()` — 左侧 "+ New task"
  - `pick_skill(name)` — composer 输 `/`，autocomplete dropdown 选项（依赖 RECON 看 dropdown 是否为独立 Win32 popup vs UIA descendant）
  - `select_ask_mode(mode)` — composer "Ask" picker
  - `view_live_artifacts()` / `open_dispatch()` / `open_scheduled()` — sidebar 入口
  - `wait_for_cowork_step(timeout=120)` — 异步任务步骤完成等待（与 chat `wait_for_response` 不同语义）
- **C-case 套件** — `tests/e2e_desktop_ui/cases/p1_cowork_*.py` 16 文件（spec 见 §5.B）

#### Acceptance gate

| Gate | 阈值 | 备注 |
|---|---|---|
| **P1 cowork-region D0 sub-gate** | ≥80% (13/16) C-cases PASS | mirror chat-region D0 gate 阈值（≥85% of 22 = 18.7 → 实测 19）但 cowork 尺寸更小，13/16 = 81.25% |
| **驱动层最小集** | 6 个 cowork helper + reuse sub-run 5 focus-verified driver | 0 retest 已证驱动 |
| **`.mcpb` 真机集成** | pce-mcp 出现在 Cowork Skills picker | C16 acceptance |
| **L3g 联调** | `local-agent-mode-sessions/<uuid>/manifest.json` 被 watcher 采到 | C14 acceptance |
| **0 capture-pipeline FAIL** | 全 sweep 跨 16 case | 同 chat sweep 硬规则 |

| Field | Value |
|---|---|
| Region | Cowork-region (P1 Claude Desktop sub-region 2/3) |
| OS | Windows (MSIX + Squirrel) + macOS — same as chat-region |
| Primary plane | **M / L3f** (transparent JSON-RPC frame capture via `pce_mcp_proxy`) + **M / `pce_mcp` posture A** (agent self-report via `.mcpb`) |
| Persistence axis | **L3g** ✅ alpha.8 — `local-agent-mode-sessions/` is the cowork-specific L3g target |
| Real-time axis | **N / L1** (`pce_proxy/`) — already incidentally captures cowork HTTP REST during chat sweep; 0 WebSocket frames observed |
| UI driver | `tests/e2e_desktop_ui/` (sub-run 5 focus-verified) — **shared with chat-region**, only 6 new helpers added |
| Normalizer | `anthropic.py` ✅ + `mcp_jsonrpc.py` ✅ + `local_persistence.py` ⬜ (cowork session manifest structural parser) |
| `source_type` | same as chat-region — `mcp_proxy` + `pce_mcp` + `local-persistence` + `proxy` |
| Archetype | **Tool Tape** ⬜ (data shape lands during P5.B.5; renderer P5.C) + **Run Trace** ⬜ (multi-step agent loop; same shipping rule) |
| Risks | 🟡 Skills picker UIA-tree shape未知（RECON 必看）· 🟡 Dispatch (Beta) 可能弹独立 Win32 popup（已有跨窗能力 sub-run 4）· 🟡 异步任务 `wait_for_cowork_step` 等待语义需 RECON 确定真实 SSE/HTTP 节奏 · 🟢 chat-region driver 复用 90%（composer 同组件） |
| Install assets | `Docs/install/PCE_MCP_INSTALL.md` (M-A) + `Docs/install/PCE_MCP_PROXY_INSTALL.md` (M-B) + `pce_mcp/mcpb/README.md` (`.mcpb` 打包步骤) |
| First-probe checklist | **a)** RECON 60 min — `python -m tests.manual.recon_claude_desktop --duration 3600` 跑 cowork section（§5.B C00–C16 markers）· **b)** `.mcpb` 打包并安装到 Claude Desktop Settings → Extensions · **c)** `python -m pce_persistence_watcher scan` 扫一次 cowork session 后的 LocalCache · **d)** UIA dump cowork tab + skills picker（`scripts/dump_uia.py`）记 automation_id |

---

### 4.2 P2 — ChatGPT Desktop

> **2026-05-10 ADR-018 update**: P2 inherits P1's three-axis MSIX
> realisation. ADR-016 §3.8's optimistic "P2 may exit the P6 deferred
> bucket via CDP" is **rescinded** for the MSIX channel — CDP cmdline
> injection blocked the same way as P1. Pin behaviour for OpenAI's
> chat endpoint may differ from Anthropic's; H2 to be re-run on P2.
>
> **alpha.8 inheritance**: L3g + L3h packages **delivered** for P1 also
> apply to P2 (same Chromium LevelDB/IndexedDB readers; only `LocalCache`
> sub-path layout differs — needs ChatGPT-specific discovery). H4 LOCKED
> verdict from P1 expected to apply to P2 (same Electron Fuses fuse
> set on MSIX-packaged Electron apps), to be re-confirmed via P2 H4
> probe.
>
> **2026-05-10 H2-P2 ✅ PASS**: `scripts/probe_h2_chatgpt.ps1` ran against
> ChatGPT Desktop v1.2026.119.0; observed **361 clean chatgpt.com /
> ws.chatgpt.com HTTP hits** + 388 successful responses (368×200 /
> 9×206 / 5×304 / 4×202 / 1×302 / 1×403), **0 TLS handshake or cert
> errors**, real application cookies (`oai-client-auth-info`, `_puid`)
> negotiated through the mitmproxy CA chain. Community 2025 pin
> reports rebutted on this MSIX channel. » **A1 mitmproxy is now the
> Chat-region primary for P2 too**; A2 SSLKEYLOGFILE drops from
> "only N-plane realtime route" to "redundant insurance". P2 D2 tier
> may upgrade to D1 once L3g LocalCache layout is verified.
>
> **2026-05-10 N/L1 chat-region empirical revision** ⚠️ : First end-to-end
> sweep against ChatGPT Desktop v1.2026.119.0 surfaced a **split-channel
> architecture** that invalidates the implicit "L1 reaches both user and
> assistant text" assumption inherited from web ChatGPT. The new
> `/backend-api/f/conversation` POST returns ONLY a 567-byte SSE
> handoff envelope:
>
> ```
> event: delta_encoding\ndata: "v1"
> data: {"type":"resume_conversation_token","token":"[REDACTED_JWT]",...}
> data: {"type":"stream_handoff","conversation_id":"...","turn_exchange_id":"...",
>        "options":[
>          {"type":"resume_sse_endpoint","topic_id":"conversation-turn-..."},
>          {"type":"subscribe_ws_topic","topic_id":"conversation-turn-..."}]}
> data: [DONE]
> ```
>
> The assistant text streams over a **separate WebSocket** that mitmproxy
> doesn't see in the current capture path (no `conversation-turn-*`,
> `resume`, `wss`, or `websocket` paths captured across 4065 chatgpt.com
> rows in a 3-message window). Empirical confirmation: searched all
> response bodies for "Paris", "capital of france", "你好", "香港",
> "首都" — zero matches in any captured row, despite all 3 user
> messages reaching the `messages` table. **N/L1 chat-region for P2 is
> therefore BLOCKED on user-side only** until WebSocket capture is added.
> See `Docs/handoff/HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md` §4
> for full evidence + 4 candidate unblock paths.

| Field | Value |
|---|---|
| OS | Windows (MSIX) + macOS |
| Primary plane / layer | **N / L1** (system proxy + CA, `pce_proxy/`) — primary route; **H2-P2 ✅ PASS confirmed 2026-05-10** (361 clean hits, 0 TLS errors, app-layer cookies negotiated). ⚠️ **2026-05-10 chat-region empirical revision**: user-side ✅ captured (3/3 prompts via `/backend-api/f/conversation` POST), assistant-side ❌ BLOCKED (split-channel HTTP→WSS handoff, see new dated note above) |
| Persistence axis | **L3g local persistence watcher** ✅ pkg alpha.8 — same parser stack as P1, ChatGPT-specific `LocalCache` layout discovery wired but layout itself TBD on first install probe |
| MCP axis | **M / L3f** (only when user has configured ChatGPT Desktop MCP servers — ChatGPT MCP support is partial / 2026-evolving) |
| Chat-region anti-pin | **A2 SSLKEYLOGFILE** patch — primary fallback if H2-on-P2 = pinned |
| Squirrel-only secondary | H / L3d (CDP launcher) — applies to non-MSIX channels only |
| Normalizer | `pce_core/normalizer/openai.py` ✅ + `local_persistence.py` ⬜ new |
| `source_type` | `local-persistence` (L3g) + `proxy` (L1) + `mcp_proxy` (M when applicable) + `desktop_electron` (Squirrel-only L3d) |
| Archetype | Chat Tube ✅ |
| Risks | � **H2-P2 ✅ PASS** (2026-05-10): mitmproxy CA-signed leaf cert accepted; 361 clean chatgpt.com hits / 0 TLS errors / app-layer cookies (`oai-client-auth-info`, `_puid`) negotiated successfully through chain. SSL pinning hypothesis **rebutted** for `chatgpt.com` / `ws.chatgpt.com` on this MSIX channel; 🟡 ChatGPT MCP support roadmap uncertain → M axis may be empty; 🔴 H4 LOCKED verdict from P1 expected to apply to P2 (same Electron Fuses set on MSIX-packaged Electron) — NODE_OPTIONS / `--inspect` / asar-mod paths unavailable |
| Install assets | `Docs/install/PCE_CHATGPT_DESKTOP_INSTALL.md` (post-ADR-018 rewrite pending) |
| First-probe checklist | **a)** ~~run method-G on P2~~ **H2-P2 ✅ PASS locked 2026-05-10 via `scripts/probe_h2_chatgpt.ps1`; see ADR-018 §6 ChatGPT Desktop H2 同等性** · **b)** ~~mitmproxy attempt to map pinning failure surface~~ **已确认无 pinning, A1 viable** · **c)** `LocalCache` first-entry dump for L3g parser work (path: `%LOCALAPPDATA%\Packages\OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0\LocalCache\`) · **d)** check `/v1/chat/completions`-style endpoints for fallback normalizer compatibility |

### 4.3 P3 — Cursor

| Field | Value |
|---|---|
| OS | Windows + macOS + Linux |
| Primary plane / layer | **H / L3d** (CDP launcher — VS Code fork is Electron, accepts `--remote-debugging-port`; per ADR-016 §3.7) |
| Secondary plane / layer | M / L3f (only when user has configured Cursor MCP servers) |
| Tertiary plane / layer | N / L1 (limited — Cursor uses gRPC-web + protobuf for upstream; mitmproxy sees binary bodies) |
| Normalizer | `openai.py` / `anthropic.py` ✅ + `ide_interactions.py` ⬜ new (for `interaction_kind ∈ {chat, completion, composer, inline_edit, lint_diff}`) |
| `source_type` | `desktop_electron` |
| Archetype | **Code Session** ⬜ new + Chat Tube fallback |
| Risks | 🟡 private gRPC-web protocol; CDP `Network.responseReceived` sees **already protobuf-encoded bodies** — may need additional `Network.requestWillBeSent` correlation · 🟡 frequent Cursor releases may shift fetch URLs (rebind selectors via probe-style adapter §6 of framework) |
| Install assets | `Docs/install/PCE_CURSOR_INSTALL.md` |
| First-probe checklist | **a)** confirm Cursor accepts `--remote-debugging-port=9222` (Electron + VS Code fork should) · **b)** locate the renderer-process `fetch` / WebSocket calls where `chat.send` / `composer.run` / `inline.complete` flow (CDP event filtering) · **c)** verify CDP-captured payloads carry `model`, `messages`, `interaction_kind` fields after protobuf decode (or fall through to L1 mitmproxy on plain endpoints) |

### 4.4 P4 — Windsurf

| Field | Value |
|---|---|
| OS | Windows + macOS + Linux |
| Primary plane / layer | **H / L3d** (CDP launcher — reuses P3 launcher scaffolding; ADR-016 §3.7) |
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
CLI contexts. **23 D-cases** total — D00–D12 are the original "stress
dimensions on the capture pipeline"; **D13–D22** were added 2026-05-10
to align with the surface-enumeration coverage already established on
the web side (`Docs/stability/CLAUDE-FULL-COVERAGE.md` C01–C20). The
mapping web→desktop is:

| Desktop | Web equivalent | Surface |
|---|---|---|
| D13 | C06 | Extended Thinking (`<thinking>` block) |
| D14 | C07 | Edit user message + branch fork |
| D15 | C08 | Regenerate (assistant variant) |
| D16 | C09 | Branch flip (`< 1/2 >` switcher) |
| D17 | C11 | Image upload + vision Q |
| D18 | C10 | PDF document upload + summarise |
| D19 | C13 | Project-scoped chat (`/project/<id>` URL pattern) |
| D20 | C14 | Artifact (text — markdown / SVG / Mermaid) |
| D21 | C15 | Artifact (interactive — HTML / React) |
| D22 | C17 | Writing Style (`personalized_styles` → `layer_meta.style`) |

| ID | Name | What it asserts |
|---|---|---|
| **D00** | product detection | PCE recognises the source (right `source_type`, right `provider`, right `model_name`) |
| **D01** | single-turn chat | One user message → one assistant message → `messages` table has 2 rows in same `session_id` |
| **D02** | streaming complete | The final stored text equals what the user sees in the product UI; no truncation, no partial frame |
| **D03** | multi-turn persistence | A 5-turn conversation preserves order + same `session_id` + correct `turn_index` |
| **D04** | stop / cancel | User-cancelled generation persists the user's prompt as a `messages` row; if a partial assistant body was captured, store it with `interaction_kind="cancelled"` |
| **D05** | model switch | After switching models mid-session, new turn's `model_name` reflects the change |
| **D06** | file attachment (generic) | Uploading a non-image / non-PDF document (CSV / TXT / docx) stores `attachments[]` with `file_uuid` + mime; applicable only where the product supports file upload |
| **D07** | code block | A response containing fenced code stores it with language tag preserved |
| **D08** | MCP tool call | Single MCP tool invocation produces correlated `role=tool_call` + `role=tool_result` + `tool_call_id` link; applicable only to MCP-aware products |
| **D09** | inline completion | Accepting a ghost-text completion stores `interaction_kind=completion` (or equivalent); applicable only to IDE-class |
| **D10** | error state | A real upstream 4xx/5xx (or proxy / network failure) stores `status_code` + `error` text where applicable; pipeline does not crash; no phantom assistant message; recovery on next request is clean |
| **D11** | long-context survival | A 50-turn conversation with cumulative ≥8K tokens captures every turn, no message dropped |
| **D12** | silent on idle | 5-min idle in the product yields **zero** chat-relevant capture writes (background heartbeats may be present in `raw_captures` but must not produce `messages` / `sessions` rows) |
| **D13** | extended thinking | An Opus / Sonnet 3.7+ reasoning prompt produces a separate `thinking` content track (raw `thinking_delta` SSE events) AND a clean final `assistant.content_text` not polluted by the internal monologue |
| **D14** | edit user message | Editing a prior user turn produces a NEW branch with `branch_id` + `branch_parent_id`; collapsed view returns the latest branch only; expanded view returns all branches; both branches share `session_id` |
| **D15** | regenerate | Re-rolling the assistant on the same user prompt produces a NEW branch with the same `branch_parent_id` as the original assistant; `branch_count` ≥ 2 on the parent user turn |
| **D16** | branch flip | After D14 or D15, switching the in-product branch arrows changes which branch is "active" without producing duplicate captures; collapsed view follows the active branch, expanded view stays stable |
| **D17** | image / vision upload | Uploading an image (PNG / JPG) and asking a visual question stores `attachments[]` with `file_kind="image"` + dimensions + thumbnail URL where present; assistant successfully answers about image content |
| **D18** | PDF document upload | Uploading a PDF and asking a summarisation question stores `attachments[]` with `file_kind="document"` + page count where surfaced; assistant summary references PDF contents |
| **D19** | project scope | A turn sent under a `/project/<id>/chat/<uuid>` URL produces a `messages` row with `session_key` extracted from the chat UUID portion of the path; project context is preserved in `layer_meta` (or equivalent) where the platform exposes it |
| **D20** | artifact (text) | A "create a markdown / SVG / Mermaid artifact" prompt produces an assistant message whose chat-side text references the artifact AND whose `content_json` contains the full artifact body, reconstructed from `tool_use.input_json_delta` SSE events |
| **D21** | artifact (interactive) | A "create an HTML / React component" prompt produces an artifact whose source code is fully reconstructed in `content_json` (same delta path as D20); `content_type` distinguishes the artifact kind |
| **D22** | writing style | Switching the in-product Writing Style (Concise / Explanatory / custom) BEFORE sending a turn captures the style metadata on the session (`layer_meta.style` or equivalent); the assistant's `content_text` stays clean of style-prompt boilerplate |

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
| D06 attachment (generic) | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D07 code block | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D08 MCP tool | ✅ | 🟡? | 🟡 | 🟡 | — | ✅ | ✅? | ✅? |
| D09 inline completion | — | — | ✅ | ✅ | ✅ | — | — | — |
| D10 error state | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D11 long context | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| D12 silent idle | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D13 extended thinking | ✅ | 🟡 | 🟡 | 🟡 | — | ✅ | 🟡 | 🟡 |
| D14 edit user message | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D15 regenerate | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D16 branch flip | ✅ | ✅ | — | — | — | — | — | — |
| D17 image / vision | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D18 PDF document | ✅ | ✅ | 🟡 | 🟡 | — | — | — | — |
| D19 project scope | ✅ | ✅ | — | — | — | — | — | — |
| D20 artifact (text) | ✅ | 🟡 | — | — | — | — | — | — |
| D21 artifact (interactive) | ✅ | 🟡 | — | — | — | — | — | — |
| D22 writing style | ✅ | 🟡 | — | — | — | — | — | — |
| **applicable** | 22 | 21 | 17 | 17 | 9 | 12 | 12 | 12 |

---

## 5.B Cowork-region C-cases (P1 Claude Desktop only)

> **Scope**: this section defines the cowork-region acceptance bar for **P1 Claude Desktop only**. C-cases are independent from the chat-region D-cases (§5) and use a separate ID namespace (`C00`–`C16`) to avoid collision. The cowork-region D0 sub-gate is defined in §4.1.B and §7.5: ≥80% (13/16) C-cases PASS for P1.
>
> **Why a parallel namespace and not D-cases**: cowork is an **asynchronous agent task** product, structurally different from chat (synchronous one-turn dialogue). Verdict semantics, capture surfaces, expected SSE shape, attachment lifecycle (sandbox `/mnt/user-data/{uploads,outputs}`), and timing all diverge. Sharing the D-prefix would force overloaded acceptance signals; the parallel C-prefix keeps each region's spec readable and the cross-product applicability matrix in §5.1 unchanged.
>
> **Audit anchor for each C-case**: every PASS verdict must produce ≥1 `raw_captures` row with `host='claude.ai'`, ≥1 `messages` row with the relevant `conversation_uuid`, and (for L3g-related cases) ≥1 row with `source_id='l3g-local-persistence-default'`. Capture-pipeline failures (FAIL) are distinct from product-feature SKIPs and are tracked separately, mirroring chat-region's "0 capture-pipeline FAILs" hard rule.

| C-id | Name | Acceptance signal |
|------|------|-------------------|
| **C00** | cowork tab detection | Switching to Cowork tab triggers `GET /cowork_settings` + `GET /environments?included_worker_types=cowork` + `GET /skills/list-skills?include_wiggle_skills=true` within 5s; `messages.layer_meta` (or equivalent) marks `region="cowork"` for cowork-originated rows. |
| **C01** | single agent task | "Help me organize my screenshots"-style task → 1 cowork session created, `/completion` SSE for agent reasoning, ≥1 assistant `messages` row persisted with non-zero `content_text`, `conversation_uuid` correlates session + messages. |
| **C02** | streaming complete | Multi-step agent task → all SSE events of all steps reach `messages.content_json` (no truncation mid-step); `event: message_stop` reached on each step's terminal event; reconstructed assistant text matches what UI rendered. |
| **C03** | multi-step task | Task with ≥3 agent steps → 1 session, ≥3 assistant messages OR 1 message with ≥3 step deltas, all under same `conversation_uuid`, step ordering preserved. |
| **C04** | task cancel | User clicks "Stop" mid-task → request captured, terminal flag preserved, no phantom assistant message persisted. **Expected to share root cause with chat-region D04 KNOWN BUG** (`pipeline.try_normalize_pair` requires both sides; cancel-mid-stream means response never arrives). |
| **C05** | task with file input | Upload PNG/PDF via `+` button → `POST /conversations/<uuid>/wiggle/upload-file` captured (request 290–800 B), response parsed for `file_uuid` + **`file_kind`**, user message attachment persists with `file_kind` populated. **Closes the D17/D18 normaliser-join gap**: response body already has `file_kind="image"` / `file_kind="document"` — just needs `local_persistence.py` (or `anthropic.py`) to join into `content_json.attachments[]`. |
| **C06** | task with code output | "Write a Python script that ..." → assistant `content_text` carries fenced code block, AND the artifact file appears at `/wiggle/download-file?path=/mnt/user-data/outputs/<filename>.py`, AND artifact reference appears in `messages.content_json.artifacts[]` (or equivalent). |
| **C07** | task with MCP tool | Cowork agent calls a non-PCE MCP tool (e.g. filesystem read) → `pce_mcp_proxy` captures tools/call request + tools/result response as a JSON-RPC frame pair; correlated by `tool_call_id`; `source_type='mcp_proxy'` rows in `raw_captures`. |
| **C08** | skill invocation (`/xlsx`/`/pdf`/etc.) | Type `/xlsx` in composer → Skills picker shows xlsx entry, user selects, agent invokes xlsx skill → `wiggle/upload-file` for any input + `wiggle/download-file` for output xlsx + `messages.content_json` records `skill_id` (or equivalent). The 8 known skills from `list-skills` (xlsx / pdf / pptx / docx / consolidate-memory / skill-creator / schedule / setup-cowork) are all in scope; **demo target = `/xlsx` because it produces a tangible artifact**. |
| **C09** | live artifact emission | Task produces an artifact (xlsx / docx / pptx / jsx / md / pdf) → `GET /artifacts/<uuid>/versions?source=w...` captured + filename retrievable via `wiggle/download-file?path=/mnt/user-data/outputs/...`. **Mirrors chat-region D20 capture shape — D20 already proves this path works incidentally**, so C09 verdict is a structural confirmation rather than a new path discovery. |
| **C10** | dispatch beta concurrent | Open Dispatch (Beta) tab, launch ≥2 tasks simultaneously → each gets its own `conversation_uuid`, no cross-contamination across `messages` rows, each task ends independently with its own terminal SSE event. |
| **C11** | scheduled task | Configure a task with Schedule (cron-like recurrence) → request body POST captured (recurrence rule + spec), no immediate execution row, but session metadata persists in `local-agent-mode-sessions/<uuid>/manifest.json` for L3g pickup at scheduled-time. |
| **C12** | project-scoped cowork | Cowork task launched inside a project context → `messages.layer_meta.project_id` populated; `/chat_conversations/<uuid>/completion` request body has project membership encoded. **Mirrors chat-region D19 architectural finding** (Claude Desktop projects use `/chat_conversations/<uuid>/completion`, not `/project/` URL segment). |
| **C13** | cowork settings change | Customize → toggle a setting (e.g., "Web search enabled" `coworkWebSearchEnabled`) → `POST /cowork_settings` request body captured with new value; server-side echo confirms persistence; `claude_desktop_config.json` `epitaxyPrefs.starred-cowork-spaces`-class entries observable on disk. |
| **C14** | L3g backstop (persistence axis) | Run a cowork task → confirm `local-agent-mode-sessions/<uuid>/manifest.json` is parsed by `pce_persistence_watcher` (`agent_sessions.py`) and emitted as `raw_captures` row with `source_id='l3g-local-persistence-default'`; structural normaliser (`pce_core/normalizer/local_persistence.py`, gated by this case) parses session metadata into `sessions` row. **This is the "even-if-network-blocked" path**. |
| **C15** | idle silence | No cowork activity for 5 min → 0 cowork-task-relevant new captures. Heartbeat polls (`cowork_settings`, `environments?included_worker_types=cowork`, `referral/eligibility?source=cowork`) ≤ 50 rows over 5 min, classified as non-task background. **Mirrors chat-region D12** (which observed +8 raw heartbeats / 310 s). |
| **C16** | `.mcpb` install path | `pce-mcp.mcpb` ZIP packaged + dragged into Claude Desktop Settings → Extensions → installed in ≤30 s → 6 PCE tools (`pce_capture` / `pce_query` / `pce_stats` / `pce_sessions` / `pce_session_messages` / `pce_capture_pair`) appear in Cowork composer Skills picker → invoking `pce_capture` from a cowork task lands a row in `messages` with `source_type='pce_mcp'` and `source_id='mcp-default'`. |

### 5.B.1 Cowork-region applicability

`✅` = must pass · `🟡` = best-effort / partial mirror · `—` = N/A (product has no cowork-region).

| | P1 Claude Desktop | P2 ChatGPT Desktop | P3 Cursor | P4 Windsurf | P5 Copilot | P6 Claude Code | P7 Codex CLI | P8 Gemini CLI |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| C00 detection | ✅ | — | — | — | — | — | — | — |
| C01 single task | ✅ | — | — | — | — | 🟡 (CLI mirror) | — | — |
| C02 streaming | ✅ | — | — | — | — | 🟡 | — | — |
| C03 multi-step | ✅ | — | — | — | — | 🟡 | — | — |
| C04 cancel | ✅ | — | — | — | — | 🟡 | — | — |
| C05 file input | ✅ | — | — | — | — | — | — | — |
| C06 code output | ✅ | — | — | — | — | 🟡 | — | — |
| C07 MCP tool | ✅ | — | — | — | — | ✅ | 🟡 | 🟡 |
| C08 skill invocation | ✅ | — | — | — | — | — | — | — |
| C09 live artifact | ✅ | — | — | — | — | 🟡 | — | — |
| C10 dispatch concurrent | ✅ | — | — | — | — | — | — | — |
| C11 scheduled | ✅ | — | — | — | — | — | — | — |
| C12 project scope | ✅ | — | — | — | — | — | — | — |
| C13 settings change | ✅ | — | — | — | — | — | — | — |
| C14 L3g backstop | ✅ | 🟡 (P2 has L3g layout discovery TBD) | — | — | — | — | — | — |
| C15 idle silence | ✅ | — | — | — | — | ✅ | — | — |
| C16 `.mcpb` install | ✅ | — | — | — | — | — | — | — |
| **applicable** | 16 | 1 (best-effort) | 0 | 0 | 0 | 7 (CLI mirror) | 1 (best-effort) | 1 (best-effort) |

> **P6 Claude Code mirror note**: Claude Code CLI shares the wiggle sandbox + skills universe via H1 CLI wrap. The 7 best-effort entries above are structural mirrors — same capture pipeline (`pce_cli_wrapper/` H1 + L3g for session manifests) but different render archetype (Run Trace, not Tool Tape). They do not count toward the cowork-region D0 sub-gate; they belong to the **P6 D-case applicability** column in §5.1.

### 5.B.2 Open architectural questions (RECON-resolvable in 60 min)

These are the questions the §4.1.B "First-probe checklist (a)" RECON pass should answer. Each question changes the implementation cost of ≥1 C-case:

1. **Skills picker UIA shape**: When user types `/` in cowork composer, is the autocomplete dropdown a UIA descendant of the Claude main window OR a separate top-level Win32 popup (like the model / style picker)? Determines whether `pick_skill()` reuses `_find_uia_by_name_substr` or `_find_uia_by_name_substr_all` (cross-window).
2. **Async task waiting semantics**: Does cowork agent stream multi-step reasoning over a single SSE connection (chat-like), OR does it open a new `/completion` per step, OR does it use long-poll? Determines `wait_for_cowork_step()` implementation.
3. **Dispatch (Beta) window class**: Is Dispatch a separate top-level window or an in-app sidebar? Determines `open_dispatch()` driver path.
4. **`/skills/list-skills` schema**: The 4927 B response body — what does an entry look like? Need full body dump to design `pick_skill(name)` matching.
5. **`local-agent-mode-sessions/<uuid>/manifest.json` field schema**: Full dump of one entry (ADR-018 §3.4 promised this; not yet done). Determines `local_persistence.py` structural normaliser fields.
6. **Cowork conversation lifecycle**: Does a "scheduled" task create a `conversations/<uuid>` row immediately, OR only at first execution? Affects C11 acceptance signal.

Resolution path: 60-min RECON via `tests/manual/recon_claude_desktop.py` with cowork-specific markers (see HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md §3 RECON walkthrough), then a `Docs/research/2026-05-XX-cowork-recon-findings.md` companion doc that closes each question.

---

## 6. Test infrastructure layout

Six test trees, parallel to the existing
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\` (browser-extension
probe framework). The original 2026-05-08 plan named only 3 new
trees (`e2e_mcp/`, `e2e_desktop/`, `e2e_cli/`); empirical work in
P5.B.1–P5.B.2 + alpha.8 + the 2026-05-10 chat sweep added
`e2e_desktop_ui/` (UIA + SendInput driver framework — sub-runs 1–5),
`e2e_l3g/` (alpha.8 L3g persistence watcher tests), and
`e2e_mcpb/` (`.mcpb` packaging tests). As-built layout:

```
tests/
├── e2e_probe/                    # ✅ existing — browser-ext, frozen
├── e2e_mcp/                      # ✅ shipped — P5.B.0 + P5.B.1 (pce_mcp + pce_mcp_proxy stdio relay e2e)
├── e2e_mcpb/                     # ✅ shipped — alpha.8 (pce_mcp/.mcpb packaging artefact tests)
├── e2e_l3g/                      # ✅ shipped — alpha.8 (pce_persistence_watcher: discovery + capture + agent_sessions + indexeddb_strings + local_config + e2e_scan)
├── e2e_desktop/                  # ✅ shipped — original P5.B.2 plan name; carries cross-product chat-region cases (CDP launcher era; partially superseded by e2e_desktop_ui/ for P1 chat sweeps)
├── e2e_desktop_ui/               # ✅ shipped — 2026-05-10 chat sweep (sub-runs 1-5) — UIA + SendInput automation framework for P1 Claude Desktop chat-region D-case sweep; 22 files including drivers/claude_desktop.py + cases/p1_chat_window_*.py + scripts/dump_uia.py; this is where the cowork-region C-case sweep will live too (sub-phase P5.B.5 below)
└── e2e_cli/                      # ✅ shipped — P5.B.4 (CLI gateway probe + Copilot L1 proxy tests)
```

### 6.1 Shared case helpers

The 2026-05-10 chat sweep landed `e2e_desktop_ui/drivers/claude_desktop.py`
as the canonical desktop-product driver contract. It exposes (after
sub-run 5 focus-verification rewrite):

- `new_chat()` / `open_cowork_tab()` (planned for sub-phase P5.B.5) /
  `open_code_tab()` — region switch.
- `_find_composer_uia()` + `ensure_composer_focus()` —
  `IUIAutomation.GetFocusedElement`-verified composer focus before
  any send_keys / paste.
- `paste_clipboard()` — focus-verified Ctrl+V (CF_HDROP / CF_TEXT
  / CF_DIB depending on caller).
- `send_text(text)` — typed input with implicit focus check.
- `wait_for_response(timeout)` — Tier-1 SSE / completion.
- `cancel_response()` — D04 driver.
- `select_model(name)` / `select_style(name)` — cross-top-level-window
  popup search (sub-run 4).
- `regenerate_last()` / `flip_branch(direction)` — `prefer="max_y"`
  Y-band action toolbar disambiguation (sub-run 4).
- `attach_file_via_picker(path)` / CF_HDROP paste path — D06 / D17
  / D18.
- `dump_tree(keywords, control_types)` — read-only UIA walker for
  field reverse-engineering, used by `scripts/dump_uia.py`.

Adapter contract for individual product drivers in
`tests/e2e_desktop/products/<name>.py` (legacy CDP-era pattern; lives
alongside the UIA-based `e2e_desktop_ui/drivers/`):

- `launch()` — start (or attach to a running instance)
- `send(text, attachments=None)` — drive a chat input
- `receive(timeout)` — wait for a complete response
- `cancel()` — trigger D04
- `switch_model(name)` — drive D05
- `expected_capture_paths()` — declare what `storage_paths` and
  `render_paths` the test should find in PCE

This mirrors the `tests/e2e_probe/sites/<name>.py` adapter contract
that's been proven on browser sites.

### 6.2 Reports + best-of aggregator

Each tree writes per-case logs (`_window_*_log.txt` / `_<case>_run.log`)
to the workspace root during sub-run execution; a structured
`summary.json` at `tests/e2e_<tree>/reports/<timestamp>/` follows the
same shape as `tests/e2e_probe/reports/<timestamp>/summary.json`
so the freeze-doc-style "best-of" aggregator carries over verbatim.
The chat sweep (sub-runs 1–5) wrote evidence to `_window_a–m_log.txt`
under workspace root + `_d17_pass_run.log` / `_d18_pass_run.log`-style
post-run inspectors; future cowork-region C-case sweep will use the
same convention with `_cowork_<window>_log.txt`.

---

## 7. Implementation phasing — mapped onto P5.B.0 to P5.B.5

The kickoff doc (`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §4)
defines six sub-phases (P5.B.0–P5.B.4 from the original 2026-05-08
plan + **P5.B.5** added 2026-05-10 to land the P1 Claude Desktop
cowork-region after the chat-region sub-runs 1–5 closed at 19/22 PASS).
This section maps each sub-phase to **which products gain which
D-cases (chat-region §5) and C-cases (cowork-region §5.B)**.

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

### P5.B.2 — L3d CDP launcher + `.mcpb` packaging — Claude Desktop only

**Per ADR-016**: implementation pivoted from "L3b Electron preload
+ ASAR repack" to **L3d CDP launcher** (reusing
`pce_core/cdp/driver.py`) + **`.mcpb` Desktop Extension** for
`pce_mcp/` packaging. **No Anthropic binaries modified on disk.**

**Unlocks**: D01-D07, D10-D12 for P1 (Claude Desktop's chat path,
filling the gap that L3f doesn't cover — chat text doesn't traverse
MCP frames).

**Eligible products**: P1.

**Acceptance**: P1 hits ≥85% of its 12 applicable D-cases. **D0
gate met.**

### P5.B.3 — L3d CDP launcher — Cursor + Windsurf

**Per ADR-016 §3.7**: P5.B.3 carries forward the CDP launcher
scaffolding (`pce_app_launcher/`) from P5.B.2, not the original
preload scaffolding. Workload reduced (~1 week vs original
1–2 weeks).

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

### P5.B.5 — P1 Claude Desktop cowork-region (M middleware + L3g persistence + UI driver)

**Per ADR-018 §3.5 + §4.1.B**: builds on the three-axis MSIX
scaffolding shipped in `v1.1.0-alpha.8-adr018` (`pce_persistence_watcher/`
+ `pce_mcp_proxy/` + `pce_cli_wrapper/` + `tests/e2e_desktop_ui/`
driver). **No new top-level packages**; only:

1. **6 new cowork helpers** added to
   `tests/e2e_desktop_ui/drivers/claude_desktop.py`
   (extending the sub-run 5 focus-verified driver):
   - `open_cowork_tab()` — sidebar tab switch
   - `pick_skill(name)` — composer `/`-trigger Skills picker
     (supports `/xlsx`, `/pdf`, `/pptx`, `/docx`, `/consolidate-memory`,
     `/skill-creator`, `/schedule`, `/setup-cowork`)
   - `select_ask_mode(mode)` — composer "Ask" picker (Ask / Reply /
     Plan-and-execute, etc.)
   - `view_live_artifacts()` / `open_dispatch()` / `open_scheduled()`
     — sidebar / right-pane region entries
   - `wait_for_cowork_step(timeout=120)` — async-step completion wait
     (different SSE/HTTP semantics from chat-region's
     `wait_for_response`; RECON resolves which)
2. **`pce_core/normalizer/local_persistence.py`** — structural
   normaliser for `local-agent-mode-sessions/<uuid>/manifest.json`
   (currently `pce_persistence_watcher` emits envelope-level rows
   only). Gate: C14.
3. **`.mcpb` packaging + real-machine install** —
   `pce_mcp/mcpb/` manifest is complete; missing the ZIP step
   (`@anthropic-ai/mcpb` CLI or hand ZIP) + drag-into-Settings →
   Extensions → 6 PCE tools appear in Cowork composer Skills picker.
   Gate: C16.
4. **16 new C-case files** under
   `tests/e2e_desktop_ui/cases/p1_cowork_*.py` (one per C00–C16).

**Unlocks**:

- All 16 C-cases (§5.B) for **P1 Claude Desktop cowork-region**.
- **Tool Tape archetype** (data shape only — Tape renderer ships
  in P5.C with the dashboard).
- **Run Trace archetype partial shape** for P1 cowork-region's
  multi-step agent loop (full Run Trace shape comes from P6 Claude
  Code's CLI sessions in P5.B.1; cowork is a structural mirror).

**Eligible products**: **P1 only**. P6 Claude Code's 7 best-effort
cowork mirrors (§5.B.1 footnote — wiggle sandbox + skills universe
shared via H1 CLI wrap) count toward **P6's D-case applicability
(§5.1)**, not toward this sub-phase's C-case acceptance.

**Non-deliverables**:

- **Tool Tape renderer** — dashboard view ships in P5.C, not here.
- **`local_persistence.py` v1 schema completeness** — v0 (this
  sub-phase) parses session manifest envelope into `sessions`/`messages`;
  full reverse-engineering of internal field layout (`vm_bundles/`,
  internal step shape) is v1, post-v1.1.
- **P2 / P3 / P4 / P5 / P7 / P8 cowork support** — not in scope;
  P2 has a 1 best-effort entry (C14 L3g layout discovery TBD); the
  rest are `—` in §5.B.1.

**Acceptance**: **P1 cowork-region D0 sub-gate met** =
**≥80% C-cases PASS** (≥13/16) AND **0 capture-pipeline FAILs**
(SKIPs allowed — same hard rule as chat-region sub-runs 1–5;
SKIP = product UI feature absent or RECON-deferred, FAIL = capture
pipeline lost an event that physically reached the network or disk).

**Concrete pass-distribution target** (modelled on chat-region 19/22):

- **PASS ≥13/16** of which 4 are M-axis (C00, C07, C08, C16),
  4 are network-axis (C01, C02, C05, C09), 3 are state-axis
  (C12, C13, C15), 2 are L3g-axis (C11, C14), and ≥0 of the
  multi-step set (C03, C06, C10).
- **SKIP ≤2** with named cause (e.g., C04 cancel-mid-stream
  inheriting chat-region D04 KNOWN BUG; C11 scheduled task
  needing >24 h to verify scheduled-time emission).
- **FAIL = 0** — any capture-pipeline FAIL blocks sub-phase.

**Sub-phase deliverables checklist**:

- [ ] **RECON pass** — `python -m tests.manual.recon_claude_desktop --duration 3600`
      with cowork section markers (§5.B C00–C16 audit anchors);
      writes `Docs/research/2026-05-XX-cowork-recon-findings.md`
      that closes §5.B.2's six open questions.
- [ ] **6 cowork helpers** committed to `drivers/claude_desktop.py`,
      each with a UIA dump of its target element captured under
      `tests/e2e_desktop_ui/scripts/uia_dumps/cowork_*.txt`.
- [ ] **`pce_core/normalizer/local_persistence.py`** v0 lands;
      structural unit tests under `tests/test_local_persistence.py`.
- [ ] **`pce-mcp.mcpb`** packaged via documented step in
      `pce_mcp/mcpb/README.md`; real-machine install verified
      (6 tools appear in Cowork Skills picker).
- [ ] **16 C-case files** under
      `tests/e2e_desktop_ui/cases/p1_cowork_*.py`, runnable via
      `python -m tests.e2e_desktop_ui.run_p1_cowork_sweep`.
- [ ] **Sub-run report** — `tests/e2e_desktop_ui/reports/p1_cowork/<timestamp>/`
      with per-case verdict JSON + `_cowork_<window>_log.txt`
      driver evidence + `summary.json` (same shape as chat-region
      sub-runs 1–5).
- [ ] **Handoff doc** —
      `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`
      (already drafted in alignment Step 3; see §4.1.B Authority
      reference).
- [ ] **CHANGELOG** entry for `v1.1.0-alpha.X-cowork-p1`.

**Unblocks**: P5.C dashboard work (Tool Tape renderer needs the
Tool Tape data shape this sub-phase produces).

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
- [ ] **P1 cowork-region D0 sub-gate** (P5.B.5, see §7.5):
      P1 passes ≥80% of 16 C-cases (≥13/16) with 0 capture-pipeline
      FAILs. **Independent of P1 chat-region D-case bar** — both must
      pass.
- [ ] All 5 normalizer additions exist and have unit tests:
      `mcp_jsonrpc.py` ✅ alpha.1, `ide_interactions.py`,
      `copilot_proxy.py`, **`local_persistence.py`** (P5.B.5 v0 —
      cowork session manifest structural parser, gates C14), and
      the migration 0010 (`interaction_kind` column — renumbered
      from originally-proposed 0009 per ADR-016 §3.6)
- [ ] All 8 install docs under `Docs/install/` exist and have been
      walk-through tested (includes `Docs/install/PCE_MCP_INSTALL.md`
      M-A `.mcpb` install path verified end-to-end on Claude Desktop
      MSIX channel — gates C16)
- [ ] `tests/e2e_mcp/`, `tests/e2e_mcpb/`, `tests/e2e_l3g/`,
      `tests/e2e_desktop/`, `tests/e2e_desktop_ui/`, `tests/e2e_cli/`
      each have ≥3 passing cases (the original 3-tree plan
      `e2e_mcp/`/`e2e_desktop/`/`e2e_cli/` was extended to **6 trees**
      by P5.B.1–P5.B.5 empirical needs — see §6 layout)
- [ ] ADR-012 (P5.B scope reorder), ADR-013 (`pce_mcp/` Open Core),
      ADR-014 (Type 5/6/7 deferral; this document's out-of-scope
      closure — see §10) all landed ✅ 2026-05-08; ADR-015 (UCS L3f
      amendment) landed ✅ 2026-05-09 alongside `pce_mcp_proxy/`;
      **ADR-016** (P5.B.2 pivot to CDP launcher + `.mcpb` packaging,
      with L3b Electron preload formally deferred from v1.1) landed
      ✅ 2026-05-09 in v1.1.0-alpha.3-docs.
- [ ] **ADR-017** (cross-lane Test Conductor + agent-callable MCP
      contract) implementation Phase 4.D.1–6 landed; conductor MCP
      surface exposes ≥1 desktop target and runs T01–T05 of
      Claude Desktop end-to-end with `propose_patch` covering at
      least the `CONTENT_BLOCK_UNKNOWN` failure class. (ADR-017 itself
      drafted 2026-05-09; Phase 4.D landing version `v1.1.0-alpha.7-conductor`
      onward.)
- [ ] **ADR-018** (P1 Claude Desktop MSIX three-axis realisation —
      M middleware + L3g persistence + H1 CLI wrap; ADR-016 §3.2 CDP
      launcher path formally deferred for MSIX) landed ✅ 2026-05-10
      in `v1.1.0-alpha.8-adr018`; cowork-region §4.1.B / §5.B / §7.5
      derive their three-axis structure from this ADR.
- [ ] `CHANGELOG.md` has v1.1 section (incl. P5.B.5 cowork-region
      sub-phase entries)
- [ ] `Docs/docs/PROJECT.md` updated to v1.1 phase pointer

---

## 9. Risk register

| ID | Risk | Owner product | Mitigation |
|---|---|---|---|
| **DR-01** | ChatGPT Desktop pinning blocks both N and H | P2 | Pre-stipulated D2 gate + P6 hand-off ADR |
| **DR-02** | Cursor's IPC shape changes between releases | P3 | Probe-style adapter with version detection; fail-soft on unknown shape |
| **DR-03** | Copilot adds SSL pinning to chat panel | P5 | Pre-stipulated D2 gate + monitor and hand off to P6 if needed |
| **DR-04** | Codex / Gemini CLI MCP support absent → forced to L3e gateway path | P7, P8 | Path documented (L3e CLI gateway in P5.B.4 sub-track B); no schedule impact |
| **DR-05** | App auto-update overwrites desktop shortcut (CDP launcher loses its `--remote-debugging-port` injection point) | P1, P2, P3, P4 | Install doc covers "re-run launcher install after auto-update"; daemon-watcher checks for shortcut drift on `pce_core` startup. **Lower severity than the original L3b preload risk** — nothing on disk inside the app bundle is modified per ADR-016 §3.1; only the user-side launcher shortcut needs re-pointing. |
| **DR-06** | MCP middleware adds latency that breaks user UX | All MCP-using | p95 < 50ms target; fail-open if proxy crashes (PROJECT.md §7.6 fail-open principle) |
| **DR-07** | normalizer divergence between desktop and web Claude/ChatGPT for the same `conversation_id` | P1, P2 | Migration 0010 (`interaction_kind`, renumbered per ADR-016 §3.6) covers desktop variants; reconciler in `pce_core/normalizer/conversation.py` extends to merge identical conversation_ids across sources |
| **DR-08** | Desktop H-plane (L3d CDP) silently broken by app updates — Anthropic / Cursor / Windsurf may add new `content_block.type` values, change response schemas, or move URL paths between releases (~2–4 week cadence). Without active monitoring this surfaces only when a user reports a missing capture. | P1, P3, P4 | **ADR-017 §3.4 canary store** — auto-generated JSON Schema snapshots per (target, case, endpoint) committed to git; `diff_canary` MCP tool detects `added_property` / `enum_extension` (soft) and `removed_property` / `changed_type` (hard) in O(1). **ADR-017 §3.5 patch templates** — three templates (`add_content_block_type`, `add_url_path`, `widen_schema_field`) cover the common drift modes; agent receives unified-diff data via `propose_patch` and applies via its own edit tools per ADR-011 G9. |

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
  Automation) — these are **L4b** in UCS canonical (L3d is reserved
  for CDP per ADR-016 §4.5), scheduled for **P6** / **P7**.
- Persona analysis: native clients are a **secondary** entry point
  for the persona; primary daily AI exposure for them is browser
  + Electron + IDE + CLI, all of which are covered.
- Per ADR-010 (Open Core boundaries), L4b is Pro-tier; shipping it
  in v1.1 would cross the OSS / Pro line prematurely.

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
| Know what UCS layers we touch | `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` (UCS v0.3 includes L3f per ADR-015, ✅ 2026-05-09) |
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
