# TASK-001: Proxy PoC

- Status: Ready
- Priority: P0
- Owner: Local agent
- Date: 2026-04-05

## 1. 背景

PCE 当前阶段的目标是建立“记录 -> 看见”的基础设施，而不是先做 infer 或注入。

因此第一个执行任务必须是一个最小可运行的本地代理原型，证明下面这条链路可行：

`请求进入 -> 转发上游 -> 请求/响应脱敏 -> 写入 SQLite -> 可被本地查看`

## 2. 目标

实现一个最小的本地 Proxy PoC，至少满足：

1. 能拦截指定 AI 域名的 HTTPS 请求
2. 能把请求与响应脱敏后写入本地 SQLite
3. 能通过最小脚本或命令查看最近的记录
4. 如果存储失败，尽量不影响原请求继续完成

## 3. 输入文档

开工前必须阅读：

- `docs/decisions/2026-04-05-foundation-session.md`
- `docs/PROJECT.md`
- `docs/engineering/ARCHITECTURE.md`
- `docs/engineering/adr/ADR-001-first-phase-is-record-not-intervention.md`
- `docs/engineering/adr/ADR-002-local-proxy-as-core.md`
- `docs/engineering/adr/ADR-003-dual-install-modes.md`

## 4. 范围

### In Scope
- 一个可运行的 mitmproxy PoC
- 少量 AI 域名 allowlist
- SQLite schema 与落库逻辑
- 请求与响应的脱敏处理
- 最小查看脚本或 CLI
- 运行说明文档

### Out of Scope
- 注入
- infer
- continuity model
- 浏览器插件
- 桌面壳
- Dashboard UI
- 企业化能力

## 5. 建议实现边界

### 5.1 目标域名
第一版 PoC 建议先只支持：

- `api.openai.com`
- `api.anthropic.com`

其他域名可留配置口，但不要求本轮全部实现。

### 5.2 推荐技术
- Python
- mitmproxy
- sqlite3

可选：
- FastAPI 仅在你认为对本地查看脚本确有帮助时再引入

### 5.3 最小数据层

本轮最低要求必须实现：

- `sources`
- `raw_captures`

如果成本可控，可额外实现最小 `sessions` / `messages` 归一化结构；
但注意：不要因为这部分拖慢 PoC 目标。

### 5.4 脱敏要求
以下字段或内容不得明文落库：

- Authorization
- Cookie
- Set-Cookie
- API key
- bearer token

允许的策略：
- 删除字段
- 用 `REDACTED` 替代值

## 6. 验收标准

必须同时满足：

- [ ] Proxy 可以启动
- [ ] 对 allowlist 域名的请求可以被拦截并继续成功转发
- [ ] 至少一组请求/响应会写入 SQLite
- [ ] 数据库中看不到敏感头明文
- [ ] 提供一个脚本或命令，能查看最近 N 条 capture
- [ ] 提供清晰的本地运行说明
- [ ] 当写库失败时，请求仍尽量放行，不直接导致整体调用失败

## 7. 建议交付物

期望本轮至少交付：

1. 代理脚本
2. SQLite schema 或初始化脚本
3. 脱敏函数
4. 最小查看脚本
5. 启动说明文档
6. 实现报告

## 8. 停止条件

出现以下情况时不要自行扩大范围，应暂停并回报：

- 需要把本任务扩展成桌面应用
- 需要引入明显超出 PoC 的大型依赖
- 需要在本轮同时做注入
- 需要覆盖大量额外平台或大量额外域名
- 发现当前仓库结构与该任务完全不兼容，需要先重构整体骨架

## 9. 回报格式

本轮结束后必须回报：

### 9.1 Changed Files
- 新增文件
- 修改文件
- 删除文件

### 9.2 What Works
- 已跑通的链路
- 支持的 provider / host

### 9.3 What Does Not Work Yet
- 未实现部分
- 已知限制

### 9.4 How To Run
- 环境准备
- 启动命令
- 验证方法

### 9.5 Risks / Follow-ups
- 下一步最应该做什么
- 需要人拍板的问题
