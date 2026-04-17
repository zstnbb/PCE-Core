# PCE Docs

这是 PCE 项目的所有落盘文档入口。它用来替代长聊天，指导后续实现与迭代。

截至 2026-04-17，项目已跨过 Foundation 阶段（TASK-001 完成），正式进入工业化阶段 P0。

## 这套文件怎么用

先读顺序如下（推荐第一次接手项目的人按这个顺序看）：

1. `docs/decisions/2026-04-05-foundation-session.md`
   - 看项目起点是怎么定义的。
2. `docs/decisions/2026-04-17-industrialization-roadmap.md`
   - 看进入工业化阶段后，方向怎么收口。
3. `docs/PROJECT.md`
   - 看项目当前阶段、目标、边界、产品原则与阶段划分。
4. `docs/engineering/ARCHITECTURE.md`
   - 看 v0.2 架构：抓 / 存 / 渲染三大能力、模式设计、数据流。
5. `docs/engineering/adr/`
   - 看每一个关键决策为什么这样选。共 7 份 ADR。
6. `tasks/TASK-002-P0-stabilize-current-pipeline.md`
   - 当前阶段的执行入口。
7. `handoff/HANDOFF-TASK-002.md`
   - 可直接交给本地 agent 的 handoff。

## 阶段与任务对照

| 阶段 | 任务单 | Handoff | 状态 |
|---|---|---|---|
| Foundation | `tasks/TASK-001-proxy-poc.md` | `handoff/HANDOFF-TASK-001.md` | 已完成 |
| P0 稳定现状 | `tasks/TASK-002-P0-stabilize-current-pipeline.md` | `handoff/HANDOFF-TASK-002.md` | 当前活动 |
| P1 存层工业化 | `tasks/TASK-003-P1-storage-standardization.md` | 待 P0 完成后产出 | 待启动 |
| P2 抓层工业化 + UX | `tasks/TASK-004-P2-capture-ux-upgrade.md` | 待 P1 完成后产出 | 待启动 |
| P3 渲染层工业化 + UX | `tasks/TASK-005-P3-desktop-shell.md` | 待 P2 完成后产出 | 待启动 |

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

当前不建议先追求“自动 infer / 自动干预 / 自动连续性建模”。
当前应该优先把“记录 -> 看见”打通，先形成一条稳定、可复用、可扩展的本地基础设施。
