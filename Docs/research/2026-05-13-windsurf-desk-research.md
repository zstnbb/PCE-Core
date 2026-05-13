# P4 Windsurf (Cascade) — Desk Research

> **Date**: 2026-05-13
> **Status**: hypothesis (pre-RECON formal closure)
> **Tier**: D1 (per DESKTOP-PRODUCT-MATRIX §4.4)
> **Predecessor**: S2 harvest session (2026-05-13 02:30) — empirical data already in hand
> **Next**: 阶段 (2) RECON formal findings, see [Open Questions §7]

---

## 1. 产品概览

- **厂商**: Codeium (2026 年被 Cognition/Devin 收购)
- **主网址**: https://windsurf.com / https://codeium.com
- **客户端类型**: Desktop IDE (基于 VS Code fork, Electron)
- **平台**: Windows + macOS + Linux
- **主要 surface**:
  - **Cascade** — 主聊天面板 (Write mode + Chat mode)
  - **Autocomplete (Tab)** — 行内代码补全
  - **Command** — 内联命令 (Ctrl+I)
  - **Code Lenses** — 代码上方的 AI 操作按钮
  - **Terminal AI** — 终端内 AI 辅助
  - **MCP integration** — 支持 MCP 协议连接外部工具
  - **Memories & Rules** — 跨会话上下文持久化
  - **Workflows** — 可复用的 agent 工作流
  - **Worktrees** — git worktree 并行 Cascade 任务
  - **Agent Command Center / Spaces / Devin** — 高级 agent 功能
- **与已接入 PCE 的产品相似度**:
  - 与 Cursor (P3) 极为相似 — 同为 VS Code fork, 同为 IDE-class AI
  - 与 Claude Desktop (P1) 不同 — Windsurf 是 IDE 不是纯聊天客户端
  - Desktop 客户端 ≠ web app 套壳 — Cascade 是原生 Electron 面板

---

## 2. 数据流假说

### 2.1 网络面 (N)

- **聊天 (Cascade)**: gRPC over HTTPS → `server.codeium.com`
  - 管理面 (GetCliTeamSettings / GetUserStatus / GetPlanStatus / GetCliModelConfigs) — **已验证可 MITM**
  - 聊天面 (Cascade 实际对话流) — **已验证 cert-pinned**, TLS handshake failure
  - 端点推测: `/exa.language_server_pb.LanguageServerService/GetChatMessage` 或类似 gRPC service
- **Autocomplete**: gRPC → `server.codeium.com` (同一 host, 不同 service path)
- **协议**: gRPC (protobuf wire format), NOT plain HTTP JSON
- **已知域名**: `server.codeium.com`, `auth.windsurf.com`, `api.codeium.com`

### 2.2 本地持久化 (H / L3g)

- **S2 harvest 实测结论 (修正)**: Cascade **在本地存储了会话数据, 但是加密的**
  - `~/.codeium/windsurf/cascade/*.pb` — 17 个 protobuf 文件 (56-198 KB)
    - 高熵数据, 无可读字符串 → **加密存储** (非明文 protobuf)
    - 文件名是 UUID, 对应 Cascade 会话 ID
  - `~/.codeium/windsurf/memories/*.pb` — 同样加密
  - `~/.codeium/windsurf/implicit/*.pb` — 同样加密
  - `~/.codeium/windsurf/code_tracker/active/<repo>_<commit>/` — 文件快照 (明文)
    - 存储 Cascade 编辑过的文件副本, 以 git commit hash 为目录名
    - 这是代码变更追踪, 不是聊天内容
  - `%APPDATA%\Windsurf\User\History\` — VS Code 文件编辑历史 (非聊天)
- **MCP config**: `~/.codeium/windsurf/mcp_config.json` (明文 JSON)
- **结论**: L3g 路线对 Windsurf 聊天面 **不可行** (数据存在但加密)

### 2.3 MCP 面 (M / L3f)

- Windsurf 原生支持 MCP (stdio / HTTP / SSE 三种 transport)
- MCP config 位于 `~/.codeium/windsurf/mcp_config.json`
- 支持 tools / resources / prompts
- **假说**: 可以注册一个 PCE MCP server, Cascade 每次对话时调用 `pce/capture_turn` tool
- **限制**: 100 tools 上限; 需要 Cascade 主动调用 tool (不保证每轮都调)
- **可行性**: 中等 — 需要 prompt engineering 或 Cascade Hooks 机制配合

### 2.4 UI 面 (U)

- Electron 应用, 理论上可以用 UIA / CDP
- Cascade 面板是 webview 渲染
- **假说**: CDP (Chrome DevTools Protocol) 可以 attach 到 Cascade webview, 拦截 fetch/XHR
- 这是 DESKTOP-PRODUCT-MATRIX §4.4 定义的 Primary plane (H / L3d)

### 2.5 Electron preload 注入 (L3b)

- Windsurf 是 Electron app, 理论上可以注入 preload script
- 可以 hook `net.request` / `electron.net` 模块拦截所有网络请求
- **风险**: Codeium 可能检测 preload 注入 (但目前无证据)
- 这是 Pro 功能路线

### 2.6 NODE_EXTRA_CA_CERTS 注入 (L1 增强)

- Windsurf 基于 Electron (Node.js runtime)
- 设置 `NODE_EXTRA_CA_CERTS` 环境变量可以让 Node 信任额外的 CA
- **假说**: 启动 Windsurf 时注入 mitmproxy CA cert, 可以解除 cert-pinning
- **已有证据**: S2 harvest 中 117 次 TLS handshake failure 来自 `server.codeium.com`
- **风险**: 如果 Codeium 使用了 Node 层面之外的 pinning (如 native module), 此法无效

---

## 3. 与 PCE UCS 层映射

| UCS 层 | 适用性 | 备注 |
|---|---|---|
| **L0** (Kernel) | ❌ Pro only | 不在 v1.x scope |
| **L1** (TLS MITM) | ⚠️ 管理面 OK, 聊天面 cert-pinned | 需 NODE_EXTRA_CA_CERTS 解锁 |
| **L2** (Frida) | ❌ Pro only | 备选方案 |
| **L3b** (Electron preload) | 🟡 Pro only | 最强但最侵入 |
| **L3d** (CDP) | 🟡 可能 | 需验证 Windsurf 是否接受 `--remote-debugging-port` |
| **L3f** (MCP middleware) | ✅ 官方支持 | 最安全路线, 但覆盖面有限 |
| **L3g** (本地持久化) | ❌ 不可行 | S2 已证实 Cascade 不本地存储聊天 |
| **L4b** (UIA) | 🟡 备选 | 可以读 UI 文本但不精确 |

**推荐主路线**: **Option A (NODE_EXTRA_CA_CERTS + L1)** — 解锁 cert-pinning 后走已有 mitmproxy 管线

**推荐辅助路线**: **L3f (MCP middleware)** — 作为 L1 不可用时的 graceful fallback

---

## 4. 与 cognitive framework plane 映射

| Plane | 适用性 | 首选层 |
|---|---|---|
| **N** (网络) | ✅ 主路线 (需解锁 pinning) | L1 + NODE_EXTRA_CA_CERTS |
| **H** (进程/本地) | ❌ 聊天面不可行 | — |
| **M** (MCP) | ✅ 辅助路线 | L3f |
| **U** (UI) | 🟡 备选 | L3d CDP / L4b UIA |

---

## 5. Tier 终决 (回顾阶段 0)

DESKTOP-PRODUCT-MATRIX §4.4 定义 P4 Windsurf 为 **Tier D1** (高价值但复用 P3 scaffolding, 边际风险低)。

基于 desk research 发现:
- **复杂度比预期高**: cert-pinning 阻断了最简单的 L1 路线
- **但有明确解法**: NODE_EXTRA_CA_CERTS 是 Electron 标准机制, 不是 hack
- **L3g 不可行**: 与 Cursor/Copilot 不同, Windsurf 不本地存储聊天

**结论**: 维持 D1 tier, 但实施路线从 "复用 P3 L3d scaffolding" 调整为 "L1 + cert trust injection"。

---

## 6. 探针 RECON 计划

### 探针 1: NODE_EXTRA_CA_CERTS 验证 (网络面 N)

**目的**: 验证设置 `NODE_EXTRA_CA_CERTS=<mitmproxy-ca-cert.pem>` 后, Cascade 聊天流量是否能被 mitmproxy 捕获。

**最小输入**:
1. 关闭 Windsurf
2. 设置环境变量 `NODE_EXTRA_CA_CERTS=C:\Users\ZST\.mitmproxy\mitmproxy-ca-cert.pem`
3. 设置 `http_proxy` / `https_proxy` 指向 mitmproxy (127.0.0.1:8080)
4. 启动 Windsurf
5. 在 Cascade 中发送一条 prompt: "What is 2+2?"
6. 检查 mitmproxy 是否捕获到 `server.codeium.com` 的聊天请求

**预期产物**:
- 成功: raw_captures 中出现 Cascade 聊天的 gRPC request/response pair
- 失败: 仍然 TLS handshake failure → 说明 Codeium 有额外 pinning 层

**时间预算**: ≤ 30 分钟

### 探针 2: CDP attach 验证 (UI 面 U / L3d)

**目的**: 验证 Windsurf 是否接受 `--remote-debugging-port=9222` 启动参数。

**最小输入**:
1. 启动 Windsurf: `windsurf.exe --remote-debugging-port=9222`
2. 浏览器访问 `http://127.0.0.1:9222/json`
3. 检查是否返回 CDP target 列表

**预期产物**:
- 成功: CDP target 列表包含 Cascade webview
- 失败: 端口无响应 / Windsurf 拒绝该参数

**时间预算**: ≤ 15 分钟

### 探针 3: MCP capture tool 验证 (M 面 / L3f)

**目的**: 验证注册一个 PCE MCP server 后, Cascade 是否能调用 capture tool。

**最小输入**:
1. 在 `~/.codeium/windsurf/mcp_config.json` 注册 `pce` MCP server
2. 重启 Windsurf
3. 在 Cascade 中发送 prompt 并观察是否调用了 PCE tool

**预期产物**:
- 成功: PCE MCP server 收到 tool call
- 失败: Cascade 不主动调用 → 需要 prompt engineering

**时间预算**: ≤ 30 分钟

---

## 7. Open Questions

- **Q1**: NODE_EXTRA_CA_CERTS 是否足以解除 Codeium 的 cert-pinning? (Codeium 可能在 native addon 层做了额外 pinning)
- **Q2**: Windsurf 的 Cascade 聊天 gRPC endpoint 的确切 service path 是什么? (管理面已知, 聊天面未知)
- **Q3**: gRPC protobuf schema 是否可以从 Windsurf 的 app.asar 中提取? (用于解析捕获的 protobuf 数据)
- **Q4**: Cascade 的 "Memories" 功能是否在本地存储了足够的对话摘要可供 L3g 利用?
- **Q5**: Windsurf 是否支持 `--remote-debugging-port` (VS Code fork 通常支持, 但 Codeium 可能禁用了)
- **Q6**: MCP L3f 路线中, 如何确保 Cascade 每轮都调用 capture tool? (Cascade Hooks 机制是否可用?)
- **Q7**: Cognition/Devin 收购后, Windsurf 的架构是否有重大变化? (Devin integration 是否改变了数据流?)

---

## 8. 技术路线决策矩阵 (供 owner 决策)

| 路线 | 工时 | 覆盖面 | 侵入性 | 风险 | 推荐 |
|---|---|---|---|---|---|
| **A: NODE_EXTRA_CA_CERTS + L1** | 1-2h | 全量 (聊天+补全+管理) | 中 (需重启 Windsurf) | Codeium 可能有额外 pinning | ⭐ 首选 |
| **B: MCP middleware (L3f)** | 3-4h | 仅 Cascade 主动调用时 | 低 (官方 API) | 不保证每轮调用 | 辅助 |
| **C: CDP attach (L3d)** | 2-3h | webview 内 fetch 拦截 | 中 | 需验证 CDP 可用性 | 备选 |
| **D: Named pipe IPC (L3h)** | 5-8h | 全量 (如果能解析) | 高 (逆向工程) | pipe 路径可能变化 | 不推荐 v1.x |

**建议**: 先跑探针 1 (30 min), 如果 A 路线通, 直接走 A; 如果不通, 走 B+C 组合。

---

## 9. References

- S2 Harvest Results: `Docs/harvest/HARVEST-SESSION-S2-RESULTS.md` §2
- DESKTOP-PRODUCT-MATRIX §4.4: `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
- Windsurf MCP docs: https://docs.codeium.com/windsurf/cascade/mcp
- Windsurf SSL inspection docs: https://docs.windsurf.com/troubleshooting/windsurf-ssl-inspection
- Windsurf proxy config docs: https://docs.windsurf.com/troubleshooting/windsurf-proxy-configuration
- AI Product Landscape: `Docs/research/AI-PRODUCT-LANDSCAPE-AND-CAPTURE-ANALYSIS.md`
- Cognitive Framework: `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
