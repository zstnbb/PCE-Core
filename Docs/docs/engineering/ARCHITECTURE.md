# ARCHITECTURE

- Project: PCE
- Version: v0.1 Foundation Architecture
- Updated: 2026-04-05
- Scope: 只覆盖“记录 -> 看见”阶段，不覆盖“理解 -> 干预”的实现细节

## 1. 目标

第一版架构的目标不是让 PCE 立刻具备高级推理能力，而是建立一条稳定的本地数据管道，使用户在多个 AI 工具中的交互可以被：

- 捕获
- 归一化
- 存储
- 查询
- 回看

同时，这条管道必须能在将来平滑升级为“可注入、可评估、可更新”的更高级内核。

## 2. 设计约束

### 2.1 不改变主要使用习惯
用户不应被迫理解 proxy、endpoint 或协议细节。

### 2.2 Local-first
数据默认只存在本地，不依赖外部云端服务才能成立。

### 2.3 Fail-open
记录失败不能导致用户的 AI 使用失败。

### 2.4 Progressive enhancement
第一版先做只读记录，未来再做读写注入。

### 2.5 Security by default
必须对认证信息、Cookie、敏感头和令牌进行脱敏，避免把密钥当作可见业务数据落库。

## 3. 总体结构

```text
[User AI Tools]
    |
    |-- Complete Mode: system-level HTTPS proxy
    |-- Light Mode: env-based routing / browser extension / local adapters
    v
[Capture Layer]
    v
[Proxy Engine]
    v
[Normalizer]
    v
[Storage Engine]
    v
[Query API]
    v
[Dashboard / Desktop Shell]

Future:
[Annotation / Continuity / Injection Modules]
```

## 4. 模式设计

### 4.1 Complete Mode

目标：一次安装后，尽量用系统级方式统一覆盖 AI 域名流量。

组成：
- 系统级 HTTPS 代理
- 本地 CA 证书
- AI 域名 allowlist
- 桌面壳负责安装、启停、状态展示

优点：
- 覆盖最完整
- 用户心智最统一
- 为未来注入预留最自然的路径

代价：
- 需要用户授权安装本地证书
- 需要系统层配置与管理员权限
- 少数应用可能受证书锁定影响

### 4.2 Light Mode

目标：降低信任门槛与安装复杂度。

组成：
- 环境变量或 API 基址覆盖
- 浏览器插件或网页端补充采集
- 针对特定工具的本地适配器

优点：
- 用户更容易接受
- 不一定需要系统级证书
- 便于早期验证与分步接入

代价：
- 覆盖不完整
- 维护适配器和插件更碎片化

### 4.3 模式关系

两种模式共享同一后端：

- 相同的存储层
- 相同的查询层
- 相同的时间线与搜索视图
- 相同的后续分析模块入口

差别只在“怎么进来”，不在“进来之后怎么处理”。

## 5. 组件分解

### 5.1 Capture Layer

职责：把不同来源的流量、安全地送入 PCE 内核。

主要输入：
- 系统代理流量
- 通过环境变量路由过来的 API 请求
- 浏览器插件发送的网页对话数据
- 未来可能出现的工具专用 adapter

核心要求：
- 明确区分 source
- 只允许名单内 AI 域名或可信本地适配入口进入
- 所有非目标流量一律放行或忽略

### 5.2 Proxy Engine

职责：拦截、转发、记录请求与响应。

当前阶段：
- 只读请求
- 只读响应
- 原样转发上游
- 异步送入存储队列

未来阶段：
- 在转发前进行上下文注入
- 在响应后做评估或打分

核心要求：
- 极低额外延迟
- 如果内部存储或解析出错，依然尽量不影响上游响应
- Header 脱敏要在持久化前完成

### 5.3 Normalizer

职责：把不同上游协议转成 PCE 自己的统一结构。

为什么必须独立成层：
- OpenAI、Anthropic、Google 请求格式不同
- 网页插件抓取到的对话格式与 API 请求又不同
- 如果没有统一结构，后面的搜索、时间线和分析都会被来源格式绑死

输出目标：
- 原始快照仍然保留
- 同时尽力产生统一的 session / message 视图

### 5.4 Storage Engine

职责：持久化原始快照和归一化结果，并提供稳定索引。

当前阶段建议使用：
- SQLite

原因：
- 零配置
- 本地单文件
- 对第一版足够
- 易于调试与导出

### 5.5 Query API

职责：给本地 dashboard、未来分析模块和导出工具提供统一查询入口。

第一版最小能力：
- 按时间列出记录
- 按 source 列出记录
- 按关键词搜索消息内容
- 查看单条 capture 详情

### 5.6 Dashboard

职责：把“看见”变成可用体验。

第一版最小界面：
- 时间线或列表视图
- 过滤器（来源 / 时间 / Provider）
- 搜索框
- 单条详情页
- 状态指示（当前模式、数据目录、是否暂停记录）

### 5.7 Desktop Shell

这不是第一开发动作，但它是最终产品形态的重要容器。

职责包括：
- 安装向导
- 系统托盘 / 菜单栏图标
- 状态查看
- 模式切换
- 代理启停
- 本地证书安装与卸载引导

## 6. 数据流

### 6.1 当前阶段数据流

```text
request enters capture layer
    -> target host checked against allowlist
    -> request forwarded upstream
    -> request snapshot queued
    -> response received
    -> response snapshot queued
    -> normalizer extracts structured fields
    -> storage writes raw + normalized data asynchronously
    -> query API exposes records
    -> dashboard renders timeline/search
```

### 6.2 关键要求

- 存储必须异步，不要阻塞主请求路径
- 请求和响应都要记录
- 失败时尽量只丢记录，不丢业务调用
- 非目标域名不处理

## 7. 存储分层

为了兼顾第一版的忠实记录和后续的可分析性，存储建议分为三层。

### Tier 0: Raw Capture Layer

这是第一版最重要的事实层，尽量不可变。

建议表：`raw_captures`

建议字段：
- `id`
- `created_at`
- `source_id`
- `direction` (`request` / `response`)
- `pair_id`（用于关联同一来回）
- `host`
- `path`
- `method`
- `provider`
- `model_name`
- `status_code`
- `latency_ms`
- `headers_redacted_json`
- `body_text_or_json`
- `body_format`
- `error`
- `session_hint`

说明：
- 这里存的是低层事实
- 必须脱敏后再落库
- 这是将来调试、回放和重新归一化的依据

### Tier 1: Normalized Session Layer

用于“看见”阶段的搜索、时间线和跨来源统一视图。

建议表：
- `sources`
- `sessions`
- `messages`

`sources` 建议字段：
- `id`
- `source_type`（proxy / browser_extension / adapter）
- `tool_name`
- `install_mode`（light / complete）
- `active`
- `notes`

`sessions` 建议字段：
- `id`
- `source_id`
- `started_at`
- `ended_at`
- `provider`
- `tool_family`
- `session_key`
- `message_count`
- `title_hint`
- `created_via`

`messages` 建议字段：
- `id`
- `session_id`
- `capture_pair_id`
- `ts`
- `role`
- `content_text`
- `content_json`
- `model_name`
- `token_estimate`

### Tier 2: Derived Meaning Layer

这不是第一版必须实现，但要预留位置。

未来可能表：
- `annotations`
- `continuity_models`
- `inference_runs`
- `intervention_logs`

## 8. 域名与过滤策略

第一版建议只针对明确的 AI 相关域名做拦截，例如：

- `api.openai.com`
- `api.anthropic.com`
- `generativelanguage.googleapis.com`

原则：
- allowlist 优先
- 不做全流量无差别存档
- 未来通过配置文件扩展域名范围

## 9. 脱敏与隐私策略

以下信息默认不应以明文落库：

- `Authorization`
- `Cookie`
- `Set-Cookie`
- 任何 API Key
- 任何可直接复用的 bearer token

推荐处理方式：
- 头字段整体删除
- 或保留字段名但将值替换为 `REDACTED`

同时需要支持：
- 暂停记录
- 删除指定时间段数据
- 导出指定范围数据

## 10. 性能与稳定性预算

第一版记录模式建议目标：

- 代理新增延迟：尽量控制在用户几乎不可感知范围
- 业务调用优先级高于记录优先级
- 任何内部异常不得导致大面积请求失败

## 11. 技术栈建议

### 当前核心
- Python
- mitmproxy
- SQLite

### 查询与本地接口
- FastAPI（可选，推荐）

### 桌面壳（后续）
- Tauri 优先
- Electron 作为备选

### 本地展示（后续）
- React 或轻量 Web UI

## 12. 分阶段实现建议

### Stage 1A - Proxy PoC
- 跑通 mitmproxy
- allowlist 指向少量 AI 域名
- 把请求与响应脱敏后写入 SQLite
- 提供最小命令行查看脚本

### Stage 1B - Normalized View
- 从 raw capture 生成 session / message 视图
- 解决最小会话归并问题
- 提供基础搜索能力

### Stage 1C - Local Dashboard
- 时间线
- 搜索
- 详情页
- 状态页

### Stage 1D - Desktop Packaging
- 菜单栏 / 托盘
- 模式切换
- 启停管理
- 安装引导

## 13. 暂不纳入本架构实现范围的模块

这些模块在架构上预留位置，但不应阻塞当前开发：

- 连续性模型
- 自动 infer 引擎
- 自动注入策略
- 回答质量评估器
- 企业化多用户能力
