# TASK-006: P5.A Subscription Capture(v1.0 首版对外发布)

- Status: Ready(需 R1–R7 仓库拆分完成后开工)
- Priority: P5.A
- Owner: Local agent
- Date: 2026-04-18
- Phase: UCS P5.A — v1.0 Subscription Capture
- Target Release: v1.0.0(~4 周后)

## 1. 背景

P4 阶段已交付完整的 P0 ~ P4 工业化基础(724 pytest + ~220 vitest pass,观测/分析/语义/移动端引导/CDP 全部就位)。

2026-04-18 本轮讨论采纳了 **Universal Capture Stack (UCS)** 作为捕获层北极星架构,并拍板了三条关键决策:
- **D-1 = A**:小步快发,4 周后发 v1.0
- **D-2 = B**:Open Core(OSS = Apache-2.0,Pro = 专有)
- **D-3 = A**:开发者先行(Cursor/Copilot/Codex 用户)

本任务是 UCS 路线图的第一个切片:**P5.A Subscription Capture**。交付完成后发布 PCE 的第一个对外稳定版本 v1.0.0。

## 2. 目标

在 4 周内交付一个**可面向开发者公开发布**的 PCE v1.0,能够:

1. 捕获 Tier 1 订阅类 AI 的交互(ChatGPT Web + Claude Web + Copilot VS Code + ChatGPT Desktop 无 pinning + Codex CLI 等)
2. 在浏览器扩展中补齐 F2 Web-Embedded 的 4 个主力站点(Notion AI / M365 Copilot / Figma AI / Gmail)
3. 首跑向导让新用户 ≤ 5 分钟拿到第一条捕获
4. 建立 CaptureEvent v2 统一数据契约作为后续 Pro 层递交事件的公共 API
5. 对 Pinning 失败给出明确诊断与建议(铺垫 P6 L2 Frida)

## 3. 输入文档

开工前必须按顺序读完:

1. `docs/decisions/2026-04-17-industrialization-roadmap.md`(工业化整体方向)
2. `docs/decisions/2026-04-18-ucs-and-release-strategy.md`(本轮决议 — 本任务直接产物)
3. `docs/PROJECT.md`(v2026-04-18 更新版)
4. `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`(1340 行工业级设计文档)
5. `docs/engineering/adr/ADR-009-universal-capture-stack.md`(UCS 北极星决策)
6. `docs/engineering/adr/ADR-010-open-core-module-boundary.md`(模块归属 — 决定你写的代码进哪个仓库)
7. `docs/engineering/ARCHITECTURE.md`(当前 v0.2 架构,注意 UCS 将在 P5/P6/P7/P8 中把它推进到 v0.3)

## 4. 前置条件(R1–R7)

**本任务不能在 R1–R7 完成之前开工**。仓库拆分与开源门面准备是 P5.A 编码的前置条件:

- R1 `LICENSE`(Apache-2.0)+ `NOTICE` + `CONTRIBUTING.md` + `SECURITY.md` + `CODE_OF_CONDUCT.md` 到位
- R2 所有源文件加 SPDX header(`# SPDX-License-Identifier: Apache-2.0`)
- R3 `github.com/zstnbb/pce-pro` 私有仓库创建完毕(P5.A 暂不往里放东西,但必须先存在)
- R4 `pce` CI 加 import-direction 检查
- R5 `pce-pro` CI 占位
- R6 `README.md` 更新 OSS vs Pro 功能矩阵
- R7 `PCE-Core` → `pce` 仓库改名

R1–R7 完成约 0.5–1 day,可由人与 agent 协作完成。

## 5. 范围

### In Scope(按 UCS §9.1 全量 11 项)

| 编号 | 任务 | 文件落点 | 预估 |
|---|---|---|---|
| P5.A-1 | Allowlist 补 6 个订阅域 | `pce_core/config.py` | 0.5h |
| P5.A-2 | KNOWN_APPS 补 ChatGPT Desktop + Codex CLI | `pce_core/electron_proxy.py` | 1h |
| P5.A-3 | **CaptureEvent v2 schema + `/api/v1/captures/v2`** | `pce_core/capture_event.py`(新)+ `pce_core/server.py` | 2d |
| P5.A-4 | 数据库 migration `0006_capture_event_v2` | `pce_core/migrations/0006_capture_event_v2.py` | 0.5d |
| P5.A-5 | **首跑向导** `/onboarding`:串 cert + proxy + Electron 重启 | `pce_core/server.py` + dashboard | 3d |
| P5.A-6 | **Pinning 失败检测 + UI 红点 + 3 条建议** | `pce_proxy/addon.py` + dashboard | 2d |
| P5.A-7 | Per-app bypass UI | dashboard | 1d |
| P5.A-8 | 浏览器扩展 F2 站点 P1(M365 Copilot + Notion AI) | `pce_browser_extension_wxt/entrypoints/` | 4d |
| P5.A-9 | 浏览器扩展 F2 站点 P2(Figma AI + Gmail) | 同上 | 4d |
| P5.A-10 | 隐私中间件:统一脱敏调用 | `pce_core/server.py::captures_ingest` | 1d |
| P5.A-11 | 发布打包:Win/macOS/Linux 主包 + 扩展商店提交 | `pce.spec` + CI | 3d |

### Out of Scope(P5.B 及以后做)

- L3b Electron preload 注入(P5.B)
- L3c VS Code 扩展原生 API hook(P5.B)
- Cursor gRPC-web 特殊处理(P5.B)
- L2 Frida SSL hook(P6)
- L0 Kernel Redirector(P7)
- L4b Accessibility 桥(P7)
- Capture Supervisor 自动调度(P8)
- 任何 Pro 模块(不在 `pce` 仓库,去 `pce-pro`)

## 6. 关键建议实现边界

### 6.1 CaptureEvent v2 schema(P5.A-3 + 4)

这是本任务**最重要**的交付,是整个 UCS 路线图的数据契约基石。详细 schema 见 UCS §5。关键点:

- `pce_core/capture_event.py` 用 `pydantic` 定义统一 envelope
- `/api/v1/captures/v2` 接受新 envelope,返回 `{"accepted": true, "fingerprint": "..."}`
- 兼容中间件:v1 老接口继续可用,内部转 v2 落库
- migration `0006` 新增表 `capture_events_v2`(不碰现有 `raw_captures` / `messages`)
- 字段冻结后**只加不删**,字段语义变更必须走 v3

### 6.2 首跑向导(P5.A-5)

按 UCS §3.3 设计,路径 `/onboarding`:

1. 检测当前状态(CA 是否已装 / 系统代理是否已开 / 目标应用是否在运行)
2. 一键安装 CA(复用 `pce_core/cert_wizard/`)
3. 一键开系统代理(复用 `pce_core/proxy_toggle/`)
4. 列出检测到的 Electron 应用,提示"需要重启以生效"(复用 `pce_core/electron_proxy.py`)
5. 验证环节:用户发一条 ChatGPT 消息,dashboard 显示"捕获成功"(实时 WS 推)

验收:新用户从"装完 exe"到"看到第一条捕获" P95 ≤ 5 分钟(100 次测试)。

### 6.3 Pinning 诊断(P5.A-6)

按 UCS §3.2 设计,`pce_proxy/addon.py` 检测到 TLS 握手失败时:

- 统计每个 domain 的失败率
- 失败率 > 20% 视为"疑似 pinning"
- dashboard 右上角红点 + 展开面板显示 3 条建议:
  1. 该应用可能启用了证书 pinning。PCE v1.2 会加入 L2 Frida 解决(预计 X 月)
  2. 暂时方案:在"Per-app bypass"中将此应用旁路
  3. 如果你迫切需要,在 Discord / GitHub Discussion 告诉我们

### 6.4 Per-app bypass(P5.A-7)

按 UCS §3.1 约束:某些应用即使装了 CA 也过不去(pinning / 企业 MDM)。提供"旁路名单" UI:

- dashboard 设置页:列出所有 KNOWN_APPS 的进程名
- 每个前面一个勾选框:勾选则将其从 `NO_PROXY` 中排除(而不是导流到 mitmproxy)
- 保存到 `~/.pce/config.json`
- 进程启动器(`electron_proxy.py` / `service_manager.py`)启动子进程时读取该配置

### 6.5 浏览器扩展 F2 新站点(P5.A-8/9)

覆盖 UCS 附录 A 的 F2 形态:

- **P1**:`m365copilot.*` + `notion.so/ai`(两者重要性高,用户基数大)
- **P2**:`figma.com/ai` + `mail.google.com`(Gmail 的 "Help me write")

每个站点的提取器遵循现有 `pce_browser_extension_wxt/entrypoints/` 的 detector 模式。复用 P2.5 已经完善的 extraction 基础设施。

### 6.6 隐私中间件(P5.A-10)

按 UCS §6.1 设计。当前 `pce_core/server.py::captures_ingest` 各分支各自调脱敏,统一到中间件:

- 新文件 `pce_core/redact.py`(或扩展已有脱敏模块)
- 作为 FastAPI dependency 注入到 `/api/v1/captures/*` 所有路由
- 对 header(`Authorization` / `Cookie` / `X-Api-Key`)+ body(Bearer / eyJ / sk-)脱敏
- 脱敏规则可通过 `PCE_REDACT_PATTERNS` 环境变量扩展

### 6.7 发布打包(P5.A-11)

- `pce.spec` 已存在,扩展以确保三平台都能 onefile 打包
- CI 加 `.github/workflows/release.yml`:打 tag 后自动 build 三平台 + attach 到 GitHub Release
- **Chrome Web Store 审核倒排时间**:P5.A-11 完成前 2 周必须先提交扩展,才能 v1.0 发布日同步上架

## 7. 验收标准(UCS §10.1 全量)

必须同时满足:

- [ ] ChatGPT 网页(L3a)单条捕获成功率 ≥ 98%
- [ ] ChatGPT Desktop(L1,无 pinning 版)单条捕获成功率 ≥ 95%
- [ ] Copilot VS Code(L1)单条捕获成功率 ≥ 95%
- [ ] Codex CLI(L1)单条捕获成功率 ≥ 95%
- [ ] 新用户"装 → 拿到第一条"时长 ≤ 5 分钟(100 次测试 P95)
- [ ] 首跑向导失败率 < 1%
- [ ] 浏览器扩展 M365 Copilot / Notion AI / Figma AI / Gmail 文本捕获成功率 ≥ 90%
- [ ] **所有现有 724 pytest + ~220 vitest 继续 pass**
- [ ] 新增 ≥ 30 个单元测试覆盖 CaptureEvent v2 + 隐私中间件
- [ ] Pinning 失败时 dashboard 显示红点 + 3 条建议
- [ ] 性能:整机 CPU ≤ 6%, RAM ≤ 600MB(仅 L1 + L3a + 基础 Supervisor)
- [ ] `GET /api/v1/health` 增加 `capture_event_v2` 字段,显示 v2 通道流量
- [ ] Chrome Web Store 扩展已提交并通过审核(或得到预期 back-and-forth 反馈)

## 8. 建议交付物

### 新增
- `pce_core/capture_event.py` — CaptureEvent v2 pydantic schema
- `pce_core/redact.py` — 统一隐私中间件(或扩展现有)
- `pce_core/migrations/0006_capture_event_v2.py`
- `pce_core/dashboard/onboarding.html` + `onboarding.js` — 首跑向导 UI
- `pce_core/dashboard/bypass.html` + 对应 JS — Per-app bypass UI
- `pce_browser_extension_wxt/entrypoints/{m365copilot,notion_ai,figma_ai,gmail}/` × 4
- `.github/workflows/release.yml` — 三平台 release CI
- `tests/` 新增 ≥ 30 个单元测试

### 修改
- `pce_core/config.py` — Allowlist 补订阅域
- `pce_core/electron_proxy.py` — KNOWN_APPS 补 ChatGPT Desktop + Codex CLI
- `pce_core/server.py` — `/api/v1/captures/v2` + 首跑向导 API + 隐私中间件接入
- `pce_proxy/addon.py` — Pinning 失败检测
- `pce_core/dashboard/app.js` + `health.js` — 红点 + bypass UI
- `pce.spec` — 跨平台 onefile 打包验证
- `README.md` — 加 OSS vs Pro 功能矩阵、安装说明、HOWTO

### 文档
- `docs/decisions/2026-05-??-P5.A-completion.md` — 完成回报

## 9. 停止条件

出现以下情况时暂停并回报,不要自行扩大范围:

- CaptureEvent v2 schema 设计时发现现有 `raw_captures` / `messages` 结构不兼容,需要大规模 schema 重构(超出 migration 0006 能力范围)
- Chrome Web Store 多轮拒审(>2 轮),需要调整扩展架构(可能需要走 ADR-003 的 Webstore Mode)
- 发现 L1 层的某个 Tier 1 应用(如 Codex CLI)实际上也有 pinning(需要评估是否推迟到 P6)
- 跨平台打包某平台挂了(例如 macOS notarization 卡住),需要更换打包策略
- Electron 应用重启检测不可靠(某些用户环境下),需要人工介入决策 fallback 方案

## 10. 回报格式

本轮结束后按以下结构回报(沉淀到 `docs/decisions/2026-05-??-P5.A-completion.md`):

### 10.1 Changed Files
- 新增 / 修改 / 删除

### 10.2 What Works
- 11 项子任务每项的完成状态
- 各验收指标的实测数据(不是"大概 95%",是"实测 100 次中 96 次成功")
- 新用户体验时长实测

### 10.3 What Does Not Work Yet
- 未达标的验收项及原因
- 已知限制

### 10.4 How To Run
- 干净环境从零到第一条捕获的完整流程
- 如何跑回归测试
- 如何本地复现 Pinning 诊断场景

### 10.5 Release Readiness
- Chrome Web Store 审核状态 / 反馈
- VS Code Marketplace 提交状态(如有)
- GitHub Release 准备度

### 10.6 Risks / Follow-ups
- P5.B 应该先解哪一块
- Pinning 诊断阶段收集到哪些应用最需要 L2 Frida(用于 P6 优先级)
- 有没有暴露新的架构隐患
- 有没有需要人拍板的问题(D-4/D-5/D-6/D-7/D-8/D-9 中是否有已可决策)

## 11. 特别提醒

- **ADR-010 的依赖方向约束**:本轮任务产出的所有代码都在 `pce` 仓库(OSS),**不允许** import `pce_agent_*` 或 `pce_core.capture_supervisor`。R4 的 CI 检查会抓你。
- **CaptureEvent v2 是公共 API**:schema 一旦 merge 到 main,后续只允许加字段不许改 / 删。设计时要让 Pro 层(L0/L2/L3b/L4b)未来能干净地往里填字段而不破坏兼容。
- **Chrome Web Store `<all_urls>` 审核**:按 ADR-003,扩展有双模式。Sideload 版(`<all_urls>`)通过 GitHub Releases 分发,Webstore 版(细化 host permissions)走商店审核。P5.A-11 必须提前规划两条分发路径。
- **"小步快发"不是"草率发布"**:验收标准的每一条都必须实测通过,不允许"差不多就发"。
- **任何你觉得"顺手一起做了"的改动**,如果超出 P5.A 范围,请记录在 Follow-ups 里,留给 P5.B。
