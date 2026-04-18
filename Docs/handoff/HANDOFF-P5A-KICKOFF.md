# HANDOFF: P5.A Subscription Capture(UCS 首次切片 + v1.0 对外发布)

你接到的是 PCE 从 P4 工业化基础迈向 **UCS(Universal Capture Stack)** 北极星架构的第一张执行任务单。

完成本轮后,PCE 将发布第一个**对外公开**的 stable 版本 `v1.0.0`,并且 GitHub 仓库会从私有转为 Apache-2.0 公开。

## 你先读这些文件

按顺序读完,**不要跳**。前 5 项是架构背景,后 3 项是本轮具体任务:

### 架构与决议(读一次建立整体认识)

1. `Docs/docs/decisions/2026-04-05-foundation-session.md`
   - PCE 项目起点
2. `Docs/docs/decisions/2026-04-17-industrialization-roadmap.md`
   - 工业化 P0–P3 收敛
3. `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md` ⭐
   - **本轮任务的直接上游**:UCS 采纳 + Open Core + A+A 发布路径
4. `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` ⭐
   - **1340 行工业级设计文档**。13 章 + 3 附录。是 P5/P6/P7/P8 所有阶段的蓝本
5. `Docs/docs/PROJECT.md`(v2026-04-18 更新版)
   - 当前阶段定义
6. `Docs/docs/engineering/ARCHITECTURE.md`
   - 当前 v0.2 架构(UCS 将在 P5–P8 推进到 v0.3)

### 关键 ADR(每份都是一页纸)

7. `Docs/docs/engineering/adr/ADR-001-record-not-intervention.md`
8. `Docs/docs/engineering/adr/ADR-002-local-proxy-as-core.md`
9. `Docs/docs/engineering/adr/ADR-003-dual-install-modes.md`
10. `Docs/docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
11. `Docs/docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`
12. `Docs/docs/engineering/adr/ADR-009-universal-capture-stack.md` ⭐ **UCS 北极星决策**
13. `Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md` ⭐ **决定你写的代码进哪个仓库**

### 本轮任务

14. `Docs/tasks/TASK-006-P5A-subscription-capture.md` ⭐
    - 本轮执行入口,11 项原子任务

## 你的目标

4 周内交付 **PCE v1.0.0 Subscription Capture**,让开发者用户能够:

1. 装完 5 分钟内拿到第一条捕获(ChatGPT Web / Claude Web / Copilot VS Code / ChatGPT Desktop 无 pinning / Codex CLI)
2. 浏览器扩展覆盖 4 个新 F2 站点(M365 Copilot / Notion AI / Figma AI / Gmail)
3. Pinning 失败时有明确诊断 + 3 条建议
4. 建立 **CaptureEvent v2** 统一数据契约(UCS 后续所有层的公共 API)

具体 11 项原子任务见 `TASK-006` §5。

## 前置条件:仓库拆分必须先完成(R1–R7)

**本任务不能在 R1–R7 完成之前开工**。原因:

- R1–R7 建立 Open Core 边界(OSS `pce` vs Pro `pce-pro`)
- R4 的 CI import-direction 检查是你写代码的"护栏"
- R6 的 `README.md` 是 v1.0 对外发布的门面

R1–R7 清单:

| # | 动作 | 工时 |
|---|---|---|
| R1 | 加 `LICENSE`(Apache-2.0)+ `NOTICE` + `CONTRIBUTING.md` + `SECURITY.md` + `CODE_OF_CONDUCT.md` | 0.5h |
| R2 | 所有 `.py` / `.ts` / `.js` 源文件加 SPDX header | 1h(脚本) |
| R3 | 创建 `github.com/zstnbb/pce-pro` 私有仓库 + 专有 LICENSE | 0.5h |
| R4 | `pce` CI 加 import-direction 检查 | 1h |
| R5 | `pce-pro` CI 设置 secrets 存放占位 | 1h |
| R6 | 更新 `README.md` 说明 OSS vs Pro 功能矩阵 + 安装指引 | 1h |
| R7 | `PCE-Core` → `pce` 仓库改名 | 0.1h |

**R3、R5、R7 需要人在 GitHub 后台操作**;其余可由 agent 执行。

## 你的工作方式

**先规划再动代码**,按以下顺序:

1. **读完全部 14 项输入文档**(预计 2–3 小时一次性投入)
2. **输出实施计划**:11 项子任务每项的文件落点 + 预期风险 + 验证方式
3. **首先实施 P5.A-3 + P5.A-4**(CaptureEvent v2 + migration 0006),这是所有后续工作的基础
4. **然后实施 P5.A-1 + P5.A-2**(Allowlist / KNOWN_APPS 补全,快速建立信心)
5. **然后并行推进 P5.A-5 + P5.A-6 + P5.A-7**(首跑向导 / Pinning 诊断 / Bypass UI)
6. **然后实施 P5.A-8 + P5.A-9**(浏览器扩展 F2 站点)
7. **最后 P5.A-10 + P5.A-11**(隐私中间件 + 发布打包)
8. **贯穿全程**:测试新增 ≥ 30 个单元测试

## 强约束(UCS 纪律)

### 范围硬约束

- **不做** L3b Electron preload 注入(P5.B)
- **不做** L3c VS Code 扩展原生 API hook(P5.B)
- **不做** Cursor gRPC-web 特殊处理(P5.B)
- **不做** L2 Frida(P6)
- **不做** L0 Kernel(P7)
- **不做** Capture Supervisor 自动调度(P8)
- **不做** 任何移动端原生(F7 是 Tier 3,不投资)
- **不做** AI 自动分析 / 总结(属于"理解"层,不是"抓"层)

### Open Core 依赖约束(ADR-010)

- 本轮**所有代码**都在 `pce` 仓库(OSS)
- **禁止** `import pce_agent_*` 和 `from pce_core.capture_supervisor import ...`
- CI 会自动抓违规,不允许绕过
- 如果某个功能你觉得"应该是 Pro",记录在 Follow-ups,留给 P5.B 转移

### CaptureEvent v2 公共 API 约束

- 一旦 schema merge 到 main,**只允许加字段,不许改 / 不许删**
- 字段语义变更必须走 v3 路径
- 设计时预留 `layer_meta` 逃生舱字段,让 Pro 层未来能干净填充

### 发布纪律

- Chrome Web Store 审核倒排时间:**P5.A-11 完成前 2 周必须先提交扩展**
- 验收标准的每一条都必须实测(TASK-006 §7)
- **不允许"差不多就发"**

## 本轮最重要的交付

1. **`pce_core/capture_event.py`** — CaptureEvent v2 pydantic schema(UCS §5 完整实现)
2. **`/api/v1/captures/v2`** — ingest gateway 新端点 + v1 兼容中间件
3. **`pce_core/migrations/0006_capture_event_v2.py`**
4. **首跑向导 `/onboarding`** — cert + proxy + Electron 重启 + 验证环节
5. **Pinning 诊断 + dashboard 红点 + 3 条建议**
6. **Per-app bypass UI**
7. **浏览器扩展 4 个新 F2 站点**(M365 / Notion / Figma / Gmail)
8. **隐私中间件统一化**
9. **三平台 release CI + Chrome Web Store / VS Code Marketplace 提交**
10. **P5.A 完成回报** `docs/decisions/2026-05-??-P5.A-completion.md`

## 你完成后要这样回报

参照 HANDOFF-TASK-002 的回报结构,但本轮额外要求:

### A. Plan
- 你对 P5.A 边界的理解
- 11 项任务每项的文件落点
- 你做的风险识别(比如某个应用实际有 pinning,需要提前 flag)

### B. Changed Files
- 新增 / 修改 / 删除

### C. What Works
- 每项验收标准的实测数据(TASK-006 §7 每一条)
- 新用户 5 分钟首捕获的实测视频 / 日志

### D. What Does Not Work Yet
- 未达标项及原因
- 已知限制

### E. How To Run
- 干净环境从零装到第一条捕获的完整流程
- 如何跑全量回归(724 pytest + 新增 ≥ 30 tests + vitest)
- 如何本地复现 Pinning 诊断场景
- 如何打三平台 onefile 包

### F. Release Readiness
- Chrome Web Store 审核状态(状态码 + 审核员反馈)
- VS Code Marketplace 提交状态(若本轮做)
- GitHub Release 准备度
- 是否已准备好发布公告草稿(Show HN / Reddit / Twitter)

### G. Risks / Follow-ups
- P5.B 应优先解哪一块(基于 P5.A 实测反馈)
- Pinning 诊断收集到哪些应用最需要 L2 Frida(P6 优先级排序)
- 是否有 D-4/D-5/D-6 的新证据可供用户决策
- 任何架构隐患

### H. Completion Note
- 同时产出 `Docs/docs/decisions/2026-05-??-P5.A-completion.md`
- 作为 P5.B 开工的上下文锚点

## 特别提醒

### 关于 Open Core 边界

ADR-010 是**不可违反的工程约束**,不是指导建议:

- 你看到"这个功能感觉应该放 Supervisor 自动化"— 立刻停,记到 Follow-ups,留 P8
- 你看到"这个功能应该走 L2 Frida"— 立刻停,记到 Follow-ups,留 P6
- 你看到"这个功能应该 Electron preload 注入"— 立刻停,记到 Follow-ups,留 P5.B

### 关于发布的真实性

"小步快发"不等于"草率发布"。v1.0 是 PCE 第一次面对公众:
- 安装流程要像"装 VS Code 扩展"那样流畅
- README 的"5 分钟 quickstart"必须真的 5 分钟跑通
- 捕获失败要有清晰提示,不能让用户一头雾水

用户明确表达:**10 个 stars 就很开心**。这不是降低标准,是保护心态。不要为了追流量硬加 features 偏离 P5.A 范围。

### 关于"后来的 agent"

本 handoff 和 TASK-006 + 决议记录 + UCS 设计文档 是未来所有 agent 的**入口文档**。

如果你在本轮发现新的重要上下文(某个应用的特殊形态 / 某个技术路径的死结 / 某个新的 D-point),**回写到 UCS 设计文档的相应章节**,不要只写在回报里。

让文档成为长期可复用的知识库,而不是只对本轮有效的便签。
