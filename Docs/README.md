# PCE Docs

这是 PCE 项目的所有落盘文档入口。它用来替代长聊天，指导后续实现与迭代。

截至 2026-05-12：P0–P4 全部完成；P5.A 浏览器扩展子系统已硬冻结；P5.B IDE & Desktop & MCP Capture 在 chat-region 走通 D0 释放门（Claude Desktop 19/22 PASS），cowork-region + code-region 作为并行轨道 carry forward；**P5.C Meta-Pipeline & Maintainability 已于 2026-05-12 完成并 tag v1.1.5**，本轮决议把可维护性作为产品一等约束（ADR-019），跨 lane 三件套契约 + Test Conductor + 健康度矩阵 + 9 ADR-011 gates 全绿，进入 P6 Coverage Polish。

## 这套文件怎么用

先读顺序如下（推荐第一次接手项目的人按这个顺序看，约 2.5 小时建立全面认识）：

### ⭐ 新 agent 接班 60 秒定位 (read first)

`docs/engineering/PCE-STANDARD-WORKFLOW.md` ⭐ — **任何接到 "做 X 这个产品 / 修 X 这个 bug" 任务的 agent 必须先读 60 秒**。它把 PCE 接产品 / 修 bug 的完整生命周期定义成 6 阶段（决策 → Desk → RECON → 标准 → 闭环 → 稳定），每阶段输入 / 工具 / 产物 / 完成标准都列了, 加一张编号速查表 (T/D/E/C/F-case, S0-SX, L0-L4, plane N/H/M/U, G1-G9) 和一份 5 分钟自检清单。读完它再去翻具体 doc, 不会迷路。

### P5.C 接手 agent (active)

1. `docs/PROJECT.md` ⭐
   - 项目总边界、§11 阶段划分含 P5.C、§13.A 接手 P5.C 的阅读路径。
2. `docs/decisions/2026-05-12-meta-pipeline-and-maintenance-strategy.md` ⭐
   - **本轮决议**：P5.C "为什么" 的真理来源、9 章。
3. `docs/engineering/META-PIPELINE-FRAMEWORK.md` ⭐
   - **P5.C "怎么做" 的工程级定义**：4 lane × 三件套契约（capture/verification/health-as-data）+ AdapterContract + Test Conductor manifest 规范。
4. `docs/engineering/adr/ADR-019-maintenance-as-first-class-concern.md`
   - 治理优于功能作为产品一等约束的决策固化。
5. `stability/DESKTOP-D-CASE-EXECUTION-STANDARD.md` + `stability/PCE-PIPELINE-HEALTH-MATRIX.md` ⭐
   - **P5.C "什么算做完" 的两个验证契约**。
6. `docs/engineering/adr/ADR-017-test-conductor-cross-lane-agent-contract.md`
   - P5.C.2 实施依据（status: Proposed → Adopted）。
7. `handoff/HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` ⭐
   - **当前 active**，P5.C.0 → P5.C.5 子阶段执行入口。

### P5.B 余项轨道 agent

1. `docs/PROJECT.md` §13.B
2. `research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` ⭐ — strategy 轴
3. `stability/DESKTOP-PRODUCT-MATRIX.md` ⭐ — validation 轴（D/C/E case 全集）
4. `handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` 🟡 — carry-on
5. `handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` — cowork standard-down
6. `docs/engineering/META-PIPELINE-FRAMEWORK.md` ⭐ — 余项 lane 也需遵守三件套契约

### 历史背景（按需读）

- `docs/decisions/2026-04-18-ucs-and-release-strategy.md` — UCS 采纳 + Open Core + A+A 发布路径
- `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` — UCS 工业级设计文档（1340 行）
- `docs/decisions/2026-04-05-foundation-session.md` — 基础阶段
- `docs/decisions/2026-04-17-industrialization-roadmap.md` — P0–P3 工业化收敛

## 阶段与任务对照

| 阶段 | 任务单 | Handoff | 状态 |
|---|---|---|---|
| Foundation | `tasks/TASK-001-proxy-poc.md` | `handoff/HANDOFF-TASK-001.md` | 已完成 |
| P0 稳定现状 | `tasks/TASK-002-P0-stabilize-current-pipeline.md` | `handoff/HANDOFF-TASK-002.md` | 已完成 |
| P1 存层工业化 | `tasks/TASK-003-P1-storage-standardization.md` | — | 已完成 |
| P2 抓层工业化 + UX | `tasks/TASK-004-P2-capture-ux-upgrade.md` | — | 已完成 |
| P3 渲染层工业化 + UX | `tasks/TASK-005-P3-desktop-shell.md` | — | 已完成 |
| P4 长期演进 | — | — | 已完成 |
| P5.A Subscription Capture (v1.0) | `tasks/TASK-006-P5A-subscription-capture.md` | `handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` | ✅ 浏览器扩展子系统硬冻结 |
| P5.B IDE & Desktop & MCP (v1.1) | `tasks/TASK-007-P5B-ide-desktop-mcp.md` (待起草) | `handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` 🟡 carry-on | chat-region D0 已过；cowork+code 余项与 P5.C 并行 |
| **P5.C** Meta-Pipeline & Maintainability (v1.1.5) | (待起草) | `handoff/HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` ⭐ | **当前活动**（P5.C.0 契约冻结同 commit 落地） |
| P5.D IDE plugin (v1.1.6) | 待 P5.C 完成后产出 | — | 待启动 |
| P6 Pinning-Proof / Coverage Polish (v1.2) | 待 P5.D 完成后产出 | — | 待启动 |
| P7 Force Capture (v1.3) | 待 P6 完成后产出 | — | 待启动 |
| P8 Full Supervisor (v2.0) | 待 P7 完成后产出 | — | 待启动 |

## 文件分工

- `archive/chats/`
  - 原始聊天归档。只归档，不作为日常上下文主输入。
- `docs/decisions/`
  - 单次长讨论的收敛结果。一页纸左右，替代大段聊天。
- `docs/PROJECT.md`
  - 项目的总边界与当前阶段定义。
- `docs/engineering/ARCHITECTURE.md`
  - 技术结构、模块、数据流、实现边界。
- `docs/engineering/adr/`
  - 关键工程决策记录，后续不要轻易推翻。
- `docs/engineering/PCE-STANDARD-WORKFLOW.md` ⭐
  - **PCE 接产品 / 修 bug 完整生命周期 (6 阶段) + 编号速查表 + 5 分钟自检清单**。任何新 agent 接班先读 60 秒, 再翻具体 doc。
- `docs/engineering/META-PIPELINE-FRAMEWORK.md`
  - P5.C 跨 lane 三件套契约（capture/verification/health-as-data）+ AdapterContract 规范，是 Meta-Pipeline 阶段所有 lane 的工程契约。
- `stability/`
  - 验证执行标准与覆盖矩阵：浏览器 T-case (`PCE-PROBE-E2E-EXECUTION-STANDARD.md`)、桌面 D-case (`DESKTOP-D-CASE-EXECUTION-STANDARD.md`)、健康度 (`PCE-PIPELINE-HEALTH-MATRIX.md`)、桌面产品矩阵 (`DESKTOP-PRODUCT-MATRIX.md`)、站点 tier (`SITE-TIER-MATRIX.md`).
- `research/`
  - 战略性认知文档：例如 `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` 4 张捕获面 × 应用类型 × normalizer 三段。
- `legal/` （2026-05-12 P5.C.0 新增）
  - 法律治理产物（ADR-019 §3.5）：`THREAT-MODEL.md` per-layer 风险矩阵 + 7 个法律理论防御论据；`CEASE-AND-DESIST-RESPONSE.md` 24h 函件响应 playbook + vendor kill-switch + P0–P3 escalation tree。任何新抓取技术 merge 前必须能落入 THREAT-MODEL §3 矩阵某一行；任何法律函件按 CEASE-AND-DESIST-RESPONSE §1 流程响应。
- `tasks/`
  - 给本地 agent 的执行入口。
- `handoff/`
  - 可直接发给本地 agent 的简短执行说明。

## 推荐协作循环

每一轮都按这个顺序走：

1. 长讨论结束后，把原始聊天归档到 `archive/chats/`
2. 生成一份新的 `docs/decisions/<date>-<topic>.md`
3. 只把长期有效的部分同步回 `PROJECT.md`、`ARCHITECTURE.md`、`ADR`
4. 生成下一张 `TASK-xxx.md`
5. 把对应 `HANDOFF-xxx.md` 交给本地 agent
6. 本地 agent 回传实现计划、变更清单、测试结果、阻塞点
7. 再次更新决议、架构或 ADR，而不是继续堆聊天

## 角色分配

- Web 端 AI
  - 负责把长讨论压缩成决议、规格、ADR、任务单
- 本地 agent
  - 负责贴着仓库做实现、测试、改动说明、阻塞上报
- 人
  - 只在关键分叉点拍板，并把文档归档到固定目录

## 当前建议

基础设施（记录 -> 看见）已在 P0–P4 打通；UCS 北极星架构 + Open Core + A+A 发布路径已锁定（2026-04-18 决议）；P5.A 浏览器扩展子系统硬冻结；P5.B chat-region D0 释放门已过。当前进入 **P5.C Meta-Pipeline & Maintainability** 阶段——把跨 lane 三件套契约 + Test Conductor + 健康度矩阵 + 治理产物落地，让项目所有者「无法主体维护」约束下系统能持续生存。

近期重点：

1. P5.C.0 契约冻结（**本 commit 已交付** — 6 份新文档 + PROJECT.md/Docs/README.md 更新）
2. P5.C.1 健康度骨架（`pce_core/health.py` + 4 lane 接入 + dashboard 健康度视图）
3. P5.C.2 Test Conductor MVP（激活 ADR-017，8 MCP tool + canary + classifier）
4. P5.B 余项（cowork + code）作为并行轨道，由独立 agent 推进

绝对不追求的：

- 对所有 AI 产品一次性全覆盖（UCS Tier 3 明确不投资 F7 移动端 / F8 OS 集成）
- 把维护负担留给项目所有者（ADR-019 把治理作为产品 P0）
- 自动 infer / 自动干预 / 自动连续性建模（属于"理解"层，不在 P5–P8 范围）
- 客户端 telemetry 上报到外部（Local-first 硬约束，PROJECT.md §7.1）

一句话语境：抓 / 存 / 渲染的工业化已完，浏览器 + 桌面 chat 已走通 D0；现在把跨 lane 契约 + 治理工程化，让 PCE 在项目所有者无法亲自维护时仍能生存与迭代。
