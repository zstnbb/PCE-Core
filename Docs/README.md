# PCE Foundation Pack

这是一套根据 2026-04-05 讨论整理出的可落盘文件，用来替代长聊天，指导后续实现与迭代。

## 这套文件怎么用

先读顺序如下：

1. `docs/decisions/2026-04-05-foundation-session.md`
   - 看这轮到底定了什么。
2. `docs/PROJECT.md`
   - 看项目当前阶段、目标、边界和产品原则。
3. `docs/engineering/ARCHITECTURE.md`
   - 看第一版内核的结构、数据流和技术边界。
4. `docs/engineering/adr/`
   - 看为什么选这些方案，而不是别的方案。
5. `tasks/TASK-001-proxy-poc.md`
   - 这是本地 agent 当前应该执行的任务单。
6. `handoff/HANDOFF-TASK-001.md`
   - 这是可以直接交给本地 agent 的 handoff 文本。

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
