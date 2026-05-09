# ADR-015: UCS 增设 L3f — MCP middleware 层

- Status: Accepted
- Date: 2026-05-09
- Phase: P5.B.1（伴随 `pce_mcp_proxy/` 一同落地）
- Supersedes: 无（ADR-009 §UCS 层枚举的增设）
- Related: ADR-009（UCS 采纳）、ADR-010（Open Core 边界）、ADR-013（`pce_mcp` 系归 OSS 归属，本 ADR 同样适用于 `pce_mcp_proxy/`）、ADR-012（P5.B scope 重排，把 L3f / L3b 提到 L3c 之前）

---

## 1. 决议（一句话）

**在 UCS（Universal Capture Stack）层枚举里正式增设 `L3f — MCP middleware`，作为与 `L3a/L3b/L3c/L3d/L3e` 并列的一等捕获层；其参考实现是 `pce_mcp_proxy/`（捕获姿态 B），与 `pce_mcp/`（姿态 A）共同覆盖 MCP 协议面（M）的工业级捕获。**

---

## 2. 背景：为什么 UCS 原稿没有 L3f

UCS v0.2（[`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`](../UNIVERSAL-CAPTURE-STACK-DESIGN.md)）的层枚举：

- **L1** 系统代理 + CA — 网络面 N
- **L2** SSL pinning bypass — 网络面 N（突破）
- **L3a** 浏览器扩展（WXT）— H + N
- **L3b** Electron preload 注入 — H + M（子进程可见）
- **L3c** IDE / VS Code Extension API — H
- **L3d** 原生 app accessibility（AX / UIA）— U
- **L3e** LiteLLM / OpenAI-compatible gateway — N（重定向）
- **L4b** UI hook / OCR — U

UCS v0.2 起草时（2026-04-18，决议见 `decisions/2026-04-18-ucs-and-release-strategy.md`），P5 阶段重心是浏览器子系统 + 桌面 chat（Electron）。MCP 协议的产品落地主要被 Claude Desktop / Cursor 等带动起来，是 2026-05 之后才进入 P5.B 视野的强信号。框架 v2（[`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../../../research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md) §4）首次系统总结了 M 面的三种捕获姿态（A/B/C），并把姿态 B 显式标注为 **L3f — MCP middleware proxy**。

ADR-012（P5.B scope 重排）把 L3f / L3b 摆到 L3c 之前，但没有正式更新 UCS 层枚举本身——这是因为彼时 `pce_mcp_proxy/` 还在拟稿阶段，过早写 ADR 修 UCS 文档会与实际实现脱节。P5.B.1 已让 `pce_mcp_proxy/` 工业化落地（11 个 e2e_mcp 测试 + 真 stdio 协议），且 `pce_mcp/`（姿态 A）已完成正名并发布安装文档（P5.B.0）——**两块都齐了，UCS 增设 L3f 的时机到了**。

---

## 3. L3f 的精确定义

### 3.1 一句定义

> **L3f — MCP middleware**：在 MCP host（Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI / Cascade-Windsurf / 任何 MCP 主机）与 MCP server（filesystem / git / postgres / sequential-thinking / 用户自建）之间，以 stdio JSON-RPC 2.0 为协议面 M 的捕获层。

### 3.2 与既有层的边界

| 比较维度 | L3a 浏览器扩展 | L3b Electron preload | **L3f MCP middleware** |
|---|---|---|---|
| 捕获面 | H + N | H + M（子进程） | **M（独占）** |
| 工艺 | DOM + content script + fetch hook | preload + IPC + child_process hook | **stdio JSON-RPC 帧观察** |
| 协议结构化程度 | T2 半结构化（DOM 体）| T2/T1（preload 拦下的 fetch 已结构化） | **T1 完全结构化**（JSON-RPC 是协议级 schema） |
| 部署侵入度 | 装扩展 | 装扩展或 patch electron | **改一行 host 配置** |
| 跨进程边界 | 局限于一个浏览器 | 局限于一个 Electron 进程 | **跨语言、跨进程、跨主机通杀**（任何说 stdio MCP 的客户端都能套） |

L3f 与 L3a / L3b 不竞争，而是**互补**：
- L3a 抓 web 端 chat（H + N）
- L3b 抓 Electron 端 chat（H + M chat 子集）
- **L3f 抓所有 MCP 工具调用（M tool 子集）**——L3a / L3b 都吃不到的部分

### 3.3 与 L1 系统代理的关系

L1（mitmproxy）覆盖 HTTPS over TCP 的 N 面。MCP 协议本身**不走 HTTPS**——它是 stdio + JSON-RPC，进程内 pipe，根本不出 socket。所以 L1 即便完整工作，也**结构性看不到任何 MCP 流量**。这是 L3f 必须独立存在的根本原因，也是它区别于 L1 / L2 的核心结构差异。

### 3.4 两种实现姿态

L3f 的引入同时合法化两种已落地的实现：

```
姿态 A · pce_mcp/                   姿态 B · pce_mcp_proxy/
┌──────────┐    ┌──────────┐       ┌──────────┐    ┌──────────┐    ┌──────────┐
│   host   │ ── │ pce_mcp  │       │   host   │ ── │ proxy    │ ── │ upstream │
└──────────┘    │ (server) │       └──────────┘    │ + 旁路写  │    │ MCP svr  │
                └─────┬────┘                        └─────┬────┘    └──────────┘
                      ▼                                   ▼
                   SQLite                              SQLite
```

- **A**：`pce_mcp/` 自身就是一个 MCP server，提供 `pce_capture / pce_query / ...` 工具，依赖 agent 主动调用（agent-cooperative ledger）。
- **B**：`pce_mcp_proxy/` 透明夹在 host 和真 MCP server 之间，无差别拷贝每一帧（transparent wire-tap）。

两者的产物都进入 `raw_captures` 表，但 `source_id` 不同：
- 姿态 A → `mcp-default`（`source_type='mcp'`）
- 姿态 B → `mcp-proxy-default`（`source_type='mcp_proxy'`，由 migration 0009 注册）

---

## 4. 决议内容

### 4.1 UCS v0.3 层枚举修订

`UNIVERSAL-CAPTURE-STACK-DESIGN.md` 的层枚举增加一行：

```
L3f — MCP middleware（夹在 host ↔ server 之间的 stdio JSON-RPC 透明代理）
       捕获面：M
       OSS / Pro：OSS（与 L3a/L1 同级）
       参考实现：pce_mcp_proxy/（姿态 B）
       兄弟模块：pce_mcp/（姿态 A，同样落 L3f）
```

`ARCHITECTURE.md` 在 v0.3 章节同步标注 v0.3 = "UCS 含 L3f"。

### 4.2 source_type 枚举扩展（已落地）

`CaptureIn.source_type` 在 P5.B.1 已新增：

| 枚举值 | UCS 层 | 状态 |
|---|---|---|
| `mcp_proxy` | L3f（姿态 B）| ✅ 已落地（migration 0009 注册 source row）|

`mcp`（姿态 A）保持不变，仍然映射到 L3f。

### 4.3 Open Core 归属

L3f 的两个参考实现（`pce_mcp/` 和 `pce_mcp_proxy/`）均归 **OSS（Apache-2.0）**，与 ADR-013 的判断一致并扩展之。理由：

- 这两个包都属于"捕获用户可见的 AI 流量"的能力底座
- 用户的安装动机受 **paywall 高度敏感**，把它们关进 Pro 等于把整个 P5.B 战略锁死
- 它们的代码都不依赖任何 PCE 专有的 closed-source 资产（no Pro DB、no Pro UI、no Pro fleet ops）

将来构筑在 L3f 之上的**多租户 fleet ops / 团队级 MCP audit / 跨主机 session 同步**等增值层，仍然可以落 Pro，只要它们不污染 `pce_mcp/` 与 `pce_mcp_proxy/` 自身。

### 4.4 测试义务

任何未来对 L3f 实现的修改 PR 必须：

- 引用本 ADR 编号 `ADR-015`
- `tests/e2e_mcp/` 全部 case GREEN（11 个 stdio e2e + 11 个 proxy e2e = 22 个）
- 不退化 `tests/test_mcp.py` 的 11 个 in-process case
- 如新增协议方法、source_id、normalizer 路径，写迁移 + 在本 ADR 后续修订或新 ADR 中登记

---

## 5. 决议依据

### 5.1 为什么不用一个新的 source_type 就够，非要动 UCS 层枚举？

UCS 是 PCE 全捕获能力的**北极星架构**。把 L3f 留在 source_type 层、不进 UCS 层枚举，会出现四个具体问题：

1. **Open Core 边界讨论会反复重燃**。UCS 层枚举是 ADR-010 直接引用的判断锚点。L3f 不在枚举里，每次有人问"`pce_mcp_proxy/` 归 OSS 还是 Pro"都得重读 ADR-013 + 框架 v2 + 这个 ADR 才能合议；进枚举后，"L3f → OSS（与 L3a 同级）"是一句话能查的事实。
2. **roadmap 缺少索引**。HANDOFF / PROJECT.md 现在用 UCS 层名作为产品×层 unlock 矩阵的列标题。L3f 不在枚举里，等于把 P5.B 主战场之一**藏起来**。
3. **架构教学价值丢失**。新 agent 接手时读 UCS 设计文档作为入门，文档里没 L3f 就以为 PCE 不做 MCP——实际正好相反。
4. **未来 L3f 子层（L3f-A / L3f-B / L3f-C）扩展无依据**。现在已经有姿态 A / B；姿态 C（preload + MCP 子进程注入）将来要落，没有 UCS 层做祖先，分类会乱。

### 5.2 为什么是 `L3f` 而不是 `L3g` 或单独的 L4？

- 现有 `L3a..e` 是**应用 / 客户端层**的捕获工艺（浏览器扩展、preload、IDE API、AX、gateway）。L3f 在抽象高度上是同级的——它也是"贴近某种特定客户端形态做工艺级捕获"。
- L4 系列预留给**渲染 / OCR / UI hook 等更高层 / 更脏的工艺**（L4b 已占位）。MCP middleware 远比 OCR 干净，落 L4 会误导。
- 字母 `f` 仅是接续编号，无语义负担。

### 5.3 为什么把这件事压到 P5.B.1 而不是 P5.B.0？

ADR-012（P5.B scope 重排）已说清：P5.B.0 = `pce_mcp/` 资产正名（姿态 A 工业化），P5.B.1 = `pce_mcp_proxy/` 落地（姿态 B 工业化）。**UCS 文档的层枚举改写应该在两个姿态都跑得通之后做，避免出现"文档先于代码"的反向漂移**。这条 ADR 在 P5.B.1 收尾时落，正好对齐。

---

## 6. 影响清单

### 6.1 文档同步项

- `UNIVERSAL-CAPTURE-STACK-DESIGN.md` — §层枚举增加 L3f；§Open Core 表把 L3f → OSS 列出
- `ARCHITECTURE.md` — v0.3 章节注明含 L3f
- `Docs/docs/PROJECT.md` — §12 ADR 列表把 ADR-015 状态由 "拟" 改为 "✅ P5.B.1 落地"
- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` — §7 ADR 表同步
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` — §8 sign-off 把 ADR-015 标 ✅

### 6.2 代码现状（P5.B.1 已落地）

- `pce_mcp_proxy/` —— 完整包，6 个文件
- `pce_core/db.py` —— `SOURCE_MCP_PROXY` 常量
- `pce_core/migrations/0009_mcp_proxy_source.py` —— 注册 `mcp-proxy-default` 源
- `pce_core/migrations/__init__.py` —— `EXPECTED_SCHEMA_VERSION = 9`
- `pce_core/normalizer/mcp_jsonrpc.py` —— Tier 1 归一化器
- `pce_core/normalizer/registry.py` —— 注册新 normalizer
- `tests/e2e_mcp/test_pce_mcp_proxy_stdio.py` —— 11 个 e2e cases
- `tests/e2e_mcp/_mock_upstream.py` —— 测试用迷你 MCP server
- `Docs/install/PCE_MCP_PROXY_INSTALL.md` —— 8 主机安装指南

### 6.3 不影响

- 浏览器子系统（已硬冻结）
- L1 / L2 / L3a / L3d / L3e / L4b — 各自独立
- 现有 `pce_mcp/` 行为（仅在 README.md 里把姿态 B 状态从 "⏳" 改为 "✅"）

---

## 7. Open question · 留给 P5.B.2+

- **session 边界精化**：v1 用 `mcp-proxy:<upstream>:<YYYY-MM-DD>` 做 session_key（按主机+日期切分）。P5.B.2 应该把 `initialize` ↔ proxy 退出当作正式的 session lifecycle，把跨日同一会话保持完整。
- **server-initiated request 配对**：当前 v1 把 sampling / roots/list 等服务器主动请求记为单帧观察，不做 id 配对。如果将来 host-side 的回应路径（response 走"request"通道、id 由 host 分配）值得追踪，需要双向 pending map + 路由策略。
- **大 payload 限速**：当前观察队列无界，理论上百 MB 级 `tools/call` 响应在低 I/O 系统上可能内存累积。P5.B.x 视生产数据决定是否加 high-water mark + drop policy。
- **tools/list 与 resources/list 的归一**：v1 故意把这两类 enumeration 留在 Tier 0 不做归一。如果 dashboard 需要 "agent X 在某次会话里看到了什么工具目录" 这一视图，再加。

任何处理上述 open question 的 PR 必须新写 ADR 引用本 ADR 作为前置。

---

*End of ADR-015.*
