# ADR-007: OTLP 导出作为可选二级通道，本地 SQLite 仍为唯一可信来源

- Status: Accepted
- Date: 2026-04-17
- Related: ADR-002-local-proxy-as-core.md, ADR-004-align-schema-with-openinference.md

## Context

在 ADR-004 之后，PCE 的归一化数据将对齐 OpenInference 语义。这自然引出一个问题：

> 要不要把 OTLP 协议作为 PCE 的主通道？或者直接把数据写到 Langfuse / Phoenix / Jaeger 这类 OTel 后端？

这个问题必须明确回答，否则随着 OpenInference 集成推进，存层可能被无意识地拖向"服务端部署"方向，违反 PCE 的 local-first 原则（见 PROJECT.md 第 7.1 条）。

## Decision

- 本地 SQLite 是 PCE 数据的**唯一可信来源 (source of truth)**
- OTLP 导出通道以**可选、次级、非默认**方式存在
- 不支持"以 OTLP 后端替代本地 SQLite"的部署模式
- 不强制用户安装任何 OTel 后端

具体机制：

- `pce_core` 可以在归一化完成后，额外 emit 一条 OTLP span
- 目标端点由环境变量 `OTEL_EXPORTER_OTLP_ENDPOINT` 决定
- 默认不设端点、不发数据
- 发送失败不回滚本地落库，也不阻塞主路径
- 用户可在 pce_app 设置中一键开启"本地 Phoenix 视图"，这会启动一个本地 Phoenix 实例并指向它

## Rationale

1. **Local-first 是底线原则**。任何把数据路径默认导向远端后端的选择都违反 PROJECT 第 7.1 条。
2. **OpenTelemetry 协议适合当成出口，不适合当成存储**。其异步 / 批处理 / drop 策略不符合 PCE"忠实记录"的底线。
3. **本地 SQLite 对单用户场景已经足够**。引入 Postgres / ClickHouse / Redis 这类服务端组件只有在规模化部署时才有意义，而 PCE 第一阶段明确不追求多用户。
4. **把 OTLP 定位为"导出/协作通道"而非"存储通道"**，能同时满足两条诉求：本地数据主权不变 + 用户想接第三方工具时能接。
5. **避免 ADR-002 被稀释**。ADR-002 把"本地统一代理"作为核心，把 OTLP 摆到与之并列只会造成混乱。

## Consequences

### Positive

- 存层核心不受 OTel 生态演化节奏影响
- 用户可以按需接入 Phoenix / Langfuse / Jaeger / 自建 OTel 后端
- 未来即便 OTel 整个生态换代，我们的 SQLite 永远可读
- 失败模式简单：OTLP 出错不影响主路径

### Negative

- 需要维护一条"向 OpenInference attributes 的映射"代码路径
- 若用户主要看 Phoenix 视图，其体验受 Phoenix 版本影响（不在我们可控范围）
- 内部 trace 数据与用户可见数据之间可能出现轻微语义差异

## Guardrails

- OTLP 导出必须在独立进程 / 独立 exporter 中运行，失败不阻塞 `insert_capture`
- 导出开关默认关闭
- 启动时如果检测到用户配了 OTel endpoint 但无法连通，必须在 pce_app 给出可见提示，不静默丢数据
- OTLP span 与 SQLite 记录必须携带同一 `pair_id` / `session_id`，允许事后对齐

## Review Trigger

以下情况重新评估：

- 出现 PCE 确实要支持多设备同步的需求（届时需要重新讨论整体数据主权模型）
- OpenInference / OTel GenAI 出现某种反过来要求数据必须在远端的演进
- 出现一种比 OTLP 更适合"local-first + 标准互通"的传输协议
