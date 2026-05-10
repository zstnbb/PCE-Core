# ADR-018: 闭源 Store AI 应用抓取策略 — 8 面 23 路径威胁模型 + 5 红线筛选 + 13 条可营运路径

- Status: Accepted
- Date: 2026-05-10
- Phase: P5.B.2 修订（基于 MSIX 实测脱险后的整体策略重定）
- Supersedes (partial): ADR-016 §3.2 — CDP launcher 作为 P1 Claude Desktop 在 **Windows MSIX** 渠道下的 H 面主路；ADR-016 §3.8 关于 P2 ChatGPT Desktop 经 CDP 适用的乐观推测；`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` §10.4 P5.B.2 acceptance 中"Claude Desktop 经 CDP launcher 抵达 ≥85% D-cases" 的判定路径
- Related: ADR-009 (UCS canonical)、ADR-010 (Open Core)、ADR-013 (`pce_mcp/` OSS)、ADR-015 (UCS L3f)、ADR-016 (CDP + .mcpb)、ADR-017 (Test Conductor)

---

## 1. 决议（一句话）

**对于 MSIX/App Store 分发的闭源 Electron AI 桌面应用（P1 Claude Desktop / P2 ChatGPT Desktop / 同类未来应用），PCE 以"8 面 23 路径威胁模型"为分析底盘，经 5 条红线（屏幕捕获 / 封号风险 / 高维护 / 用户复杂操作 / 违规风险）筛选后保留 13 条可营运路径，重组三主轴：M 面（E1 `pce_mcp` + E2 `pce_mcp_proxy`）+ 新增 L3g · Local Persistence Watcher（C1-C5 LocalCache 解析）+ CLI 包装（H1）；Chat 区主路改回 L1（A1 system proxy + CA），辅以 A2 `SSLKEYLOGFILE` 作 P5.B 内补丁；CDP launcher（ADR-016 §3.2）保留为 Squirrel 渠道的可选路径但不再作为 MSIX 渠道下的主路。**

---

## 2. 背景

### 2.1 MSIX 6 次实验全失败 — ADR-016 在 Windows MSIX 上失效

ADR-016 §3.2 把 P5.B.2 的 H 面主路改为 L3d CDP launcher。该决议在 macOS / Windows Squirrel 渠道下成立，但在 **Windows MSIX 渠道**下被 6 次实测推翻：

| 方法 | 操作 | 结果 |
|---|---|---|
| **Method A** | `Popen(Claude.exe, --remote-debugging-port=9222)` | WindowsApps 路径 ACL 拒绝执行 |
| **Method B** | `Invoke-CommandInDesktopPackage -Command Claude.exe` | 容器内 PATH 不含 `Claude.exe` |
| **Method B'** | 同 B 但绝对路径 | 静默 no-op |
| **Method C** | 替换桌面快捷方式 LNK 加 `--remote-debugging-port` | MSIX activation host 启动时**剥掉**所有命令行参数 |
| **Method D** | `explorer shell:appsFolder\<AUMID> --remote-debugging-port` | 同 C，参数被剥 |
| **Method E** | COM API `IApplicationActivationManager::ActivateApplication(aumid, args)` | HRESULT=S_OK 但进程不接收 args（MSIX 启动协议把 args 当用户文档而非 process argv） |
| **Method F** | Popen user-space `claude-code\<ver>\claude.exe` | 启动的是 CLI agent 不是 GUI；无 CDP |

**结论**：MSIX activation 不允许第三方注入命令行参数。`--remote-debugging-port` 的注入入口在 MSIX 渠道**完全封死**。

**事实核查 2026-05-10 增项**：
- Anthropic 在 Windows 已**全量**转 MSIX（确认 `Get-AppxPackage Claude*` PackageFullName = `Claude_1.6608.2.0_x64__pzs8sxrjxfjjc`，无 Squirrel 安装路径）
- 开源社区无 MSIX CDP 注入方案（`wallneradam/claude_autoapprove` 等假设 Squirrel 安装路径，对 MSIX 无效）
- ChatGPT Desktop Windows 渠道也是 MSIX；同样困境

### 2.2 用户重新提问 — "是不是没法抓了？"

2026-05-10 用户提问 **"那我们要捕捉这个 claudedesktop 的内容 已经完全没有办法了吗？"**，触发对捕获面策略的根本性重审：不再死盯 ADR-016 选定的 L3d，而是回到 UCS canonical（`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`）+ Cognitive Framework（`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`）的全捕获面（N + H + M + U）重新评估。

`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md:458` 原文清晰写明：

```
1a Electron 聊天 | Claude Desktop | M (首) + H | pce_mcp server ✅ + L3b ⬜ | ...
```

`M (首)`——M 面是**首选**捕获面。ADR-016 把焦点放在 H 面 L3d 是因为它当时是技术上最"干净"的路径；MSIX 失效后，回到 M 面主线 + 多面冗余是回归 framework 原始设计意图。

### 2.3 实测发现的 5 个新事实

| Fact | 证据 | 对决策影响 |
|---|---|---|
| **AppxManifest Capabilities = `{}`** | `Get-AppxPackage Claude* \| Select Capabilities` 返回空 | MSIX 容器化是"软的"——没有声明任何特权能力，进程仍接受 user session env 继承；Frida/DLL 注入技术上仍可能（但触红线，见 §3.3） |
| **`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\` 完全用户可读** | `Get-ChildItem` 列出 `Local Storage\leveldb\` / `IndexedDB\` / **`local-agent-mode-sessions\`** / **`vm_bundles\`** / `claude-code-vm\` / `Cache\` / `logs\` 等 | **持久化面是被严重低估的金矿** — 三区（Chat/Cowork/Code）数据全在 user-space 可读路径 |
| **`claude_desktop_config.json` 已存在（442 bytes）** | 同上目录 | M 面 posture B（`pce_mcp_proxy install`）通路就绪，无需 onboarding wizard 创建文件 |
| **Anthropic Skills 是用户可写机制** | Anthropic 官方 Skills spec | F1 维度 — PCE 可发布"PCE-Logger Skill"作为 agent loop 推理过程的合规观测点（实验性，见 §3.4） |
| **Chromium 尊重 `SSLKEYLOGFILE` env var** | Chromium 公开 debug feature | A2 路径 — 被动 TLS 解密，**完全合规、零注入、可绕 pinning**（限 Chromium 网络栈，Node.js 端不写） |

---

## 3. 决议内容

### 3.1 MSIX 渠道：CDP launcher 不再作为 P1/P2 主路

ADR-016 §3.2 在 Windows Squirrel + macOS 渠道下仍成立——P1 Claude Desktop 用户安装的若是 Squirrel 包（早期或企业 sideload 渠道），CDP launcher 仍是有效路径，`pce_app_launcher/claude_desktop/` 代码不删除。

**但 MSIX 是 2026 起 Windows 默认渠道**——本 ADR 锁定：

- **MSIX 渠道下，CDP launcher 不可达。**`pce_app_launcher detect` 应识别 MSIX 安装并 fail-soft 提示（不当死错），文档说明 MSIX 用户走本 ADR §3.5 的"三主轴"
- **Squirrel 渠道下，CDP launcher 仍是可选 H 面补充**——但不再是 D0 验收路径
- DESKTOP-PRODUCT-MATRIX.md §4.1 P1 / §4.2 P2 / §4.3 P3 / §4.4 P4 中 "Primary plane / layer = H/L3d" 的字段需重排为多主轴并行

### 3.2 8 面 23 路径威胁模型（canonical for 闭源 Store Electron AI 应用）

本 ADR 把"闭源 Store AI 应用可能的所有捕获路径"系统化为 8 面 × 23 路径，作为后续所有同类决策的分析底盘：

#### A. 网络面（5 路径）

| # | 名字 | 原理 | MSIX 影响 |
|---|---|---|---|
| **A1** | system proxy + mitmproxy CA | Chromium 默认读 Windows system proxy；CA 自动安装 | 无（env 通过 user session 继承） |
| **A2** | `SSLKEYLOGFILE` 被动 TLS 解密 | Chromium 写 pre-master secret；外部解密无 MITM 无 pin 问题 | 无 |
| **A3** | Frida hook BoringSSL `SSL_read/SSL_write` | 在 `Electron Framework.dll` 加密前读明文 | 无（admin） |
| **A4** | WinDivert / WFP 内核包导流 | 强制 Claude.exe outbound TCP 重定向到 mitmproxy | 无 |
| **A5** | eBPF uprobes | Linux only（不在 Windows 范围） | N/A |

#### B. 进程面（5 路径）

| # | 名字 | 原理 | MSIX 影响 |
|---|---|---|---|
| **B1** | `NODE_OPTIONS=--require <preload.js>` | env 注入 Node.js 启动钩子 | ❌ **H4 实测后 DEAD** — Claude Desktop v1.6608.2.0 的 `EnableNodeOptionsEnvironmentVariable` fuse = Disabled，Electron 主动忽略所有 `NODE_OPTIONS` 注入；同批锁死的 fuse 还有 `EnableNodeCliInspectArguments` / `EnableEmbeddedAsarIntegrityValidation` / `OnlyLoadAppFromAsar`（见 §6 H4） |
| **B2** | DLL 注入 + 进程内 CDP server | `WriteProcessMemory + CreateRemoteThread` 注 DLL | 需 SeDebugPrivilege |
| **B3** | Frida JS hook V8 / fetch | 比 B1 更通用 | 同 A3 |
| **B4** | MSIX 重打包 + 自签 | `MakeAppx unpack` → 改 `app.asar` → 重签 | 自动更新覆盖 |
| **B5** | 命令行 `--remote-debugging-port` | ADR-016 § 路径 | **MSIX 完全堵死**（§2.1） |

#### C. 存储面（5 路径）

| # | 名字 | 内容 | 解析 |
|---|---|---|---|
| **C1** | `Local Storage\leveldb\` | Chromium LevelDB — 应用首选项 + 部分对话索引 | `plyvel` / `leveldb-export` |
| **C2** | `IndexedDB\<origin>\` | 包装的 LevelDB | 同 C1 |
| **C3** | `Cache\Cache_Data\`, `Network\` | Chromium HTTP cache | 私有格式（高难） |
| **C4** | `local-agent-mode-sessions\` | **Cowork agent loop 持久化**（JSON or LevelDB） | 待解析 |
| **C5** | `vm_bundles\`, `claude-code-vm\` | Cowork/Code 沙盒环境快照 | 待解析 |

#### D. UI 面（3 路径）

| # | 名字 | 提供 |
|---|---|---|
| **D1** | Windows UIA `IUIAutomation` | a11y 树文本（结构化，非屏幕像素） |
| **D2** | OCR 屏幕识别 | 屏幕像素 → 文本 |
| **D3** | RDP 帧录制 | 屏幕像素 |

#### E. MCP 面（2 路径，已铺）

| # | 名字 | 状态 |
|---|---|---|
| **E1** | `pce_mcp` server (posture A) | ✅ 100%；`.mcpb` 包构建中（`pce_mcp/mcpb/`，ADR-016 §3.3） |
| **E2** | `pce_mcp_proxy` middleware (posture B) | ✅ 100%（ADR-015 落地） |

#### F. Skills 面（1 路径，新增）

| # | 名字 | 设计 |
|---|---|---|
| **F1** | PCE-Logger Skill | 写 markdown skill 到用户 Skills 目录，Claude 在 agent loop 中加载，调 `tool: pce_log_step` 上报推理步骤 |

#### G. 文件系统驱动面（1 路径）

| # | 名字 | 实现 |
|---|---|---|
| **G1** | Windows minifilter driver | 内核态拦截 Claude.exe 对 LocalCache 的 write |

#### H. CLI 包装面（1 路径）

| # | 名字 | 实现 |
|---|---|---|
| **H1** | 包装 `claude-code\<ver>\claude.exe` | 注册 wrapper 到 PATH 优先位 / 修改 LNK 指向 PCE wrapper |

### 3.3 5 红线筛选 → 13 条可营运路径

本 ADR 锁定 5 条红线作为**任何捕获路径必须同时不触**的硬约束：

| 红线 | 含义 | 触线立即排除 |
|---|---|---|
| **R1 屏幕捕获** | 抓取屏幕像素或视频 | 用户隐私感知重，UX 失败感强 |
| **R2 高封号风险** | 触发应用 anti-tamper / AV 误报 / Anthropic ToS 反向工程禁令 | 长期不可持续，付费 power user 不接受 |
| **R3 高维护成本** | 每次应用 / OS 更新需重做大量工作（≥1 人周/季度） | 工程债无上限 |
| **R4 用户复杂操作** | 安装/激活需用户多步、跨域、跨权限边界（UAC + 装驱动 + 改证书 + 卸载重装等） | 与 Habit-preserving 原则（PROJECT.md §7.2）冲突 |
| **R5 违规风险** | 修改分发包、ToS 明禁、版权侵权、合规灰区 | Anthropic 任意一次升级即可批量识破 |

**筛选结果**：

```
保留 (13)                                  排除 (10)
────────────────────────────────────────  ─────────────────────────
A1  system proxy + CA          [网络]      A3  Frida BoringSSL    [R2+R3+R5 三占]
A2  SSLKEYLOGFILE              [网络]      A4  WinDivert 内核     [R3+R4]
B1  NODE_OPTIONS preload ❌      [进程]      B2  DLL 注入 CDP       [R2+R5]
C1  Local Storage LevelDB      [存储]      B3  Frida JS hook      [R2+R5]
C2  IndexedDB                  [存储]      B4  MSIX 重打包        [R2+R3+R5 三占]
C3  HTTP Cache  ⚠️             [存储]      B5  CDP cmdline        [N/A 已堵 §2.1]
C4  local-agent-mode-sessions  [存储]      D2  OCR                [R1]
C5  vm_bundles                 [存储]      D3  RDP 帧             [R1]
D1  Windows UIA                [UI]        G1  minifilter 驱动    [R3+R4]
E1  pce_mcp .mcpb              [MCP]       A5  eBPF               [N/A Linux only]
E2  pce_mcp_proxy              [MCP]
F1  PCE-Logger Skill           [Skills]
H1  CLI wrap claude.exe        [包装]
```

`⚠️` C3 边缘通过 — Chromium HTTP cache 私有格式维护成本相对高，仅作辅助。

`❌` B1 标签— **H4 实测（2026-05-10）后确认 DEAD**：Claude Desktop 的 Electron Fuses 锁死 `EnableNodeOptionsEnvironmentVariable`。保留的路径实际为 **12 条**（见 §6 H4 VALIDATED）。

**关键被排除项的理由记录**（供未来若用户/法律环境变化时重审）：

- **A3/B3 Frida**：UCS doc §3.3 详细设计了 P6 v1.2 "Pinning-Proof"，但本次 5 红线评估认定 Frida 同时触 R2（AV 误报 ≥5% per UCS doc §3.3.4 自陈）+ R3（每版 Electron 偏移变 + 签名脚本管理）+ R5（Anthropic 消费者条款 §2 明禁 reverse engineer，UCS doc §6.2 自陈"Frida agent 运行时 verify 签名"是为防止"第三方替换 ssl_log.js 借 PCE 进程偷应用明文"——这条 self-defense 设计本身就承认 Frida 注入位于灰区）。**结论：Frida 路径从 v1.x 主线移除，仅在 Pro 渠道+用户显式 risk consent 时启用**（见 §3.7）。

- **B4 MSIX 重打包**：违反 Anthropic 消费者条款 §2（reverse engineer / disassemble），且自动更新会覆盖修改，属 R5+R3 双触。**结论：永不实施**。

- **A4 WinDivert / G1 minifilter**：内核驱动 EV 签名 + WHQL 认证年成本 ≥10K USD，每次 Windows update 可能要重做，触 R3 上限；用户首次启用需 UAC 多次 + 显式批准驱动加载，触 R4。**结论：v1.x 不实施；P7 v1.3 "Force Capture" 阶段重审，仅企业自部署场景考虑**。

- **D2/D3 屏幕捕获**：直接触 R1，与 PROJECT.md §7.x 隐私原则冲突。**结论：永不作为主路；UCS doc §3.5.c L4c "触发式 OCR" 保留为用户显式快捷键触发的兜底**（不在本 ADR 范围）。

### 3.4 引入 L3g · Local Persistence Watcher（新 UCS 子层）

`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` 中既有 L 层（L0/L1/L2/L3a-f/L4a-c）没有专门覆盖"应用自己持久化到用户可读路径的数据"。C1-C5 的捕获方式既不属于 L1（不是网络层）也不属于 L4（不读 UI），UCS 当前唯一近邻是 L3c "VS Code 扩展"——但 L3c 是 IDE 内 plugin，不是文件系统观察者。

**本 ADR 增设 L3g · Local Persistence Watcher** 作为 UCS 第 13 个子层：

```
L3g · Local Persistence Watcher
├── 使命: 解析应用自己写到用户可读路径的持久化数据 (LevelDB / IndexedDB / SQLite / JSON)
├── 实现位置: pce_persistence_watcher/ (新建, OSS per ADR-013 同等论据)
├── 适用对象: 任何把对话/状态写在 %LOCALAPPDATA%\Packages\<X>\LocalCache\
│            或 %APPDATA%\<X>\ 的 Chromium-based 应用
├── 触发模式:
│   ├── 主动轮询 (polling, 5-60s)
│   ├── ReadDirectoryChangesW / inotify 文件 watcher
│   └── 应用退出时一次性 dump (LevelDB 锁存场景)
├── source 字段: source=L3g_local_persistence
└── quality_tier: T1_structured (LevelDB/IndexedDB 解析后) 或 T2_ui_text (UI 持久化)
```

**与既有层的边界**：

- **vs L3c (IDE 扩展)**：L3c 在应用进程内部观察 API；L3g 在外部观察文件
- **vs L4a (剪贴板)**：L4a 观察用户主动复制；L3g 观察应用自动落盘
- **vs L1 (MITM)**：L1 抓实时网络；L3g 抓持久化结果（秒级延迟）

**适用产品矩阵**：P1 Claude Desktop / P2 ChatGPT Desktop（一旦实现一份 LevelDB 解析器即对所有 Chromium-based 应用通用）。

**新增 source enum**：`source=L3g_local_persistence`，`pce_core/capture_event.py::CaptureSource` Literal 增项；`pce_core/db.py::SOURCE_L3G_LOCAL_PERSISTENCE = "local-persistence"`；migration 新加（编号 0011，见 §5.2）。

### 3.5 三主轴并行 — MSIX Claude Desktop / ChatGPT Desktop 实施模型

替代 ADR-016 §3.2 的"L3d 主路 + L3f 辅"二元模型，本 ADR 锁定**三主轴并行**：

```
                  ┌────────────────────────────────────────┐
                  │  Claude Desktop / ChatGPT Desktop     │
                  │  (MSIX, Windows)                       │
                  └────┬────────────┬─────────────┬───────┘
                       │            │             │
                       ▼            ▼             ▼
               ┌──────────────┐ ┌─────────┐ ┌──────────────┐
               │ Axis 1: M    │ │ Axis 2: │ │ Axis 3: H1   │
               │ MCP plane    │ │ L3g     │ │ CLI wrap     │
               │              │ │ persist │ │              │
               │ E1 + E2      │ │ C1+C4   │ │ Code 区主    │
               │ Cowork 区主  │ │ Cowork+ │ │              │
               │              │ │ Chat 兜 │ │              │
               └──────────────┘ └─────────┘ └──────────────┘
                       │
                       │  Chat 区主路 = L1 (A1+A2+B1)
                       │  分场景启用,见 §3.6
                       │
                  ┌────▼─────┐ ┌──────┐ ┌──────┐
                  │ A1 mtm   │ │ A2   │ │ B1   │
                  │ + CA     │ │ key  │ │ NODE │
                  │ (现成)   │ │ log  │ │ OPTS │
                  └──────────┘ └──────┘ └──────┘
```

**三主轴的角色分工**：

| 主轴 | 覆盖区 | 路径 | 当前完成度 | 交付时间 |
|---|---|---|---|---|
| **Axis 1: M 面** | Cowork 区 | E1 `pce_mcp` `.mcpb` + E2 `pce_mcp_proxy` | E1 90% (mcpb 打包待落)、E2 100% | 1 周（E1 .mcpb 收尾） |
| **Axis 2: L3g** | 持久化兜底（Cowork+Chat+Code 共用） | C1+C2+C4+C5 解析器 | 0% | 1-2 周 |
| **Axis 3: H1** | Code 区 | wrap `claude-code\<ver>\claude.exe` | 0% | 1 周 |
| **Chat 区主路** | Chat 区 | A1 `pce_proxy` (现成) | ✅ **H2 实测 PASS** (2026-05-10, 172 洁净 hits, 0 TLS 错误; 见 §6 H2) | 已就绪 |
| **Chat 区补丁** | Chat 区备份 / 冗余 | A2 `SSLKEYLOGFILE` | ✅ **H3 实测 PASS** (见 §6 H3); Phase 5 实现 0% | 3 天 |
| ~~**Chat 区进阶**~~ | ~~Chat 区结构化~~ | ~~B1 `NODE_OPTIONS` preload~~ | ❌ **H4 实测后 DEAD** (Electron Fuses 锁死; 见 §6 H4) | 不实施 |

### 3.6 三场景覆盖度（与 H2/H3/H4 实测结果绑定）

Chat 区的最终覆盖度是 H2（Anthropic 不 pin api.anthropic.com）/ H3（Claude Desktop 写 SSLKEYLOGFILE）/ H4（Electron Fuses 允许 NODE_OPTIONS）三个独立假设的笛卡尔积。本 ADR 锁定**三个有意义场景**作为 v1.1 acceptance gate 的判定依据：

| 场景 | 假设组合 | A1 | A2 | B1 | C1-C5 | D1 | Chat 覆盖 | Cowork | Code | 三区合计 |
|---|---|:-:|:-:|:-:|:-:|:-:|---|---|---|---|
| **乐观** (hypo) | H2✓ + H3✓ + H4✓ | ✅ T1 | ✅ T1 | ✅ T1 | ✅ | ✅ | **~95% T1** | ~95% | ~95% | **~95%** |
| **中性** (hypo) | H2✓ + H3✗ + H4✗ | ✅ T1 | — | — | ✅ | ✅ | **~92% T1** | ~95% | ~95% | **~92%** |
| ⭐ **已实测** (2026-05-10) | **H2✓ + H3✓ + H4✗** | ✅ T1 | ✅ T1 | ❌ | ✅ | ✅ | **~94% T1** (A1+A2 双冗余) | ~95% | ~95% (H1 已交付) | **~94%** |
| **悲观** (hypo) | H2✗ + H3✗ + H4✗ | ❌ | ❌ | ❌ | ✅ | ✅ T2 | **~50% T2 主导** | ~95% | ~85% (H1 替代) | **~75%** |

**Acceptance gate 判定规则**：

- **三区合计 ≥85%** → P1 D0 gate 通过（满足 PROJECT.md / DESKTOP-PRODUCT-MATRIX.md §3 的 ≥85% D0 验收线）
- **乐观/中性场景** 都达 D0；**悲观场景下 P1 仍可发版但 marketing 措辞需弱化"Chat 区实时抓取"描述**

**触线动作**：H2/H3/H4 的实测脚本统一为 `tests/manual/method_g_capture_feasibility.ps1`（见 §5.2），实施在 ADR-018 落地后的第一个 commit。

### 3.7 P6 Frida 从 v1.x 主线移除（重大调整）

UCS doc §1（Roadmap 节）原写：

```
P5.A → v1.0 "Subscription Capture"  = L1 + L3a
P5.B → v1.1 "IDE & Electron"        = v1.0 + L3b/L3c preload/扩展
P6   → v1.2 "Pinning-Proof"         = v1.1 + L2 Frida
P7   → v1.3 "Force Capture"         = v1.2 + L0 Kernel + L4b AX
P8   → v2.0 "Full Supervisor"       = v1.3 + 全自动调度/降级
```

**本 ADR 修订**：

```
P5.A → v1.0 "Subscription Capture"   = L1 + L3a
P5.B → v1.1 "IDE & Desktop & MCP"    = v1.0 + L3f (M plane) + L3g (持久化) + H1 (CLI wrap)
                                       + A2 (SSLKEYLOGFILE) + L4a (剪贴板)
                                       〔L3b preload 仅 Squirrel 渠道有限实施〕
                                       〔L3d CDP launcher 仅 Squirrel/macOS 渠道〕
P6   → v1.2 "Coverage Polish"        = v1.1 + L4b Accessibility (UIA T2 兜底)
                                       + B1 NODE_OPTIONS preload (有 Fuses 时)
                                       + L3c VS Code 扩展
P7   → v1.3 (Pro)                    = v1.2 + L0 Kernel (Pro)
                                       〔L2 Frida 仅 Pro 渠道+用户显式 risk consent〕
P8   → v2.0 "Full Supervisor"        = v1.3 + 全自动调度/降级
```

**关键改动**：

1. **P6 不再叫 "Pinning-Proof"，改叫 "Coverage Polish"**——pinning 问题改用 A1+A2+L3g 组合解决（A1 大概率通；A2 兜底 pin 但仅 Chromium net stack；L3g 兜所有 pin 失败场景到秒级延迟的持久化层）。Frida 从主线撤出。
2. **L4b Accessibility 提前到 P6**（原 P7）——D1 UIA 是当前评估下唯一稳定的 Chat 区文本兜底，提前到 v1.2 价值远大于继续推 Frida。
3. **B1 NODE_OPTIONS 移到 P6**（原 P5.B 计划范围，按 ADR-016 §4.4 已被 CDP 替代；现 CDP 失效后 B1 重新有意义但取决于 Fuses 实测，所以挪到 P6 而非 P5.B）。
4. **L2 Frida 进 Pro 渠道**——保留 UCS doc §3.3 的设计文档作为参考，但 v1.x 主仓库不交付 `pce_agent_frida/`。Pro 渠道（独立 GitHub repo `pce-pro`）若实施需用户显式同意 risk consent + 由用户在企业自部署环境下激活。
5. **L0 Kernel** 同 Pro 化处理（保留原 P7 v1.3 计划但移到 Pro）。

### 3.8 P5.B.2 实施顺序修订

ADR-016 §5.3 原排了 4 phase 实施顺序（mcpb → proxy install → CDP launcher → migration 0010）。本 ADR 替换为：

```
Phase 1 [本 ADR 落地后立即]:
  写 method_g_capture_feasibility.ps1
  一次跑 H2 (system proxy + pin) + H3 (SSLKEYLOGFILE) + H4 (Electron Fuses)
  → 输出 "Chat 区今天能不能通" 的判决书 JSON
  → 把场景 (乐观/中性/悲观) 锁定

Phase 2 [E1 收尾,1 周内]:
  pce_mcp/mcpb/ 打包 (按 ADR-016 §3.3 原计划) + 双击安装到 Claude Desktop
  → 验证 Cowork 区 MCP frame 抵达 PCE Core
  → 这一步与 H2/H3/H4 无关,无条件可做

Phase 3 [L3g 子层落地,1-2 周]:
  pce_persistence_watcher/ 新建
    - LevelDB 解析器 (C1 Local Storage)
    - IndexedDB 解析器 (C2)
    - local-agent-mode-sessions 解析器 (C4 — 先扫描格式)
    - vm_bundles 解析器 (C5 — 先扫描格式)
  Migration 0011: source=L3g_local_persistence

Phase 4 [H1 CLI wrap, 1 周]:
  pce_cli_wrapper/ 新建,先专做 claude-code\<ver>\claude.exe
  注册到 PATH 优先位 / 修改 LNK
  → Code 区 stdin/stdout 全捕

Phase 5 [A2 SSLKEYLOGFILE, 3 天 — H3 实测已 PASS，见 §6]:
  pce_proxy/ 加 SSLKEYLOGFILE 模式
  cert_wizard 加 keylog 文件管理

Phase 6 [文档收尾]:
  Migration 0010 (interaction_kind, 按 ADR-016 §3.6 原计划) 不变
  本 ADR 的所有交叉引用更新 (DESKTOP-PRODUCT-MATRIX, framework, kickoff)
```

每 phase 一个独立 commit + 测试 GREEN，最早从 v1.1.0-alpha.4-ADR018 开始。

### 3.9 OSS / Pro 归属

**全部 13 条保留路径归 OSS（Apache-2.0）**——理由与 ADR-013 / ADR-015 / ADR-016 §3.9 完全平行：

1. 它们都属于"捕获用户可见的 AI 流量"的能力底座
2. 用户安装动机受 paywall 高度敏感
3. 它们的代码不依赖任何 PCE 专有的 closed-source 资产

**新建包归属**：

| 包 | 归属 | 理由 |
|---|---|---|
| `pce_persistence_watcher/` | OSS | 同 ADR-013 论据 |
| `pce_cli_wrapper/` | OSS | 同 ADR-013 论据 |
| `pce_mcp/mcpb/` | OSS | ADR-016 §3.9 已定 |
| `pce_skills/` (F1 Skills 实验性) | OSS | 同 ADR-013 论据 |
| `pce_agent_frida/` | **Pro only**（不进主仓库） | §3.7 — Pro 渠道+用户显式 risk consent |
| `pce_agent_kernel/` | **Pro only**（不进主仓库） | 同上 |

将来构筑在 L3g / H1 / .mcpb 之上的 fleet ops / 多租户 / 团队级审计可以归 Pro，只要不污染这五个 OSS 包自身。

### 3.10 测试义务

任何对本 ADR 涉及包的修改 PR 必须：

- 引用本 ADR 编号 `ADR-018`
- `tests/e2e_mcp/` + `tests/e2e_desktop/` + `tests/e2e_cli/` (P5.B.4 新建) 全绿
- 不退化已有测试套
- 新增 source enum / migration / normalizer 路径都写迁移 + 在本 ADR 后续修订或新 ADR 中登记
- L3g 解析器必须有 mock LevelDB fixture (`tests/fixtures/l3g/`) 防止实仓污染

---

## 4. 决议依据

### 4.1 为什么不"再试一次 MSIX CDP 注入"

6 次方法已穷举 MSIX 公开 API + 半公开 COM 接口的所有注入点。继续尝试只剩两条路：

1. **逆向 MSIX activation host 协议**——属于 R5 违规（ToS 反向工程禁令）
2. **等 Microsoft 在 Windows 11 24H2+ 改 MSIX 行为**——不在我们手中

**结论**：MSIX CDP 路径是死局，时间花在它身上是机会成本损失。

### 4.2 为什么 Frida 在 5 红线下必须撤出主线

UCS doc §3.3 是 2025 末写的，当时 PCE 的 risk model 比现在宽松。本次 5 红线评估是首次把 Anthropic ToS 反向工程禁令（§3.3 中已自陈"ssl_log.js 签名"是为防偷窃，间接承认 Frida 注入位于灰区）+ AV 误报率 ≥5% (§3.3.4 自陈) + 每版 Electron 偏移变 (§3.3.1 隐含的实施成本) 三项**同时**作为否决条件。

任意单项都可在企业场景说服用户接受；三项叠加则 v1.1 D0 ≥85% 验收的稳定性预期不可达。

**反向论证**：如果 P6 Frida 落地后某次 Anthropic update 触发 Defender 全量误报，PCE 用户会经历"之前能用现在打不开"的故障——这是 v1.x 阶段（"Infrastructure before intelligence"，PROJECT.md §7.3）不可接受的。

### 4.3 为什么 L3g 独立成层而不是塞进 L3c

**L3c 当前定义**（UCS doc §3.4.c）：VS Code 扩展，hook `vscode.lm.registerChatResponseProvider` 等 IDE plugin API。

**L3g 本质**：观察文件系统持久化，不依赖任何应用 plugin API，对所有 Chromium-based 桌面应用通用。

如果塞进 L3c，会让"L3c"这个概念既包含"在 IDE plugin 内部"又包含"在文件系统外部"两种完全不同的实施技术，损害 UCS canonical 的概念一致性。**独立成 L3g 的工程成本是"加一个 enum 值 + 一个 migration"，但概念清晰度收益远大于此**。

### 4.4 为什么 SSLKEYLOGFILE 现在补不晚

UCS doc 没写 SSLKEYLOGFILE 因为 2025 末 PCE 的 N 面策略是 mitmproxy MITM 主路。MITM 一旦遇到 cert pinning 就失败；当时的 fallback 是 L2 Frida。

5 红线把 Frida 撤出后，**N 面缺一个 anti-pin fallback**。SSLKEYLOGFILE 完美填这个空位：

- 完全合规（Chromium 公开 debug feature）
- 零注入（只设 env var）
- 绕过 pinning（捕获在 TLS 握手层，pin 检查在握手后）
- 实施成本 3 天（修改 `pce_proxy/` + cert_wizard）

**唯一限制**：只对 Chromium 网络栈生效，Node.js 主进程 fetch 不写 keylog。但 Claude Desktop / ChatGPT Desktop 的 chat 流量主要从 renderer (Chromium) 发，命中率应足够。具体覆盖比例需 H3 实测。

### 4.5 为什么 PCE-Logger Skill (F1) 是合规探索而非红线触发

Anthropic Skills 是用户自己往 Claude Desktop 写文件夹+markdown 的官方机制——Anthropic 设计 Skills 时**就预期**第三方写 Skills。我们写一个 markdown skill 提示 Claude "每步推理后调用某 tool" 是合规的"prompt engineering"，不修改 Claude binary、不注入进程、不绕协议。

**实验性的部分**：依赖 Claude 自己愿意调用我们提示的 tool。这是 prompt-following 行为，不是技术 hook。如果 Claude 在某次 RLHF 后主动忽略此类 logging skill，F1 就降级为只能拿到"Claude 加载了哪个 skill"的元数据，仍合规。

**结论**：F1 不触红线，作为 Cowork 区 reasoning trace 的实验性补充值得 prototype（1 周内出可演示样例）。

---

## 5. 影响清单

### 5.1 文档同步项（本 commit 一并落）

- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`
  - §4.2 P5.B.2 改写为"三主轴 + 三场景判定"模型，CDP launcher 退为 Squirrel-only 备用
  - §10.4 acceptance gate 加"H2/H3/H4 实测先行"前置条件
  - §11 sign-off ADR 列表加 ADR-018
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
  - §4.1 P1 Claude Desktop：
    - Primary plane / layer 改为多主轴 ("M / L3f + L3g + L1")
    - Secondary plane / layer：H / L3d 加注 "Squirrel only"
    - Risks 段加"MSIX 渠道下 CDP 不可用，参 ADR-018"
    - First-probe checklist 加 H2/H3/H4
  - §4.2 P2 ChatGPT Desktop：同 §4.1 调整
  - §7 P5.B.2 实施段重写按 §3.5 + §3.8
  - §8.2 sign-off：ADR-018 列入
  - §9 DR-05 风险描述更新（CDP 在 MSIX 渠道不可用，rest 不变）
  - §9 加新风险 DR-08：Anthropic Skills API 演化导致 F1 失效（缓解：F1 实验性，不在 D0 acceptance line）
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
  - §7 总图加 L3g 行；P1 Claude Desktop 行的 "L3b ⬜" 改为 "L3g ⬜ + L1 ✅"
  - 顶部加一段"2026-05-10 MSIX 脱险更新参 ADR-018"导航
- `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`
  - §1 Roadmap 块按 §3.7 修订
  - §3 加 §3.4.g L3g 子层定义（按 §3.4 全文）
  - §3.3 L2 Frida 段顶部加"v1.x 不实施，参 ADR-018"标记
  - §5.2 CaptureSource Literal 加 `"L3g_local_persistence"`
- `Docs/docs/PROJECT.md`
  - §12 ADR 列表加 ADR-018
  - 顶部 "Current Phase" 描述微调指向 ADR-018
- `pce_mcp/README.md`
  - §M 姿态表更新："L3d CDP launcher (ADR-016)" → "L3d (Squirrel only) + L3g (MSIX 主路, ADR-018)"
- `CHANGELOG.md`
  - 加 [1.1.0-alpha.4-ADR018] 条目

### 5.2 代码同步项（按 §3.8 phase 序）

- **Phase 1**：`tests/manual/method_g_capture_feasibility.ps1`（H2/H3/H4 一次跑），输出 `tests/manual/_reports/method_g_<timestamp>.json`
- **Phase 2**：`pce_mcp/mcpb/` 完成（ADR-016 §3.3 已设计，本 ADR 不改）
- **Phase 3**：
  - `pce_persistence_watcher/` 新建包（OSS）
    - `__init__.py`, `__main__.py`
    - `leveldb_reader.py` (C1+C2 解析器)
    - `agent_mode_sessions.py` (C4 解析器)
    - `vm_bundles.py` (C5 解析器)
    - `cache_reader.py` (C3 ⚠️ 边缘，可后置)
    - `watcher.py` (`ReadDirectoryChangesW` 文件 watcher)
  - `pce_core/migrations/0011_l3g_local_persistence_source.py`（registers `local-persistence` source row）
  - `pce_core/db.py::SOURCE_L3G_LOCAL_PERSISTENCE = "local-persistence"`
  - `pce_core/capture_event.py::CaptureSource` Literal 加 `"L3g_local_persistence"`
  - `pce_core/normalizer/` 加 `local_persistence.py` (T1/T2 解析路由)
  - `tests/e2e_l3g/` 新建：mock LevelDB / mock IndexedDB / mock local-agent-mode-sessions JSON fixture
- **Phase 4**：
  - `pce_cli_wrapper/` 新建包（OSS）
    - `__init__.py`, `__main__.py`
    - `claude_code_wrapper.py` (wrap claude-code\<ver>\claude.exe)
    - `install.py` (注册 LNK / PATH)
  - `pce_core/db.py::SOURCE_L3E_CLI_WRAPPER = "cli-wrapper"`（如尚未有；UCS doc §3.4.e L3e 名义已存在，复用）
  - `tests/e2e_cli/` 新建（DESKTOP-PRODUCT-MATRIX.md §6 已规划目录）
- **Phase 5（仅 H3 通过时）**：
  - `pce_proxy/keylog_mode.py` 新建：解析 SSLKEYLOGFILE 文件 → 与已有 mitmproxy flow 关联
  - `pce_core/cert_wizard/keylog.py` 新建：keylog 文件路径管理
  - `pce_core/server.py` 加 `/api/v1/cert/keylog/start|stop|status` endpoints

### 5.3 不影响

- 浏览器子系统（已硬冻结）
- L1 mitmproxy 现有逻辑（A2 是补丁，不替换）
- 已落地的 `pce_mcp/` Python 实现
- 已落地的 `pce_mcp_proxy/`
- 已落地的 33+ 测试套
- ADR-017 Test Conductor 设计（独立维度）
- macOS / Linux 渠道（本 ADR 仅修正 Windows MSIX 渠道；macOS 仍按 ADR-016 §3.2 走 CDP launcher）

---

## 6. Open Questions · 留给 Phase 1+ 验证

- **H2 实测**：✅ **VALIDATED 2026-05-10**（Claude Desktop v1.6608.2.0）。**方法**：`scripts/probe_h2_claude.ps1` — 装 mitmproxy CA 到 CurrentUser Root（免 UAC）+ 启 mitmdump 链连 upstream 7890（保证用户 GFW 绕穿不断）+ system proxy 翻到 mitmproxy + 重启 Claude + 25s 观察，测后在 `try/finally` 里回滚一切。**产出**：341 KB log，**172 个洁净 `anthropic.com` / `claude.ai` HTTP 命中**（含 `GET https://claude.ai/`、`GET https://claude.ai/api/organizations/<id>/sync/settings`、`HEAD https://api.anthropic.com/api/desktop/win32/x64/msix/update?device_id=<uuid>` 等），**0 条真实 TLS handshake / 证书错误**（最初 4 条“错误”均为 regex 假阳，已在 probe 脚本里收窄）。**结论**：Anthropic 在 Claude Desktop MSIX 端 **不对 api.anthropic.com / claude.ai 做 cert pinning**；mitmproxy CA 签的 leaf cert 被客户端完全接受。» A1 路径 viable；与 H3 PASS 并论，Chat 区拥有 A1+A2 双 cert-pin-proof 通道。
- **H3 实测**：✅ **VALIDATED 2026-05-10**（Claude Desktop v1.6608.2.0, AUMID `Claude_pzs8sxrjxfjjc!Claude`）。**路径**：User 级 env var via `[Environment]::SetEnvironmentVariable('SSLKEYLOGFILE', $path, 'User')` + 重启 Claude。**失败路径**：进程级 `$env:SSLKEYLOGFILE` + `Start-Process shell:appsFolder\<AUMID>` 不传递（MSIX activation 不继承调用者 process env）。**产出**：20s 启动期间 7780 bytes 写入，10 个 TLS 1.3 会话，每会话 5 标签齐全（CLIENT_HANDSHAKE_TRAFFIC_SECRET / SERVER_HANDSHAKE_TRAFFIC_SECRET / CLIENT_TRAFFIC_SECRET_0 / SERVER_TRAFFIC_SECRET_0 / EXPORTER_SECRET）— 足以供 mitmproxy / Wireshark 解密。**覆盖边界**：Chromium 网络服务进程走的全部 TLS（包含订阅 web view 命中的 `api.anthropic.com` / `claude.ai`）被全拍；Node.js main 进程的 `https.request` 不写 keylog，未被覆盖（与本 ADR §3.4 A2 行 caveat 一致）。» A2 路径进入可营运状态。
- **H4 实测**：❌ **VALIDATED LOCKED 2026-05-10**（Claude Desktop v1.6608.2.0）。**方法**：`npx -y @electron/fuses read --app "C:\Program Files\WindowsApps\Claude_1.6608.2.0_x64__pzs8sxrjxfjjc\app\Claude.exe"`。**产出** (Fuse Version v1)：`RunAsNode` = Disabled、`EnableNodeOptionsEnvironmentVariable` = **Disabled**、`EnableNodeCliInspectArguments` = Disabled、`EnableEmbeddedAsarIntegrityValidation` = Enabled、`OnlyLoadAppFromAsar` = Enabled。**战术含义**：B1 `NODE_OPTIONS` preload 死（本文 §3.2 B1 行、§3.3 保留表、§3.5 Chat 区进阶行 已钉死）；任何 Node 注入或 asar 修改路径（含 `--inspect` Chrome DevTools / asar 标外加载）全部死；幸运的是 A1 + A2 都在进程外，免疫于 fuses。
- **C4 格式**：`local-agent-mode-sessions/` 内的 JSON / LevelDB schema 待解析；解析器开发要先写一个 dump 工具看看格式。
- **C5 格式**：`vm_bundles/` / `claude-code-vm/` 同上。
- **F1 PCE-Logger Skill prototype**：写一个 markdown skill 测 Claude 是否会调用我们提示的 tool；如不调用，F1 降级为元数据-only 维度。
- **ChatGPT Desktop 同等性**：本 ADR 的 8 面分析对 P2 ChatGPT Desktop 完全适用，但 H2 实测在 P2 上的结果可能与 P1 不同（OpenAI 历史上更倾向 pin）。Phase 1 应在 P1 P2 各跑一次。
- **Squirrel 渠道用户的 onboarding 路径**：少数仍在 Squirrel 安装 Claude Desktop 的用户，PCE 应自动检测渠道并提供 CDP launcher (ADR-016 §3.2) 的 setup；MSIX 用户走本 ADR §3.5 三主轴。**`pce_app_launcher detect` 增强需要识别两个渠道并 routing**。
- **L3g 在 macOS 的对应路径**：本 ADR 主要分析 Windows MSIX；macOS 上 Claude Desktop 写 `~/Library/Application Support/Claude/`，结构相似但需独立验证。Phase 3 实施时一并处理。
- **D1 UIA 何时启用**：本 ADR §3.5 把 UIA 留在 P6（"Coverage Polish"）。但悲观场景下 UIA 是 Chat 区主要兜底。**如果 H2 实测显示 pin 死且 H3 也不通**，UIA 可能需要从 P6 提前到 P5.B 内；这一权衡留给 Phase 1 实测后再决定。

任何处理上述 open question 的 PR 必须新写 ADR 引用本 ADR 作为前置。

---

*End of ADR-018.*
