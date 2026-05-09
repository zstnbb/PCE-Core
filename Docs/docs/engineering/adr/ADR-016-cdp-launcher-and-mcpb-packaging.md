# ADR-016: P5.B.2 改走 CDP launcher + `.mcpb` 打包；ASAR repack 路线否决

- Status: Accepted
- Date: 2026-05-09
- Phase: P5.B.2（kickoff 阶段，未启动实施前修订）
- Supersedes: `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §4.2 P5.B.2 / §4.3 P5.B.3 的旧"L3b Electron preload"实施方案
- Related: ADR-009（UCS 采纳，L3d=CDP 在 UCS canonical 中已定义）、ADR-010（Open Core 边界）、ADR-012（P5.B scope 重排）、ADR-013（`pce_mcp/` OSS 归属）、ADR-015（UCS 增设 L3f）

---

## 1. 决议（一句话）

**P5.B.2 不再走 L3b Electron preload + ASAR repack 路线；改走 L3d Chrome DevTools Protocol launcher（复用 `pce_core/cdp/driver.py` 已有 80% 资产）作为 H 面捕获主路，并把 `pce_mcp/` 打包为 `.mcpb` Desktop Extension（Node 轻量 stdio→HTTP 代理）走 Anthropic 官方一键安装通道；L3b "Electron preload" 在 v1.1 不实施，留 Pro 后续考虑。**

---

## 2. 背景：为什么原 P5.B.2 选了 ASAR/preload，以及为什么这次否决

### 2.1 原方案的形成背景（kickoff §4.2 旧版）

`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §4.2 旧版（被本 ADR supersede 那段）规定 P5.B.2 = "L3b Electron preload (Claude Desktop)，1-2 周"，交付物包括 `pce_preload/claude_desktop/` 目录、`fetch` / `XMLHttpRequest` / `child_process.spawn` 三类 hook、改 `app.asar` 的安装脚本。

这个方案在 2026-05-08 P5.A 收尾起草 P5.B kickoff 时是合理的，理由是：

1. 当时的判断是 "Claude Desktop 不 SSL-pin，是 H 面 demo 的最易目标"
2. UCS doc §3.4.b 给 L3b 排了 `NODE_OPTIONS=--require <preload.js>` 等 4 种注入手段
3. `pce_agent_electron/` 是 UCS 蓝图里的"P5.B 核心新建"

### 2.2 为什么 2026-05-09 修订

P5.B.0 / P5.B.1 落地 + 一次事实核查（详见 §4.1）后，5 个新事实让原方案失效：

1. **`.mcpb` Desktop Extensions 已 GA**——Anthropic 在 2025-12 发布 spec 0.3，2026 上半年作为 Claude Desktop / Claude Code / "MCP for Windows" 的官方一键安装机制全面铺开。**这意味着我们原本要为 `pce_mcp/` 写的"手编 JSON 配置 + onboarding wizard"已经被 Anthropic 解决**——直接打 `.mcpb` 即享受 native 一键安装、自动更新、OS keychain 管密钥。
2. **Anthropic 消费者条款 §2** 明文禁止 "reverse engineer, decompile, or disassemble the Services"；Bedrock 商业条款同样明文禁 "reverse engineer or duplicate the Services"（[Anthropic legal](https://www.anthropic.com/news/updates-to-our-consumer-terms)）。ASAR repack 修改 Anthropic 签名的 Electron 二进制，**法律意义上属于灰色甚至越线**——这在持续付费的 power user 用户群体里制造了**不可承受的封号风险**（Anthropic 任意一次 ASAR integrity 升级即可批量识破打补丁的安装实例）。
3. **Electron ASAR integrity validation 默认 disabled**（[electronjs.org/docs/latest/tutorial/asar-integrity](https://www.electronjs.org/docs/latest/tutorial/asar-integrity)）但 Anthropic 可在任一版本通过 Electron Fuses opt-in。我们无法预防、也无法在被开启后兜底。
4. **CDP `--remote-debugging-port=9222` 在当前 Claude Desktop 上工作**——已由 [jedi.be 2026 blog](https://jedi.be/blog/2026/automating-claude-desktop-via-chrome-devtools-protocol/) 直接验证，并存在多个开源先例（`electron-mcp-server` / `claude-autoapprove` / `Claude_Automation` / `chrome-devtools-mcp`）作为参考实现。
5. **`pce_core/cdp/driver.py` 已存在 80% 实现**（UCS doc §2.3 注明，源自 P4 落地的 `pce_core/cdp/`）。原方案要从零写 `pce_preload/`；新方案站在已有 CDP infrastructure 上做 Claude Desktop 适配——工程复用度反而更高。

---

## 3. 决议内容

### 3.1 否决：L3b Electron preload + ASAR repack

**v1.1 期间不实施 L3b "Electron preload + ASAR repack" 路线**。具体含义：

- 不创建 `pce_preload/` 包
- 不写 `pce_agent_electron/`（UCS doc §3.4.b 列为"P5.B 核心新建"的目录）
- 不修改任何 Electron 应用的 on-disk `app.asar`
- kickoff 旧版 §4.2 P5.B.2 + §4.3 P5.B.3 中的 preload 交付物全部从 v1.1 路线图移除
- 旧版 §10.1 unlock 矩阵的 "P5.B.2 L3b Claude" / "P5.B.3 L3b IDE" 列重命名为 "L3d Claude" / "L3d IDE"

L3b 这一层在 UCS 蓝图里**保留**——它仍是 Pro 路径上有意义的捕获工艺（如果未来有理由突破"封号红线"，例如企业自部署场景里用户自愿承担风险）；只是 v1.1 的 P5.B.2 不再用它。

### 3.2 P5.B.2 新主路：L3d CDP launcher（复用 `pce_core/cdp/`）

P5.B.2 的 H 面捕获改为：

```
用户启动 Claude Desktop ──┐
                          ▼
              pce_app_launcher (新建)
              │
              ├─ 把 Claude.exe / Claude.app 加 --remote-debugging-port=9222
              ├─ Python CDP 客户端连 ws://127.0.0.1:9222/devtools/...
              ├─ 订阅 Network.responseReceived / Network.webSocketFrameReceived
              └─ 抓到 fetch payload → POST /api/v1/captures/v2 → pce_core
```

**模块定位**：

- 新建包：`pce_app_launcher/claude_desktop/` (OSS，per ADR-013 同等论据)
- 复用：`pce_core/cdp/driver.py`（80% 已有，P4 落地）
- 新写：CDP event filter + Anthropic API payload normalizer 适配（如果 `anthropic.py` 已有的逻辑能直接消费 CDP 事件结构则零增量）
- launcher 同时替换桌面快捷方式 → 用户双击同一图标即透明走 launcher

**source_type**：保留 `desktop_electron`（已在 kickoff §3 注 9 规划），归一化路径走 `pce_core/normalizer/anthropic.py`（与 web Claude 同 normalizer，确保 conversation_id 跨源合并）。

### 3.3 `pce_mcp/` 打包为 `.mcpb` Desktop Extension

`pce_mcp/` 当前是 Python MCP server。Anthropic 官方推荐 Node.js 实现以零依赖运行（[Anthropic engineering blog](https://www.anthropic.com/engineering/desktop-extensions)："Node.js ships with Claude for macOS and Windows, which means your bundle will work out-of-the-box"）。

**架构**：写一个 **Node 轻量代理**作为 `.mcpb` 内部 server——它通过 stdio 跟 Claude Desktop 讲 MCP，跟本机 `pce_core` HTTP API（`127.0.0.1:8000` 或用户配置端口）交互完成实际工作。

```
┌──────────────────────────────┐
│ Claude Desktop (host)        │
│        │ stdio MCP           │
│        ▼                     │
│  pce_mcp.mcpb (Node proxy)   │
│        │ HTTP                │
│        ▼                     │
│  pce_core (Python daemon)    │
│        │                     │
│        ▼                     │
│  本地 SQLite                  │
└──────────────────────────────┘
```

**新建路径**：`pce_mcp/mcpb/`（OSS，per ADR-013）
- `manifest.json`（spec 版本 0.3）
- `server/index.js` —— Node MCP server，~150 行，stdio↔HTTP 转发
- `node_modules/`（bundled per `npm install --production`）
- `icon.png`（PCE logo）
- `mcpb pack` 产物：`pce_mcp-<version>.mcpb`（ZIP archive）

**manifest.json 关键字段**：
- `server.type = "node"`
- `server.entry_point = "server/index.js"`
- `server.mcp_config.command = "node"` + `args = ["${__dirname}/server/index.js"]`
- `user_config`：暴露 `pce_core_url`（默认 `http://127.0.0.1:8000`）、`pce_data_dir`（可选覆盖）
- `compatibility.platforms = ["darwin", "win32", "linux"]`
- `privacy_policies` —— **必填**，明示"本扩展把你的 AI 对话记录到本机 SQLite，数据不离开你的电脑"
- `tools` 列出 6 个对应当前 `pce_mcp` 的工具签名
- `description` —— 必须**毫不含糊**说明"This extension captures all your Claude conversations into a local SQLite database for personal review"

**保留 Python 实现**：现有 `pce_mcp/server.py` 不删除，作为 advanced user 路径（`uv run pce-mcp` 可继续用）。`.mcpb` 是 default 一键路径，Python 是 power user fallback。

**migration 影响**：无（schema 不变，只是发行渠道多了一个）。

### 3.4 双轨分发

`pce_mcp.mcpb` 同时走两条发行路径：

1. **自托管**：放到 `https://github.com/zstnbb/PCE-Core/releases` 与每次 git tag 同步发布；用户走 Claude Desktop 的 Settings → Extensions → Advanced settings → "Install Extension..." 加载本地文件。
2. **Anthropic 公开 directory 提交**：通过 [desktop extensions interest form](https://docs.google.com/forms/d/14_Dmcig4z8NeRMB_e7TOyrKzuZ88-BLYdLvS6LPhiZU/viewform) 申请进入 directory，进入后享受**自动更新 + 一键搜索发现**。Review 失败的 fallback 是继续走自托管。

两轨并行不冲突。自托管是 v1.1 必交付项；directory 申请是 v1.1 nice-to-have。

### 3.5 姿态 C 重定义

在 `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §4.4 旧版本中，姿态 C = "Electron preload + MCP 子进程注入"——既听 chat 也跟踪 `child_process.spawn` 的 MCP stdio 子进程 pipe。

**重定义**：

| 维度 | 旧姿态 C | 新姿态 C |
|---|---|---|
| chat 文本捕获 | preload hook `fetch` | **L3d CDP `Network.responseReceived`** |
| MCP 子进程跟踪 | preload hook `child_process.spawn` | **L3f middleware（`pce_mcp_proxy/`）已覆盖** |
| 实现包 | `pce_preload/` | `pce_app_launcher/` + 已有 `pce_mcp_proxy/` |
| 部署侵入度 | 改 `app.asar` | 替换桌面快捷方式 |
| 封号风险 | 中等且上不封顶 | 接近零（CDP 是官方 Electron flag）|
| 信息覆盖 | chat + MCP 子进程 spawn 上下文 | chat + MCP 子进程**全帧** |

**结论**：新姿态 C 在 chat 维度持平、在 MCP 工具调用维度反而**更细**（`pce_mcp_proxy/` 给的是每一帧 JSON-RPC，preload 给的只是 spawn 上下文），且封号风险归零。这是一个全面优化，不是妥协。

### 3.6 Migration 重编号：`interaction_kind` 从 0009 → 0010

kickoff 旧版 §10.4 把 `messages.interaction_kind TEXT NULL` 标为 "Migration 0009 (proposed)"。但 P5.B.1 已用 0009 注册 `mcp-proxy-default` source row（ADR-015 §4.2 + `pce_core/migrations/0009_mcp_proxy_source.py`），`EXPECTED_SCHEMA_VERSION = 9`。

**修订**：

- `interaction_kind` 改为 **migration 0010**
- 落地时机：**P5.B.3 启动时**（不变；与原计划一致）
- `EXPECTED_SCHEMA_VERSION = 10`（P5.B.3 中升）
- kickoff §10.4 / §11 sign-off / matrix §8.2 同步改 "0009" → "0010"

### 3.7 P5.B.3（Cursor / Windsurf）继承新方案

kickoff 旧版 §10.3 写 "P5.B.2 must land before P5.B.3 starts, because Cursor / Windsurf preload (P5.B.3) reuses the install scaffolding pattern proven on Claude Desktop"——此论断仍成立，**但 scaffolding 改为 CDP launcher 而非 preload**：

- `pce_app_launcher/cursor/` + `pce_app_launcher/windsurf/` 复用 `pce_app_launcher/claude_desktop/` 抽象
- `pce_core/cdp/driver.py` 同样适用（VS Code fork 也是 Electron）
- `pce_core/normalizer/ide_interactions.py` 仍按原计划新建，`interaction_kind` 仍按原计划落 migration 0010

P5.B.3 工时估计**降低**（原 1-2 周 → 估 1 周），因为不需要为 `pce_preload/cursor/` 反向工程 Cursor 的 `app.asar` 内部结构。

### 3.8 P2 ChatGPT Desktop 重新评估（Open Question，不在本 ADR 决议范围）

旧 kickoff 把 P2 划到 P5.B.4-C 做"L3b preload best-effort，pinning blocks 则交 P6 Frida"。**CDP 改变了这个判断**：

- CDP 在 TLS 之上工作，与 SSL pinning **正交**
- 如果 ChatGPT Desktop 接受 `--remote-debugging-port`（Electron 默认接受，OpenAI 没理由禁），CDP launcher 同样适用
- 这意味着 P2 可能**不需要**等 P6 Frida

但这是 P5.B.4-C 启动时再核查的事，**本 ADR 不替它做决定**。本 ADR 只把 §3.7 写成"如 P5.B.4-C 验证 CDP 在 ChatGPT Desktop 上工作，可考虑把 P2 H 面提前到 P5.B 内"——具体重新分类需要新 ADR。

### 3.9 OSS / Pro 归属

**`pce_app_launcher/`、`pce_mcp/mcpb/` 均归 OSS（Apache-2.0）**——理由与 ADR-013 / ADR-015 完全平行：

1. 它们都属于"捕获用户可见的 AI 流量"的能力底座（ADR-010 §核心论据）
2. 用户安装动机受 paywall 高度敏感
3. 它们的代码不依赖任何 PCE 专有的 closed-source 资产

将来构筑在 L3d / `.mcpb` 之上的 fleet ops / 多租户 / 团队级审计可以归 Pro，只要不污染这两个包自身。

### 3.10 测试义务

任何对 `pce_app_launcher/` 或 `pce_mcp/mcpb/` 的修改 PR 必须：

- 引用本 ADR 编号 `ADR-016`
- `tests/e2e_mcp/` + `tests/e2e_desktop/` 全绿
- 不退化已有 33 个测试
- 新增协议路径、source_id、normalizer 路径都写迁移 + 在本 ADR 后续修订或新 ADR 中登记

---

## 4. 决议依据

### 4.1 事实核查 2026-05-09 的关键发现

| Fact | 证据 | 对决策影响 |
|---|---|---|
| `.mcpb` Desktop Extensions GA + 官方 spec 0.3 | [github.com/modelcontextprotocol/mcpb](https://github.com/modelcontextprotocol/mcpb) + [Anthropic engineering blog](https://www.anthropic.com/engineering/desktop-extensions) | `pce_mcp` 直接获得官方一键安装通道；onboarding wizard 计划重组 |
| CDP 在 Claude Desktop 工作 | [jedi.be 2026](https://jedi.be/blog/2026/automating-claude-desktop-via-chrome-devtools-protocol/) | L3d 路线技术风险归零 |
| 多个 CDP 先例 | `electron-mcp-server` / `claude-autoapprove` / `Claude_Automation` / `chrome-devtools-mcp` | 我们不孤立，可参考实现 |
| Anthropic 消费者条款明禁 reverse engineer | [Anthropic legal](https://www.anthropic.com/news/updates-to-our-consumer-terms) | ASAR repack 法律风险 |
| Electron ASAR integrity 可被 Anthropic opt-in | [electronjs.org/.../asar-integrity](https://www.electronjs.org/docs/latest/tutorial/asar-integrity) | ASAR repack 命运不在我们手上 |
| `pce_core/cdp/driver.py` 已 80% 实现 | UCS doc §2.3 line 186 | L3d 不是从零起步 |

### 4.2 为什么不"先 probe ASAR integrity，确认没启用再做 ASAR"

这是上一轮提案。被否的原因是即使 probe 通过：

1. ToS 风险不会因为 integrity 没启用而消失
2. macOS code signature 失效 + 用户手动 `xattr -cr` 的体验是 power user 也难以接受的
3. Anthropic auto-update 后注入丢失，要写自愈 + 用户文档"重装"——长期运维成本无上限
4. 即使 P5.B.2 通过，P5.B.3 (Cursor/Windsurf) 又要重做一遍 ASAR 反向工程

CDP launcher 同时解决以上 4 个问题——**probe 即使通过也不应该走 ASAR**。

### 4.3 为什么 `.mcpb` 用 Node 不用 Python 或 UV runtime

- **Python (`server.type=python`)**：用户机器要装 Python 或我们打整个 venv（~50MB）。实际验证：`pydantic` 等编译依赖跨平台打包不可靠（[mcpbundles.com](https://www.mcpbundles.com/docs/concepts/mcpb-files) 已警告）
- **UV runtime (`server.type=uv`)**：experimental，[mcpb spec](https://github.com/modelcontextprotocol/mcpb) 标 `v0.4+` 才稳定，跟当前 0.3 spec 有 gap
- **Node (`server.type=node`)**：Claude Desktop 内置 Node runtime，零外部依赖；轻量代理 ~150 行 JS 易维护；现有先例（`mcp-proxy` / `chrome-devtools-mcp` 等）大部分是 Node 实现

`.mcpb` 内部是 **Node 轻量代理 → 本机 pce_core HTTP**，**真正的 Python `pce_mcp/server.py` 仍是 advanced user 入口**——OSS 仓库不丢失任何能力，只是 default UX 走 Node 代理。

### 4.4 为什么不用 `NODE_OPTIONS=--require <preload.js>`（UCS doc §3.4.b 列的首选）

UCS doc §3.4.b 把 `NODE_OPTIONS=--require <preload.js>` 列为 L3b 首选注入手段。我们这次也跳过它的原因：

1. **Electron 22+ 限制了 NODE_OPTIONS 在生产构建里的可用项**——`--require` 在 Electron 较新版本不一定接受，要逐版本核实
2. **依然需要替换启动方式**——env var 不会自己生效，仍然要 launcher，**则在已经"换 launcher"的代价上，CDP 比 NODE_OPTIONS 更省事**（CDP 写 Python 客户端就行；NODE_OPTIONS 还要写一份 preload.js）
3. **NODE_OPTIONS 抓到的是 fetch 层；CDP 抓到的是 Network 事件，等价**——保真度持平
4. **NODE_OPTIONS 的"侵入度"模糊**——它通过环境变量改变了 Node 的行为，跟 ASAR repack 性质不同但仍属"在 Anthropic 进程里运行我们的代码"。CDP 是"在 Anthropic 进程外面观察"，更干净

### 4.5 为什么本 ADR 不试图统一 UCS doc / framework / pce_mcp README 的 L3 层名

调研发现**三个 doc 在 L3d 上不一致**：

- `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`：**L3d = CDP**, L3f = OTel auto-instr, L4b = Accessibility（**canonical**）
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`：L3d = AX/UIA, L3f = MCP middleware（reshuffled）
- `pce_mcp/README.md`：L3d = CDP, L3f = MCP middleware（混合）
- ADR-015 §2 引用的"UCS v0.2 层枚举"实际是 framework 的 reshuffled 版本，不是 UCS doc 的版本

**本 ADR 锁定**：以 **UCS doc 为 canonical**——`L3d = CDP`，`L3f = MCP middleware`（按 ADR-015 决议升级覆盖原 L3f = OTel auto-instr）。其他 doc 的层名清理留给后续 clean-up ADR（编号待定）。

理由：本 ADR 的范围是 P5.B.2 实施方案变更，不是 UCS 层名学术整理。把后者塞进本 ADR 会让决议臃肿、影响审阅效率。

---

## 5. 影响清单

### 5.1 文档同步项（本次 commit 一并落）

- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`
  - §4.2 P5.B.2 重写（preload → CDP + .mcpb 双 phase）
  - §4.3 P5.B.3 调整（preload scaffolding → CDP scaffolding）
  - §7 ADR 表加 ADR-016
  - §10.1 unlock 矩阵列名 L3b → L3d
  - §10.4 migration 0009 → 0010
  - §11 sign-off 同步改 0010
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
  - §4.1 P1：Secondary plane H/L3b → H/L3d
  - §4.2 P2：Primary plane H/L3b → H/L3d（pinning rationale 仍成立）
  - §4.3 P3：Primary plane H/L3b → H/L3d
  - §4.4 P4：Primary plane H/L3b → H/L3d
  - §7 P5.B.2 / §7 P5.B.3 描述按 §3.2 / §3.7 重写
  - §8.2 sign-off：migration 0010；ADR-016 列入 ADR 清单
  - §9 DR-05 风险描述软化（CDP launcher 对 auto-update 鲁棒）
  - §10.1 Type 5 deferral 措辞调整：AX/UIA 是 L4b（不是 L3d）
- `Docs/docs/PROJECT.md`
  - §12 ADR 列表加 ADR-016
  - §UCS 块对 P5.B 描述微调
- `pce_mcp/README.md`
  - §M 姿态表 row C：实现从 `pce_preload/` 改为"L3d CDP launcher（`pce_app_launcher/`）+ L3f middleware（`pce_mcp_proxy/`）联合，详 ADR-016"
  - §与 UCS 关系：保持 L3d = CDP（已经一致）

### 5.2 后续 follow-up（不在本 commit 范围）

- 新 ADR：UCS doc / framework / ADR-015 §2 三处层名一致化清理
- UCS doc §3.4.b（L3b "P5.B 核心新建"）的状态注记从"主目标"改为"v1.1 不实施"
- UCS doc §3.4.f L3f = OTel auto-instr 的归属问题（被 ADR-015 撤换为 MCP middleware）
- 若 P5.B.4-C 验证 CDP 适用 ChatGPT Desktop，写新 ADR 把 P2 提前

### 5.3 代码现状（尚未实施，本 ADR 是 kickoff 阶段决议）

无新代码——本 ADR 是 P5.B.2 启动前的方案修订。实施在后续 commits：

- Phase 1（`pce_mcp.mcpb` 打包）→ `pce_mcp/mcpb/`
- Phase 2（`pce_mcp_proxy` CLI helper）→ `pce_mcp_proxy/install.py`
- Phase 3（CDP launcher）→ `pce_app_launcher/`
- Phase 4（migration 0010）→ `pce_core/migrations/0010_interaction_kind.py`

每 phase 一个独立 commit + tag，最早从 v1.1.0-alpha.4 开始。

### 5.4 不影响

- 浏览器子系统（已硬冻结）
- L1 / L2 / L3a / L3e / L3f 各自独立，本 ADR 不动
- 已落地的 `pce_mcp/` Python 实现（保留为 advanced user 路径）
- 已落地的 `pce_mcp_proxy/`
- 已落地的 33/33 测试

---

## 6. Open Questions · 留给 P5.B.2+

- **CDP 多窗口竞争**：用户开多个 Claude Desktop 实例会争同一个 9222 端口。launcher 需要 port discovery / 自动分配机制（jedi.be 文章提到"Multi-instance orchestration — Run multiple Claude windows on different debug ports"）
- **CDP 对 thinking blocks 的可见性**：Anthropic 在 Opus 4 / 4.5 / 4.7 加入了 reasoning trace（`<thinking>` block）。需验证 CDP 看到的 fetch payload 是否包含这部分（应该是，因为 thinking 是通过 `<function_calls>` 等标签流回的；但要 e2e 验证）
- **`.mcpb` 在 directory 提交后的 review 反馈**：Anthropic curator 可能要求改 `description` 措辞、加更详细的 `privacy_policies`，甚至拒绝。准备好回滚到自托管。
- **CDP launcher 在 macOS 上的快捷方式替换**：macOS `.app` bundle 不像 Windows 快捷方式那么简单替换；可能需要 LaunchServices 注册或 Automator wrapper。先验证。
- **保密性与"keylogger 范畴"对外措辞**：jedi.be 作者直接说出 "MCP server with response extraction = AI output capture" 的描述。我们的 Anthropic directory 提交、PCE 官网文案、`.mcpb` description 都要主动这样描述自己——这是诚实姿态而非营销劣势。

任何处理上述 open question 的 PR 必须新写 ADR 引用本 ADR 作为前置。

---

*End of ADR-016.*
