# TASK-003: P1 存层工业化

- Status: Pending P0
- Priority: P1
- Owner: Local agent
- Date: 2026-04-17
- Phase: Industrialization P1

## 1. 背景

P0 稳定了现有链路之后，存层仍然有两个结构性问题：

1. 字段命名是 PCE 自己发明的，不对齐 OpenInference / OTel GenAI，外部工具无法读取
2. 数据没有标准导出路径，用户无法把自己的数据带走

P1 的目的不是加新能力，是让我们这张 SQLite 第一次成为"对外开放的公民"，同时保持 local-first 的底线不变。

## 2. 目标

让 PCE 的存层同时具备：

1. 归一化数据能以 OpenInference 属性视图对外呈现
2. 可选的 OTLP 导出通道（默认关闭）
3. 完整的导出 / 导入 / 保留策略
4. 管道自身每一步都被 trace 覆盖

## 3. 输入文档

开工前必须阅读：

- `docs/decisions/2026-04-17-industrialization-roadmap.md`
- `docs/engineering/ARCHITECTURE.md`（v0.2 的第 7 节）
- `docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
- `docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`
- P0 完成回报（`docs/decisions/2026-04-??-P0-completion.md`）

## 4. 范围

### In Scope
- `pce_core/normalizer/openinference_mapper.py`：从 `messages` / `sessions` 到 OpenInference 属性集的单向映射
- `pce_core/otel_exporter.py`：OTLP 导出器（opt-in，失败不阻塞）
- `pce_core/export.py`：OTLP JSONL / 标准 JSON 导出
- 导入 API：从 OTLP JSONL 还原数据（幂等）
- 数据保留策略配置（按天 / 按条）
- 管道内部 trace（normalize / reconcile / persist / session_manager）
- 必要的 migration：`0002_openinference_compat.py`

### Out of Scope
- 破坏性字段重命名（严禁）
- 把 OTLP 后端当成主存储
- Parquet 导出（留给 P4 视需求）
- 向量索引（留给 P4）

## 5. 建议实现边界

### 5.1 OpenInference 映射层

新建 `pce_core/normalizer/openinference_mapper.py`：

- 函数 `to_openinference_attributes(message: MessageRecord, session: SessionRecord, raw: RawCapture | None) -> dict[str, Any]`
- 产出符合 OpenInference spec 的 attribute 字典
- 参考 `ARCHITECTURE.md` 7.2 的映射表，尽量完整
- 对于 list 类型（如 `input_messages`），遵循 OpenInference 的扁平化约定

映射层只读，不修改数据库。

### 5.2 OTLP 导出器

新建 `pce_core/otel_exporter.py`：

- 启动时若 `OTEL_EXPORTER_OTLP_ENDPOINT` 存在则初始化
- 暴露 `emit_capture_span(pair_id, messages, session)`
- `pipeline.py` 的 `_persist_result` 在落库成功后调用
- 使用 `opentelemetry-sdk` 的 BatchSpanProcessor
- 任何异常记录日志后直接吞掉，不向上抛

### 5.3 导出 / 导入

在 `pce_core/server.py` 增加：

- `GET /api/v1/export?format=otlp&since=...&until=...` 流式返回 JSONL
- `GET /api/v1/export?format=json&...` 返回标准 JSON（包含 raw + normalized）
- `POST /api/v1/import` 接收 OTLP JSONL，幂等（按 `pair_id` + 内容 hash 去重）

### 5.4 保留策略

在配置中引入：

- `PCE_RETENTION_DAYS`（默认空，即无限保留）
- `PCE_RETENTION_MAX_ROWS`（默认空）

后台任务每 6 小时扫描并按策略删除。删除操作必须同时清理 raw_captures、messages、sessions 的孤儿行、FTS5 索引。

### 5.5 管道 trace

在 `pce_core/normalizer/pipeline.py` 等关键位点用 `opentelemetry.trace` 产出 span：

- `pce.ingest.receive`
- `pce.redact`
- `pce.raw.persist`
- `pce.normalize`
- `pce.reconcile`
- `pce.session.resolve`
- `pce.persist.message`
- `pce.otel.emit`
- `pce.fts.index`

这些 span 默认也走 OTLP 导出器（如果开启），与业务 span 共用同一条通道，但不同 instrumentation scope。

### 5.6 Migration `0002_openinference_compat`

采用"加新字段、保留旧字段、先双写"策略：

- `messages` 增加：`oi_role_raw` / `oi_input_tokens` / `oi_output_tokens` / `oi_attributes_json`
- 写入路径：老字段与新字段同时写
- 读取路径：老字段为主，映射层优先走新字段
- 不删除任何旧字段

## 6. 验收标准

必须同时满足：

- [ ] `to_openinference_attributes` 对每条 provider（OpenAI / Anthropic / Conversation / Generic）都能产出 spec 一致的属性字典
- [ ] 设 `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` 并启动本地 Phoenix 时，能在 Phoenix 看到 PCE 产出的 trace
- [ ] `GET /api/v1/export?format=otlp` 能流式导出最近 N 条
- [ ] 把 export 结果 POST 回 `/api/v1/import` 幂等，不会重复
- [ ] 设 `PCE_RETENTION_DAYS=1` 并等待后台任务触发后，1 天前数据被清理，FTS 与索引不剩孤儿行
- [ ] 管道 trace 在 Phoenix 中可以看到完整的 `pce.ingest -> pce.persist.message` 链条
- [ ] P0 冒烟测试全部仍通过
- [ ] 增加针对 OI 映射、OTLP 导出、导入幂等、保留策略的新测试

## 7. 建议交付物

- 新增：`pce_core/normalizer/openinference_mapper.py`
- 新增：`pce_core/otel_exporter.py`
- 新增：`pce_core/export.py`
- 修改：`pce_core/normalizer/pipeline.py` / `pce_core/server.py` / `pce_core/config.py`
- 新增：`pce_core/migrations/0002_openinference_compat.py`
- 新增：`tests/test_openinference_mapping.py` / `tests/test_otel_export.py` / `tests/test_import_export.py` / `tests/test_retention.py`
- 更新：`requirements.txt`（添加 `opentelemetry-sdk` / `opentelemetry-exporter-otlp`）
- 完成回报：`Docs/decisions/2026-04-??-P1-completion.md`

## 8. 停止条件

- 发现 OpenInference spec 对某个 provider 缺少必要属性（需要额外讨论）
- 发现迁移路径必然涉及破坏性字段变更
- 发现 OTLP 导出引入不可忽略的主路径延迟
- 发现数据保留策略会与未来"理解层"标注冲突

## 9. 回报格式

同 TASK-002 的第 9 节。
