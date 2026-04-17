# ARCHITECTURE

- Project: PCE
- Version: v0.3 Storage-Standardization Architecture
- Updated: 2026-04-17
- Scope: 覆盖“记录 -> 看见”阶段的完整实现，同时明确"抓 / 存 / 渲染"三大能力的工业级与用户友好目标，为“理解 -> 干预”阶段预留扩展点

## 0. 版本说明

v0.3 相对 v0.2 的主要变化（P1 阶段落地）：

- 存层 schema 升级到版本 3：`messages` / `sessions` 加上 OpenInference 兼容的
  `oi_role_raw` / `oi_input_tokens` / `oi_output_tokens` / `oi_attributes_json`
  / `oi_schema_version` 字段，采用"加新字段保留旧字段"的双写迁移（ADR-004）
- 归一化层新增 `pce_core/normalizer/openinference_mapper.py`，
  产生 message / session / pair-span 三个粒度的 OI 属性视图
- 新增可选 OTLP 导出器 `pce_core/otel_exporter.py`（默认关闭，ADR-007），
  在 `OTEL_EXPORTER_OTLP_ENDPOINT` 存在且 `opentelemetry-sdk` 可导入时启用
- 管道关键阶段（`pce.pipeline.*` / `pce.normalize` / `pce.persist.message`）
  用 OTel span 包装，SDK 未安装时为 no-op
- `GET /api/v1/export?format=otlp|json` 以 NDJSON 或 JSON 信封格式流式导出数据
- `POST /api/v1/import` 支持 NDJSON 和 JSON 信封格式的幂等导入
- 新增 `pce_core/retention.py` + `PCE_RETENTION_DAYS` / `PCE_RETENTION_MAX_ROWS`
  + `/api/v1/retention` / `/api/v1/retention/sweep` 端点
- 整体测试矩阵从 P0 的 23 条冒烟扩展到 89 条（45 P1 单元 + 44 冒烟）

v0.2 相对 v0.1 的主要变化：

- 引入“抓 / 存 / 渲染”作为一级能力划分
- 明确对齐 OpenInference / OpenTelemetry GenAI 语义（见 ADR-004）
- 桌面壳选型收口到 Tauri（见 ADR-005）
- 浏览器扩展构建链迁 WXT（见 ADR-006）
- OTLP 导出定位为可选次级通道（见 ADR-007）
- 引入工业化阶段 P0-P3 与非目标列表
- 强化 schema migration、可观测性、健康指标等"工业级"约束

## 1. 目标

v0.2 架构的目标是在不改变"记录 -> 看见"产品路径的前提下，把整条本地数据管道从"能跑"提升为"能给非技术用户装、能稳定长期运行、能和开源标准互通"。

核心能力必须同时满足：

- 抓：多来源 AI 交互数据能被完整、可观测、可健康度量地带进系统
- 存：以标准化 schema 持久化，支持导出 / 导入 / 保留策略，具备正规 migration
- 渲染：以对用户友好的方式呈现、搜索、管理，具备桌面壳与托盘体验

同时，这条管道必须能在将来平滑升级为"可注入、可评估、可更新"的更高级内核。

## 2. 设计约束

### 2.1 不改变主要使用习惯
用户不应被迫理解 proxy、endpoint 或协议细节。

### 2.2 Local-first
数据默认只存在本地，不依赖外部云端服务才能成立。

### 2.3 Fail-open
记录失败不能导致用户的 AI 使用失败。

### 2.4 Progressive enhancement
当前只做只读记录，未来再做读写注入；同一条管道必须向前兼容。

### 2.5 Security by default
必须对认证信息、Cookie、敏感头和令牌进行脱敏。

### 2.6 Industrial grade（v0.2 新增）
关键路径必须具备可观测性、健康指标、schema migration 与分层测试，能打成单文件分发物。

### 2.7 User friendly（v0.2 新增）
非技术用户从下载到看到第一条捕获应少于 10 分钟，核心动作（证书、代理、扩展、暂停、导出）必须有一键入口。

### 2.8 Open standard alignment（v0.2 新增）
归一化数据对齐 OpenInference / OpenTelemetry GenAI，不再发明私有字段语义（见 ADR-004）。

## 3. 三大核心能力

从 v0.2 开始，PCE 的能力统一以"抓 / 存 / 渲染"三轴组织，它们彼此独立演进，各自要同时达到工业级与用户友好。

### 3.1 抓 (Capture)
职责：把 AI 交互数据从多条来源带进系统，并打上可靠的来源标记。
统一入口：`pce_core` 的 Ingest API + mitmproxy addon + MCP tools。
关键要求：每条来源都能被度量成功率、失败率、延迟分布。

### 3.2 存 (Storage)
职责：将抓到的数据以标准 schema 持久化，并为查询、导出、迁移提供稳定接口。
统一底座：本地 SQLite + FTS5。
关键要求：schema 对齐 OpenInference，有正规 migration，支持导出 / 导入 / 保留策略。

### 3.3 渲染 (Render)
职责：把数据以用户友好的方式呈现、搜索、管理，并承载安装与运维入口。
统一形态：本地 dashboard + Tauri 桌面壳 + 可选 Phoenix 开发者视图。
关键要求：托盘长期常驻、健康状态显式、首次引导完整。

## 4. 双轨安装模式

抓层与渲染层共同服务两种安装模式，但三大能力的内部实现与这两种模式解耦。

### 4.1 Light Mode
- 入口：浏览器扩展 + 环境变量 + MCP Server
- 目标：零信任门槛，不要求系统级授权
- 覆盖：用户浏览器内、自己写代码调 API 的场景、AI Agent 主动录入

### 4.2 Complete Mode
- 入口：系统级 HTTPS 代理 + 本地 CA 证书
- 目标：最大覆盖率，含桌面 App / 终端脚本 / 手机 Wi-Fi 流量
- 覆盖：上述加任何走系统代理的 HTTPS AI 流量

### 4.3 模式关系
两种模式共享完全相同的抓层 Ingest API、存层 schema、渲染层界面。差别只在"怎么进来"，不在"进来之后如何处理"。见 ADR-003。

## 5. 总体结构

```text
                ┌─────────────────────────────────────────────────┐
                │                   USER AI TOOLS                 │
                └──────┬───────────┬───────────┬───────────┬──────┘
                       │           │           │           │
                 ┌─────▼───┐  ┌────▼────┐  ┌───▼────┐  ┌──▼────┐
                 │ Browser │  │ System  │  │ User   │  │ AI    │
                 │ Ext     │  │ Proxy   │  │ SDK    │  │ Agent │
                 │ (WXT)   │  │ (mitm)  │  │(LiteLLM)│  │(MCP)  │
                 └────┬────┘  └────┬────┘  └───┬────┘  └──┬────┘
                      │            │           │          │
                      └────────────┴──── Ingest API ──────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │  Normalizer │
                                  │ + Reconciler│
                                  │ + Redactor  │
                                  └──────┬──────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │ SQLite      │  ◄── source of truth
                                  │ Raw / Sess  │
                                  │ Msg / FTS5  │
                                  └──────┬──────┘
                                         │
                            ┌────────────┼──────────────┐
                            ▼            ▼              ▼
                     ┌────────────┐ ┌─────────┐  ┌────────────┐
                     │ Query API  │ │ OTLP    │  │ Export     │
                     │ /health    │ │ (opt-in)│  │ OTLP/JSONL │
                     └──────┬─────┘ └────┬────┘  └────────────┘
                            │            │
                            ▼            ▼
                     ┌────────────┐  ┌───────────┐
                     │ Dashboard  │  │ Phoenix   │
                     │ + Tauri UI │  │ (optional)│
                     └────────────┘  └───────────┘

Future: Annotation / Continuity / Injection modules mount above SQLite.
```

## 6. 抓层详解

### 6.1 浏览器扩展通道
- 框架：迁移到 WXT（见 ADR-006），保持现有 13+ 站点提取器与通用拦截器
- 子通道：DOM 提取、`fetch` / `XMLHttpRequest` / `WebSocket` / `EventSource` monkey-patch、存储快照
- 权限双模式：sideload `<all_urls>` / webstore 白名单 + `activeTab`，通过 WXT 构建变量切换
- 输出：POST 到本地 Ingest API

### 6.2 系统代理通道 (mitmproxy)
- 已有：`pce_proxy/addon.py`
- 三种捕获模式：allowlist / smart（allowlist + 启发式 AI 检测）/ all
- 子通道：Local Capture / WireGuard 手机模式 / 传统 HTTP(S) 代理
- 输出：直接调用 `pce_core.db` 与 normalizer

### 6.3 MCP 通道
- 已有：`pce_mcp/server.py`
- 工具：`pce_capture` / `pce_query_*`
- 场景：Claude Desktop / Cursor / Windsurf 等 Agent 主动把会话录入 PCE

### 6.4 SDK 捕获通道（P2 新增）
- 方式：用户在自己代码中把 `api_base` 指向本地 LiteLLM Proxy，LiteLLM 再把流量落到 PCE
- PCE 侧：读 LiteLLM log 或启用 LiteLLM 的 HTTP 回调，写入 `source = sdk`
- 优势：零维护单个 provider 的格式适配

### 6.5 归一化与多源协调
- `pce_core/normalizer/` 负责 provider 识别、消息抽取、SSE 重组
- `reconciler.py` 负责 DOM 路与网络路的消息级合并与去重（PCE 独有能力，不对外依赖）
- 脱敏在归一化之前完成（见 7.5）

### 6.6 抓层健康指标
每条来源必须上报以下维度（P0 任务）：
- 最近一次成功捕获时间
- 最近一段时间的成功数 / 失败数 / 丢弃数
- p50 / p95 处理延迟
- 活跃 session 数

## 7. 存层详解

### 7.1 三层存储

沿用 v0.1 的三层模型，字段命名升级到 OpenInference 兼容（见 7.2）。

**Tier 0: Raw Capture Layer**
表：`raw_captures`
作用：事实层，不可变，保持提供方原貌。字段不受 OpenInference 约束。
必包字段：`id` / `created_at` / `source_id` / `direction` / `pair_id` / `host` / `path` / `method` / `provider` / `model_name` / `status_code` / `latency_ms` / `headers_redacted_json` / `body_text_or_json` / `body_format` / `error` / `session_hint`。

**Tier 1: Normalized Session Layer**
表：`sources` / `sessions` / `messages`。
这一层对齐 OpenInference，具体字段映射见 7.2。

**Tier 2: Derived Meaning Layer（预留）**
未来：`annotations` / `continuity_models` / `inference_runs` / `intervention_logs`。

### 7.2 OpenInference / OTel GenAI 字段对齐

遵循 ADR-004，所有归一化产物必须能以 OpenInference 属性视图对外呈现。建议映射（P1 落实）：

| 内部字段 | OpenInference 属性 |
|---|---|
| `messages.role` | `llm.input_messages[i].message.role` |
| `messages.content_text` | `llm.input_messages[i].message.content` / `llm.output_messages[i].message.content` |
| `messages.model_name` | `llm.model_name` |
| `messages.token_estimate` | `llm.token_count.prompt` / `llm.token_count.completion` |
| `sessions.provider` | `llm.provider` |
| `sessions.session_key` | `session.id` |
| `raw_captures.latency_ms` | 作为 span duration |

迁移策略：加新字段、保留旧字段、先双写，三个 release 后删旧字段。

### 7.3 OTLP 可选次级通道

遵循 ADR-007：
- 默认关闭
- `OTEL_EXPORTER_OTLP_ENDPOINT` 驱动
- 失败不阻塞主路径
- SQLite 仍为唯一可信来源

### 7.4 Schema Migration 机制

P0 必须落地：
- `pce_core/migrations/` 目录，按 `NNNN_description.py` 命名
- 启动时检测 `CAPTURE_SCHEMA_VERSION` 并依次应用
- 每条 migration 必须可回退
- 每个 release 附带一份 migration 说明

### 7.5 脱敏（Redactor）

以下信息默认不得以明文落库：
- `Authorization` / `Cookie` / `Set-Cookie`
- 任何 API Key 或 bearer token

处理方式：
- 头字段整体删除或将值替换为 `REDACTED`
- 脱敏逻辑位于归一化**之前**，raw_captures 也必须脱敏
- 未来支持用户自定义白名单

### 7.6 数据生命周期

必须支持：
- 暂停记录（全局或按 source）
- 按时间段删除
- 按 session 删除
- 数据保留策略（只留最近 N 天 / N 条）
- 导出（OTLP JSONL、标准 JSON）与导入

## 8. 渲染层详解

### 8.1 本地 Dashboard
- 基于 `pce_core/dashboard/`
- 必备：时间线 / 搜索（FTS5）/ 详情页 / 健康指标页 / 设置页
- 可演进为 React/Svelte 组件化，但非 P3 必须

### 8.2 桌面壳（Tauri）
遵循 ADR-005：
- Rust 主进程 + Web 前端
- Python 后端以 sidecar 方式启动
- 官方 tray / updater / notification
- 所有数据访问必须走 Python 后端 HTTP API，不直连 DB

### 8.3 可选 Phoenix 视图
- 作为"开发者面板"嵌入 `pce_app`
- 用户按需 `pip install arize-phoenix`
- 通过 OTLP endpoint 读 PCE 数据
- 默认关闭

### 8.4 诊断命令
- `pce diagnose` 一键收集日志 / 配置 / 版本 / schema_version，打包成可提交的 zip
- 协助未来用户报 bug / 社区支持

## 9. 数据流

### 9.1 主流程

```text
capture arrives at Ingest API (or addon / MCP tool)
    -> source resolved + redaction applied
    -> raw snapshot persisted to raw_captures
    -> normalizer picks provider + extracts messages
    -> reconciler merges with existing DOM/network siblings
    -> session_manager resolves/creates session
    -> messages / sessions upserted
    -> optional OTLP span emitted (opt-in)
    -> FTS5 index updated
    -> Dashboard / Tauri UI reads via Query API
```

### 9.2 关键要求
- 存储异步，不阻塞业务调用
- raw 与 normalized 必须独立落库
- 任何一步失败都不能让上游请求失败
- 捕获动作自身也要打内部 trace（P1 任务）

## 10. 域名与过滤策略

- allowlist 优先，默认只拦截明确 AI 域名
- SMART 模式允许启发式扩展（基于请求体字段、响应签名）
- ALL 模式只对高级用户开放，桌面壳必须显式告知风险
- 非目标域名原样放行，不落库

## 11. 性能与稳定性预算

- 代理新增延迟：尽量不可感知
- 业务调用优先级始终高于记录优先级
- 任何内部异常不得导致大面积请求失败
- 归一化 / reconciler 长期堆积必须可排障（P1 的管道 trace 支撑此项）

## 12. 开源生态对齐策略

### 12.1 成体系吸收
- **OpenInference / OTel GenAI**：作为存层 schema 标准（ADR-004）
- **WXT**：作为浏览器扩展构建链（ADR-006）
- **Tauri**：作为桌面壳（ADR-005）
- **mitmproxy**：作为系统代理内核（ADR-002，延续 v0.1）

### 12.2 部分借鉴
- **LiteLLM**：作为 SDK 场景的中间层网关（不作为核心存储）
- **Phoenix**：作为可选开发者 UI（不替代我们自己的 dashboard）

### 12.3 不采纳
- **Langfuse 服务端部署**：Postgres + ClickHouse + Redis 太重，违反 local-first
- **Helicone 服务端部署**：同上
- **anything-analyzer 整体架构**：任务级协议分析工具，语义不兼容
- **OpenLLMetry 作为主归一器**：只覆盖 SDK 场景，不能处理网页捕获

### 12.4 自研保留项
- 浏览器扩展的 DOM 提取逻辑
- 多源 reconciler
- 跨工具 session 自动归并
- 脱敏策略
- Local-first 的桌面分发

## 13. 技术栈

### 后端核心
- Python 3.10+
- mitmproxy
- FastAPI
- SQLite + FTS5

### 存层标准
- OpenInference 属性命名
- OTLP（opentelemetry-sdk）作为可选导出

### 抓层
- 浏览器扩展：WXT + TypeScript
- SDK 通道：LiteLLM Proxy

### 渲染层
- Dashboard：原生 HTML/JS（短期）→ 可选演进为 React
- 桌面壳：Tauri + Rust
- 开发者视图：Arize Phoenix（可选）

### 工程基础设施
- 测试：pytest + Playwright（扩展 e2e）
- 打包：Tauri bundler（Win/macOS/Linux）
- 自动更新：Tauri updater

## 14. 分阶段实现路径

### P0 稳定现有链路
- 结构化日志 + 健康指标 API
- Dashboard 健康页
- 正规 schema migration
- 冒烟测试覆盖 proxy / MCP / 扩展三条入口
- 任务单：`TASK-002-P0-stabilize-current-pipeline.md`

### P1 存层工业化
- OpenInference 映射层
- OTLP 可选导出
- 导出 / 导入 / 保留策略
- 管道自身 trace
- 任务单：`TASK-003-P1-storage-standardization.md`

### P2 抓层工业化 + UX
- 浏览器扩展迁 WXT
- 跨平台 CA 证书向导
- 系统代理一键开关
- LiteLLM SDK 通道
- 代理健康守护
- 任务单：`TASK-004-P2-capture-ux-upgrade.md`

### P3 渲染层工业化 + UX
- Tauri 桌面壳
- 首次运行引导
- 托盘 / 菜单栏图标
- 自动更新
- `pce diagnose` 命令
- 可选 Phoenix 嵌入
- 任务单：`TASK-005-P3-desktop-shell.md`

## 15. 暂不纳入本架构实现范围的模块

这些模块在架构上预留位置，但不应阻塞 P0-P3：

- 连续性模型
- 自动 infer 引擎
- 自动注入策略
- 回答质量评估器
- 企业化多用户能力
- 云端同步
- 移动端原生 App
- CDP 内嵌浏览器（作为 P4 候选）
