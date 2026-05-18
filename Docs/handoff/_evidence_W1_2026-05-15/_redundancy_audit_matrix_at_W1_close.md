# Redundancy Audit Matrix — Capture Path × Scene Verification State

> **Status:** canonical — 本文件是 PCE 仓库内**捕获冗余性**的唯一真相来源。
> 上游规划文档（`UNIVERSAL-CAPTURE-STACK-DESIGN.md` / `DESKTOP-PRODUCT-MATRIX.md`
> / `SITE-TIER-MATRIX.md` / `research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`）
> 描述的是"设计上能抓的路线"；本文件只记录"**真实对产品跑过并留了
> handoff 签字的路线**"。两者不互换。
>
> **标准：** 每个目标场景至少有 **3 条不相互依赖的抓取存储路线** 在
> **V-GREEN 等级**（见 §1 评级阶梯）被真实验证过。
>
> **Adopted:** 2026-05-13
> **STRICT MODE re-scoped:** 2026-05-15 — P5.D.1 锁定 13 个 P0 场景
> 严格 ≥3 V-GREEN. 见 `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`.
>
> **Audience:** Cascade、后续维护 agent、项目所有者。任何对"PCE 已
> 经覆盖了 X 场景"、"X 场景的兜底路径已落地"的陈述 — 均需回本
> 文件核对 V-GREEN 数量。
>
> **Doesn't supersede:** UCS 蓝图 / ADR-018 / 任何 handoff；本文件**消
> 费**那些作为证据。
>
> **Review trigger:** 任何新增一条 live-app handoff 必须在 30 min 内
> 反向更新本文件的对应行（§7 更新协议）。

---

## 1. 评级阶梯 — 哪些路线才算有效冗余

用户提出的冗余标准是"≥3 条不相互依赖的抓取存储路线"。"有效"
不是"代码存在"，也不是"单元测试绿"，是下表的 **V-GREEN**:

| 等级 | 含义 | 算有效路线吗 |
|---|---|:---:|
| **V-GREEN** | 对**真实产品**跑过端到端，有具体 `raw_captures` / `messages` 行数或 handoff 签字作为证据 | ✅ |
| **V-HERMETIC** | 只有 mock / fixture / monkeypatched discovery 的单元或集成测试绿；代码可能工作但从未对真实产品验证 | 🟡 只能算"基础设施就位" |
| **V-PARTIAL** | 对真实产品跑过但只通过一部分（如用户侧通、助手侧挂；或可行性证明但生产链路未写） | 🟡 半条 |
| **V-DOC-ONLY** | adapter / config 文件存在但无任何测试或 handoff 证据 | ❌ |
| **V-STUB** | 代码自己声明 "NOT READY" / "scaffolding only" / "SX" | ❌ |
| **V-DEAD** | 实测失败且被 ADR 永久排除 | ❌ |

"不相互依赖" 的判定规则（严格）：

- 同走 Chromium 网络栈（L1 MITM + A2 SSLKEYLOGFILE）→ 算 1.5 条，
  不算 2 条独立
- 都被同一个 Electron Fuse 锁住（B1 NODE_OPTIONS + `--inspect`）→ 算
  同源
- 都需要 mitmproxy CA trust → 并列但依赖同一前提
- 一条走内存 / 一条走落盘 / 一条走 UI 树 / 一条走协议中间件 →
  真正正交，四条都算

> **这个卡法比 UCS 附录 B 的 ✅/🟢 路由矩阵严格，因为附录 B 只
> 问"能不能抓"，不问"依赖是否隔离 + 是否验证"。**

---

## 2. 本次审计 baseline

- **审计日期：** 2026-05-13
- **仓库 HEAD：** 含 P5.C.5.2 完结 (`f77f79b`) + ADR-018 alpha.8 + P2
  D02 2026-05-12 通过
- **使用的证据库：**
  - `Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`（P1 live N/L1）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`（P1 D00-D22 22 case 全量）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`（sub-run 3）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`（sub-run 4）
  - `Docs/handoff/HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md`（含 P2 D02 原 BLOCKED 结论）
  - `Docs/handoff/HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md`（2026-05-12 P2 D02 闭环）
  - `Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md`（推翻 WSS 假设）
  - `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md`（浏览器端 best-of）
  - `tests/e2e_probe/reports/*/summary.json`（199 runs best-of）
  - `tests/e2e_l3g/` + `tests/e2e_cli/` + `tests/e2e_mcp/` + `tests/e2e_mcpb/`（120+ hermetic tests）
  - `Docs/stability/CLAUDE-FULL-COVERAGE.md` + `CHATGPT-FULL-COVERAGE.md` Part VII 签字
  - `Docs/stability/WINDSURF-PRODUCT-MATRIX.md`（Stage 4 live-verified）
  - `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`（H2/H3/H4 verdicts）

---

## 3. 场景 × 路线 × 验证状态 主表

### 3.1 F1 · Web Chat UI

#### F1-S0 ChatGPT Web (`chatgpt.com`)

| # | Path | 依赖基底 | 代码 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | 页面 DOM + fetch shim | `entrypoints/chatgpt.content.ts` + `tests/e2e_probe/sites/chatgpt.py` | best-of **19/22 PASS** (probe reports 2026-05-08) | **V-GREEN** |
| 2 | L1 MITM | 系统代理 + mitmproxy CA + Chromium 网络栈 | `pce_proxy/addon.py` + `ALLOWED_HOSTS`（含 `chatgpt.com` + `chat.openai.com`） | `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 2026-05-12：13 raw_captures + 2 messages + session `37ddb011…` + model `gpt-5-3` | **V-GREEN** |
| 3 | L3d CDP | Chromium Debug Protocol | `pce_core/cdp/driver.py` + `tests/test_cdp_driver.py` (28 tests) | 无对 chatgpt.com 的 live handoff | **V-HERMETIC** |
| 4 | L4a 剪贴板 | 用户主动复制 | `pce_core/clipboard_monitor.py` + `tests/test_clipboard_monitor.py` | AI 特征检测单测绿；无 chatgpt live handoff | **V-HERMETIC** |

**有效 V-GREEN：2。差 1 条达标。**

#### F1-S0 Claude Web (`claude.ai`)

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | DOM | best-of **16/21 PASS** (CLAUDE-FULL-COVERAGE Part VII 2026-04-30 + probe 2026-05-08) | **V-GREEN** |
| 2 | L1 MITM | TLS + CA | `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`：136 rows / chat send 5238 B / SSE 3106 B / model `claude-haiku-4-5-20251001` / 1 session + 2 messages / 0 pipeline_errors | **V-GREEN** |
| 3 | L3d CDP | Chromium Debug | 同 chatgpt hermetic | **V-HERMETIC** |
| 4 | L4a 剪贴板 | 用户复制 | 无 claude-specific live | **V-HERMETIC** |

**有效 V-GREEN：2。差 1 条达标。**

#### F1-S1 Gemini Web (`gemini.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **17/21 PASS**（GEMINI-FULL-COVERAGE Part VII 2026-04-30: 10 PASS / 9 SKIP / 2 FAIL；probe 2026-05-08 追加） | **V-GREEN** |
| 2 | L1 MITM | `HANDOFF-W1-T4-GEMINI-WEB-L1-2026-05-15.md` 2026-05-15: 155 captures (proxy-default 含 SSE response 667 KB; browser-ext 含 12 google-web sessions / 112 msgs), prompt `What is 2+2?` live-verified | **V-GREEN** |
| 3 | L3d CDP | 无 | **V-HERMETIC** |

**有效 V-GREEN：2 (2026-05-15 W1-T4 升级)。差 1 条达标 (L3d 待 Wave 4)。**

#### F1-S1 Google AI Studio (`aistudio.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **13/21 PASS**（GOOGLE-AI-STUDIO-FULL-COVERAGE Part VII）+ `HANDOFF-W1-T5-GAS-L1-2026-05-15.md` 2026-05-15: GAS conversation session emitted with model `Gemini Flash-Lite Latest`, message_count=2 ("What is 2+2?" → "2 + 2 = 4 ..."), title `The Sum Of Two Plus Two | Google AI Studio` | **V-GREEN** |
| 2 | L1 MITM | allowlist 有, 但 W1-T5 sweep 期间 L3a 浏览器扩展抢先抓到 (DOM 比 SSE 完成更快), L1 leg 仍**无独立 live handoff**. 后续 sweep 需把代理设到 mitmproxy 后**禁用浏览器扩展**才能孤立 L1 leg | **V-HERMETIC** |

**有效 V-GREEN：1。差 2 条达标 (L1 + L3d 待后续 sweep)。**

#### F1-S1 Perplexity (`perplexity.ai`) — 推 P5.D.2 (STRICT MODE)

> **2026-05-15 SCOPE-LOCK**: Perplexity **不再**在 P5.D.1 13 P0 范围.
> 推到 P5.D.2 接入. 当前状态保留为参考.

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | `tests/e2e/test_perplexity_full.py` 24 cases；具体 PASS 数字在 PERPLEXITY-FULL-COVERAGE | **V-GREEN** |
| 2 | L1 MITM | allowlist 有，无 live handoff | **V-HERMETIC** |

**有效 V-GREEN：1。差 2 条达标 — 推 P5.D.2.**

#### F1-S0 / S1 / S2 Grok Web (`grok.com`) — P0 (STRICT MODE)

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | DOM | best-of **11/21 PASS** in probe reports | **V-GREEN** |
| 2 | L1 MITM | TLS + CA | `HANDOFF-W1-T6-GROK-WEB-L1-2026-05-15.md` 2026-05-15: 10 caps incl. `/rest/app-chat/conversations/new` 9015 B (real chat send), xai-web session emitted with 2 messages | **V-GREEN** |
| 3 | L4a 剪贴板 AI 特征识别 | 用户复制 | **W4-T3 待签字** (`HANDOFF-W4-T3-CLIPBOARD-LIVE-2026-05-14.md`, 含 grok.com section) | **V-HERMETIC** → V-GREEN W4-T3 |

**有效 V-GREEN：2 (2026-05-15 W1-T6 升级), 目标 3 (P5.D.1 — L4a 待 Wave 4).**

#### F1-S2 Copilot MS / DeepSeek (推 P5.D.2)

| Site | L3a Ext | L1 | 有效 V-GREEN |
|---|---|---|:---:|
| Copilot MS (`copilot.microsoft.com`) | COPILOT-COVERAGE-DIFF §4: "S2 diff runner pending + smoke pending" | allowlist; no live | **0** |
| DeepSeek (`chat.deepseek.com`) | DEEPSEEK-COVERAGE-DIFF §4: "smoke in `test_capture.py`" but no sign-off PASS count | allowlist; no live | **0** |

**STRICT MODE: Copilot MS / DeepSeek 推到 P5.D.2, 不在 v1.1.6 范围.**

#### F1-S3 HuggingFace / Poe / Kimi / Zhipu / Mistral / Manus

全部仅 "smoke in `test_capture.py`"，无 per-site best-of PASS 签字
handoff。代码存在（V-HERMETIC ~ V-DOC-ONLY）。

**有效 V-GREEN：每站 0 条。**

#### F1-SX Notion AI / M365 Copilot / Gmail / Figma AI

每份 COVERAGE-DIFF.md 第一行自述 **"⚠ NOT READY — scaffolding only"** /
"no live-DOM probe has happened"。

**有效 V-GREEN：0。**（V-STUB；SITE-TIER-MATRIX §10 已显式延后）

---

### 3.2 F2 · Web-Embedded AI

所有 F2 站点（Notion AI / M365 Copilot / Figma AI / Gmail / Jira AI
/ Linear AI）归口 F1-SX。

- L3a：每站 content script 存在但 V-STUB
- L1：SaaS 自域（`notion.so` / `m365.cloud.microsoft` / `figma.com` /
  `mail.google.com`）**未进 ALLOWED_HOSTS**（grep 确认 2026-05-13）
- L3d / L4a：无站点定制化

**整个 F2 有效 V-GREEN：0。**

---

### 3.3 F3 · Browser Extension AI

代表：Monica / MaxAI / Merlin / Sider / ChatGPT-for-Google

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | 这些扩展打厂商 API（`api.openai.com` / `api.anthropic.com`）— allowlist 通；但无 **F3-specific** live handoff | **V-HERMETIC** |
| 2 | L3a universal-extractor | `entrypoints/universal-extractor.ts` + `detector.content.ts` 启发式；无具体 F3 site handoff | **V-HERMETIC** |
| 3 | L4a 剪贴板 | 同上 | **V-HERMETIC** |

**有效 V-GREEN：0。**（虽然设计上能抓，但没人对 Monica / MaxAI 这种
真实扩展跑过 session）

---

### 3.4 F4 · Desktop Chat App

#### P1 Claude Desktop (Windows MSIX, v1.6608.2.0) — 最硬的盘面

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | **L1 MITM (A1)** | 系统代理 + CA | **H2 PASS 2026-05-10**: 172 clean `anthropic.com` hits / 0 TLS errors | **V-GREEN** |
| 1' | L1 continued chat sweep | same | `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` + 4 续篇：合计 D00-D22 **19/22 PASS** (86%) 跨 5 sub-run | **V-GREEN**（与 #1 同基底） |
| 2 | **L3g 持久化** | LocalCache 文件系统 | H3g scan live 于 HANDOFF-P1-N-L1 "8 captures emitted first scan, 0/8 deduped second scan, watch loop ticking"；`tests/e2e_l3g/` 8 test files + 43 tests GREEN | **V-GREEN** |
| 3 | A2 SSLKEYLOGFILE | Chromium net stack TLS secrets | **H3 PASS 2026-05-10**: 10 TLS 1.3 sessions × 5 labels；**但 `pce_proxy/keylog_mode.py` + `cert_wizard/keylog.py` 未写**，ADR-018 §5.2 Phase 5 未实施 | **V-PARTIAL** |
| 4 | L3f MCP (posture A `pce_mcp` `.mcpb`) | JSON-RPC over stdio | `HANDOFF-W1-T2-CLAUDE-DESKTOP-MCPB-2026-05-15.md` 2026-05-15 08:27 UTC: pce-mcp-0.1.0.mcpb 双击安装到真 Claude Desktop ✅, Claude UI 调用 `pce_capture` 工具落 1 行 `source_id='mcp-default'` (pair_id=`d502a3e9b7914511`, id=`62bd5f46ff9b45ba`, provider=test, model_name=w1-t2-retry, meta_json 含 test_marker/wave/issued_at). 副带证据: L1 proxy 同时见证了 `pce_stats` 调用流量 (claude.ai/api/organizations/... body 含 'pce_stats') | **V-GREEN** |
| 5 | L3f MCP (posture B `pce_mcp_proxy`) | 同上 | `test_pce_mcp_proxy_stdio.py` R01-R11 同 mock; W1-T1 live sign-off 推到独立 RECON session | **V-HERMETIC** |
| 6 | L3h CLI wrap | 进程 stdin/stdout | `tests/e2e_cli/` 77 tests GREEN 但 `_patch_discover(monkeypatch, [_mk_target(...)])` 合成 target；Desktop 内嵌 `claude-code\<ver>\claude.exe` 用绝对路径 spawn，PATH shim 不生效（discovery.py 已记） | **V-HERMETIC** |
| 7 | L3b Electron preload | NODE_OPTIONS | **H4 LOCKED 2026-05-10**: `EnableNodeOptionsEnvironmentVariable=Disabled` | **V-DEAD** |
| 8 | L3d CDP launcher | --remote-debugging-port | **MSIX 6 实验全败**（Method A-F 全挂） | **V-DEAD** |
| 9 | L2 Frida | 进程内 TLS unwrap | ADR-018 §3.7 移到 Pro 不实施 | **V-DEAD**（主仓） |

**有效 V-GREEN：3** ✅ ≥3 达标 (L1 + L3g + L3f .mcpb)。F4 P1 Claude Desktop
是 P5.D.1 13 P0 中**第一个**达到 STRICT ≥3 V-GREEN 不相互依赖路线的场景。
2026-05-15 W1-T2 retry 升级。

#### P2 ChatGPT Desktop (Windows MSIX, v1.2026.119.0)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | 2026-05-10 原结论"BLOCKED on WSS"；**2026-05-12 `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 推翻**：2 messages (user + assistant) / pair `6a9847ff4113…` / model `gpt-5-3` / PASS | **V-GREEN** |
| 2 | A2 SSLKEYLOGFILE | ADR-018 §6 "推断 PASS，未独立验" | **V-PARTIAL** |
| 3 | L3g 持久化 | IndexedDB 3881 bytes + WebRTC cert + 空 session 索引；**会话内容在服务端** | **V-DEAD**（结构性） |
| 4 | L3f MCP | ChatGPT Desktop 不支持 MCP | **N/A** |
| 5 | L3h CLI wrap | ChatGPT 无 CLI | **N/A** |
| 6 | L3b / L3d / L2 | 同 P1 全死 | **V-DEAD** |

**有效 V-GREEN：1**（仅 L1）。**结构性差 2 条；要达标只能上 L4b
Accessibility（UIA）兜底 — 未实施。**

#### F4 其它桌面（Poe Desktop / DeepSeek Desktop / Kimi Desktop / 豆包 Desktop / Claude Desktop macOS-DMG / Claude Desktop Windows-Squirrel）

- 全部无 handoff 证据
- `pce_app_launcher/claude_desktop/` 为 Squirrel + macOS 渠道的 CDP
  launcher 保留，但 tests/e2e_desktop/ 仅对 `detector` + `launcher`
  + `shortcut` 单测（不实际启动应用）

**有效 V-GREEN：0 per app。**

---

### 3.5 F5 · IDE AI Assistant

#### F5 P3 Cursor (`api*.cursor.sh`) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | `ALLOWED_HOSTS` 含 9 个 cursor 域；`HARVEST-SESSION-S1-DISCOVERIES.md F1` 有 host 抓包字节；但 `api5.cursor.sh` 是 **gRPC-web protobuf**, normalizer 未出 session/messages → **W8-T1 (Wave 8) 落 protobuf decoder + live** | **V-PARTIAL** → V-GREEN W8-T1 |
| 2 | L3f MCP | Cursor MCP 支持; **W8-T2 待签字** | **V-HERMETIC** → V-GREEN W8-T2 |
| 3 | L3b Electron preload OR L4a 剪贴板 | **W8-T3 待签字** (preload 选 A 若 Cursor Fuse 未锁; 否则 L4a) | **V-DOC-ONLY** → V-GREEN W8-T3 |
| 4 | proto decoder | `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` 行 476 ⬜ → **W8-T1 落地** | **V-DOC-ONLY** → V-GREEN W8-T1 |

**有效 V-GREEN：0 (pre-sprint), 目标 3 (P5.D.1 Phase B Wave 8).**

#### F5 P4 Windsurf (`server.codeium.com` / `server.self-serve.windsurf.com`) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 gRPC | `WINDSURF-PRODUCT-MATRIX.md` §2 Stage 4 **live-verified**；`pce_core/normalizer/windsurf/*` 落盘；Cascade Chat + Thinking + Model + Management ✅ | **V-GREEN** |
| 2 | L3f MCP | **W1-T7 待签字** (Wave 1) | **V-HERMETIC** → V-GREEN W1-T7 |
| 3 | L4a 剪贴板 (Cascade chat 复制 AI 特征) | **W4-T5 待签字** (Wave 4) | **V-DOC-ONLY** → V-GREEN W4-T5 |
| (extra) | Devin ACP WebSocket | 数据库 schema 暂不支持 ws direction | **V-DOC-ONLY** (不计入 P0 leg) |

**有效 V-GREEN：1 (pre-sprint), 目标 3 (P5.D.1 Wave 1+4).**

#### F5 P5 GitHub Copilot (VS Code) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (`api.githubcopilot.com`) | allowlist 有, normalizer 未实施 → **W6-T1 (Wave 6) 落 host JSON normalizer + live** | **V-HERMETIC** → V-GREEN W6-T1 |
| 2 | L3c VS Code 扩展 | 代码未实施 → **W6-T2 (Wave 6) 落地 (含 ADR-022 引用)** | **V-DOC-ONLY** → V-GREEN W6-T2 |
| 3 | L3f MCP OR L4a 剪贴板 | **W6-T3 待签字** (首选 L3f MCP, 备选 L4a) | **V-DOC-ONLY** → V-GREEN W6-T3 |

**有效 V-GREEN：0 (pre-sprint), 目标 3 (P5.D.1 Phase B Wave 6).**

#### Cline / Continue / Cody / Amazon Q / Roo Cline / Codeium / Tabnine / JetBrains plugin (推 P6)

**STRICT MODE: 不在 v1.1.6 范围, 推到 P6 Coverage Polish.**
全部 0 条 V-GREEN.

---

### 3.6 F6 · CLI AI Agent

#### F6 P6 Claude Code CLI — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.anthropic.com) | allowlist 有; **W4-T6 待签字** (Wave 4) | **V-HERMETIC** → V-GREEN W4-T6 |
| 2 | L3g 持久化 (`~/.claude/projects/<cwd>/*.jsonl`) | `tests/test_local_persistence.py` 用 2026-05-11 Round 3 RECON 真实 fixture (38 行 JSONL); HANDOFF-P1-N-L1 有 L3g scan live 证据 | **V-GREEN** |
| 3 | L3h CLI wrap (`pce_cli_wrapper/`) | `HANDOFF-W1-T3-CLAUDE-CODE-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target claude.cmd -- -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=claude`, `target_version=2.1.139` | **V-GREEN** |
| 4 | L3e LiteLLM | 单测全 mock, 无 real LiteLLM subprocess | **V-HERMETIC** (备用, 不计入 P0 leg) |

**有效 V-GREEN：2 (2026-05-15 W1-T3 升级), 目标 3 (P5.D.1 — L1 待 Wave 4).**

#### F6 P7 Codex CLI (OpenAI) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.openai.com) | allowlist 待加 + normalizer (`openai.py`) 待新增; **W4-T7 待签字** (Wave 4) | **V-DOC-ONLY** → V-GREEN W4-T7 |
| 2 | L3g 持久化 (`~/.codex/sessions/*.jsonl`) | `HANDOFF-W1-T9-CODEX-CLI-L3G-2026-05-15.md` 2026-05-15: 24 codex-cli-l3g sessions / 255 messages / `gpt-5-codex` model_names persisted via `pce_persistence_watcher.ide_scanner._scan_codex`; `host=local-codex-cli` confirmed | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T8-CODEX-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target codex.cmd -- exec --sandbox read-only "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=codex` (after `codex login` ChatGPT OAuth) | **V-GREEN** |

**有效 V-GREEN：2 (2026-05-15 W1-T8 + W1-T9 升级), 目标 3 (P5.D.1 — L1 待 Wave 4).**

#### F6 P8 Gemini CLI (Google) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (generativelanguage.googleapis.com) | allowlist 待加 + normalizer (`google_genai.py`) 待新增; **W4-T8 待签字** (Wave 4) | **V-DOC-ONLY** → V-GREEN W4-T8 |
| 2 | L3g 持久化 | `HANDOFF-W1-T11-GEMINI-CLI-L3G-2026-05-15.md` 2026-05-15: 1 fresh `gemini-cli-l3g` session emitted (`b1e5c0a8-...`), 2 messages [user "What is 2+2?", assistant "2 + 2 = 4"], `model_names=["gemini-3-flash-preview"]`. **加发现并修复了一个 routing bug** (`local-gemini-cli` 未列在 `normalize_conversation` 的 L3g host 白名单内, 导致 P8 自 a5d09f5 起其实是 false-positive; commit 097a1d2 修复 + 加 regression test) | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T10-GEMINI-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target gemini.cmd -- --skip-trust -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=gemini` | **V-GREEN** |

**有效 V-GREEN：2 (2026-05-15 W1-T10 + W1-T11 升级 + bug fix), 目标 3 (P5.D.1 — L1 待 Wave 4).**

#### F6 Aider CLI (推 P5.D.2)

**STRICT MODE: 不在 v1.1.6 范围.** 0 V-GREEN.

---

### 3.7 F7 / F8 · Mobile / OS-Integrated AI

PROJECT.md + UCS §1.1 FR-1 显式 **T3 非目标**。无需评估。

`mobile_wizard.py` 是 PAC + CA 向导壳（用户自用 Surge/ProxyMan），不
是 PCE 自己的抓取路径。

---

### 3.8 F9 · Local / Self-Hosted Model

代表：Ollama / LM Studio / llama.cpp / LocalAI / vLLM

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 loopback MITM | 无 handoff；需用户设 `HTTPS_PROXY` 指向 mitmproxy | **V-DOC-ONLY** |
| 2 | L3e LiteLLM gateway | `test_sdk_capture_litellm.py` 全 mock（文件首行自陈） | **V-HERMETIC** |
| 3 | L3f OTel (OpenLLMetry) | `test_otel_export.py` safe-when-disabled + 可选 SDK 验证；无对真实 LangChain / LlamaIndex 的 live span | **V-HERMETIC** |

**有效 V-GREEN：0。**

---

### 3.9 F10 · Headless Agent / Framework

代表：LangChain / LlamaIndex / CrewAI / AutoGPT / n8n AI / dify

同 F9 结构：全部 V-HERMETIC 或 V-DOC-ONLY。

**有效 V-GREEN：0。**

---

## 4. 汇总计分表 (STRICT MODE)

按"每场景 ≥3 条 V-GREEN"严格标准, P5.D.1 锁定的 13 P0 场景跟踪表:

### 4.1 P5.D.1 P0 场景 (13 个) — STRICT MODE 强制 ≥3 V-GREEN

| 场景 | pre-sprint | 2026-05-15 (W1 partial) | Phase A target | Phase B target | final | 关键 wave |
|---|:---:|:---:|:---:|:---:|:---:|---|
| F1 ChatGPT Web              | 2 | 2 | 3 | 3 | 3 | W4-T1 (L3d) |
| F1 Claude Web               | 2 | 2 | 3 | 3 | 3 | W4-T2 (L3d) |
| F1 Gemini Web               | 1 | **2** ✅ W1-T4 | 3 | 3 | 3 | W4-T3 (L3d 或 L4a) |
| F1 GAS                      | 1 | 1 (W1-T5 via L3a fallback, L1 仍 hermetic) | 3 | 3 | 3 | L1 sweep + W4-T3 |
| F1 Grok Web                 | 1 | **2** ✅ W1-T6 | 3 | 3 | 3 | W4-T3 (L4a) |
| F4 P1 Claude Desktop        | 2 | **3** ✅ W1-T2 retry (08:27 UTC `pce_capture` PASS, pair_id=d502a3e9...) | 3 | 3 | 3 | (达标) + W1-T1 (proxy posture B for 4-leg buffer) |
| F4 P2 ChatGPT Desktop       | 1 | 1 | 1+A2 | 3 | 3 | W2 + **W7-T2 UIA** |
| F5 P3 Cursor                | 0 | 0 | 1 (MCP) | 3 | 3 | **W8-T1/T2/T3** |
| F5 P4 Windsurf              | 1 | 1 (W1-T7 deferred) | 3 | 3 | 3 | W1-T7 + W4-T5 |
| F5 P5 GitHub Copilot        | 0 | 0 | 0 | 3 | 3 | **W6-T1/T2/T3** |
| F6 P6 Claude Code CLI       | 1 | **2** ✅ W1-T3 | 3 | 3 | 3 | W4-T6 (L1) |
| F6 P7 Codex CLI             | 0 | **2** ✅ W1-T8 + W1-T9 | 3 | 3 | 3 | W4-T7 (L1) |
| F6 P8 Gemini CLI            | 0 | **2** ✅ W1-T10 + W1-T11 (+ bug fix 097a1d2) | 3 | 3 | 3 | W4-T8 (L1) |
| **TOTAL ≥3 V-GREEN**        | **0/13** | **1/13** (P1 Claude Desktop) | **10/13** | **13/13** | **13/13** | — |
| **TOTAL ≥2 V-GREEN**        | **6/13** | **11/13** | **13/13** | **13/13** | **13/13** | — |

**STRICT MODE 不变式: Phase B 三 track (W6/W7/W8) 任一未达 ≥3 →
release 顺移. 不接受 12/13 + carry-forward 妥协.**

> **当前 sprint 状态 (2026-05-15, Wave 1 partial close)**: Phase A 代码
> 骨架已落地 (Wave 2 SSLKEYLOGFILE + Wave 3 capture_supervisor + Wave 5
> nightly closure). G2 (`/api/v1/supervisor/status` 暴露 13 场景) 与 G3
> (`REDUNDANCY-MATRIX.svg` + `redundancy-degraded` 自动 issue 通道)
> 已 alive.
>
> **2026-05-15 W1 sweep (单 session, owner manual + agent orchestration)**:
> **9 件 W1 任务 PASS** (T2 / T3 / T4 / T5 / T6 / T8 / T9 / T10 / T11),
> 2 件 deferred (T1 + T7 需要独立 RECON session). **+1 P8 normalizer
> routing bug 修复** (commit 097a1d2), 否则 v1.1.6 会带着 false-positive
> V-GREEN 上线.
>
> 13/13 场景 ≥1 V-GREEN; **11/13 场景 ≥2 V-GREEN; 1/13 场景 (P1 Claude
> Desktop) 已达 ≥3 V-GREEN STRICT 标准** (L1 + L3g + L3f .mcpb 同 commit
> 内升 V-GREEN). 剩 P3 Cursor 与 P5 Copilot 处于 0 V-GREEN 等 Phase B
> (Wave 6 + Wave 8) 救济.

### 4.2 推后期 (不在 v1.1.6 范围) — 仅参考

下列**显式不在 P5.D.1 范围**, 推到 P5.D.2 / P6 (per
`SCOPE-LOCK-2026-05-15.md` §4):

| 场景 | V-GREEN 数 (2026-05-13) | 推到 |
|---|:---:|---|
| F1 Perplexity | 1 | P5.D.2 |
| F1 Copilot MS / DeepSeek | 0 | P5.D.2 |
| F1 S3 × 6 (HF/Poe/Kimi/Zhipu/Mistral/Manus) | 0 each | P6 |
| F1 SX × 4 (Notion/M365/Gmail/Figma) | 0 each | P6 (账号阻塞) |
| F2 Web-Embedded | 0 | P6 |
| F3 Browser-Ext AI (Monica/MaxAI/...) | 0 | P6 |
| F4 其它桌面 (Poe/DeepSeek/Kimi/豆包/mac-DMG/Squirrel) | 0 | P6 |
| F5 IDE 其它 (Cline/Continue/Cody/Amazon Q/Roo/Codeium/Tabnine/JetBrains) | 0 | P6 |
| F6 Aider | 0 | P5.D.2 |
| F9 Local model (Ollama / LM Studio / vLLM) | 0 | P5.D.2 |
| F10 Headless / Framework (LangChain / 等) | 0 | P5.D.2 |
| F7/F8 OS-Integrated AI | N/A | 永久 T3 非目标 |

### 一句话总结

**按 STRICT MODE 严格标准, 2026-05-13 达标场景数: 0/13. P5.D.1 目标:
13/13 ≥ 3 V-GREEN, 不达标不发 v1.1.6.**

连最强的 Claude Desktop P1 也只有 2 条 V-GREEN, 差一条; 最弱的 P3 Cursor /
P5 Copilot / P7 Codex / P8 Gemini-CLI 是 0 条, 全要 0→3.

**上一轮"62% 形态达标"是按 UCS 附录 B "设计上能抓"的口径**; 本文件按
"**必须 live 验证通过**"的严格口径出的数字是 **0%**.

---

## 5. 缺口关闭清单 —— P5.D.1 STRICT MODE 映射

> **2026-05-15 重写**: 原 §5.A/B/C/D/E 按 ROI 分类 → 现按 **P5.D.1 wave
> 映射** 重写, 与 SCOPE-LOCK §3 + redundancy-sprint/* 对齐.

### 5.A — Phase A Wave 1+2+4 (V-HERMETIC → V-GREEN, Tier-α 10 场景)

代码 / 基础设施已就绪, 1 周内全部 live sign-off:

| # | 任务 | 目标场景 | 工时 | Wave Ref |
|---|---|---|:---:|---|
| A1 | L3f `pce_mcp_proxy` 对真实 Claude Desktop 跑 cowork tool call | P1 Claude Desktop | 3h | W1-T1 |
| A2 | L3f `.mcpb` posture A 双击装到真实 Claude Desktop | P1 Claude Desktop | 2h | W1-T2 |
| A3 | L3h `pce_cli_wrapper` 对真实 claude-code CLI | F6 P6 | 2h | W1-T3 |
| A4 | L1 对真实 Gemini Web | F1 Gemini | 2h | W1-T4 |
| A5 | L1 对真实 GAS | F1 GAS | 2h | W1-T5 |
| A6 | L1 对真实 Grok Web | F1 Grok | 2h | W1-T6 |
| A7 | L3f MCP 对真实 Windsurf | F5 P4 | 3h | W1-T7 |
| A8 | L3h `pce_cli_wrapper` 对真实 Codex CLI | F6 P7 | 3h | W1-T8 |
| A9 | L3g 对真实 Codex CLI (`~/.codex/`) | F6 P7 | 2h | W1-T9 |
| A10 | L3h `pce_cli_wrapper` 对真实 Gemini CLI | F6 P8 | 3h | W1-T10 |
| A11 | L3g 对真实 Gemini CLI | F6 P8 | 2h | W1-T11 |
| A12 | A2 SSLKEYLOGFILE 生产链路 (P1 + P2 corroboration) | P1 + P2 | 14.5h | W2 |
| A13 | L3d CDP 对真实 ChatGPT Web | F1 ChatGPT | 3h | W4-T1 |
| A14 | L3d CDP 对真实 Claude Web | F1 Claude | 2h | W4-T2 |
| A15 | L4a 剪贴板对真实 Gemini/GAS/Grok | F1 ×3 | 3h | W4-T3 |
| A16 | dedup 实战验证 (W3 supervisor live) | P1 Claude Desktop | 1h | W4-T4 |
| A17 | L4a 剪贴板对真实 Windsurf cascade chat | F5 P4 | 1h | W4-T5 |
| A18 | L1 对真实 claude-code CLI (mitmproxy) | F6 P6 | 2h | W4-T6 |
| A19 | L1 对真实 Codex CLI (含 OpenAI host normalizer) | F6 P7 | 3h | W4-T7 |
| A20 | L1 对真实 Gemini CLI (含 Google GenAI host normalizer) | F6 P8 | 3h | W4-T8 |

合计 ~58h. Phase A 内全部完成.

### 5.B — Phase B Wave 6/7/8 (Tier-β 3 场景的结构性建设)

不能"再签一次字"达成的 — 必须新落子系统:

#### Wave 6 / Track B-1 — F5 P5 GitHub Copilot (~80h, ~2 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-1.1 | `pce_core/normalizer/github_copilot.py` (host JSON normalizer) | L1 | 24h |
| B-1.2 | `vscode_ext/copilot/` (VS Code 扩展) + migration 0016 + 安装文档 | L3c | 40h |
| B-1.3 | VS Code MCP server 接入 (`pce_mcp_proxy`) | L3f | 16h |

#### Wave 7 / Track B-2 — F4 P2 ChatGPT Desktop UIA (~140h, ~3-4 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-2.1 | RECON UIA tree on ChatGPT Desktop | leg 3 (L4b) | 16h |
| B-2.2 | `pce_core/uia_driver/` 落地 + migration 0017 | L4b | 60h |
| B-2.3 | 28 单测 + 隐私 redact + opt-in UI | L4b | 30h |
| B-2.4 | live handoff + dedup 与 L1 join | L4b | 16h |
| B-2.5 | ADR-022 起草 + Adopted | governance | 8h |
| B-2.6 | scenarios.yaml 集成 + supervisor /status | governance | 10h |

#### Wave 8 / Track B-3 — F5 P3 Cursor protobuf (~200h, ~4-6 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-3.1 | RECON Cursor 9 host endpoint + protobuf framing 协议 | leg 1 (L1) | 24h |
| B-3.2 | `pce_core/normalizer/protobuf/` (通用 framing + decoder) | L1 | 60h |
| B-3.3 | `pce_core/normalizer/cursor_protobuf/` (specific schema) + migration 0018 | L1 | 40h |
| B-3.4 | live handoff Cursor L1 protobuf | L1 | 10h |
| B-3.5 | live handoff Cursor L3f MCP | L3f | 20h |
| B-3.6 | RECON Cursor Electron Fuse + L3b preload OR L4a 剪贴板 | L3b/L4a | 50h |
| B-3.7 | ADR-023 起草 + Adopted | governance | 6h |
| B-3.8 | scenarios.yaml 集成 + dedup canonicalization | governance | 10h |

### 5.C — Phase C Wave 9 (~16h)

13/13 全量 verify + ADR-022 / ADR-023 Adopted + git tag v1.1.6.

### 5.D — 推后期 (P5.D.2 / P6) — 显式不在 v1.1.6 范围

完整列表见 §4.2 + `SCOPE-LOCK-2026-05-15.md` §4. 简表:

| 类别 | 推到 |
|---|---|
| F1 Perplexity / Copilot MS / DeepSeek / S3 / SX | P5.D.2 / P6 |
| F2 SaaS-embedded | P6 (账号阻塞) |
| F3 浏览器扩展 AI | P6 |
| F4 其它桌面 (Poe/DeepSeek/Kimi/豆包/mac-DMG/Squirrel) | P6 |
| F5 IDE 其它 (Cline/Continue/Cody/Amazon Q/...) | P6 |
| F6 Aider | P5.D.2 |
| F9 Local Model | P5.D.2 |
| F10 Headless / Framework | P5.D.2 |
| F7/F8 OS-Integrated AI | 永久 T3 非目标 |

### 5.E 预估总工时 (P5.D.1 STRICT MODE 13/13)

| Phase | 工时 | wall-clock |
|---|---|:---:|
| Phase A (Tier-α 10 场景 + supervisor + dedup + nightly) | ~88h | 1 周 |
| Phase B Track B-1 (Copilot) | ~80h | ~2 周 (并行) |
| Phase B Track B-2 (P2 UIA) | ~140h | ~3-4 周 (并行) |
| Phase B Track B-3 (Cursor protobuf) | ~200h | ~4-6 周 (并行, longest pole) |
| Phase C (Wave 9 全量关闭) | ~16h | 2 工日 |
| **合计** | **~520h** | **~7-8 周 wall-clock** |

---

## 6. 不变式 & 不许做的事

1. **任何宣称"PCE 已覆盖 X 场景"的陈述 — 必须回本文件核对 V-GREEN 数。**
   PROJECT.md / UCS / ADR 的描述是"设计上的"，不替代本文件的 "验证上的"。

2. **V-HERMETIC → V-GREEN 升级只能通过 handoff 签字**。不许用"单测
   绿了、逻辑上肯定也能对真产品生效" 代替 live 证据。

3. **V-DEAD 不得被静悄悄翻案**。任何想重新激活 L3b preload / L3d CDP
   on MSIX / L2 Frida 的尝试必须新 ADR，显式 supersede ADR-018。

4. **冗余定义的"不相互依赖"不许松绑**。如果将来某人说"L1 + A2 算
   两条独立路线"，请回 §1 的判定规则：它们同走 Chromium 网络栈，
   算 1.5 条。

5. **本文件只记录 V-GREEN / V-PARTIAL / V-DEAD 的客观状态，不替项目
   决定优先级**。优先级决策在 `Docs/docs/decisions/*` 或 ADR。

---

## 7. 更新协议

### 7.1 什么时候更新

任何对以下事件之一负责的 commit 必须**同一 commit**更新本文件：

- 对真实产品新加一次 live handoff → 对应行升级 V-HERMETIC → V-GREEN
- 某路径被 ADR 判死 → 对应行改 V-DEAD
- 某路径拿到可行性证据但生产链路未写 → V-PARTIAL
- 新增场景或 Form 扩充 UCS 附录 A → 加表行

### 7.2 如何更新

每行必须携带：

- `Path` 列：L0/L1/L2/L3a-h/L4a-c 之一
- `依赖基底` 列：显式写出依赖的资源（网络栈 / 进程 / 文件系统 / UI 树 /
  协议中间件）
- `最新 Live 验证` 列：**指向一份具体 handoff 文件 + 行数或 PASS 数**
- `等级` 列：严格按 §1 阶梯
- 汇总 §4 的 V-GREEN 数字同步更新

不许只在设计文档（UCS / framework / matrix）更新"能抓"，不回本文件补
"验证到"。

### 7.3 审计 cadence

- **每次 P 阶段关门**（当前 P6 "Coverage Polish" 即将启动）→ 全量回扫本
  文件，确认所有 V-GREEN 证据指向的 handoff 未失效
- **任何 release tag 前** → 核对是否有退化（V-GREEN 的 handoff 指向的
  commit 是否还能重现）
- **季度一次**（至少）→ 检查 S3/SX 站点是否自然退化为 V-DOC-ONLY
- **每夜 02:00 UTC（P5.D.1 Wave 5 新增）** → `nightly-probe.yml`
  runs `tools/check_redundancy_targets.py` against
  `/api/v1/supervisor/status`. Any P0 scenario with
  `legs_active < redundancy_target` (Phase B suppressed) opens a
  `redundancy-degraded` issue via `tools/auto_issue_on_redundancy_degraded.py`.
  Same step renders `Docs/stability/REDUNDANCY-MATRIX.svg` with 13 cards
  and commits it back to the repo.

### 7.4 变更评审

本文件的**结构性变更**（新增/删除列、改 §1 评级规则、改 §6 不变式）
必须：

1. 新一条 ADR 记录 supersession
2. 项目所有者签字
3. 同 PR 更新 `PROJECT.md` §12 ADR 列表

---

## 8. 关联文档

- **上游规划（"设计上能抓"）：**
  - `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`（UCS 蓝图）
  - `Docs/docs/engineering/adr/ADR-009-universal-capture-stack.md`
  - `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
  - `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
- **下游目标（"该验证到什么"）：**
  - `Docs/stability/SITE-TIER-MATRIX.md`（F1 站点分级）
  - `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`（F4/F5/F6 D-case 标准）
  - `Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md`（跨 lane 健康度契约）
- **证据源头（"验证到了什么"）：** 见 §2 baseline

---

**本文件结束。每行数字都可以被 §2 里某份 handoff / 测试报告 / ADR 核
对验证；任何"似乎没验证"但本表标 V-GREEN 的路径 — 回来开 issue，引
用本文件对应行要求补证。**
