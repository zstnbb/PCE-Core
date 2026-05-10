# PCE × 桌面端 AI 应用 · 捕获认知框架 (v2)

> **Status**: canonical — P5.B 阶段（v1.1 IDE & Electron）所有 agent 开工
> 之前的必读文档。任何要为新桌面端产品接入 PCE 的设计讨论，都应以
> 本文件的术语（**捕获面 / UCS 层 / Archetype Tube**）作为公共词汇。
>
> **Adopted**: 2026-05-08（浏览器扩展硬冻结同日）
>
> **Supersedes**（部分）:
> - `Docs/research/AI-PRODUCT-LANDSCAPE-AND-CAPTURE-ANALYSIS.md` §一
>   「AI 产品分类全景」的**产品中心视角** — 本文件保留其事实发现，
>   但把分类轴从「产品形态」翻转为「捕获面」。
> - `Docs/research/IMPLEMENTATION-ROADMAP.md` §实施路径总览的
>   STEP 3/4 顺序 — 本文件给出新一轮 P5.B 攻击顺序（见 §7）。
>
> **Audience**: Cascade（与本文件作者）、后续实施 agent、项目所有者。
>
> **2026-05-10 ADR-018 修订导航**:
> - 本文件 §7 表中 P1 Claude Desktop 行 "M (首) + H | `pce_mcp` server ✅ + L3b ⬜"
>   在 **Windows MSIX 渠道**下重定为 "M (首) + Persist + N | `pce_mcp` ✅
>   + `pce_mcp_proxy` ✅ + L3g ⬜ (新增) + L1 ✅"。Squirrel + macOS 渠道
>   仍按本文件原文 + ADR-016 走。
> - 新增 UCS 子层 **L3g · Local Persistence Watcher** — 解析应用自己写
>   到用户可读路径的持久化数据 (LevelDB / IndexedDB / 应用专有 JSON)。
>   定义见 ADR-018 §3.4。
> - L2 Frida、L0 Kernel 移到 Pro 渠道 (ADR-018 §3.7)。L4b Accessibility
>   提前到 P6 v1.2 ("Coverage Polish")。
> - 详见 `@Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`。

---

## 0. 起源：为什么要写这个

2026-05-08 浏览器扩展（`pce_browser_extension_wxt/`）在 S0/S1 站点的
best-of 探针矩阵上稳定在 ≥16 PASS / ≤5 SKIP / 0 FAIL，branch-capture
三件套（migration 0008 + rich_content envelope + dashboard expand/collapse
toggle）同日落地，**浏览器扩展这根钉子钉完了**。同日起 S2/S3 正式延后，
桌面端 AI 应用进入我们视野。

但在进入之前，团队在一轮复盘里发现：

1. 上次 `AI-PRODUCT-LANDSCAPE-AND-CAPTURE-ANALYSIS.md` 的**产品中心**分类
   （8 大品类）虽然完整，但**没有帮我们回答「从哪儿插管子」**。
2. `pce_mcp/server.py` (`@f:\INVENTION\You.Inc\PCE Core\pce_mcp\server.py:1-406`)
   **已经存在且能工作**，但从未被纳入任何阶段的路线图——这意味着路线图
   本身的**分类维度丢失了一整根轴（MCP 面）**。
3. P5.B 如果按「先挑一个产品做」的思路开展，很可能选完 Cursor 做完了
   preload，却发现同一个 preload 稍作扩展就能覆盖 Claude Desktop / Windsurf
   / Zed 的 MCP 子进程。**正交性没有在分类阶段暴露出来，就会在实施阶段
   以重复劳动的形式还账**。

所以本文件要做的，不是新一轮市场调研，而是**把认知单位从「产品」切换成
「捕获面 × 会话原型」**，让任何一个新桌面 AI 产品进入视野时，都能在
5 分钟内被定位到一张既有的图上。

---

## 1. 第〇层 · 心智反转

### 1.1 反转前（直觉思路）

> 我们要做哪些桌面 AI 应用？Cursor？Claude Desktop？Windsurf？…

这个思路的问题：**它让产品成为一等公民，让捕获技术成为二等公民**。实际
工程中恰好相反——**一种捕获技术能复用到的产品，远多于一个产品能被攻破
的技术**。

### 1.2 反转后（本文件采用）

> **PCE 能在哪几张「皮」上插管子？每张皮对应哪一类 UCS 层？每张皮上
> 长出来的产品是哪些？**

这个思路把「插管子的位置」作为一等公民。产品只是某几个位置的**聚合样本**。

### 1.3 与既有架构哲学的一致性

本视角直接继承 `Docs/research/IMPLEMENTATION-ROADMAP.md:9-10` 早已
确立的核心原则：

> **"差别只在'怎么进来'，不在'进来之后怎么处理'。"**
> — ARCHITECTURE.md §4.3

我们只是把这句话里的「怎么进来」从一个抽象的承诺，细化为**4 张可以
点名、可以排优先级、可以对账测试的捕获面**。

---

## 2. 第一层 · 捕获面（4 Planes）

### 2.1 定义

**捕获面 (Capture Plane)** = PCE 能在用户 × AI 交互链路上放置 capture
hook 的物理位置。每个面有自己独立的协议、权限要求、抗变更特性、信息
密度。

PCE 视角下的 4 张捕获面：

```
                          用户
                            │
            ┌───────────────┼───────────────┐
            │               │               │
        【U 面】          【H 面】          【N 面】        【M 面】
        渲染 UI          进程内部         网络出口        工具调用
     ────────────    ────────────     ────────────    ────────────
     AX / UIA        preload          mitmproxy       stdio MCP
     DOM scrape      IDE extension    +CA injection   SSE MCP
     OCR             V8 / Node hook   gRPC decode     JSON-RPC frame
```

### 2.2 各捕获面特性对照

| 面 | 信号位置 | 代表 UCS 层 | 抗变更 | 信息密度 | 代价 |
|---|---|---|---|---|---|
| **N · 网络面** | HTTPS / WebSocket / gRPC 流量 | L1, L2, L3e | 中 — 域名/路径易变，pinning 会 block | 中 — 要解协议 | 低 — 系统代理 + CA |
| **H · 进程面** | 目标客户端进程内部（V8 heap / Node fetch / ChildProcess） | L3a, L3b, L3c | **高** — 插在产品代码身上 | 高 — 拿到结构化对象 | 中 — 每个目标写一个 hook |
| **M · MCP 面** | MCP host ↔ tool server 之间的 JSON-RPC 帧 | **L3f（新轴）**+ `pce_mcp` as server | **极高** — MCP 是 Anthropic 发布的**标准协议** | **极高** — 语义级别的 tool call / result | **极低** — 改一行 config |
| **U · 渲染面** | 屏幕像素 / AX 树 / UIA 控件 | L3d / L4b / L4c | 低 — UI 一改就崩 | 低 — 很难重建 role / turn | 高 — 要写全套 UI 状态机 |

### 2.3 关键工程洞察

1. **M 面是 2024-2026 AI 生态给 PCE 的「免费午餐」**。MCP 是开放标准，写
   一个适配器就能同时覆盖十几个 host。所有其他面的投入都是**一个产品一
   个产品地付账**。
2. **H 面的性价比随目标而变**。Electron-class 目标只需 preload
   （`L3b`），插件-class 目标（VS Code extension）只需 extension API
   （`L3c`），两者之间有共享套路但不完全互通。
3. **N 面是万用兜底**，但面对 pinning、私有协议（Cursor gRPC-web）、
   或 localhost（Ollama）时各有额外工程（L2 Frida / proto decoder /
   L3e gateway）。
4. **U 面是终极兜底**（OS 原生客户端无其他面可抓），但因为信息密度最低，
   按 UCS 规划放到 P6/P7 阶段（见 `UNIVERSAL-CAPTURE-STACK-DESIGN.md`）。

### 2.4 同一产品可能跨多面

这是「捕获面视角」带来的**组合性红利**。典型对照：

| 产品 | N | H | M | U |
|---|---|---|---|---|
| Claude Desktop | ✅（不 pin） | ✅（Electron preload） | ✅（MCP 鼻祖） | ✅（AppKit/WPF AX） |
| Cursor | ⚠ gRPC-web | ✅（VS Code fork preload） | ✅（2024 起支持） | ✅ |
| Claude Code (CLI) | ✅ | ❌（Node CLI 无 UI 进程） | ✅ | ❌ |
| ChatGPT Desktop | ⚠ 疑似 pin | ✅ | 🟡 2025 加入 connectors | ✅ |
| Copilot (VS Code) | ✅（厂商中继） | 🟡（extension API 受限） | ❌ | ✅ |

PCE 每接入一个新产品时，要做的**第一个判断**是：**在哪个面上性价比最高
的被捕获？** 而不是「要不要做这个产品」。

---

## 3. 第二层 · 应用类型学（6 + 1 类）

类型学**仍然有用**，但它的角色从「排优先级的依据」下降为「把面的组合
样本打包给人看」。每类给：**代表 / 它暴露哪些面 / PCE 首选面 / 备胎 /
会话语义是否可复用现有 normalizer**。

### 类型 1 · Electron 聊天客户端（Web 版的桌面镜像）

| 子类 | 代表 | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|---|
| 1a · 同步 web 版 | **ChatGPT Desktop** / **Claude Desktop** / Perplexity Desktop / Poe Desktop | N + H (+ M 仅 Claude/ChatGPT) | M > H > N | 全 | ✅ 完全复用 `anthropic.py` / `openai.py` |
| 1b · MCP host | Claude Desktop（MCP 鼻祖） | N + H + **M** | **M** | H | ✅ |
| 1c · 有 SSL pinning | ChatGPT Desktop（社区报告 2025） | H + (N 受阻) | **H** | N + L2 Frida | ✅ |

**归一化关键**：会话语义与 web 版**完全等价** — 同一批用户、同一批
`conversation_id`、同一批 `model_name`。只需在 `CaptureIn.source_type`
里新增 `desktop_electron` 枚举值。

### 类型 2 · IDE-class AI 编辑器（VS Code fork 系）

| 子类 | 代表 | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|---|
| 2a · MCP-aware | **Cursor** / **Windsurf** / **Zed** | N + H + **M** | **M** | H | 🟡 需新增 `interaction_kind` |
| 2b · 非 MCP | Trae（字节）/ Replit Desktop | N + H | H | N | 🟡 |
| 2c · 私有协议 | Cursor 用 gRPC-web + protobuf | N（要解 proto）+ H | H | proto decoder | 🟠 |

**归一化关键**：一次「会话」的语义与 web **完全不同**——它由
`workspace × session × interaction-mode` 三元组定义，且包含 Cmd-K /
Tab / Composer / lint-diff 等**多种 interaction kind**。现有 messages
表已支持 `role` 字段，但需新增 `interaction_kind`（`completion` /
`chat` / `agent_loop`）。

### 类型 3 · IDE 寄生插件（在别人的 IDE 里）

| 代表 | host | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|---|
| **GitHub Copilot** | VS Code / JetBrains | N（厂商中继）+ H（受限） | **N + L1** | L3c 受限 | 🟠 新 `copilot_proxy.py` |
| **Cline** | VS Code | N（用户直连 OpenAI/Anthropic）+ **M** | **M + N** | L3c | ✅ |
| **Continue** | VS Code / JetBrains | N + **M** | M + N | L3c | ✅ |
| **Cody / JetBrains AI** | 各自 host | N | N | — | 🟡 |

**归一化关键**：**最分裂的一类**。Copilot 的 inline completion 不是
对话（没有 assistant turn）；Cline 的 task 是 agent loop（tool call
密集）。要求 normalizer 按 `interaction_kind` 分支处理。

### 类型 4 · CLI Agent（终端里跑的 agent 循环）

| 代表 | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|
| **Claude Code** | N（直连 Anthropic）+ **M** | **M** | N (L3e gateway) / L1 | ✅（`anthropic.py` + tool result） |
| **Codex CLI** | N（直连 OpenAI）+ **M** | **M** | N / L1 | ✅ |
| **Gemini CLI** | N + M | M | N | 🟡 `genai_semconv.py` |
| **Goose** (Block) | **M**（MCP-first） | M | — | 🟡 |
| **Aider** / Plandex / OpenHands | N | **L3e gateway** | L1 | ✅ |

**归一化关键**：**ROI 之王 + 结构化程度最高**。一次 `claude code`
invocation ≈ 几百次 tool call + 几十次 LLM round-trip。要支持
`role ∈ {user, assistant, tool, tool_result, system, observation}`
（messages 表已经有 `oi_role_raw` 为此留了空间）。

### 类型 5 · 原生（非 Electron）客户端

| 代表 | OS | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|---|
| **Raycast AI** | macOS | N（固定域名）+ U | **N (L1)** | L4b AX | ✅ |
| **BoltAI / MacGPT / ChatX** | macOS | N（用户 API key）+ M（部分） | M / N | L4b | ✅ |
| **Quicker AI 插件** | Windows | N | N | UIA | ✅ |

**归一化关键**：与 web 版等价，但 attachment / system prompt 来自 OS
全局剪贴板/选区，需扩 `source_evidence` 字段。

### 类型 6 · 本地 LLM 桌面应用

| 代表 | 暴露面 | 首选 | 备胎 | 会话复用 |
|---|---|---|---|---|
| **LM Studio** / **Ollama Desktop** | N（localhost）+ H | **L3e localhost gateway** | H | ✅（OpenAI-compatible） |
| **Jan** / **GPT4All** / **Msty** | N（localhost） | **L3e** | — | ✅ |
| **AnythingLLM Desktop** | N + **M** | **M** | L3e | ✅ |
| **Open WebUI** (自部署) | N（localhost web） | L1 + L3a* | L3e | ✅ |

（L3a* = 浏览器扩展；已封存，不再投入。）

**归一化关键**：provider=local 的 `model_name` 形如 `llama3:70b-q4`
/ `qwen2.5-coder:32b-instruct`，schema 已能装。叙事加分项：
**捕获完全不依赖外网**，最契合 local-first 原则。

### 类型 〇 · MCP Server（被代理的对象，不是客户端）

这一类**不是用户直接使用的应用**，而是被类型 1/2/3/4 调用的工具。
PCE 的 L3f 层姿态 B（MCP middleware proxy）就是**夹在 host 和这些
工具之间**。

| 代表 | 用途 | PCE 关系 |
|---|---|---|
| `@modelcontextprotocol/server-filesystem` | 文件读写 | **被 L3f 代理** |
| `@modelcontextprotocol/server-git` / `-github` / `-postgres` / `-slack` / `-puppeteer` | 各类数据源 / 执行器 | **被 L3f 代理** |
| 社区数百个（`mcp.so` 列表） | — | **被 L3f 代理** |

---

## 4. 第三层 · 抓 — UCS 层 × 捕获面 × 实施模块对照表

PCE 的**所有**捕获前端**都往同一个入口写数据**
（`Capture Ingest API`，`POST /api/v1/captures`，
`@f:\INVENTION\You.Inc\PCE Core\pce_core\models.py:12-66`）。下表是
UCS 层 ↔ 捕获面 ↔ 实施模块 ↔ 当前状态的实操映射：

| UCS 层 | 名称 | 捕获面 | 实施模块 | 当前状态 |
|---|---|---|---|---|
| **L1** | 系统代理 + CA | N | `@f:\INVENTION\You.Inc\PCE Core\pce_proxy\addon.py` | ✅ 工作中（mitmproxy） |
| **L2** | SSL pinning bypass | N（突破） | ⬜ 待建（Frida 脚本） | P6 目标 |
| **L3a** | 浏览器扩展（WXT） | H + N | `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\` | 🔒 **2026-05-08 硬冻结** |
| **L3b** | Electron preload 注入 | H + M（子进程可见） | ⬜ 待建 | **P5.B 主目标** |
| **L3c** | IDE / VS Code Extension API | H | ⬜ 待建 | P5.C |
| **L3d** | 原生 app accessibility（AX / UIA） | U | ⬜ 待建 | P6 |
| **L3e** | LiteLLM / OpenAI-compatible gateway | N（重定向） | ⬜ 待建 | P5.B 子目标 |
| **L3f** | **MCP middleware proxy**（夹在 host ↔ server 之间） | M | ⬜ 待建（独立于 `pce_mcp` server） | **P5.B 高 ROI 钉子** |
| **L4b** | UI hook / OCR | U | ⬜ 待建 | P6 |
| — | **PCE 自身作为 MCP server**（姿态 A） | M（agent 自报） | `@f:\INVENTION\You.Inc\PCE Core\pce_mcp\server.py:1-406` | ✅ **已工作但未发布** |

### 4.1 「PCE 作为 MCP server」vs「PCE 作为 MCP proxy」

**这是一对经常被混淆的角色**。分开讲清楚：

**姿态 A · PCE 作为 MCP server**（`pce_mcp/server.py`，**已存在**）

```
┌──────────────────┐     MCP/stdio     ┌─────────────┐
│ MCP host (Claude │ ◄───────────────► │   pce_mcp   │
│ Desktop / Cursor)│                    │  (server)   │
└──────────────────┘                    └──────┬──────┘
                                               │ 调用 pce_core.db
                                               ▼
                                         SQLite
```

- 已实现工具：`pce_capture` / `pce_query` / `pce_stats` / `pce_sessions`
  / `pce_session_messages` / `pce_capture_pair`（均见 server.py）。
- **依赖 agent 主动调用**。Cascade / Claude / Cursor 里的 AI 需要被
  system prompt 引导去 `pce_capture(...)` 上报自己的动作，否则 PCE
  不会知道。
- **优势**：零 hooking、零权限、跨所有 MCP host 通杀。
- **短板**：合规依赖 agent 自觉性。

**姿态 B · PCE 作为 MCP middleware proxy**（`L3f`，**待建**）

```
┌──────────────────┐  MCP/stdio  ┌────────────┐  MCP/stdio  ┌─────────────┐
│ MCP host (Claude │ ──────────► │ pce-mcp-   │ ──────────► │ upstream    │
│ Desktop / Cursor)│             │ proxy (L3f)│             │ MCP server  │
└──────────────────┘             │ capture    │             │ (fs/git/...)│
                                  └─────┬──────┘             └─────────────┘
                                        ▼ 旁路写入 Capture Ingest
                                   SQLite
```

- **完全透明**。所有 host ↔ server 的 JSON-RPC 帧（请求/响应/
  notification/error）被拷贝一份写入 PCE，host 和 server 都感知不到
  中间人。
- **无损**。比 L1 系统代理更结构化，因为 MCP 协议本身就是 JSON-RPC
  而不是任意 HTTP。
- **代价**：一个 Python 透明代理（估 200-300 行）+ 一个 normalizer
  （`pce_core/normalizer/mcp_jsonrpc.py`，新建）+ 用户端改 MCP config
  一行（指向 proxy 而非真 server）。

**姿态 C · MCP-aware 进程内 hook**（`L3b` 的扩展，**待建**）

Electron preload 注入时，同时观察主进程里 `ChildProcess.spawn` 出去
的 MCP stdio 子进程 pipe。一次 hook 抓到两层：**chat 文本（preload
拦 fetch）+ tool 调用（preload 拦 child_process）**。

---

## 5. 第四层 · 存 — Normalizer 三段流水线

PCE 的归一化层是**整个系统最重的资产**。异构捕获在这里折叠成一张统一
的 messages 表。位置：`@f:\INVENTION\You.Inc\PCE Core\pce_core\normalizer\`

```
[任意 Capture Plane]
        │
        ▼
┌────────────────────────────────────────────────┐
│ 段①  Provider 适配                              │
│   raw bytes / DOM / JSON-RPC frame              │
│     ↓                                            │
│   provider-native dict                           │
│                                                  │
│   已有实现:                                       │
│     - normalizer/anthropic.py        (28KB)      │
│     - normalizer/openai.py           (21KB)      │
│     - normalizer/genai_semconv.py    (Google)    │
│     - normalizer/sse.py   (流式 chunk 拼接)      │
│     - normalizer/openinference_mapper.py         │
│                                                  │
│   P5.B 新增待建:                                  │
│     - normalizer/mcp_jsonrpc.py  (M 面)          │
│     - normalizer/ide_interactions.py  (Cursor    │
│            的 completion/chat/composer 分支)     │
└────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────┐
│ 段②  Conversation 重组                          │
│   N 个 capture  →  1 个逻辑 conversation         │
│                                                  │
│   职责:                                           │
│     - dedupe (fingerprint)                       │
│     - branch / variant reconciliation            │
│     - threading contract 生成                    │
│     - role 归并 (user/assistant/tool/...)        │
│                                                  │
│   实现:                                           │
│     - normalizer/conversation.py     (16KB)      │
│     - normalizer/message_processor.py (39KB)     │
│     - normalizer/reconciler.py       (16KB)      │
│     - normalizer/session_manager.py              │
└────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────┐
│ 段③  Storage 落库                                │
│                                                  │
│   sessions    (id, provider, model_name, ...)    │
│   messages    (id, role, content_text,           │
│                content_json, branch_id,          │
│                branch_parent_id, turn_index)     │
│   raw_captures  (原始字节，forensic)             │
│                                                  │
│   schema: pce_core/db.py + migration 0001-0008   │
│   0008 = branch semantics (ADR-2026-04-26)       │
└────────────────────────────────────────────────┘
```

### 5.1 对桌面新阶段的 4 条推论

1. **类型 1（Electron 聊天）** 完全复用段① 的 `anthropic.py` /
   `openai.py`，只需在 `CaptureIn.source_type` 加新枚举。
2. **类型 2（IDE-class）** 需要新增 `interaction_kind` 字段区分
   `chat` / `completion` / `agent_loop`。messages 表 schema 加一列
   即可（migration 0009，拟）。
3. **类型 4（CLI agent）** 的 M 面 JSON-RPC 需要新写
   `mcp_jsonrpc.py`，但 messages 表已经支持 `role=tool` /
   `tool_call_id`（早就为 OpenAI tool calling 留了位，见
   `pce_core/normalizer/openai.py`）。
4. **branch/variant 契约**（2026-05-08 落地的 migration 0008 + rich_content
   envelope）对 IDE / agent 这类 multi-turn tool loop **比对普通
   chat 更有价值**——agent 的「重跑一次 tool call」本质就是一次
   variant。这意味着 P5.B 的归一化层有**现成的分支/变体表达工具**。

### 5.2 source_type 扩展计划

`CaptureIn.source_type` 当前枚举（`pce_core/models.py:15-18`）：
`proxy | browser_extension | mcp | ide_plugin | local_model`

P5.B 拟新增：

| 枚举值 | UCS 层 | 覆盖 |
|---|---|---|
| `desktop_electron` | L3b | 类型 1 (Electron 聊天)、类型 2a/2b (IDE-class) |
| `ide_extension` | L3c | 类型 3 (IDE 插件) |
| `cli_gateway` | L3e | 类型 4 (Aider / 不原生支持 MCP 的 CLI) |
| `mcp_proxy` | L3f | 姿态 B，类型 0 (被代理的 MCP server) |
| `native_ax` | L3d/L4b | 类型 5 (原生客户端)，留位到 P6 |

---

## 6. 第五层 · 渲染 — 4 种 Archetype Tube

不同类型的会话在 dashboard 上**不该长一个样**。Chat 用气泡好看，
Agent loop 用气泡就是地狱。本文件提议 4 种 archetype tube：

| Archetype | 适用类型 | 视觉形态 | 关键字段 | 状态 |
|---|---|---|---|---|
| **Chat Tube** | 1, 5, 6 | 左右对话气泡 + 分支切换 + attachment chip | `role × text × attachments × branch_id × turn_index` | ✅ 已上线（2026-05-08 扩展分支 UI） |
| **Tool Tape** | 类型 0 / M 面 / Cline / Goose | 时序磁带：tool_call → tool_result 一对对卷轴 | `tool_name × args_json × result_json × duration_ms × error` | ⬜ 拟（P5.B） |
| **Code Session** | 2, 3 (IDE) | 文件树 + diff + chat side panel | `workspace × file × diff_range × selection × interaction_kind` | ⬜ 拟（P5.C） |
| **Run Trace** | 4 (CLI agent) | 树状递归 trace（类似 OTel 火焰图） | `span × parent_span × kind × tool/llm × tokens × duration_ms` | ⬜ 拟（P5.B 尾声） |

### 6.1 渲染契约延续 2026-05-08 模式

四个 archetype **共享**同一套「storage_paths ↔ render_paths」对账
思路（见 `pce_core/rich_content.py:178-228` 已有的 variant/branch
契约）：

```
Chat Tube     : threading.{branch_id, current_branch_id}
                    ↔ rich_content.branch_tree / branch_choices
Tool Tape     : tool_call.{id, name, args}
                    ↔ rich_content.tool_tape[*].{call, result}      [拟]
Code Session  : code_event.{file, range, action}
                    ↔ rich_content.code_session.{files, diff}       [拟]
Run Trace     : span.{id, parent_id, kind}
                    ↔ rich_content.run_trace.{root_span, children}  [拟]
```

每个 archetype 都遵循「storage 是真相、render 是契约」的同一原则。
E2E 探针（`tests/e2e_probe/`）只需要新增 4 个 contract 校验，**测试
框架本身不变**。

### 6.2 Archetype 与类型的映射

一个产品的主 archetype 由**其高频使用形态**决定，不由它是不是
Electron 决定。示例：

- Claude Desktop 主用是聊天 → **Chat Tube**
- Cline 主用是 agent 循环 → **Tool Tape** 为主 + Chat Tube 夹带
- Claude Code 主用是 CLI 跑批 → **Run Trace** 为主
- Cursor 主用是 inline edit + chat + composer → **Code Session**

---

## 7. 第六层 · 总图 — 抓 × 存 × 渲染 端到端矩阵

按「类型」横切，每行给完整的 capture plane / UCS 层 / normalizer /
render archetype / 距离评估。

| 类型 | 代表 | 捕获面 | UCS 层 | Normalizer | Render | 距离 |
|---|---|---|---|---|---|---|
| 1a Electron 聊天 (Squirrel/macOS) | **Claude Desktop** | M (首) + H | `pce_mcp` server ✅ + L3b ⬜ | anthropic.py ✅ | Chat Tube ✅ | 🟢 **1 周可上线** |
| 1a Electron 聊天 (**MSIX**, ADR-018) | **Claude Desktop** | M (首) + Persist + N | `pce_mcp` ✅ + `pce_mcp_proxy` ✅ + **L3g** ✅ alpha.8 (`pce_persistence_watcher/`) + L1 ✅ + H1 CLI wrap ✅ alpha.8 (`pce_cli_wrapper/`) | anthropic.py ✅ + mcp_jsonrpc.py ✅ + local_persistence ⬜ (envelope-level emit shipped, structural normaliser gated by cowork D-case need) | **Chat Tube ✅** (chat-region sub-runs 2–5: D00–D22 19/22 PASS 86%) + **Run Trace ⬜** (cowork-region agent loop) + **Tool Tape ⬜** (cowork-region MCP tool calls + skills) | 🟢 chat-region：~94% T1 实测交付 (2026-05-10 sub-runs 1–5) · cowork-region：RECON + C-case sweep 启动中（sub-phase P5.B.5）· code-region：H1 CLI wrap 交付 |
| 1a' pin 变种 (Squirrel) | **ChatGPT Desktop** | H (pin 推回 H) + N | L3b ⬜ + L1 ✅ | openai.py ✅ | Chat Tube ✅ | 🟡 2-3 周 |
| 1a' pin 变种 (**MSIX**, ADR-018) | **ChatGPT Desktop** | N (首) + Persist | L1 ✅ + A2 keylog ⬜ + L3g ✅ (同 P1包，需 ChatGPT-specific LocalCache layout discovery) | openai.py ✅ + local_persistence ⬜ | Chat Tube ✅ user-side / ⚠️ assistant-side WS-handoff BLOCKED (split-channel，见 MATRIX §4.2 dated note) | 🟡 P2 chat-region user-side ✅ / assistant-side 需 WebSocket 捕获互补 (候选 4 路径见 HANDOFF-P1-D03-D05-P2-EMPIRICAL) |
| 2a IDE-class | **Cursor / Windsurf** | M + H | L3f ⬜ + L3b ⬜ | openai/anthropic ✅ + `interaction_kind` ⬜ | Code Session ⬜ | 🟡 3-4 周 |
| 2c 私有协议 | Cursor 原生 | N (gRPC proto) | L1 ✅ + proto decoder ⬜ | openai 兼容 | Code Session ⬜ | 🟠 4-6 周 |
| 3 插件 | **Cline / Continue** | M + N (直连) | L3f ⬜ + L1 ✅ | mcp_jsonrpc ⬜ + openai/anthropic ✅ | Tool Tape + Chat Tube | 🟢 2 周（走 M 面） |
| 3' Copilot | — | N (厂商中继) | L1 ✅ | copilot_proxy ⬜ | Chat Tube 精简 | 🟠 6+ 周（IP 风险） |
| 4 CLI agent | **Claude Code / Codex CLI** | M + N | L3f ⬜ + L3e ⬜ | mcp_jsonrpc + 现有 | **Run Trace ⬜** | 🟢 2 周（M 面） |
| 4' Aider | — | N | L3e ⬜ | openai/anthropic ✅ | Run Trace ⬜ | 🟡 3 周 |
| 5 原生 | Raycast / BoltAI | N | L1 ✅ | 现有 | Chat Tube | 🟡 1-2 周（窄） |
| 6 本地 LLM | **LM Studio / Ollama** | N (localhost) + M | L3e ⬜ + L3f ⬜ | openai 兼容 ✅ | Chat Tube (model=local) | 🟢 几天 |

**图例**：🟢 现有管道复用率 > 80%；🟡 需新增局部模块；🟠 需新协议
解码或 IP 谈判。

> **ADR-016 / ADR-017 对账注记（2026-05-09）**
>
> 上表 1a / 1a' / 2a 行的 "UCS 层" 列保留了当时的 `L3b ⬜` 标注
> 以备前后参考；**实际 v1.1 实施已切换为 L3d CDP launcher**（per
> ADR-016 §3.1，ASAR repack 路线被否决），不再触碰任何 Anthropic /
> Cursor / Windsurf 的 `app.asar`。Claude Desktop 的 1a 行因此应
> 读作 `pce_mcp server ✅（M 面 posture A）+ pce_mcp_proxy ✅（M 面
> posture B / L3f）+ pce_app_launcher ⚠️（H 面 / L3d CDP，e2e 验证
> 中）`，其 "🟢 1 周可上线" 距离评估**已被超越**：Phase 1–3 在
> 2026-05-09 已落地（v1.1.0-alpha.4 → alpha.6），剩余 ~3–5 天为
> normalizer 落 `interaction_kind` + 真机 e2e 收尾。
>
> 测试编排维度（本表未列出）由 **ADR-017** 统一新建 `pce_test_conductor/`
> 覆盖：8 个 MCP tool + 9 值 FailureKind + JSON Schema canary 主动
> 巡检桌面 H 面更新打穿（DR-08）+ 3 个补丁模板。Phase 4.D.1–6
> 与本表行均一一对账。

---

## 8. 第七层 · P5.B 攻击顺序推论

**核心策略转折**：从「先挑产品」转为「先点亮捕获面」。

| 顺序 | 捕获面 | 一面通杀产品数 | 工时估 | 战略价值 |
|---|---|---|---|---|
| **1** | **M 面 · 姿态 B (L3f MCP middleware)** | **9** — Claude Desktop, Cursor, Windsurf, Zed, Cline, Continue, Claude Code, Codex CLI, Goose, AnythingLLM | **3-5 天** | **同时开 9 个产品的锁** |
| 2 | M 面 · 姿态 A (pce_mcp server) 资产正名 | **同上 9 个** | **1 晚** | 已有代码打包发行（install 文档 + e2e） |
| 3 | H 面 · Electron preload (L3b) | 4-5 — Claude/Cursor/Windsurf/Trae | 2-3 周 | 解锁 SSL pinning + 抓 chat 文本（M 面之外的补位） |
| 4 | N 面 (local) · L3e gateway | 5-8 — Aider/LM Studio/Ollama/Jan/... | 1 周 | 长尾本地党 |
| 5 | N 面 (cloud-pinned) · L2 Frida | 1 — ChatGPT Desktop | 2-3 周（→ P6） | 完成性 |
| 6 | U 面 · L3d/L4b AX/UIA | 3-5 — Raycast 等原生 | 4-6 周（→ P6/P7） | 兜底 |

### 8.1 P5.B 官方攻击顺序（本文件建议）

```
P5.B.0  MCP 资产正名 (姿态 A)          1 晚   ██
P5.B.1  MCP middleware proxy (L3f)     3-5 天 █████
P5.B.2  L3b Electron preload (Claude)  1-2 周 ██████████
P5.B.3  L3b preload (Cursor/Windsurf)  1-2 周 ██████████
P5.B.4  L3e gateway (CLI / local LLM)  1 周   █████
```

这个顺序保证：
- **每一步都是可发布的产品切片**（符合 ADR-009 P5.A 精神）。
- **姿态 A/B 先上，立刻覆盖 9 个 MCP-aware 产品**，即使后续 L3b
  没做完，这些产品**也已经有数据流进 PCE**。
- **L3b/L3e 上线后是增量优化**（让不走 MCP 的路径也能被抓），不是
  替换关系。

---

## 9. 附录 A · 新桌面 AI 产品 5 步体检法

当下次出现一个新桌面 AI 产品（比如一个我们还没听过的 Sora 客户端 /
某个 agent startup 的桌面 app），按这个顺序在 5 分钟内完成定位：

1. **它把对话或动作暴露在哪几张皮上？**（N / H / M / U）
2. **PCE 现有的 UCS 层里，哪一个已经/需要扩展才能插管子？**（查 §4 表）
3. **它的原始流量需要哪一个 normalizer §① provider 适配器？要不要
   新写？**（查 §5.1 推论）
4. **它的主 archetype 是哪一种？**（Chat / Tool / Code / Run）
5. **走完抓→存→渲，能不能和已有的 storage_paths / render_paths
   契约对账？**（如果需要新契约，就是一次 migration 级别的工作）

**通过标准**：五个问题答完，你应该能用一句话回答
「这个新产品距离 PCE 有多远」：

```
<产品名> 走 <UCS 层>，复用 <normalizer>，渲染 <archetype>，
距离 PCE 大约 <工时>。
```

---

## 10. 附录 B · 与现有文档的关系

| 现有文档 | 与本文件关系 |
|---|---|
| `Docs/docs/PROJECT.md` | 本文件 = P5.B 阶段的战术细化，不改变 PROJECT.md 的长期演化路径（记录→看见→理解→干预） |
| `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` | 本文件 **新增 L3f** 到 UCS 层枚举，建议 UCS 下一版（v0.3）把 L3f 列入正式章节 |
| `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md` | 本文件**推翻** §P5.B 原 scope 的「L3b + L3c 双栈」策略，提议改为 **L3f 先行 + L3b 次之 + L3c 延后 P5.C**。需要新 ADR 记录该变更 |
| `Docs/research/AI-PRODUCT-LANDSCAPE-AND-CAPTURE-ANALYSIS.md` | 保留其市场事实（§1 8 品类），**废弃**其按「产品形态」作为一等公民的默认分类视角 |
| `Docs/research/IMPLEMENTATION-ROADMAP.md` | STEP 3/4 顺序已被 P5.B.x 替换；文档本身作为 Foundation 阶段历史档案保留 |
| `Docs/stability/SITE-TIER-MATRIX.md` | 浏览器扩展的站点分层；本文件**不涉及**，但 2026-05-08 同日 S2/S3 正式延后决定应在 SITE-TIER-MATRIX 里补注 |
| `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` | 配套文档（拟），记录 browser-ext 硬冻结状态与 known issues |
| `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` | 配套文档（拟），把本文件 §8 攻击顺序翻译成具体 TASK-007 条目 |

---

## 11. 附录 C · 术语表

**捕获面 (Capture Plane)** — PCE 能在用户×AI 交互链路上放置 hook
的物理位置。本文件定义 4 张：N / H / M / U。

**UCS 层 (UCS Layer)** — Universal Capture Stack 的技术实施层。
L1-L4b，每个 UCS 层落在 1-2 个捕获面上。

**姿态 A / B / C** — PCE 在 M 面（MCP）上的三种角色：
- A：PCE 作为 MCP server（被 agent 主动调用上报） — 已存在
- B：PCE 作为 MCP middleware proxy（夹在 host ↔ server 之间透明抓）
  — L3f，待建
- C：MCP-aware 进程内 hook（preload 同时观察子进程 MCP pipe）
  — L3b 扩展，待建

**Archetype Tube** — Dashboard 上一类会话的渲染原型。本文件定义
4 种：Chat / Tool Tape / Code Session / Run Trace。

**storage_paths / render_paths** — 2026-05-08 引入的契约校验机制。
一个 message 在 `content_json` 里必须同时包含「存储事实」路径（如
`threading.branch_id`）和「渲染契约」路径（如
`rich_content.branch_tree`），E2E 探针逐路径对账。

**interaction_kind** — 拟新增的 message 字段，区分 IDE 类产品内不同
的 AI 交互模式：`chat` / `completion` / `agent_loop` / `composer`
/ `lint_diff` / 其他。

**Host** 与 **Server** — 在 MCP 语境下：Host = 调方（Claude
Desktop / Cursor），Server = 被调方（filesystem / git / 自定义）。
PCE 的姿态 A 是成为 Server，姿态 B 是成为两者之间的中介。

---

## 12. 使用本文件的正确姿势

- **新 agent 接手 P5.B**：先读 `Docs/docs/PROJECT.md` §1-§11，然后
  读本文件全部，然后读 `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`
  （拟），总计 2-3 小时可建立完整认知。
- **要为新产品写 coverage / task doc 时**：先在本文件 §7 总图里定位
  该产品，再在 §9 附录 A 跑一遍 5 步体检法，最后才动笔。
- **要改变 P5.B 攻击顺序时**：直接修改本文件 §8，并在 `Docs/docs/decisions/`
  新增 ADR 记录原因（类似 ADR-009 记录 UCS 采纳）。
- **要新增一个捕获面或 archetype 时**：这是**一等事件**，必须同时
  更新本文件 §2 / §6 + UCS-DESIGN.md + 新建 ADR。
