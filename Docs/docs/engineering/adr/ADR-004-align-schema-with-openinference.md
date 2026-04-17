# ADR-004: 存层 schema 对齐 OpenInference / OpenTelemetry GenAI

- Status: Accepted
- Date: 2026-04-17
- Related: 2026-04-17-industrialization-roadmap.md

## Context

第一版 PoC 阶段 PCE 自行定义了 `messages` / `sessions` / `raw_captures` 的字段语义（例如 `role`、`content_text`、`content_json`、`model_name`）。

这套私有结构在单机闭环下可用，但存在以下问题：

- 同方向的开源生态（Langfuse / Phoenix / OpenLLMetry / Helicone）已经基于 OpenTelemetry 的 GenAI 语义约定和 Arize 的 OpenInference 规范形成事实标准
- 每新增一个 provider 格式，我们都要手写归一化逻辑，并重新定义字段含义
- 与第三方工具（trace 查看器、分析工具、导出目标）无法互通
- 用户导出的数据没有标准格式，对外迁移成本高
- 未来我们的数据无法被 AI agent 以"通用结构"直接读取

## Decision

PCE 的归一化层输出结构以 **OpenInference 规范为主**，并以 **OpenTelemetry GenAI semconv 作为兼容映射**。

具体落实：

- `messages` 表的核心字段映射到 OpenInference 的 input/output/llm 属性命名
- 新增字段不再发明自己的命名，必须优先查找 OpenInference / OTel GenAI 是否已经定义
- 对每条归一化产物，保留一个"OpenInference attribute 视图"方法，允许以标准属性集导出
- 原始层 `raw_captures` 不受此约束（它是事实层，保持提供方原貌）
- 多源 reconciler 与脱敏 redactor 是 PCE 的独有价值，不被标准约束，继续自研

迁移策略：

- 不做破坏性字段重命名
- 采用"加新字段、保留旧字段、先双写"的方式推进
- 至少保留三个 release 周期的旧字段读取兼容
- 升级通过正规 migration 机制执行（见 P1 任务单）

## Rationale

1. 对齐开源标准的**数据侧**收益巨大：任何基于 OTel 的工具都能读我们的数据；我们能 emit OTLP 到任何 OTel-compatible 后端。
2. 对齐的**代码侧**成本可控：字段映射是一层薄适配，不会侵入 reconciler / redactor / session_manager 这些真正的核心逻辑。
3. OpenInference 选型依据：它比 OTel GenAI semconv 更具体、更成熟、已被 Arize Phoenix 原生支持，且本身就声明"与 OTel 兼容"。
4. 双写迁移保证现有用户数据不被破坏。

## Consequences

### Positive

- 数据从此有了"行业通用"解读方式
- OTLP 导出可以做得非常薄
- 减少未来"又一个 provider 改了格式"的维护负担
- 为"用户把数据带走、接到别的工具"这个用户友好诉求打好基础

### Negative

- 需要一次谨慎的字段映射与迁移
- 存在短期的字段冗余（新旧并存）
- 对 OpenInference 规范的后续变更需要跟进

## Guardrails

- 任何字段映射变更必须同时更新 `pce_core/migrations/`
- 新字段命名必须有明确的 OpenInference / OTel GenAI 来源引用
- `raw_captures` 不对齐标准，永远保留提供方原貌
- 脱敏、去重、reconciliation 逻辑保持为 PCE 自己的表征，不把它压进 OpenInference 字段

## Review Trigger

以下情况需要重新评估本 ADR：

- OpenInference 规范出现不兼容的破坏性演进
- OTel GenAI semconv 稳定落地且与 OpenInference 产生冲突
- 用户真实场景反馈"标准字段不够用"
