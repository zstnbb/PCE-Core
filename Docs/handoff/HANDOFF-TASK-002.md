# HANDOFF: TASK-002 P0 稳定现有链路

你现在接到的是 PCE 工业化阶段的第一张执行任务单。

## 你先读这些文件

按顺序读完，不要跳：

1. `Docs/docs/decisions/2026-04-05-foundation-session.md` — 了解项目起点
2. `Docs/docs/decisions/2026-04-17-industrialization-roadmap.md` — 了解为什么现在做 P0
3. `Docs/docs/PROJECT.md` — 当前阶段定义（2026-04-17 更新版）
4. `Docs/docs/engineering/ARCHITECTURE.md` — v0.2 架构
5. `Docs/docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
6. `Docs/docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`
7. `Docs/tasks/TASK-002-P0-stabilize-current-pipeline.md` — 本轮任务单

## 你的目标

让 PCE 当前三条入口（mitmproxy / 浏览器扩展 / MCP）从"能跑"变成"可观测 + 可维护 + 可冒烟验证"：

1. 每条来源可查询健康指标
2. 有正规 migration 机制
3. CI 可一键跑三条入口的冒烟测试
4. Dashboard 有一页健康状态

不要在本轮引入任何新功能，不要碰 OpenInference schema 迁移（那是 P1）。

## 你的工作方式

先做两步，然后再动代码：

1. 给出仓库内的最小实施计划，说明每一项改在哪个文件 / 目录
2. 说明你如何在本地验证，尤其是 migration 的验证方式
3. 再开始实现

## 强约束

- 不做 OpenInference schema 迁移
- 不做 OTLP 导出
- 不迁移浏览器扩展到 WXT
- 不做桌面壳
- 不引入重型新依赖（最多允许 `python-json-logger` 或等价物）
- 不能破坏现有任何测试
- 不能让捕获主路径变慢到用户可感知的程度

## 本轮最重要的交付

1. `GET /api/v1/health` 返回完整健康指标
2. `pce_core/migrations/` 目录 + `0001_baseline.py` + runner
3. Dashboard 健康页
4. `tests/smoke/` 三条冒烟测试
5. `.github/workflows/smoke.yml` 或 `scripts/run_smoke.{sh,ps1}`
6. 完成回报文档

## 你完成后要这样回报

### A. Plan
- 你怎么理解 P0 的边界
- 你每个交付物放在哪里
- 你为什么这样放

### B. Changed Files
- 新增
- 修改
- 删除

### C. What Works
- 健康指标的字段是否全部非空
- 三条冒烟路径是否都通
- migration 是否可被触发

### D. What Does Not Work Yet
- 未完成项
- 已知限制

### E. How To Run
- 本地怎么起服务
- 怎么访问健康页
- 怎么一键跑冒烟

### F. Risks / Follow-ups
- 你建议 P1 先解哪一块
- 你发现的任何新架构隐患
- 你需要人拍板的问题

### G. Completion Note
- 同时在 `Docs/docs/decisions/` 下面放一份 `2026-04-??-P0-completion.md`
- 内容简要总结本轮完成情况，供 P1 开工时参考

## 特别提醒

- ADR-004 / ADR-007 讨论的 OpenInference 与 OTLP 是 P1 才做的事，本轮只需要**知道**即将到来，不要在 P0 提前引入
- 任何你觉得"顺手一起做了"的改动，如果超出 P0 范围，请记录在 Follow-ups 里，不要直接合进去
- 如果你发现 P0 的验收标准写得和实际仓库不匹配，停下来回报，不要凭自己判断改范围
