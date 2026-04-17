# TASK-002: P0 稳定现有链路

- Status: Ready
- Priority: P0
- Owner: Local agent
- Date: 2026-04-17
- Phase: Industrialization P0

## 1. 背景

Foundation 阶段的 TASK-001 已交付一条可跑通的 proxy + 归一化 + SQLite + 浏览器扩展 + MCP 链路。

进入工业化阶段之前，必须先把当前链路从"能跑"提升为"可观测 + 可维护 + 可冒烟验证"的状态，否则后续 P1/P2/P3 每一步都会踩到"其实底下在偷偷坏掉"的坑。

本任务不引入任何新功能，只夯实现有链路。

## 2. 目标

让 PCE 当前的三条入口（mitmproxy / 浏览器扩展 / MCP）同时具备：

1. 每条来源有明确可查询的健康指标
2. 每次数据库 schema 变更有正规 migration 机制
3. CI 可以一键跑通冒烟测试覆盖三条入口
4. 本地 dashboard 提供一页健康状态

## 3. 输入文档

开工前必须阅读：

- `docs/decisions/2026-04-17-industrialization-roadmap.md`
- `docs/PROJECT.md`（v2026-04-17 更新版）
- `docs/engineering/ARCHITECTURE.md`（v0.2）
- `docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
- `docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`

## 4. 范围

### In Scope
- 结构化日志框架（统一格式 + 分级 + 可切换）
- 健康指标 API 与查询函数
- Dashboard 健康状态页
- `pce_core/migrations/` 目录与最小可用 migration runner
- 冒烟测试：proxy、MCP、extension 各至少一条端到端路径
- CI 配置（GitHub Actions 或本地脚本）把冒烟跑起来

### Out of Scope
- OpenInference schema 迁移（P1 做）
- OTLP 导出（P1 做）
- 扩展迁 WXT（P2 做）
- 桌面壳（P3 做）
- 任何 AI 分析能力

## 5. 建议实现边界

### 5.1 健康指标 API

在 `pce_core/server.py` 扩展 `/api/v1/health` 返回：

```json
{
  "schema_version": 7,
  "sources": {
    "proxy": {
      "last_capture_at": "2026-04-17T09:12:33Z",
      "captures_last_1h": 42,
      "captures_last_24h": 187,
      "failures_last_24h": 3,
      "drop_rate": 0.015,
      "latency_p50_ms": 18,
      "latency_p95_ms": 92
    },
    "browser_extension": { ... },
    "mcp": { ... }
  },
  "pipeline": {
    "normalizer_errors_last_24h": 0,
    "reconciler_errors_last_24h": 0,
    "fts_index_lag_ms": 12
  },
  "storage": {
    "db_size_bytes": 48213504,
    "raw_captures_rows": 1823,
    "messages_rows": 3417,
    "sessions_rows": 89
  }
}
```

`pce_core/db.py` 已有 `get_capture_health` 函数，基于它扩展。

### 5.2 结构化日志

统一使用 Python 标准 logging，但输出采用 JSON 格式（`python-json-logger` 或自行实现）：

```json
{"ts":"...","level":"INFO","logger":"pce.pipeline","event":"message_persisted","pair_id":"...","source":"proxy","provider":"openai","latency_ms":18}
```

关键位点必须打 event：
- `pce.addon` capture_received / capture_forwarded / persist_failed
- `pce.pipeline` normalize_start / normalize_done / reconcile_merged / persist_done
- `pce.mcp` tool_called / tool_completed

### 5.3 Schema Migration

新建 `pce_core/migrations/` 目录：

```
migrations/
├── __init__.py         # 注册表与 runner
├── 0001_baseline.py    # 把当前 schema 标记为起点
└── README.md           # 写法说明
```

每份 migration 提供 `upgrade(conn)` 与 `downgrade(conn)`。启动时 `init_db()` 读取 `CAPTURE_SCHEMA_VERSION` 并按序应用到目标版本。

当前 `CAPTURE_SCHEMA_VERSION` 的值需作为 `0001_baseline` 的终点。未来 P1 的 OpenInference 字段新增作为 `0002_*.py`。

### 5.4 Dashboard 健康页

在 `pce_core/dashboard/` 新增或扩展一个 `health.html` + 对应 JS：
- 顶部显示三条来源的卡片
- 中部显示 pipeline 错误计数
- 底部显示 storage 用量
- 每 10 秒自动刷新

不要求样式精美，要求信息完整、刷新稳定。

### 5.5 冒烟测试

新增或强化 `tests/smoke/` 目录：

- `test_proxy_smoke.py`：起 `mitmdump` 子进程 → 对准 loopback mock → 触发一条 allowlist 请求 → 验证 raw_captures / messages 各多出一行
- `test_mcp_smoke.py`：调 `pce_capture` tool → 验证落库
- `test_extension_smoke.py`：构造一次模拟的 extension POST → 验证落库 + session 归并

这些测试应能在 CI 无浏览器环境下跑通（浏览器扩展的真实 e2e 不在本轮，只验证 Ingest API 层）。

### 5.6 CI 配置

提供 `.github/workflows/smoke.yml` 或 `scripts/run_smoke.sh`，至少能在本地一键把上述冒烟全跑一遍。

## 6. 验收标准

必须同时满足：

- [ ] `GET /api/v1/health` 返回上述结构，所有字段都有真实值（非 null 占位）
- [ ] 三条入口在接收数据时都会打印结构化 JSON 日志
- [ ] `pce_core/migrations/` 目录存在，含 `0001_baseline.py` 与 README
- [ ] 启动时若 schema_version 落后，会自动应用到最新版；若领先，拒绝启动并清楚报错
- [ ] Dashboard 访问 `/health` 或 `/dashboard/health` 能看到健康页
- [ ] `tests/smoke/` 三条冒烟测试在 clean checkout + `pip install -r requirements.txt` 之后能全部通过
- [ ] CI 配置或本地脚本能一键跑通冒烟
- [ ] 既有 `tests/` 下的所有测试不被破坏

## 7. 建议交付物

- 修改：`pce_core/db.py` / `pce_core/server.py` / `pce_core/dashboard/`
- 新增：`pce_core/migrations/`（目录 + baseline + README）
- 新增：`tests/smoke/`（三份冒烟测试）
- 新增：`.github/workflows/smoke.yml` 或 `scripts/run_smoke.{sh,ps1}`
- 新增：一份 `Docs/decisions/2026-04-??-P0-completion.md` 完成回报（由 agent 写）

## 8. 停止条件

出现以下情况时不要自行扩大范围，应暂停并回报：

- 发现既有 schema 与 migration 起点不兼容，需大面积改动
- 冒烟测试要求引入重型依赖（Playwright 浏览器除外，但浏览器 e2e 不在本轮）
- 发现健康指标要求触及 reconciler 内部语义（这部分留给 P1）
- 发现某条入口（如扩展）在当前 Ingest API 上本就不工作

## 9. 回报格式

本轮结束后按如下结构回报：

### 9.1 Changed Files
- 新增 / 修改 / 删除

### 9.2 What Works
- 已跑通的健康指标
- 已跑通的冒烟路径
- migration 的触发验证方式

### 9.3 What Does Not Work Yet
- 未触及的点
- 已知限制

### 9.4 How To Run
- 如何起服务
- 如何访问健康页
- 如何跑冒烟

### 9.5 Risks / Follow-ups
- P1 应该先解哪一块
- 有没有暴露新的架构隐患
- 有没有需要人拍板的问题
