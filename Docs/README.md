# PCE Docs

这是 PCE 项目的所有落盘文档入口。它用来替代长聊天，指导后续实现与迭代。

截至 2026-04-18，P0–P4 全部完成；本轮采纳 **Universal Capture Stack (UCS)** 作为捕获层北极星架构，采纳 **Open Core** 商业模型，采纳 **A+A 发布路径**（小步快发 + 开发者优先）；P5.A Subscription Capture 即将开工。

## 这套文件怎么用

先读顺序如下（推荐第一次接手项目的人按这个顺序看，约 2–3 小时建立全面认识）：

1. `docs/PROJECT.md`
   - 看项目当前阶段、目标、边界、产品原则与阶段划分。
2. `docs/decisions/2026-04-18-ucs-and-release-strategy.md` ⭐
   - **本轮决议**：UCS 采纳 + Open Core + A+A 发布路径。当前所有工作的直接上游。
3. `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` ⭐
   - **UCS 工业级设计文档**（1340 行，13 章 + 3 附录），P5–P8 所有阶段的蓝本。
4. `docs/engineering/adr/`
   - 看每一个关键决策为什么这样选。共 10 份 ADR，其中 ADR-009（UCS）和 ADR-010（Open Core）是本轮新增。
5. `docs/decisions/2026-04-05-foundation-session.md`
   - 项目起点的定义（需要背景时读）。
6. `docs/decisions/2026-04-17-industrialization-roadmap.md`
   - P0–P3 工业化收敛（需要理解已完成阶段时读）。
7. `docs/engineering/ARCHITECTURE.md`
   - 当前 v0.2 架构，UCS 将在 P5–P8 推进到 v0.3。
8. `tasks/TASK-006-P5A-subscription-capture.md` ⭐
   - **当前执行入口**，P5.A 的 11 项原子任务。
9. `handoff/HANDOFF-P5A-KICKOFF.md` ⭐
   - 可直接交给本地 agent 的 P5.A handoff。

## 阶段与任务对照

| 阶段 | 任务单 | Handoff | 状态 |
|---|---|---|---|
| Foundation | `tasks/TASK-001-proxy-poc.md` | `handoff/HANDOFF-TASK-001.md` | 已完成 |
| P0 稳定现状 | `tasks/TASK-002-P0-stabilize-current-pipeline.md` | `handoff/HANDOFF-TASK-002.md` | 已完成 |
| P1 存层工业化 | `tasks/TASK-003-P1-storage-standardization.md` | — | 已完成 |
| P2 抓层工业化 + UX | `tasks/TASK-004-P2-capture-ux-upgrade.md` | — | 已完成 |
| P3 渲染层工业化 + UX | `tasks/TASK-005-P3-desktop-shell.md` | — | 已完成 |
| P4 长期演进 | — | — | 已完成 |
| **P5.A** Subscription Capture (v1.0) | `tasks/TASK-006-P5A-subscription-capture.md` | `handoff/HANDOFF-P5A-KICKOFF.md` | **当前活动**（R1–R7 前置中）|
| P5.B IDE & Electron (v1.1) | 待 P5.A 完成后产出 | — | 待启动 |
| P6 Pinning-Proof (v1.2) | 待 P5.B 完成后产出 | — | 待启动 |
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

基础设施（记录 -> 看见）已在 P0–P4 打通。当前进入“按照 UCS 北极星架构分阶段迭代 + 首次对外发布”阶段。

近期重点：

1. 完成 R1–R7 仓库拆分与开源门面文件（P5.A 编码的前置条件）
2. 开工 P5.A 11 项原子任务（见 `TASK-006`）
3. 4 周后发 v1.0 Subscription Capture（OSS `github.com/zstnbb/pce` 从私有转公开，Apache-2.0）

绝对不追求的：

- 对所有 AI 产品一次性全覆盖（UCS Tier 3 明确不投资 F7 移动端 / F8 OS 集成）
- v1.0 就上 ChatGPT Plus 大众用户（技术门槛不匹配，待 v2.0 Supervisor 就绪后再扩展）
- 自动 infer / 自动干预 / 自动连续性建模（属于“理解”层，不在 P5–P8 范围）

一句话语境：抓 / 存 / 渲染的工业化已完，接下来是把抓层按 UCS 推到全形态 75% 覆盖率，同时产品化对外发行。
