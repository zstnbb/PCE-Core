# HANDOFF: TASK-001 Proxy PoC

你现在接到的是 PCE 的第一张执行任务单。

## 你先读这些文件

- `docs/decisions/2026-04-05-foundation-session.md`
- `docs/PROJECT.md`
- `docs/engineering/ARCHITECTURE.md`
- `docs/engineering/adr/ADR-001-first-phase-is-record-not-intervention.md`
- `docs/engineering/adr/ADR-002-local-proxy-as-core.md`
- `docs/engineering/adr/ADR-003-dual-install-modes.md`
- `tasks/TASK-001-proxy-poc.md`

## 你的目标

实现一个最小可运行的本地代理原型，证明下面这条链路已经成立：

`allowlist AI request -> proxy intercept -> upstream forward -> redacted request/response persisted to SQLite -> local inspect command`

## 你的工作方式

先做两步，然后再动代码：

1. 给出当前仓库的最小实施计划
2. 说明你准备把代码放到哪里，以及为什么这样放

然后再开始实现。

## 强约束

- 不做注入
- 不做 infer
- 不做 dashboard
- 不做桌面壳
- 不把任务扩大成完整产品
- 不能把敏感头或 token 明文落库
- 不能因为写库失败而直接让请求整体失败

## 本轮最重要的交付

1. proxy 脚本
2. sqlite schema
3. redaction 逻辑
4. inspect 脚本
5. run 文档

## 你完成后要这样回报

### A. Plan
- repo 结构判断
- 放置路径
- 实现步骤

### B. Changed Files
- 新增
- 修改
- 删除

### C. Run / Verify
- 如何启动
- 如何触发一条测试请求
- 如何看到 capture 已经落库

### D. Current Limits
- 目前只支持哪些 host
- 哪些地方还是 PoC

### E. Next Best Step
- 下一步最值得做什么
- 哪些点需要人拍板
