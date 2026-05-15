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

| 等级 | 含义 | 算 ≥3 invariant 吗 |
|---|---|:---:|
| **V-GREEN** | 对**真实产品**跑过端到端 **+ 被动 / 自动 / 完整对话抓取**（不依赖用户为每条消息做手动动作）+ **无 ToS / 封号风险** (服务端探测不到, 或厂商明确允许), 有具体 `raw_captures` / `messages` 行数或 handoff 签字作为证据 | ✅ |
| **V-GREEN-C** *(新, 2026-05-15, "with-compliance-caveat")* | 同 V-GREEN 的捕获质量 + 已有 live 证据, **但路径本身带有可观察的合规/封号风险敞口** (如 mitmproxy 的 TLS fingerprint 跟正常 Chrome 可区分 → Cloudflare bot manager 在收紧时可能识别). 计入 invariant 但**ship gate 必须配套一条 V-GREEN-clean leg**避免单点风险. | ✅ (有条件) |
| **V-AUX** *(新, 2026-05-15)* | 对真实产品有证据，但**需要用户为每条消息做主动操作触发**（剪贴板复制、人工导出、手动截图、人工 `pce_capture` MCP 调用）。提供辅助信号 / 监控能力, **不替代被动 leg** | ❌ 不计入 invariant (但仍是合法记录) |
| **V-HERMETIC** | 只有 mock / fixture / monkeypatched discovery 的单元或集成测试绿；代码可能工作但从未对真实产品验证 | 🟡 只能算"基础设施就位" |
| **V-PARTIAL** | 对真实产品跑过但只通过一部分（如用户侧通、助手侧挂；或可行性证明但生产链路未写） | 🟡 半条 |
| **V-DOC-ONLY** | adapter / config 文件存在但无任何测试或 handoff 证据 | ❌ |
| **V-STUB** | 代码自己声明 "NOT READY" / "scaffolding only" / "SX" | ❌ |
| **V-DEAD** | 实测失败且被 ADR 永久排除 | ❌ |

### 1.0 为什么 V-GREEN-C 需要从 V-GREEN 拆出 (2026-05-15 amendment)

P5.D.1 sprint 走到 W4 时, owner 提出合规性 / 封号风险审视:

> "如果这个能力要求用户改变日常的行为我不能接受 ... 也许我们需要自研一套路径而不是找现成的了"

L1 MITM (mitmproxy) **当前是主要捕获路径** (DB 占比 43%, 16K+ rows), 但
工程现实下它**带有可观察的服务端风险敞口**:

| 检测维度 | mitmproxy 在 Chromium TLS handshake 上的破绽 |
|---|---|
| TLS fingerprint (JA3/JA4) | Python `cryptography` 库实现 ≠ BoringSSL (Chrome) |
| HTTP/2 帧顺序 | mitmproxy 重组帧时不一定保留原始顺序 |
| ALPN 协商列表顺序 | 不完全跟 Chrome 一致 |
| Cipher suite 顺序 | Chrome 有自己的 ClientHello suite 顺序, mitm 用 cryptography 默认顺序 |
| Extension 列表 | Chrome 的 `extensions` 字段顺序 / 内容跟 mitm 不同 |

经验数据 (2026-05-15 PCE 仓库): L1 MITM 跑了 8 周, **没有出现封号**.
但 Cloudflare 等 bot manager 随时可以收紧策略, **未来风险存在**.

A2 SSLKEYLOGFILE (后续的"自研路径") **没有这些破绽**: 它不接管 TLS
handshake, 服务器看到的是 Chrome 自己的真实 ClientHello, 跟其他正常
用户不可区分; 我们只是离线用 keys 解密自己已经合法接收的字节.

为避免"V-GREEN" 这个标签在两种风险敞口下混用造成误导, 拆等级:

- **V-GREEN** = 捕获质量 OK + 无合规风险 (e.g. L3a 浏览器扩展 / L3g 本地
  落盘 / A2 SSLKEYLOGFILE / L3h CLI wrapper / L3f MCP / L4b UIA)
- **V-GREEN-C** = 捕获质量 OK + **可观察合规风险** (目前只有 L1 MITM 一条)

**ship gate 规则**: 13 P0 场景的 ≥3 V-GREEN 计算时 V-GREEN-C 计入,
**但任何场景的 leg 集合不允许只有 V-GREEN-C 一条** (因为 L1 MITM 是单点
合规风险, 需要至少 1 条 V-GREEN-clean leg 配套).

P5.D.1 Phase 1 加项: Wave 2 完成 A2 SSLKEYLOGFILE 生产链路 (~14h), 给
所有 Chromium-based 场景补一条 V-GREEN-clean leg 作为 L1 MITM 的合规
替代; Phase 2 L4b UIA daemon (~1-2 周) 给 Web Tier-α 补第 4 条 leg.

### 1.1 为什么 V-AUX 不计入 ≥3 invariant

≥3 invariant 的语义是：**任何一条腿单独挂掉，其余还能完整抓住对话**。

V-AUX 不满足这个语义：

| 测试 | L1 / L3a / L3g (V-GREEN) | L4a 剪贴板 (V-AUX) |
|---|:---:|:---:|
| 用户跑了一轮 10-turn 对话，没做任何额外动作 | 10 messages 全部入库 | **0 messages** (用户没复制就什么都没有) |
| 用户跑了一轮但只复制了最终答案 | 10 messages 全部入库 | 1 messages (片段, 没上下文) |
| 抓取依赖什么 | 网络栈 / DOM / 文件系统 (被动基础设施) | **用户的注意力 + 习惯** |

因此 L4a 剪贴板 (和未来任何"人工导出 / 手动截图 / 人工 pce_capture
工具调用"类的路径) 归 V-AUX, 作为**辅助信号**与监控能力存在,
**不替代**被动抓取腿。

### 1.2 "不相互依赖" 的判定规则（严格, 2026-05-15 重订）

正交基底分类 (5 个):

1. **网络栈** — TLS 字节的读位置 (mitmproxy 解密后 / Npcap 抓包 + 离线解密)
2. **DOM** — 浏览器渲染层 (扩展 content script + fetch shim)
3. **落盘** — 文件系统持久化 (jsonl / SQLite / IndexedDB)
4. **UI 树** — 渲染后窗口的 accessibility tree (UIA / AT-SPI)
5. **协议中间件** — MCP / Gateway / CLI shim (协议层而非传输层)

#### 1.2.1 L1 + A2 共算 2 条独立 leg (2026-05-15 修订, 从原 1.5)

原规则: "同走 Chromium 网络栈（L1 MITM + A2 SSLKEYLOGFILE）→ 算 1.5 条"

修订理由 (8 个 failure mode 里 6 个相互独立):

| 失败模式 | L1 MITM | A2 SSLKEYLOGFILE |
|---|:---:|:---:|
| mitmproxy 进程崩 | ❌ 全挂 | ✅ 不影响 |
| 系统代理被关 | ❌ 全挂 | ✅ 不影响 |
| mitm CA trust 被吊销 | ❌ 全挂 | ✅ 不影响 |
| Cloudflare TLS fingerprint 拦 | ❌ 全挂 | ✅ 不影响 (Chrome 真 handshake) |
| Npcap driver 卸载 | ✅ 不影响 | ❌ 挂 |
| SSLKEYLOGFILE env var 丢 | ✅ 不影响 | ❌ 挂 |
| Chromium 不跑 | ❌ 全挂 | ❌ 全挂 (common cause, 罕见) |
| TLS 协议根本变 | ❌ 全挂 | ❌ 全挂 (common cause, 极罕见) |

读位置也根本不同:
- L1: 在 mitmproxy 进程**解密后**读字节
- A2: 在 NIC **加密**字节 + 进程外读 keys → 异地解密

工程现实下这是 2 条独立 leg, 不是 1.5.

#### 1.2.2 其他独立性规则 (沿用)

- 都被同一个 Electron Fuse 锁住（B1 NODE_OPTIONS + `--inspect`）→ 算
  同源
- 都需要 mitmproxy CA trust → 并列但依赖同一前提
- **一条走网络栈 / 一条走 DOM / 一条走落盘 / 一条走 UI 树 / 一条走
  协议中间件 → 真正正交, 5 条都算**

剪贴板**不在以上正交分类内**(它走"用户行为"维度), 归 V-AUX 不算 leg.

> **这个卡法比 UCS 附录 B 的 ✅/🟢 路由矩阵严格，因为附录 B 只
> 问"能不能抓"，不问"依赖是否隔离 + 是否验证 + 是否被动抓取"。**

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
| 2 | L1 MITM | 系统代理 + mitmproxy CA + Chromium 网络栈 | `pce_proxy/addon.py` + `ALLOWED_HOSTS`（含 `chatgpt.com` + `chat.openai.com`） | `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 2026-05-12：13 raw_captures + 2 messages + session `37ddb011…` + model `gpt-5-3` | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0 合规风险敞口, 待 A2 上线后降为 secondary)* |
| 3 | L3d CDP | Chromium Debug Protocol | `pce_core/cdp/driver.py` + `tests/test_cdp_driver.py` (28 tests) | 无对 chatgpt.com 的 live handoff | **V-HERMETIC** |
| 4 | L4a 剪贴板 | 用户主动复制 | `pce_core/clipboard_monitor.py` + `tests/test_clipboard_monitor.py` | AI 特征检测单测绿；无 chatgpt live handoff | **V-AUX** *(2026-05-15 重分类, 不计入 leg)* |

**有效 V-GREEN：2。差 1 条达标。**

#### F1-S0 Claude Web (`claude.ai`)

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | DOM | best-of **16/21 PASS** (CLAUDE-FULL-COVERAGE Part VII 2026-04-30 + probe 2026-05-08) | **V-GREEN** |
| 2 | L1 MITM | TLS + CA | `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`：136 rows / chat send 5238 B / SSE 3106 B / model `claude-haiku-4-5-20251001` / 1 session + 2 messages / 0 pipeline_errors | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L3d CDP | Chromium Debug | 同 chatgpt hermetic | **V-HERMETIC** |
| 4 | L4a 剪贴板 | 用户复制 | 无 claude-specific live | **V-AUX** *(2026-05-15 重分类, 不计入 leg)* |

**有效 V-GREEN：2。差 1 条达标。**

#### F1-S1 Gemini Web (`gemini.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **17/21 PASS**（GEMINI-FULL-COVERAGE Part VII 2026-04-30: 10 PASS / 9 SKIP / 2 FAIL；probe 2026-05-08 追加） | **V-GREEN** |
| 2 | L1 MITM | `HANDOFF-W1-T4-GEMINI-WEB-L1-2026-05-15.md` 2026-05-15: 155 captures (proxy-default 含 SSE response 667 KB; browser-ext 含 12 google-web sessions / 112 msgs), prompt `What is 2+2?` live-verified | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L3d CDP | 无 | **V-HERMETIC** |

**有效 V-GREEN：2 (2026-05-15 W1-T4 升级)。差 1 条达标 (L3d 待 Wave 4)。**

#### F1-S1 Google AI Studio (`aistudio.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **13/21 PASS**（GOOGLE-AI-STUDIO-FULL-COVERAGE Part VII）+ `HANDOFF-W1-T5-GAS-L1-2026-05-15.md` 2026-05-15: GAS conversation session emitted with model `Gemini Flash-Lite Latest`, message_count=2 ("What is 2+2?" → "2 + 2 = 4 ..."), title `The Sum Of Two Plus Two | Google AI Studio` | **V-GREEN** |
| 2 | L1 MITM | allowlist 有, 但 W1-T5 sweep 期间 L3a 浏览器扩展抢先抓到 (DOM 比 SSE 完成更快), L1 leg 仍**无独立 live handoff**. 后续 sweep 需把代理设到 mitmproxy 后**禁用浏览器扩展**才能孤立 L1 leg | **V-HERMETIC** |
| 3 | **L3d CDP launcher** | Chrome DevTools Protocol via `pce_core/cdp/driver.py` 28 hermetic tests GREEN; **W4-T1-extension 待签字** (与 ChatGPT/Claude Web 同批 sweep) | **V-HERMETIC** → V-GREEN W4-T1-ext |

**有效 V-GREEN：1。差 2 条达标 (L1 孤立 sweep + L3d CDP, 都需用户操作; clipboard 归 V-AUX 不计入 invariant)。**

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
| 2 | L1 MITM | TLS + CA | `HANDOFF-W1-T6-GROK-WEB-L1-2026-05-15.md` 2026-05-15: 10 caps incl. `/rest/app-chat/conversations/new` 9015 B (real chat send), xai-web session emitted with 2 messages | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L4a 剪贴板 AI 特征识别 | 用户复制 | 工具 ready (`scripts/clipboard_capture_oneshot.py`); 见 §1.1 重分类为 V-AUX (不计入 leg) | **V-AUX** *(2026-05-15 重分类)* |
| 4 | **L3d CDP launcher (Chrome --remote-debugging-port)** | Chrome DevTools Protocol | `pce_core/cdp/driver.py` 28 hermetic tests GREEN; **W4-T6alt 待签字** (Wave 4, 实际第 3 条 leg) | **V-HERMETIC** → V-GREEN W4-T6alt |

**有效 V-GREEN：2 (2026-05-15 W1-T6 升级), 目标 3 (P5.D.1 — L3d CDP 第 3 条 leg 待 Wave 4; L4a 剪贴板 V-AUX 不计入 invariant).**

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
| 1 | **L1 MITM (A1)** | 系统代理 + CA | **H2 PASS 2026-05-10**: 172 clean `anthropic.com` hits / 0 TLS errors | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 1' | L1 continued chat sweep | same | `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` + 4 续篇：合计 D00-D22 **19/22 PASS** (86%) 跨 5 sub-run | **V-GREEN-C**（与 #1 同基底） |
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
| 1 | L1 MITM | 2026-05-10 原结论"BLOCKED on WSS"；**2026-05-12 `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 推翻**：2 messages (user + assistant) / pair `6a9847ff4113…` / model `gpt-5-3` / PASS | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
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
| 3 | **L3b Electron preload** (Cursor Fuse 若未锁) **OR L3d CDP launcher** (Cursor 是 Electron 可用 --remote-debugging-port) | **W8-T3 待签字** (preload 选 A; 否则 L3d; **不再考虑 L4a 剪贴板**, 剪贴板归 V-AUX 不计入 leg 见 §1.1) | **V-DOC-ONLY** → V-GREEN W8-T3 |
| 4 | proto decoder | `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` 行 476 ⬜ → **W8-T1 落地** | **V-DOC-ONLY** → V-GREEN W8-T1 |

**有效 V-GREEN：0 (pre-sprint), 目标 3 (P5.D.1 Phase B Wave 8).**

#### F5 P4 Windsurf (`server.codeium.com` / `server.self-serve.windsurf.com`) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 gRPC | `WINDSURF-PRODUCT-MATRIX.md` §2 Stage 4 **live-verified**；`pce_core/normalizer/windsurf/*` 落盘；Cascade Chat + Thinking + Model + Management ✅ | **V-GREEN-C** *(2026-05-15 重分类: L1 gRPC 同 mitm 的 fingerprint 风险, 见 §1.0)* |
| 2 | L3f MCP | **W1-T7 待签字** (Wave 1) | **V-HERMETIC** → V-GREEN W1-T7 |
| 3 | **L3d CDP launcher** (Windsurf 是 Electron, 走 `--remote-debugging-port` attach) | `pce_app_launcher/` 已有 Electron CDP 框架; **W4-T5alt 待签字** (Wave 4, 实际第 3 条 leg, 不再用 L4a 剪贴板) | **V-HERMETIC** → V-GREEN W4-T5alt |
| (extra-1) | L4a 剪贴板 (Cascade chat 复制 AI 特征) | 工具 ready (`scripts/clipboard_capture_oneshot.py windsurf`); 见 §1.1 重分类为 V-AUX (不计入 leg) | **V-AUX** *(2026-05-15 重分类)* |
| (extra-2) | Devin ACP WebSocket | 数据库 schema 暂不支持 ws direction | **V-DOC-ONLY** (不计入 P0 leg) |

**有效 V-GREEN：1 (pre-sprint), 目标 3 (P5.D.1 Wave 1 MCP + Wave 4 L3d CDP).**

#### F5 P5 GitHub Copilot (VS Code) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (`api.githubcopilot.com`) | allowlist 有, normalizer 未实施 → **W6-T1 (Wave 6) 落 host JSON normalizer + live** | **V-HERMETIC** → V-GREEN W6-T1 |
| 2 | L3c VS Code 扩展 | 代码未实施 → **W6-T2 (Wave 6) 落地 (含 ADR-022 引用)** | **V-DOC-ONLY** → V-GREEN W6-T2 |
| 3 | L3f MCP (首选) **OR** L3d CDP (VS Code 是 Electron 可 attach, 备选) | **W6-T3 待签字** (不再考虑 L4a 剪贴板, 剪贴板归 V-AUX 见 §1.1) | **V-DOC-ONLY** → V-GREEN W6-T3 |

**有效 V-GREEN：0 (pre-sprint), 目标 3 (P5.D.1 Phase B Wave 6).**

#### Cline / Continue / Cody / Amazon Q / Roo Cline / Codeium / Tabnine / JetBrains plugin (推 P6)

**STRICT MODE: 不在 v1.1.6 范围, 推到 P6 Coverage Polish.**
全部 0 条 V-GREEN.

---

### 3.6 F6 · CLI AI Agent

#### F6 P6 Claude Code CLI — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.anthropic.com) | `HANDOFF-W4-T6-CLAUDE-CODE-L1-2026-05-15.md` 2026-05-15 08:39 UTC: claude CLI relayed via mitmproxy HTTPS_PROXY=:8080, 14 rows on api.anthropic.com via proxy-default; request body 106KB carries full prompt+tools context; pair_id `cdc9236e05e84cf4`. Caveat: SSE response body truncated to 3 B at proxy addon layer (separate hardening, commit ffecf5c 已修) | **V-GREEN-C** *(2026-05-15: CLI API-key 调用 fingerprint 风险低于 web 端浏览器, 但同走 mitm)* |
| 2 | L3g 持久化 (`~/.claude/projects/<cwd>/*.jsonl`) | `tests/test_local_persistence.py` 用 2026-05-11 Round 3 RECON 真实 fixture (38 行 JSONL); HANDOFF-P1-N-L1 有 L3g scan live 证据 | **V-GREEN** |
| 3 | L3h CLI wrap (`pce_cli_wrapper/`) | `HANDOFF-W1-T3-CLAUDE-CODE-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target claude.cmd -- -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=claude`, `target_version=2.1.139` | **V-GREEN** |
| 4 | L3e LiteLLM | 单测全 mock, 无 real LiteLLM subprocess | **V-HERMETIC** (备用, 不计入 P0 leg) |

**有效 V-GREEN：3** ✅ ≥3 达标 (L1 + L3g + L3h). **F6 P6 是 P5.D.1 13 P0 中第二个**达到 STRICT ≥3 V-GREEN 不相互依赖路线的场景 (继 F4 P1 Claude Desktop 之后). 2026-05-15 W4-T6 升级.

#### F6 P7 Codex CLI (OpenAI) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.openai.com) | `HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md` 2026-05-15: codex CLI (Rust binary) **完全绕开 HTTPS_PROXY env var**, 不接受 mitmproxy CA; mitmdump log 显示 ZERO 流量到 api.openai.com 在 codex run 期间; 1-2 day 工程修复 (Rust TLS injection 或 transparent proxy) | **V-HERMETIC** (deferred to P5.D.1 backlog) |
| 2 | L3g 持久化 (`~/.codex/sessions/*.jsonl`) | `HANDOFF-W1-T9-CODEX-CLI-L3G-2026-05-15.md` 2026-05-15: 24 codex-cli-l3g sessions / 255 messages / `gpt-5-codex` model_names persisted via `pce_persistence_watcher.ide_scanner._scan_codex`; `host=local-codex-cli` confirmed | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T8-CODEX-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target codex.cmd -- exec --sandbox read-only "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=codex` (after `codex login` ChatGPT OAuth) | **V-GREEN** |

**有效 V-GREEN：2 (2026-05-15 W1-T8 + W1-T9 升级), 目标 3 (P5.D.1 — L1 leg blocked by Rust TLS bypass, see W4-T7/T8 deferred handoff).**

#### F6 P8 Gemini CLI (Google) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (generativelanguage.googleapis.com) | `HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md` 2026-05-15: gemini CLI (Node 22+ undici fetch) **不读 HTTPS_PROXY env var on Windows**; CLI 跑成功 ("2 + 2 is 4.") 但 ZERO 流量到 generativelanguage.googleapis.com 经 mitmproxy; 需要 explicit `ProxyAgent` injection (Node) 或 transparent proxy | **V-HERMETIC** (deferred to P5.D.1 backlog) |
| 2 | L3g 持久化 | `HANDOFF-W1-T11-GEMINI-CLI-L3G-2026-05-15.md` 2026-05-15: 1 fresh `gemini-cli-l3g` session emitted (`b1e5c0a8-...`), 2 messages [user "What is 2+2?", assistant "2 + 2 = 4"], `model_names=["gemini-3-flash-preview"]`. **加发现并修复了一个 routing bug** (`local-gemini-cli` 未列在 `normalize_conversation` 的 L3g host 白名单内, 导致 P8 自 a5d09f5 起其实是 false-positive; commit 097a1d2 修复 + 加 regression test) | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T10-GEMINI-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target gemini.cmd -- --skip-trust -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=gemini` | **V-GREEN** |

**有效 V-GREEN：2 (2026-05-15 W1-T10 + W1-T11 升级 + bug fix), 目标 3 (P5.D.1 — L1 leg blocked by Node undici proxy bypass, see W4-T7/T8 deferred handoff).**

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

> **2026-05-15 重大修订 (2 次)**:
> 1. **V-AUX 拆分**: clipboard (L4a) 因用户主动行为副产品归 V-AUX, 不计 invariant.
> 2. **V-GREEN-C 拆分**: L1 MITM 因 mitmproxy TLS fingerprint 跟正常 Chrome 可区分, 带封号合规风险敞口, 拆 V-GREEN-C (见 §1.0). 计入 invariant **但 ship gate 要求每场景至少 1 条 V-GREEN-clean leg**.
> 3. "关键 wave" 列同步换成 **Phase 1 (Wave 2 A2 SSLKEYLOGFILE)** 作为合规 clean leg, **Phase 2 (L4b UIA daemon)** 作为长期 4th leg.

| 场景 | pre-sprint | 2026-05-15 (W1 close, 拆完 V-GREEN-C) | Phase 1 target (Wave 2 A2) | Phase 2 target (L4b UIA) | final | 关键 leg 来源 |
|---|:---:|:---:|:---:|:---:|:---:|---|
| F1 ChatGPT Web              | 2 (1c+1g) | 2 (L3a + L1c) | **3** (+ A2) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 Claude Web               | 2 (1c+1g) | 2 (L3a + L1c) | **3** (+ A2) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 Gemini Web               | 1 (1c) | 2 (L3a + L1c via W1-T4) | **3** (+ A2) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 GAS                      | 1 (1c) | 1 (L3a; L1 hermetic) | **2** (+ A2; L1 sweep 仍 pending) | 3 (+ UIA) | ≥3 | A2 + UIA |
| F1 Grok Web                 | 1 (1c) | 2 (L3a + L1c via W1-T6) | **3** (+ A2) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F4 P1 Claude Desktop        | 2 (1c+1g) | **3** (L1c + L3g + L3f MCP via W1-T2) ✅ | 4 (+ A2 替换 L1) | 4 | ✅ | (已达标) |
| F4 P2 ChatGPT Desktop       | 1 (1c) | 1 (L1c) | **2** (+ A2) | 3 (+ UIA) | ≥3 | A2 + UIA / Wave 7 |
| F5 P3 Cursor                | 0 | 0 (L1 partial protobuf undecoded) | 0 | 0 | **Phase B Wave 8** | Wave 8 (L1 protobuf decoder + MCP + L3g) |
| F5 P4 Windsurf              | 1 (1c) | 1 (L1c, W1-T7 MCP pending) | **2** (+ A2 替换 L1c) + W1-T7 MCP → 3 | 4 (+ UIA) | ≥3 | A2 + W1-T7 MCP |
| F5 P5 GitHub Copilot        | 0 | 0 | 0 | 0 | **Phase B Wave 6** | Wave 6 (L1 + L3c + MCP) |
| F6 P6 Claude Code CLI       | 1 | **3** (L3h + L1c + L3g, W1-T3 + W4-T6) ✅ | 4 (+ A2 替换 L1c) | 4 | ✅ | (已达标) |
| F6 P7 Codex CLI             | 0 | 2 (L3h + L3g, L1 Rust proxy blocked) | 2 (Rust 不写 keylog, A2 不适用) | **3** (+ UIA daemon 抓 terminal output) | ≥3 | UIA / Frida (Pro) |
| F6 P8 Gemini CLI            | 0 | 2 (L3h + L3g, L1 Node undici blocked) | **3** (Node 11+ 写 keylog → A2 适用) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE (Node) |
| **TOTAL ≥3 (含 V-GREEN-C)** | **0/13** | **2/13** | **9/13** (P1 + P6 + 5 Web + Windsurf via MCP) | **12/13** (+ P2 / P7 via UIA) | **13/13** | — |
| **TOTAL ≥1 V-GREEN-clean**  | n/a | **2/13** (P1 P6, 不含 L1c) | **12/13** (A2 一次覆盖 Chromium 全家) | 13/13 | **13/13** | — |
| **TOTAL ≥3 (V-GREEN-clean only, 不含 L1c)** | **0/13** | **2/13** | **6/13** (Web 3 + Windsurf 没靠 L1c) | 12/13 (Web 加 UIA) | **13/13** | — |

> **legend**: "1c" = V-GREEN-C (L1 MITM); "1g" = V-GREEN clean leg; "A2" = SSLKEYLOGFILE (V-GREEN clean).
> Phase 1 (Wave 2) 把 5 个 Web + 2 个 Desktop + 1 个 IDE + 1 个 CLI 总共 **9 个场景**从 "依赖 L1c" 升级到 "L1c + A2 双保险, 即使 L1c 被合规风险拿掉仍 ≥2 V-GREEN-clean".

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
> 2 件 deferred (T1 + T7 需要独立 RECON session). **+3 真 bug 修复**:
> (1) commit 097a1d2 — P8 Gemini L3g normalizer routing (否则 P8 自
> a5d09f5 起一直是 false-positive V-GREEN), (2) commit a1b4aa2 —
> ConversationNormalizer 1-char 助手内容被丢, (3) commit ffecf5c —
> **proxy streaming hook 没解 gzip 把 SSE 响应存成 3 byte 乱码** (影响
> 范围最大, 涉及所有走 Content-Encoding: gzip 的 chat SSE).
>
> **2026-05-15 二次对齐 V-AUX 重分类**: clipboard L4a 从 leg 降为
> 辅助证据等级 (见 §1.1). 之前几版表里 W4-T3 / W4-T5 / W6-T3 把
> clipboard 算成 leg 是误读, "关键 wave" 列同步剔除 L4a 改用 L3d
> CDP / L3f MCP / L3b preload 等真被动腿.
>
> **现状**: 13/13 场景 ≥1 V-GREEN; **11/13 场景 ≥2 V-GREEN; 2/13 场景
> 达 ≥3 STRICT 标准** (F4 P1 Claude Desktop: L1+L3g+L3f .mcpb;
> F6 P6 Claude Code CLI: L1+L3g+L3h wrapper). 剩 11 个场景的第 3 条腿
> 都是被动腿 (L3d CDP / L3f MCP / L3c VS Code ext / L3b preload), **没
> 有任何一个**依赖剪贴板.

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
| A15 | **L3d CDP 对真实 Gemini Web / GAS / Grok Web** (原 W4-T3 L4a 剪贴板降级为 V-AUX 不计 leg, 改用 L3d 作为真第 3 条腿) | F1 Gemini / GAS / Grok | 3h | W4-T3-alt |
| A16 | dedup 实战验证 (W3 supervisor live) | P1 Claude Desktop | 1h | W4-T4 |
| A17 | **L3d CDP 对真实 Windsurf** (Windsurf 是 Electron, 走 `--remote-debugging-port`; 原 W4-T5 L4a 剪贴板降级为 V-AUX) | F5 P4 | 1h | W4-T5-alt |
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
