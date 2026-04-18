# PROJECT

- Project: PCE (working title)
- Status: Active
- Current Phase: P5.A Subscription Capture 即将开工；R1–R7 仓库拆分 pending；UCS 工业级架构已采纳
- Updated: 2026-04-18

## 1. 项目定义

PCE 是一层本地、私有、可扩展的 AI 基础设施。

它的目标不是替代外部模型，而是在用户与各种 AI 工具之间建立一个长期存在的本地内核，用来：

- 统一记录用户在不同 AI 产品中的交互痕迹
- 让这些交互第一次在本地形成可回看、可检索、可比较的整体
- 为未来的连续性建模、帮助姿态调节与干预式注入提供底层承载

在产品层面，它更像一个“AI 产品统一网关”，而不是某个单独 AI 的插件。

## 2. 当前阶段判断

当前阶段不是“做最聪明的推理”，而是“把最扎实的基础设施搭起来”。

当前阶段的中心任务只有三件：

- 记录
- 通讯层
- 本地持久化

任何会明显拖慢这三件事的工作，都应延后。

## 3. 产品路径

PCE 的长期演化路径定义为：

`记录 -> 看见 -> 理解 -> 干预`

### 记录
- 捕获用户和不同 AI 工具之间的原始交互
- 形成统一、本地、可追溯的数据底座

### 看见
- 让用户能跨工具回看、检索、比较自己的 AI 交互历史
- 第一次“看到正在思考的自己”

### 理解
- 在足够多的原始记录上叠加手工标注、结构化提取与连续性模型
- 逐步发现用户稳定的模式，而不是急着解释用户

### 干预
- 在条件成熟后，以透明、可控的方式，把本地结构注入到外部模型调用链中
- 最终改变的不是“答案本身”，而是“帮助用户的方式”

## 4. 第一版目标

第一版必须实现的目标：

1. 能接入用户已经在使用的一部分 AI 场景
2. 能在本地统一记录这些交互
3. 能让用户在本地回看和检索这些交互
4. 不显著改变用户原有使用习惯
5. 不把数据上传到项目方服务器

## 5. 第一版非目标

第一版明确不追求：

- 自动 infer 引擎
- 自动连续性建模
- 全平台无差别注入
- 云端账号体系
- 团队协作或企业管理后台
- 对所有 AI 平台一次性全覆盖

## 6. 初期目标用户

初期默认只有一个真实用户：项目发起者自己。

原因：

- 可以高频验证真实使用链路
- 能快速判断“统一记录 + 本地可查”是否真的有价值
- 能在没有过度包装的情况下暴露底层问题

中短期扩展目标用户：

- AI 重度用户
- AI 产品独立开发者
- 在多个 AI 工具间频繁切换的人

## 7. 产品原则

### 7.1 Local-first
所有关键数据默认保存在本地，不依赖外部服务才能成立。

### 7.2 Habit-preserving
尽量不要求用户改变已有使用习惯。理想形态是一次安装、后台运行、长期无感。

### 7.3 Infrastructure before intelligence
优先建立稳定通道，再做聪明推理。没有通道，所有高级能力都不可信。

### 7.4 Capture before interpretation
先忠实记录，再解释，不提前替用户下结论。

### 7.5 User sovereignty
用户必须拥有暂停记录、删除数据、导出数据和关闭整套系统的能力。

### 7.6 Fail-open for workflows
记录失败可以接受，但不能阻断用户正常使用 AI 工具。

### 7.7 One pipe, multiple futures
当前基础设施必须同时兼容未来的理解与干预能力，避免重建底层。

## 8. 第一版成功标准

### 功能层
- 能成功拦截至少一种上游 AI API 请求
- 能把请求与响应稳定写入本地数据库
- 能按时间或来源工具查询历史记录
- 能展示最小的统一时间线或列表视图

### 体验层
- 记录模式下用户几乎感受不到额外延迟
- 代理异常时不阻断上游请求
- 用户能明确知道数据只在本地
- 安装后当天就能看到可感知的价值

### 产品层
- 用户确认“统一记录 + 本地可查”本身成立
- 后续“理解 / 干预”具备明确的承载基础

## 9. 约束

- 第一版应尽量减少依赖和概念复杂度
- 第一版不要把系统价值建立在复杂推理或复杂 UI 上
- 第一版要允许轻量模式和完整模式并存
- 第一版必须对敏感头信息、密钥和 Cookie 做脱敏处理

## 10. 术语表

### PCE
本项目的工作名，表示这套长期、本地、私有的 AI 交互基础设施。

### Source
数据来源，例如某个 CLI、浏览器插件、IDE、系统代理入口。

### Session
一组相关交互的归并单元，用于回看和搜索。

### Raw Capture
一次原始请求与响应的底层快照，是第一层事实记录。

### Normalized Message
从不同上游格式归一化后的消息单元，便于后续查询与分析。

### Light Mode
通过环境变量、局部 API 路由和浏览器插件收集数据的低门槛模式。

### Complete Mode
通过系统级 HTTPS 代理与本地证书统一覆盖 AI 域名流量的完整模式。

## 11. 工业化阶段划分

在 Foundation 阶段打通“记录 -> 看见”之后，PCE 进入工业化阶段，目标是将拓 / 存 / 渲染三大能力同时提升为工业级 + 用户友好。

阶段划分如下：

### P0 稳定现有链路
- 方向：让当前能跑的链路有可观测、有健康指标、有 schema migration 框架、有冒烟测试
- 任务单：`tasks/TASK-002-P0-stabilize-current-pipeline.md`

### P1 存层工业化
- 方向：schema 对齐 OpenInference / OTel GenAI；提供 OTLP 导出；完善导出 / 导入 / 保留策略与管道自身 trace
- 任务单：`tasks/TASK-003-P1-storage-standardization.md`

### P2 抓层工业化 + UX
- 方向：浏览器扩展迁 WXT；证书向导 + 系统代理开关；SDK 捕获通道（LiteLLM）；代理健康守护
- 任务单：`tasks/TASK-004-P2-capture-ux-upgrade.md`

### P3 渲染层工业化 + UX
- 方向：Tauri 桌面壳；首次引导；托盘 / 菜单栏图标；自动更新；诊断命令；可选 Phoenix 视图
- 任务单：`tasks/TASK-005-P3-desktop-shell.md`

### P5 Universal Capture Stack (UCS) — 2026-04-18 采纳

2026-04-18 本轮讨论采纳 **Universal Capture Stack (UCS)** 作为捕获层北极星架构。UCS 按“10 形态 × 5 层能力栈 + Supervisor + CaptureEvent v2 统一契约”组织，分 5 阶段渐进式落地，每阶段都是可发布的产品切片。

核心决策（本轮）：

- **D-1 = A**：小步快发（v1.0 不憋大招，4 周后发）
- **D-2 = B**：Open Core——OSS 部分 Apache-2.0，Pro 部分专有
- **D-3 = A**：开发者先行（Cursor/Copilot/Codex 用户，不瞄准大众）

阶段路线图：

| 阶段 | 版本 | 切片内容 | 工时估 |
|---|---|---|---|
| **P5.A** | v1.0 Subscription Capture | L1 收尾 + L3a 扩展 F2 站点 + CaptureEvent v2 + 首跑向导 + Pinning 诊断 | 3–4 周 |
| P5.B | v1.1 IDE & Electron | L3b Electron preload + L3c VS Code 扩展 | 4–5 周 |
| P6 | v1.2 Pinning-Proof | L2 Frida SSL hook | 6–8 周 |
| P7 | v1.3 Force Capture + Fallback | L0 Kernel + L4b AX + L4c OCR + JetBrains | 8–10 周 |
| P8 | v2.0 Full Supervisor | 完整调度 / 去重 / 自动降级 | 4–6 周 |

开源与商业边界（ADR-010）：

- **OSS `github.com/zstnbb/pce`**：L1 + L3a + L3d + L3e + L3f + L4a + L4c + CaptureEvent 契约 + Ingest Gateway + 归一化 / 存储 / 仪表板基础版
- **Pro `github.com/zstnbb/pce-pro`**：L0 + L2 + L3b + L4b + Capture Supervisor + IDE 扩展高级版 + 仪表板高级版
- 依赖方向约束：Pro 可 import OSS，OSS 永不 import Pro（CI 硬拦截）

P5.A 任务单：`tasks/TASK-006-P5A-subscription-capture.md`  
P5.A handoff：`handoff/HANDOFF-P5A-KICKOFF.md`

### P4 长期演进 — 已完成 (2026-04-18)
- 方向：DuckDB 分析层、sqlite-vec 语义检索、OpenLLMetry instrumentor、CDP 内嵌浏览器、移动端抓包引导
- 子项交付：
  - **P4.1** 语义检索 — `pce_core/embeddings.py` + `pce_core/semantic_search.py`、migration 0004、`GET /api/v1/search?mode=semantic`、`GET /api/v1/embeddings/status`、`POST /api/v1/embeddings/backfill`、dashboard 切换、43 tests。
  - **P4.2** DuckDB 分析层 — `pce_core/analytics.py` 直接通过 `sqlite_scanner` 只读访问源 SQLite，6 个 `/api/v1/analytics/*` 端点，Parquet 导出，graceful 503 fallback，25 tests。
  - **P4.3** OpenLLMetry / OTel GenAI — `pce_core/normalizer/genai_semconv.py` 同时发射 `llm.*` 与 `gen_ai.*`；`PCE_OTEL_GENAI_SEMCONV={off,both,only}`；`both` 为默认；23 tests。
  - **P4.4** CDP 内嵌浏览器 — `pce_core/cdp/` 基于 Playwright 的线程化驱动 + `python -m pce_core.cdp` CLI + 3 个 HTTP 端点 + migration 0005 注册 `cdp-embedded` 源；28 tests。
  - **P4.5** 移动端抓包向导 — `pce_core/mobile_wizard.py`（LAN IP 探测 + QR 渲染 + ASCII fallback）+ `python -m pce_core.mobile_wizard` CLI + 3 个 HTTP 端点 + `Docs/docs/engineering/MOBILE-CAPTURE.md`；15 tests。
- 全量 P4 新增约 134 测试；总回归 726 passed / 3 skipped。
- 决议：`docs/decisions/ADR-008-P4-observability-analytics.md`。

## 12. 当前文档地图

决议文档：
- `docs/decisions/2026-04-05-foundation-session.md` — 基础阶段收敛
- `docs/decisions/2026-04-17-industrialization-roadmap.md` — 工业化 P0–P3 收敛
- `docs/decisions/2026-04-18-ucs-and-release-strategy.md` — **UCS 采纳 + Open Core + A+A 发布策略**
- `docs/decisions/2026-04-18-P4-completion.md` — P4 长期演进完成回报

架构文档：
- `docs/engineering/ARCHITECTURE.md` — 版本随阶段演进，当前 v0.2（UCS 将在 P5–P8 推进到 v0.3）
- `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` — **UCS 工业级设计文档**（1340 行，13 章 + 3 附录）

ADR：
- `ADR-001` 第一阶段先做记录不做干预
- `ADR-002` 采用本地统一代理作为底层内核
- `ADR-003` 采用双轨安装模式
- `ADR-004` 存层 schema 对齐 OpenInference / OTel GenAI
- `ADR-005` 桌面壳选 Tauri 不选 Electron
- `ADR-006` 浏览器扩展构建链迁移 WXT
- `ADR-007` OTLP 导出作为可选次级通道
- `ADR-008` P4 可观测与分析：SQLite 只读 + DuckDB / sqlite-vec / gen_ai 别名 / CDP / 移动端向导
- `ADR-009` **采纳 Universal Capture Stack (UCS) 作为捕获层北极星架构**
- `ADR-010` **Open Core 模块边界 与 License 策略**

任务单：
- `tasks/TASK-001-proxy-poc.md` — 已完成 (Foundation)
- `tasks/TASK-002-P0-stabilize-current-pipeline.md` — 已完成 (P0)
- `tasks/TASK-003-P1-storage-standardization.md` — 已完成 (P1)
- `tasks/TASK-004-P2-capture-ux-upgrade.md` — 已完成 (P2)
- `tasks/TASK-005-P3-desktop-shell.md` — 已完成 (P3)
- `tasks/TASK-006-P5A-subscription-capture.md` — **当前执行入口（R1–R7 前置中）**

Handoff：
- `handoff/HANDOFF-TASK-001.md` — 已完成
- `handoff/HANDOFF-TASK-002.md` — 已完成
- `handoff/HANDOFF-P5A-KICKOFF.md` — **当前活动**

## 13. 对后来 agent 的阅读顺序推荐

第一次接手 PCE 中 P5 阶段的 agent，按以下顺序读约 2–3 小时可建立全面认识：

1. 本文件 `docs/PROJECT.md`（项目总边界）
2. `docs/decisions/2026-04-18-ucs-and-release-strategy.md` （本轮决议，直接上游）
3. `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` （UCS 工业级设计文档，全景蓝图）
4. `docs/engineering/adr/ADR-009`（UCS 决策）+ `ADR-010`（Open Core 边界）
5. `tasks/TASK-006-P5A-subscription-capture.md` （当前任务单）
6. `handoff/HANDOFF-P5A-KICKOFF.md` （执行 handoff）

还需要深入某个领域再补读对应 ADR 与早期决议。
