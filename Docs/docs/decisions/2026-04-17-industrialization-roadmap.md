# Industrialization Roadmap Session

- Date: 2026-04-17
- Topic: 将 PCE 三大核心能力（抓 / 存 / 渲染）升级为工业级 + 用户友好
- Status: Accepted
- Supersedes: 不覆盖 2026-04-05 Foundation Session，仅作为其后续演进

## 本次讨论要解决的问题

1. PCE 在实现了最初 PoC 之后，下一步该做什么才能"能给别人用"。
2. 抓 / 存 / 渲染三条链路，在什么地方应该"成体系吸收开源成果"，什么地方必须自研。
3. 如何评价市面上同方向的开源项目（以 Mouseww/anything-analyzer 为代表），我们该不该整体照搬。
4. 如何把后续的工程迭代方向固化成一条清晰、可执行、可交接的路径。

## 演化链

本轮讨论的演化路径可以概括为：

1. 先是盘点了 `anything-analyzer` 的形态，确认它是协议分析 / 逆向工具，不是我们的长期记录底座，目标用户与数据语义都不一样。
2. 进一步澄清它宣传的"全场景抓包"本质上是 mitmproxy 的固有能力 + 较好的 UX 包装，在抓的内核上并不领先我们。
3. 转向调研 LLM Observability 生态（Langfuse / Phoenix / OpenInference / OpenTelemetry GenAI / LiteLLM / WXT），确认这是距离 PCE 最近的成熟生态。
4. 识别出一个关键错位：这些开源项目服务"应用开发者为自己的 LLM 调用打 instrument"的场景；PCE 服务"终端用户从第三方网页 / App 反向嗅探 AI 对话"的场景。
5. 收束为一个明确的吸收策略：schema 与数据模型照抄开源标准，捕获层必须自研，渲染层可以白嫖现成 UI。
6. 将上述判断转化为四个阶段 P0-P3 的工程迭代路径。

## 本次最终结论

### 1. PCE 的三大核心能力被明确定义为"抓 / 存 / 渲染"

从这轮开始，PCE 的能力分工统一使用如下术语：

- 抓 (Capture)：从不同来源把 AI 交互数据带进系统
- 存 (Storage)：把数据以标准、可查询、可导出的形式持久化下来
- 渲染 (Render)：把数据以对用户友好的方式呈现、搜索、管理

这三条链路彼此独立演进，各自要同时达到"工业级"和"用户友好"。

### 2. "工业级"与"用户友好"有明确度量

工业级意味着同时满足：

- 关键路径有可靠性保证（失败率可度量、可降级）
- 数据 schema 对齐行业开源标准，不被自研格式锁定
- 自身管道可观测（自己也能被 trace / metric / log）
- 具备正规的 schema migration 与版本化
- 有分层测试覆盖
- 可以打成单文件分发物

用户友好意味着同时满足：

- 从安装到看到第一条捕获，非技术用户少于 10 分钟
- 证书与系统代理等底层动作可一键完成
- 主动显示健康状态、抓取数量、存储占用
- 用户能一键导出、删除、暂停记录
- 核心路径不需要阅读文档也能上手

### 3. 对 Mouseww/anything-analyzer 的最终态度

不整体借鉴，不在其基础上重建。理由：

- 它服务的是开发者的一次性协议分析任务
- 它没有归一化、没有去重、没有多源协调、没有脱敏
- 它的会话是任务级的，与 PCE 的长期累积方向不兼容
- 它宣传的"全场景抓包"在技术内核上不是新能力，而是 mitmproxy 能力 + UX 包装

可以向它学的只有"如何把 mitmproxy 做成非技术用户可以用的安装体验"，这部分我们用自己的 pce_app 去补。

### 4. 存层以 OpenInference + OpenTelemetry GenAI 为标准

- 向存层落库的结构字段对齐 OpenInference 属性命名
- 不重新发明自己的 role / content_text / content_json 等字段语义
- 可选地以 OTLP 协议对外导出，允许用户接入第三方 trace 查看器
- 本地 SQLite 保留为单一可信来源 (source of truth)，不上移到 Postgres / ClickHouse 这类服务端部署

### 5. 抓层分为四个通道，核心内核仍然是 mitmproxy

抓层统一为以下四种数据入口：

- 浏览器扩展（MV3，针对具体 AI 网站 DOM + 网络双通道）
- 本地系统代理（mitmproxy）
- MCP Server（AI Agent 主动把交互录进来）
- SDK 捕获通道（用户自己写代码调 API 时的可选入口，建议使用 LiteLLM 作为中转）

四个通道共享归一化层、reconciler 与存储层。

### 6. 渲染层继续由 PCE 自己主导，但允许嵌入现成 trace 查看器

- 默认界面仍然是 PCE 自己的 dashboard 和未来的桌面壳
- 提供可选"开发者视图"，允许以 OTLP 协议把数据喂给本地 Phoenix 实例
- 桌面壳采用 Tauri，而非 Electron
- 浏览器扩展构建链迁移到 WXT

### 7. 工程迭代路径分为四个阶段

后续开发按以下顺序推进，每一阶段都要交付一个可用的成果，不留半成品：

- P0 稳定现状：让当前链路具备可信度、可观测、可冒烟验证
- P1 存层工业化：schema 对齐开源标准、OTLP 导出、migration、导入导出
- P2 抓层工业化 + UX：浏览器扩展 WXT 化、证书向导、系统代理一键开关、SDK 通道
- P3 渲染层工业化 + UX：Tauri 桌面壳、首次引导、托盘、自动更新、诊断命令

每一阶段都落成独立任务单，见 `tasks/`。

## 当前明确不做的事

下列事项在本轮明确**不**纳入 P0-P3：

- AI 自动分析 / 摘要 / 打分
- 与 anything-analyzer 竞争协议逆向能力
- 完整 LLMOps 平台（prompt 管理、eval、playground、数据集管理）
- 云端同步 / 多设备
- 移动端原生 App（手机流量抓取仅作为 P4+ 指南）
- 企业化多用户 / 团队协作
- 把 Langfuse / Helicone 服务端部署整体搬进来
- 把浏览器扩展商店上架作为 P2 的硬依赖（可并行、不卡住）

## 待后续决策的问题

这些问题已经看见，但不阻塞当前阶段推进：

1. 导出格式是否提供 Parquet（JSONL + OTLP 已足够）
2. 桌面壳是否内置 Phoenix 二进制（还是让用户按需 `pip install`）
3. SDK 捕获通道是否需要支持 Anthropic 原生格式（LiteLLM 已经统一）
4. 未来"理解层"是否引入 sqlite-vec 作语义检索
5. 脱敏规则是否开放给用户自定义白名单

## 这份决议的用途

这份文档用于替代本轮长聊天，作为 P0-P3 所有任务的上下文锚点。

本轮决议同时产生了以下具体落盘文件：

- `docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
- `docs/engineering/adr/ADR-005-desktop-shell-tauri-over-electron.md`
- `docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md`
- `docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`
- `docs/PROJECT.md`（升级到反映工业化阶段）
- `docs/engineering/ARCHITECTURE.md`（升级到 v0.2）
- `tasks/TASK-002-P0-stabilize-current-pipeline.md`
- `tasks/TASK-003-P1-storage-standardization.md`
- `tasks/TASK-004-P2-capture-ux-upgrade.md`
- `tasks/TASK-005-P3-desktop-shell.md`
- `handoff/HANDOFF-TASK-002.md`

## 下一步

1. 本地 agent 按 `handoff/HANDOFF-TASK-002.md` 开始 P0。
2. P0 完成后再开 P1 的 handoff，不跨阶段并发。
3. 每个阶段完成后产出一份"阶段回报"文件，沉淀到 `docs/decisions/` 下面。
