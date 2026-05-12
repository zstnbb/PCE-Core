# PCE Standard Workflow — 产品 / 捕获面接入生命周期

> **Status**: canonical (adopted 2026-05-12).
>
> **Version**: v1.0
>
> **Audience**: 下一任 Cascade、接手 agent、外部 contributor、任何被 owner 派去"接 X 这个产品"的执行者。
>
> **Authority chain**:
> - 上承认知层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\UNIVERSAL-CAPTURE-STACK-DESIGN.md` (UCS 5 层) + `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` (4 plane × 6+1 type)
> - 上承基础设施层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` (跨 lane 三件套契约 + Test Conductor)
> - 上承策略层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md`
> - 下指执行层: 各 handoff 文件 (`Docs/handoff/`) + 各 PRODUCT-MATRIX.md
>
> **本文件提供**: 一份 6 阶段时序图,每阶段输入 / 工具 / 产物 / 完成标准 / 产物模板; 一张跨阶段编号速查表; 一个跑通过的实例; 反模式清单; 5 分钟自检清单。
>
> **本文件不提供**: 任何具体产品的捕获细节(那是 PRODUCT-MATRIX 的事), 任何具体 ADR(那是 ADR/* 的事), Test Conductor 协议(那是 META-PIPELINE-FRAMEWORK 的事)。
>
> **何时读它**: 新 agent 接到"做 X 这个产品" / "修 X 这个 bug"任务时,**先读本文 60 秒**确认在 lifecycle 哪一阶段,再去翻具体执行 doc。
>
> **何时改它**: 当 PCE 跑完一个完整产品周期、发现现行模板有漏洞时,update 本文(不要拷一份新的)。

---

## 0. TL;DR — 60 秒定位

PCE 接一个新捕获面(新产品 / 新 region / 新 UCS 层)走 **6 阶段**:

```
(0) 决策      → 决定接不接 / 接哪个 tier (S0-SX)
(1) Desk      → 爬官网 + 既有研究, 形成假说
(2) RECON     → 探针实地侦察, 验证或推翻假说
(3) 标准      → 把实测形状固化成 PRODUCT-MATRIX + (可选) ADR
(4) 闭环      → 写 adapter / driver / normalizer / 测试, 把矩阵 tile 翻绿
(5) 稳定      → Nightly + Health beacon + Conductor 守护, 不让别的 lane 倒退
```

**你现在在哪一阶段?** — 三步自检:

1. `git log --oneline -10` 看最近 commit subject 头几个字 (`recon` / `fix` / `docs(stability)` / `chore(tag)`) 对应阶段
2. `ls Docs/handoff/` 找最新一份 handoff, 标题里有 `KICKOFF` = 在阶段 0-1, `RECON` = 阶段 2, `FULL-SWEEP / SKIP-CONVERSION` = 阶段 4, `COMPLETION` = 阶段 5
3. 找到当前阶段 → 跳到本文对应 § (§3 = 决策 / §4 = Desk / §5 = RECON / §6 = 标准 / §7 = 闭环 / §8 = 稳定)

**你下一步该做什么?** — 每个阶段的 §x.5 都列了"完成标准"(checklist), 没打完不进下一阶段。

---

## 1. 流程总览图

```
┌───────────────────────────────────────────────────────────────────┐
│  ┌─────────────┐                                                   │
│  │ (0) 决策    │  产物: PRODUCT-MATRIX.md 新 § 草稿 + Tier 标记     │
│  └──────┬──────┘                                                   │
│         │                                                          │
│         ▼                                                          │
│  ┌─────────────┐                                                   │
│  │ (1) Desk    │  产物: Docs/research/<date>-<product>-desk.md     │
│  │   Research  │                                                   │
│  └──────┬──────┘                                                   │
│         │                                                          │
│         ▼                                                          │
│  ┌─────────────┐                                                   │
│  │ (2) RECON   │  产物: Docs/research/<date>-<product>-recon-     │
│  │             │       findings.md + Open Questions Q1..QN         │
│  └──────┬──────┘                                                   │
│         │                                                          │
│  ┌──────┘──────────┐ (回炉)                                        │
│  │                 │ Q 拿不下 + 改假说 → 回 Desk                    │
│  ▼                 ▲                                                │
│  ┌─────────────┐   │                                                │
│  │ (3) 标准    │───┘  产物: matrix § 表 + acceptance grid           │
│  │             │     + (可选) ADR-NNN                                │
│  └──────┬──────┘                                                   │
│         │                                                          │
│         ▼                                                          │
│  ┌─────────────┐                                                   │
│  │ (4) 闭环    │  产物: adapter + driver + normalizer + tests       │
│  │   M1..Mn    │  迭代 cycle 直到矩阵 tile ≥ 75% 翻绿                │
│  │   iter      │                                                   │
│  └──────┬──────┘                                                   │
│         │                                                          │
│         ▼                                                          │
│  ┌─────────────┐                                                   │
│  │ (5) 稳定    │  产物: tag + release notes + handoff               │
│  │             │  + ADR-011 G1..G9 全绿                              │
│  └─────────────┘                                                   │
│                                                                    │
└───────────────────────────────────────────────────────────────────┘
```

**关键不变式**:

- **不允许跨阶段**: 没出 RECON findings 就不写 matrix; 没出 matrix 就不写 adapter; 没绿测试就不发 tag。
- **允许回炉**: 任何阶段实测发现假说错, 回到上游阶段重写, 不要在下游打补丁。
- **允许并行 lane**: 同一时刻不同产品可在不同阶段(Claude Desktop 在阶段 5 的同时 ChatGPT Desktop 可以在阶段 1)。

---

## 2. 编号约定速查 (新 agent 必看)

PCE 文档里到处是 `E10` / `D04` / `Q2` / `G9` / `S0` / `H` / `L3a` 这种短码。下面是**全部的解码表**,见到不认识的回这里查。

### 2.1 测试用例 (case-as-data)

每个 case 是 `<前缀><两位数字>`。前缀按**产品 / region** 分:

| 前缀 | 含义 | 编号区间 | runner | 标准 doc |
|---|---|---|---|---|
| **T** | **T**ier 1 浏览器站点 (ChatGPT web / Claude web / Gemini / ...) | T01-T50 | `tests/e2e_probe/` | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` |
| **D** | Claude **D**esktop **Chat** region (主聊天) | D01-D22 | `tests/e2e_desktop_ui/` | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` |
| **C** | Claude Desktop **C**owork region | C01-C16 | 同上 | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.B |
| **E** | Claude Desktop Cod**E** region (inline /code tab) | E00-E25 | 同上 | 同上 §5.C |
| **F** | 跨 region / 跨 lane (e.g. F-D13 = D-case 也是 F-case) | F00+ | 多 runner | 各 matrix |
| **K** | C**K**LI lane K-case (待定义) | — | `tests/e2e_l3h/` | TBD |
| **M** | **M**CP lane M-case (待定义) | — | `tests/e2e_mcp/` | TBD |

**例**: `E10` = Claude Desktop Code-region 第 10 号测试 = 默认权限模式下的 UIA 权限对话框测试。

### 2.2 站点 Tier (S0-SX)

定义于 `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-TIER-MATRIX.md` + `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-COVERAGE-DIFF-TEMPLATE.md`:

| Tier | 含义 | 标准产物 | 例 |
|---|---|---|---|
| **S0** | 日常必备 daily driver | FULL spec (~200-300 LOC, Parts I-VI, ~20 T-cases) | ChatGPT, Claude |
| **S1** | 高频高价值 | FULL spec 同形 | Gemini, Google AI Studio, Perplexity |
| **S2** | 中等价值 | DIFF spec (~40 LOC, 8-12 T-cases, diff-from-S0) | Copilot, Grok, DeepSeek |
| **S3** | 长尾烟测 | smoke (~15 LOC, 3-5 T-cases) | HuggingFace, Poe, Kimi |
| **SX** | scaffolding, account-gated, DOM unverified | STUB + 待办 (no runner) | M365 Copilot, Notion AI |

### 2.3 UCS 层 (L0-L4)

定义于 `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-009-universal-capture-stack.md`:

| 层 | 含义 | 实现 (open-core) |
|---|---|---|
| **L0** | Kernel-mode (ETW / dtrace) | Pro repo 独占 |
| **L1** | TLS MITM (mitmproxy) | `pce_proxy/` |
| **L2** | Frida runtime hook | Pro repo |
| **L3a** | 浏览器扩展 | `pce_browser_extension_wxt/` |
| **L3d** | CDP (Chrome DevTools Protocol) | `pce_proxy/cdp_*.py` |
| **L3f** | MCP middleware | `pce_mcp_proxy/` + `pce_mcp/` |
| **L3g** | 本地持久化 watch (JSONL / SQLite) | `pce_persistence_watcher/` |
| **L3h** | CLI PATH-priority shim | `pce_cli_wrapper/` |
| **L4b** | UI Automation (UIA) | `tests/e2e_desktop_ui/drivers/` |

### 2.4 Plane (N/H/M/U)

定义于 `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`:

- **N** — 网络 (network, mitmproxy / CDP)
- **H** — 进程 (host, L3a/L3d/L3g/L3h)
- **M** — MCP (model context protocol middleware)
- **U** — UI (UIA / DOM / accessibility)

### 2.5 ADR-011 Gates (G1-G9)

定义于 `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-011-probe-remote-agent-autonomous-testing.md`:

| Gate | 含义 | 状态 (v1.1.5 时点) |
|---|---|---|
| **G1** | Capture-as-Data v2 contract | ✅ Adopted |
| **G2** | Verification-as-Data (case manifest) | ✅ Adopted |
| **G3** | Nightly auto-probe CI | ✅ Adopted (P5.C.3) |
| **G4** | Health beacon | ✅ Adopted (P5.C.1) |
| **G5** | Patches-as-Data (not action) | ✅ Adopted |
| **G6** | Canary snapshot + diff | ✅ Adopted (P5.C.2) |
| **G7** | Auto-issue on probe FAIL | ✅ Adopted (P5.C.3) |
| **G8** | Adapter manifest YAML | ✅ Adopted (P5.C.4.2) |
| **G9** | LLM-refined selector repair | ✅ Adopted (P5.C.4.3) |

**ADR-011 9 gate 全绿 = PCE 进入 maintenance phase**, 这是 v1.1.5 ship 标志。

### 2.6 Open Questions (Q1..QN)

每个 RECON findings doc 末尾会留 `Open Questions` section, 编号 `§X.Y.Z Q<n>`。这些是 RECON 没拿下来、但**不卡 ship** 的问题, 走 carry-forward 流程到下一阶段。

**例**: `MATRIX §5.C.2 Q2` = Code-region matrix 第 §5.C.2 节的第 2 个 open question = 权限对话框 UIA 名称未知, 由 E10 RECON 关闭。

---

## 3. 阶段 (0) — 决策 (Decision)

**目的**: 决定要不要接 / 接哪个 tier / 接哪些 UCS 层。

**输入**:
- owner 的一句话指令 (e.g. "做 ChatGPT Desktop")
- 现有 `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-TIER-MATRIX.md` (站点优先级)
- 现有 `@f:\INVENTION\You.Inc\PCE Core\Docs\research\AI-PRODUCT-LANDSCAPE-AND-CAPTURE-ANALYSIS.md` (产品全景)

**问 owner 的 3 个问题** (用 `ask_user_question` 工具):

1. **Tier**: S0 / S1 / S2 / S3 / SX? 这决定 spec 厚度 + 测试覆盖深度。
2. **优先级**: 顶在哪条 lane 后面? (Browser 在 v1.0 已 ship; Desktop 在 v1.1 ship; CLI / MCP 在 v1.2)
3. **范围**: 是单一 region (例: 只接 ChatGPT Desktop 的 chat) 还是全 region (chat + code + cowork)?

**工具**:
- `read_url_content` — 看产品官网首页判断"是否值得接"
- `search_web` — 找官方文档 / changelog
- `grep_search "<product>"` over Docs/research — 看有没有以前的研究痕迹

**产物**:
1. `Docs/stability/<PRODUCT>-PRODUCT-MATRIX.md` 新文件**草稿** (复制 `DESKTOP-PRODUCT-MATRIX.md` 结构当骨架, 各 § 全留 TBD)
2. `Docs/stability/SITE-TIER-MATRIX.md` 更新 tier 行
3. `Docs/handoff/HANDOFF-P<N>-<PRODUCT>-KICKOFF.md` 新 handoff (~150 LOC, 列下 4 阶段时间表)

**完成标准 (checklist)**:

- [ ] Tier 明确, 矩阵草稿落地, commit `docs(stability): <product> kickoff`
- [ ] Handoff 写明"下一个 agent 该做什么(开始 Desk Research)"
- [ ] Owner 在 commit / handoff 上回复"go"

**不要做**:
- 不写任何 adapter 代码
- 不跑任何探针
- 不写 ADR (除非引入了新的架构概念, 90% 的产品接入复用现有 ADR)

---

## 4. 阶段 (1) — Desk Research (官网调研)

**目的**: 在不动手抓包前, 通过公开信息建立对产品的**初步假说**: 数据流大概长什么样、有哪些 surface、哪些是公开 API 哪些是隐藏面。

**输入**:
- 产品官网 (homepage, pricing, features, blog, changelog)
- 产品官方文档 (API doc, MCP server doc, plugin doc)
- 产品 release notes / changelog (找最近 3 个月的功能上新)
- 第三方逆向资料 (e.g. SimonW 博客, Twitter/X dev community)

**工具**:
- `read_url_content` — 抓官网内容
- `search_web "<product> API capture WebSocket"` — 找 API 形态线索
- `grep_search "<product>"` over `pce_browser_extension_wxt/sites/` + `pce_core/sites/` — 看 web 版有没有已存在的 adapter (Desktop 经常与 web 共享 URL pattern / SSE 形状)
- `code_search "<product> normalizer"` — 看 normalizer 已经处理过哪些形状

**产物**:

新文件 `Docs/research/<YYYY-MM-DD>-<product>-desk-research.md`, 模板:

```markdown
# <Product> — Desk Research

> **Date**: YYYY-MM-DD
> **Status**: hypothesis (pre-RECON)
> **Next**: 阶段 (2) RECON, see [Open Questions §N]

## 1. 产品概览
- 厂商 / 主网址 / 客户端类型 (web / desktop / mobile / CLI)
- 主要 surface 列表 (e.g. ChatGPT: chat tab + custom GPT + canvas + GPT store + ...)
- 与已接入 PCE 的产品的相似度 (e.g. "Desktop 客户端用 WebView 套同一个 web app 吗?")

## 2. 数据流假说
- 网络: HTTP / WebSocket / SSE? endpoint 形状?
- 本地持久化: 有没有 JSONL / SQLite / config dir?
- MCP: 是否 MCP-aware? server endpoint 形态?
- UI: Electron / Tauri / 原生? UIA tree 能进吗?

## 3. 与 PCE UCS 层映射
- 主 UCS 层 (L1 / L3d / L3g / L3h / L4b)
- 辅助层 (验证用)
- 与已有 lane 是否冲突 (e.g. mitmproxy 端口能不能复用)

## 4. 与 cognitive framework plane 映射
- 主 plane (N / H / M / U)
- 哪些 plane 不适用 (e.g. CLI 产品没 U)

## 5. Tier 终决 (回顾阶段 0)
- 依据 desk 看到的复杂度, 是否需要调整 tier?

## 6. 探针 RECON 计划
- 候选探针 (mitmproxy snapshot / dump_uia / tiny probe)
- 每个探针的最小输入 (一条 prompt / 一次 navigation)
- 预期产物 (raw_captures 行数 / UIA tree dump / JSONL frame)
- 时间预算 (≤ X 小时)

## 7. Open Questions
- Q1: <尚未弄清的关键问题>
- Q2: ...
```

**完成标准 (checklist)**:

- [ ] 5-7 节都填了, Open Questions ≥ 3 条
- [ ] 至少 2 个候选探针被列入第 6 节
- [ ] commit `docs(research): <product> desk research`
- [ ] Owner / 下一个 agent 看完能直接动手跑探针

**陷阱**:
- ⚠️ **不要相信官方文档说的就是真的**。Anthropic / OpenAI 官方 doc 跟实际 API 经常对不上 — 这就是阶段 2 存在的原因。
- ⚠️ **不要在这一阶段写 PRODUCT-MATRIX 表格细节**。表格细节要等 RECON 结果, 现在写就是猜。

---

## 5. 阶段 (2) — 探针 RECON (Reconnaissance)

**目的**: 实地侦察, 验证 / 推翻阶段 1 的假说, 拿到**实测形状**。

**核心原则**: **每个假说都要有一个一手证据。** 一手 = mitmproxy 抓到的 raw_capture / UIA dump 的 element 行 / 一段实际跑的 stdout。不接受"我看官方 doc 说的"。

**三类探针** (按入手成本递增):

### 5.1 mitmproxy snapshot (网络面 N)

最便宜。流程:

1. 启动 `pce_proxy` 监听 (`python -m pce_proxy --mode tap`)
2. 配产品客户端走 8080 代理 + 安 CA cert
3. 让 owner 做一次最小操作 (登录 + 发一条 prompt)
4. 看 `~/.pce/database.sqlite` 的 `raw_captures` 行 — 那就是真实的请求 / 响应形状
5. 用 `python -m pce_core.cli replay --pair <pair_id>` 把抓到的 pair 在 normalizer 里走一遍, 看现有 normalizer 能不能就路过

**典型耗时**: 0.5-1 天 (含 cert install + bind issue troubleshoot)

### 5.2 dump_uia.py (UI 面 U)

```powershell
python -m tests.e2e_desktop_ui.scripts.dump_uia <mode>
```

已有 mode (见 `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\scripts\dump_uia.py`):

- `idle` — 默认 UIA tree 全 dump
- `hover-last` — 鼠标悬停最后一条消息后 dump (用于 hover-only 元素)
- `open-attach` / `open-style` / `open-model` — 点开 picker 后 dump
- `open-cowork` / `open-skills` / `open-dispatch` / `open-scheduled` / `open-customize` — Cowork-region 子面 dump
- `recon-permission` — E10 默认权限模式弹窗 dump (新加, see `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-E10-PERMISSION-DIALOG-RECON.md`)

新产品接入时, **如果它跑在桌面客户端**, dump 一份 `idle` mode tree 作为基线。

### 5.3 tiny probe script (任意面)

写一个 ≤ 50 LOC 的 `tools/recon_<product>.py`, 做**一件事**:

- 调一次客户端 IPC / 抓一个 JSONL frame / 跑一行 CLI 命令
- print 出原始结果
- 不存任何状态

**例**: `@f:\INVENTION\You.Inc\PCE Core\tests\manual\recon_claude_desktop_cowork.py` (52 LOC) 就是这种。

### 5.4 RECON 产物

新文件 `Docs/research/<YYYY-MM-DD>-<product>-recon-findings.md`, 模板:

```markdown
# <Product> — RECON Findings

> **Date**: YYYY-MM-DD
> **Status**: empirical (post-RECON)
> **Supersedes**: <date>-<product>-desk-research.md sec X (any hypothesis overturned)
> **Next**: 阶段 (3) write contract, see DESKTOP-PRODUCT-MATRIX-style section

## 1. 验证 / 推翻的假说
| 假说 (from desk §X) | 实测结果 | 证据 |
|---|---|---|
| <hyp 1> | confirmed/refuted | raw_captures 行 / UIA 行 / stdout 节选 |
| ... | | |

## 2. 实测数据流
- 网络: 实际 endpoint 列表 + 每个的请求/响应 schema (附 raw_capture 节选)
- 本地: 实际持久化路径 + 文件 schema (附 JSONL frame 节选)
- MCP: 实际中间件协议 frame (附 frame 节选)
- UI: 关键元素 UIA name / control_type / automation_id (附 dump 节选)

## 3. 与 UCS / plane / lane 的真实映射
更新阶段 1 §3-§4 的表格 — 用实测数据校准。

## 4. 推荐的 case-as-data grid
列 N 个最小 case 候选 (e.g. <prefix>01 vanilla chat / <prefix>02 file upload / ...), 不展开 acceptance, 只列名 + 一句话 trigger。

## 5. Open Questions (carry-forward)
- §X.Y Q1 <尚未关闭的问题, 不卡 ship, 走 carry-forward>
- ...
```

**完成标准 (checklist)**:

- [ ] 阶段 1 的每条假说都有对应"confirmed/refuted + 证据"行
- [ ] 实测数据流四个面都覆盖 (即使是 "N/A — 此产品没此面")
- [ ] 推荐了 ≥ 5 个 case 候选
- [ ] Open Questions 编号清晰, 每条标注是 carry-forward 还是 必须在阶段 3 之前关闭
- [ ] commit `docs(research): <product> recon findings`

**陷阱**:
- ⚠️ **不要在 RECON 阶段写 adapter**。RECON 是"看", 不是"做"。哪怕你看到 normalizer 漏处理一种形状, 也记到 Q 里, 阶段 4 再修。
- ⚠️ **不要在 RECON 写 acceptance criteria**。验收标准要等阶段 3 跟矩阵一起定。RECON 只列 case 候选名 + trigger。

---

## 6. 阶段 (3) — 写标准 (The Contract)

**目的**: 把 RECON 实测的"形状"固化为下游所有代码 / 测试要遵守的**合约**。一旦写完, 不允许"边写代码边改 spec"。

**两层产物**:

### 6.1 产品矩阵 (Product Matrix)

更新 `Docs/stability/<PRODUCT>-PRODUCT-MATRIX.md` (阶段 0 的草稿现在填实):

**必填 section**:

- **§1 Mission** — 这个产品为什么接、用户痛点
- **§2 Surface inventory** — 列出所有要捕获的 surface (chat / canvas / tool-use / settings / ...)
- **§3 UCS / plane mapping** — 实测后的最终映射表
- **§4 Tier 定位** — 与 SITE-TIER-MATRIX 对齐
- **§5 Region-by-region case grid** — 每个 region 一个 § (§5.A / §5.B / §5.C), 每个 § 包含:
  - 一个 case 表 (case ID / title / acceptance / status / region notes)
  - `§5.X.2 Open architectural questions` (Q1..QN, RECON carry-forward)
- **§6 Release gates** — 这个产品 ship 需要 case 矩阵翻绿的比例 (一般 ≥ 75%)
- **§7 Known limitations** — 哪些 surface 不接 (paywall / 隐私 / 难度)
- **§8 Reference handoffs** — 跟此产品相关的 handoff 文件路径

**Case 表行格式** (固定 5 列, 来自 `DESKTOP-D-CASE-EXECUTION-STANDARD.md` §1):

| Case ID | Title | Acceptance (一句话) | Status | Notes |
|---|---|---|---|---|
| D01 | vanilla user→assistant | request + response capture; one `user` + one `assistant` `messages` row; non-empty content_text | ✅ | — |

**Acceptance 必须是可验证的 SQL 断言**, 不要写"看起来对就行"。

### 6.2 ADR (可选, 仅当引入新概念)

新文件 `Docs/docs/engineering/adr/ADR-<NNN>-<topic>.md`, 模板见 `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-001-first-phase-is-record-not-intervention.md` (最简) / `ADR-017-test-conductor-cross-lane-agent-contract.md` (最复杂)。

**什么时候需要 ADR**:
- 引入了新 UCS 层 (e.g. L3i 某种新 hook 类型)
- 改了 capture/storage/render 三件套契约 (e.g. CaptureEvent 加新 enum)
- 决定 "deferred to Pro" / "out of scope"
- 决定 license / governance / threat-model 边界变化

**什么时候不需要 ADR**:
- 单纯接一个 S2/S3 站点 (DIFF spec 就够了)
- 修 selector / normalizer bug (FailureKind 框架已覆盖)
- 新增 case (case 写在 matrix 里就行)

**完成标准 (checklist)**:

- [ ] 矩阵 §1-§8 全填实 (无 TBD)
- [ ] Open Questions 列表里 carry-forward Q 都标了"会在下一 release 关"还是"长期问题"
- [ ] 如果引入新概念, ADR 写完并 status=`Proposed`
- [ ] commit `docs(stability): <product> matrix v1`
- [ ] **Owner sign-off** (commit reply "go" 或 PR approve) — 写标准是一个 freeze 动作, 必须有 owner 确认

**陷阱**:
- ⚠️ **不要让 Acceptance 依赖"主观判断"**。`assistant 回复看起来合理` ❌ → `messages 表存在 role='assistant' + content_text 非空 + token_estimate ≥ 5` ✅
- ⚠️ **不要在标准里写"分多个 PR 完成"**。标准是一份文件, ship 与否的判定基于矩阵 tile 颜色。

---

## 7. 阶段 (4) — 闭环 (Implement until tiles flip green)

**目的**: 按矩阵实现 adapter / driver / normalizer / 测试, 让每个 case 从 🟡 (pending) 翻到 ✅ (PASS) 或 ⛔ (legitimate SKIP)。

### 7.1 实现栈 (按依赖顺序)

```
1. Adapter (YAML) ── 选择器 / URL pattern / 时序参数
   ├── pce_core/adapters/<site>.yaml          (open-core)
   └── pce_browser_extension_wxt/sites/<site>.ts  (browser-only)
       ↓
2. Normalizer (Python)  ── 请求/响应 → NormalizedMessage
   └── pce_core/normalizer/<provider>.py
   只在 SCHEMA_DRIFT / 新 schema 时需要新代码 (大多数情况下复用已有)
       ↓
3. Driver (Python)  ── desktop UIA / CLI wrap / MCP client
   ├── tests/e2e_desktop_ui/drivers/<product>.py
   ├── pce_cli_wrapper/...
   └── pce_mcp_proxy/...
       ↓
4. Test cases (Python)  ── 矩阵每个 case 对应一个 _test_<caseId>
   ├── tests/e2e_desktop_ui/run_<phase>_<region>_sweep.py
   ├── tests/e2e_probe/...
   └── tests/e2e_<lane>/...
       ↓
5. Canary snapshot (JSON Schema)  ── G6 防 schema drift
   └── tests/canary/<target>.schema.json (Test Conductor 自动 infer)
```

### 7.2 迭代 cadence (M1..Mn 模式)

P5.B / P5.C 实践出的 milestone-iteration 模式:

| Milestone | 内容 | 通过标准 |
|---|---|---|
| **M1** | 框架 scaffolding (driver 类 + 1 个 _test_smoke 跑通) | smoke PASS |
| **M2** | 把 30% 的 case 跑成 live PASS | 矩阵 30% 绿 |
| **M3** | 50% case live PASS, 20% static PASS, 30% pending | — |
| **M4** | driver 完整 + acceptance 80% 覆盖 | live ≥ 50% PASS |
| **M5** | 第一次 full sweep + skip-conversion 分析 | 失败案例分类完 |
| **M6** | 修 M5 暴露的所有 fixable bug | live ≥ 70% PASS |
| **M7** | 最终 sweep + ship-decision | live ≥ 75% PASS, ready to ship |

每个 milestone 一个 commit 一份小 handoff (`HANDOFF-P<N>-<PRODUCT>-<MILESTONE>.md`), 总结当前 矩阵 tile 颜色 + 下一 milestone 要做的事。

### 7.3 live mode vs static mode (必须双轨)

每个 case 必须同时实现两种 mode (per `DESKTOP-D-CASE-EXECUTION-STANDARD.md` §1):

- **live mode**: 真跑客户端 (UIA 驱动 / mitmproxy 抓真请求 / CLI 真执行), 给出从 0 到入库的端到端验证。
- **static mode**: 只 assert 已入库的数据 (`messages` / `sessions` / `raw_captures` 表行), 不触发任何客户端动作。

**为什么必须双轨**:
- live 出 FAIL 时, 你不知道是 (a) 代码坏了 (b) 客户端环境坏了 (c) 你的 prompt 写错了。
- static 在 live 数据库上跑一次, 能立刻区分 (a) vs (b)+(c)。
- nightly CI 跑 static (无 client 依赖, 完全 reproducible), live 由 owner 手动驱动 (需要登录 client)。

### 7.4 修 broken adapter / 失败 case 的 SOP

固化在 `@f:\INVENTION\You.Inc\PCE Core\CONTRIBUTING.md` "Fix a broken adapter" § (D0 release gate, 已 ship)。6 步:

1. Reproduce locally (`pytest tests/...::test_<case>`)
2. Classify failure (`python -m pce_test_conductor classify_failure --target <t> --case <c>`)
3. AI-assisted repair suggestion (optional, `python -m tools.repair_adapter --target <t>`)
4. Edit YAML adapter (`pce_core/adapters/<site>.yaml`)
5. Verify fix (rerun pytest + run full site regression)
6. Submit PR (Conventional Commits, sign-off, CODEOWNERS reviewer)

### 7.5 完成标准 (checklist)

- [ ] 矩阵 ≥ 75% PASS (ADR-011 § release gate 默认 bar)
- [ ] 0 regression (run `python -m pytest tests/test_conductor.py tests/test_normalizer.py` 全绿)
- [ ] 每个 SKIP 在矩阵 Notes 列有"为什么 SKIP"解释 (允许的 SKIP 见 §1 D-case standard)
- [ ] 每个 FAIL 有对应 issue 链接 (`broken-adapter` label) + carry-forward 计划
- [ ] HANDOFF-P<N>-<PRODUCT>-COMPLETION.md 写完

**陷阱**:
- ⚠️ **不要为了翻绿改 acceptance**。Acceptance 是阶段 3 freeze 的, 阶段 4 不能动。要改 acceptance 就回到阶段 3 + owner sign-off。
- ⚠️ **不要在 live 之前先写 static**。Static 是基于 live 实测过的数据形状 assert 的, 没 live 数据就 assert 不出来。
- ⚠️ **不要并行修多个 region 的 case**。每个 region 顺次过 M1-M7, 否则失败原因互相污染。

---

## 8. 阶段 (5) — 稳定 (Don't break what already works)

**目的**: 把"已经接进来"的 case 守护起来, 防止后续 commit / 上游变化让它们倒退。

### 8.1 基础设施 (Meta-Pipeline 三柱)

定义于 `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md`:

| 柱 | 软件 | 目的 |
|---|---|---|
| **Test Conductor** | `pce_test_conductor/` | 跨 lane case 编排 + classifier + canary diff |
| **Health Beacon** | `pce_core/health.py` | 实时探针 (cron / Webhook) |
| **Nightly CI** | `.github/workflows/nightly-probe.yml` | 每天 UTC 02:00 跑全 case + auto-issue |

接新产品的 hookup 检查:

- [ ] `pce_test_conductor/targets/<product>.yaml` 注册 (defines runner + canary path)
- [ ] `pce_core/health.py` 包含本产品的 beacon endpoint (若有 live monitoring 需求)
- [ ] `.github/workflows/nightly-probe.yml` matrix 列里有本产品 target ID

### 8.2 Tag + release

接完一个产品 (= 阶段 4 完成 + 进入阶段 5), 决定是否单独 tag:

- **Minor (v1.X.0)**: 接了一个 S0/S1 产品 → 单独 tag
- **Patch (v1.X.Y)**: 接了一个 S2/S3 产品 / 修一组 bug → 跟下一个 minor 一起 tag
- **Pre-release (v1.X.0-alpha.N-<region>-<phase>)**: 单 region milestone, 用于 owner 验收节点

Tag 流程:

```bash
git tag -a vX.Y.Z -m "<title>

<body — quote release notes, list ADR closures, list carry-forwards>"
git push origin vX.Y.Z
```

**Release notes 写入 tag annotation**, 不另外写 GitHub Release UI (除非 owner 要求 — owner 可以直接 `gh release create vX.Y.Z --notes-from-tag`)。

### 8.3 Handoff (必须写)

每完成一个产品 / 一个 milestone, 必写一份 `HANDOFF-P<N>-<PRODUCT>-COMPLETION.md`。模板:

```markdown
# HANDOFF — <Product / phase> COMPLETION

> **Date**: YYYY-MM-DD
> **Tag**: vX.Y.Z (commit <hash>)
> **Status**: shipped

## 1. 这一阶段做了什么 (one paragraph)
## 2. 矩阵翻绿率 (X PASS / Y SKIP / Z FAIL 比 阶段开始时的 baseline)
## 3. ADR 关了几个 / 开了几个
## 4. Carry-forward (没关掉但不卡 ship 的事)
## 5. v1.X.Y readiness checklist (8 项, see HANDOFF-P5C-COMPLETION 模板)
## 6. 下一阶段建议 (P<N+1> 该做什么, 时间预算)
## 7. References (commit list / matrix path / ADR list / 关键 PR)
```

完整范本: `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P5C-COMPLETION-2026-05-12.md` (24 KB, P5.C 完整 closeout)。

### 8.4 完成标准 (checklist)

- [ ] Tag 已打且 push 到 origin
- [ ] HANDOFF-<PRODUCT>-COMPLETION.md 写完
- [ ] 内部包版本号 (`<package>/__init__.py`) 如有改动已 bump
- [ ] Nightly CI 至少跑过一次绿 (等下次 UTC 02:00 自然触发即可)
- [ ] CHANGELOG.md 头部加了对应 release section

---

## 9. 跨阶段工具速查表

| 工具 | 阶段 | 一句话用法 |
|---|---|---|
| `read_url_content <url>` | 1 | 读官网 / 官方文档 |
| `search_web "<query>"` | 1, 2 | 找第三方逆向 / community 资料 |
| `code_search "<topic>"` | 1, 4, 5 | 看代码库有没有已存在的处理 |
| `python -m pce_proxy --mode tap` | 2 | 启 mitmproxy, 让客户端走 8080 |
| `python -m tests.e2e_desktop_ui.scripts.dump_uia <mode>` | 2 | UIA tree dump |
| `python -m pce_core.cli replay --pair <pair_id>` | 2, 4 | 重放一对 capture 走 normalizer |
| `python -m pce_test_conductor run_case --target <t> --case <c>` | 4, 5 | 跑单个 case |
| `python -m pce_test_conductor classify_failure --target <t> --case <c>` | 4 | 自动分类 FailureKind |
| `python -m tools.repair_adapter --target <t>` | 4 | AI-assisted selector 修复建议 |
| `python -m pytest tests/<file> -v` | 4, 5 | 跑 pytest |
| `python -m pytest tests/test_conductor.py tests/test_normalizer.py` | 4, 5 | 烟测 P5.C 矩阵 |
| `git tag -a vX.Y.Z -m "..."` | 5 | 打 release tag |
| `git push origin master / vX.Y.Z` | 5 | 推 commit / tag |

---

## 10. 实例 — Claude Desktop Code-region 完整走过一遍

时间线对应本文 6 阶段:

| 阶段 | 时点 | 产物 |
|---|---|---|
| (0) 决策 | 2026-05-10 | `DESKTOP-PRODUCT-MATRIX.md` §5.C 开章 (草稿) |
| (1) Desk | 2026-05-10 | (跳过, 因 chat-region 已存在; code-region 信息直接从 desktop chat 复用) |
| (2) RECON | 2026-05-11 | `Docs/research/2026-05-11-code-tab-recon-findings.md` (30 KB) |
| (3) 标准 | 2026-05-11 | matrix §5.C 表 (E00-E25, 26 cases) + §5.C.2 Q1-Q5 open questions |
| (4) 闭环 | 2026-05-11~12 | M1-M7 iteration, 最终 15 PASS / 1 SKIP (E10) / 0 FAIL |
| (5) 稳定 | 2026-05-12 | tag `v1.1.0-alpha.12-code-p1` + HANDOFF-P1-CLAUDE-DESKTOP-* |

**bug 修复时回到阶段 4** (D04/E04 cancel-mid-stream):

| 时点 | 动作 | commit |
|---|---|---|
| 2026-05-12 21:30 | D04 修复(在阶段 4 框架内 fix 已 ship case 的 bug) | `71e9381` |
| 2026-05-12 21:38 | E10 RECON 工具备好 + handoff(carry-forward, 不卡当前 ship) | `8526955` |
| 2026-05-12 21:41 | tag v1.1.5(进入阶段 5) | `d911b25` |

---

## 11. Anti-patterns (反模式 — 见到立即停)

### 11.1 跳过 RECON 直接写 adapter
**症状**: PR 大改 normalizer, 但没有对应 `Docs/research/<date>-recon-findings.md`。
**修法**: stop, 回阶段 2 写 findings, 哪怕只是一次 mitmproxy 抓包贴 5 行 raw_capture 也行。

### 11.2 在 PR 里同时改 acceptance + 改代码
**症状**: 一个 commit 同时 touch `Docs/stability/*-MATRIX.md` 的 case 表 + `pce_core/normalizer/*.py`。
**修法**: 拆两个 PR — matrix 改先 owner sign-off, 代码 PR 引用 matrix commit hash。

### 11.3 修一个 case 后 cherry-pick 跑该 case 的测试
**症状**: PR 只跑 `pytest tests/test_<this_case>.py`, 没跑 conductor / normalizer 矩阵。
**修法**: 强制 `python -m pytest tests/test_conductor.py tests/test_normalizer.py` 也绿, 才能 merge。

### 11.4 不写 handoff 就交班
**症状**: 上一个 agent 留下一堆 commit, 但没 `Docs/handoff/HANDOFF-*` 总结。
**修法**: 在打 tag 之前补 handoff, 没补不准 ship。

### 11.5 把 SKIP 当 PASS 算入翻绿率
**症状**: 矩阵 "15 PASS / 1 SKIP / 0 FAIL" 报告成 "16/16 翻绿"。
**修法**: 翻绿率分母 = `applicable cases` (PASS + FAIL), 分子 = `PASS`。SKIP 单独列。`75% PASS bar` 看的是 `PASS / applicable`。

### 11.6 ADR 状态停在 Proposed 没人推动
**症状**: 半个月前的 ADR 还是 `Status: Proposed`, 但代码已经按它实现了。
**修法**: 实现完成那刻把 ADR 改成 `Status: Adopted YYYY-MM-DD via commit <hash>`, 不允许"事实上 Adopted, 文档上 Proposed"。

### 11.7 在 RECON 里写 acceptance criteria
**症状**: RECON findings 里出现 "T01 PASS = ..." 这种验收语句。
**修法**: 删, RECON 只列 case 候选 + trigger, acceptance 留给阶段 3。

### 11.8 用 trial-and-error 改 selector 直到测试绿
**症状**: 同一个 YAML 里 selector 反复 commit / revert, commit message 都是 "fix selector again"。
**修法**: stop, 跑 `dump_uia.py` 拿到 ground truth, 一次改对。试错改 selector 是负 debt — 下次 vendor 改 DOM 又会破。

---

## 12. 5-分钟自检 (新 agent 接班必跑)

新 agent 进来读到本文, 跑下面 5 步, 5 分钟内能知道"我该做什么":

1. **`git log --oneline -20`** — 看最近 20 commit 的 subject 头几个字。提取关键词:
   - `recon` / `desk` → 上一个 agent 在阶段 1-2
   - `docs(stability)` / `matrix` → 阶段 3
   - `feat` / `fix` 含 case ID → 阶段 4
   - `chore(tag)` / `release` → 阶段 5

2. **`Get-ChildItem Docs/handoff/*.md | Sort-Object LastWriteTime -Descending | Select-Object -First 3`** — 看最近 3 份 handoff, 找标题里的 `KICKOFF / RECON / FULL-SWEEP / SKIP-CONVERSION / COMPLETION` 关键字, 那是当前阶段。

3. **打开最新 handoff, 读"§下一阶段建议"或"§5 readiness checklist" 或"§4 carry-forward"** — 列出还没打勾的项。

4. **跑 `python -m pytest tests/test_conductor.py tests/test_normalizer.py 2>&1 | Select-Object -Last 3`** — 看测试基线 (P5.C 矩阵), 156 PASS 0 FAIL 是绿基线。

5. **跑 `git status` + `git log --oneline origin/master..HEAD`** — 看本地有没有未推的 commit, 有就先 push 干净。

跑完上面 5 步, 你应该能用一句话回答 owner: "上次 agent 走到 P<N>-<阶段> 的 <milestone>, 当前缺 <item>, 我下一步做 <action>"。

---

## 13. References (本文引用的全部文件)

### Engineering canonical

- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\UNIVERSAL-CAPTURE-STACK-DESIGN.md` — UCS 5 层定义
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` — 跨 lane 公共词汇 + 三件套契约
- `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` — 4 plane × 6+1 type 认知框架

### 标准 / Matrix

- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` — desktop 产品矩阵 (116 KB, 范本)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` — D-case 执行标准
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` — browser T-case 执行标准
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-TIER-MATRIX.md` — S0-SX 站点分级
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-COVERAGE-DIFF-TEMPLATE.md` — S2/S3 站点 DIFF 模板

### ADR (架构决策)

- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-009-universal-capture-stack.md` — UCS 5 层
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-011-probe-remote-agent-autonomous-testing.md` — G1-G9 gates
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-017-test-conductor-cross-lane-agent-contract.md` — Conductor
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` — meta-pipeline

### Handoff 范本

- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` — 阶段 4 范本
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P5C-COMPLETION-2026-05-12.md` — 阶段 5 范本 (24 KB)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-E10-PERMISSION-DIALOG-RECON.md` — carry-forward 范本 (5-min owner task)

### Templates

- `@f:\INVENTION\You.Inc\PCE Core\Docs\research\2026-05-11-code-tab-recon-findings.md` — RECON findings 范本 (30 KB, 实战)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\research\2026-05-11-cowork-recon-findings.md` — RECON findings 范本 (52 KB, 复杂版)

### 工具

- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\scripts\dump_uia.py` — UIA tree dump
- `@f:\INVENTION\You.Inc\PCE Core\tools\repair_adapter.py` — AI-assisted selector repair
- `@f:\INVENTION\You.Inc\PCE Core\pce_test_conductor\` — Test Conductor (MCP server)

### Process

- `@f:\INVENTION\You.Inc\PCE Core\CONTRIBUTING.md` — "Fix a broken adapter" 6 步 (D0 gate, owner-authored)
- `@f:\INVENTION\You.Inc\PCE Core\CHANGELOG.md` — Keep a Changelog 格式, 每 release 一节
