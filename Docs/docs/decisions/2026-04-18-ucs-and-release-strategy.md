# UCS 采纳与发布策略 Session

- Date: 2026-04-18
- Topic: 采纳 Universal Capture Stack (UCS) 作为捕获层北极星架构 + Open Core 边界 + 首版发布路径
- Status: Accepted
- Supersedes: 不覆盖 2026-04-17 Industrialization Roadmap,仅作为 P4 完成后的延续与收口

## 本次讨论要解决的问题

1. P4 完成后,PCE 下一阶段的架构目标是什么?捕获层是否还有独家价值?
2. 在 AI 产品大爆炸下,PCE 如何有纪律地扩展捕获能力,而不陷入"每出一款新产品修一次"的被动局面?
3. PCE 的代码要不要开源?如果开源,边界在哪里?商业化怎么做?
4. 第一个对外发布的版本应该是什么形态?瞄准哪一批种子用户?发布节奏应该多快?

## 演化链

1. 先从"PCE 捕获层还有没有独家价值"入手,盘点 10+ 订阅类 AI 产品(Copilot / Cursor / Windsurf / Cline / ChatGPT Desktop / Claude Desktop / Codex CLI 等)的鉴权与传输形态,确认它们都锁死在厂商 OAuth,BYOK 路径走不通,浏览器扩展也够不到 — 只有本地网络代理能覆盖。
2. 由此引出一个更大的问题:全品类 AI 产品该怎么收敛?按"用户坐在哪 × LLM 在哪算"两个坐标,自然收敛为 **10 种规范形态**(F1 Web Chat / F2 Web-Embedded / F3 Browser-Ext / F4 Desktop Chat / F5 IDE AI / F6 CLI / F7 Mobile / F8 OS-Integrated / F9 Local / F10 Headless Agent)。
3. 捕获能力栈按物理钩点分层,收敛为 **5 层**(L0 Kernel / L1 TLS MITM / L2 Frida SSL Hook / L3 Application Hook / L4 UI Accessibility),每层再按具体实现细分。
4. 10 形态 × 5 层 形成路由矩阵,Tier 1 ~ 7 个形态追结构化,Tier 2 ~ 2 个形态接受 UI 兜底,Tier 3 ~ 2 个形态明确不投资。
5. 将架构写成工业级设计文档(1340 行,13 章 + 3 附录),命名为 **Universal Capture Stack (UCS)**。
6. 围绕 UCS 提出 9 项开放决策(D-1 ~ D-9),在本轮先拍板其中最关键的 3 项:D-1 发布节奏、D-2 开源边界、D-3 目标用户。
7. D-2 拍板:采纳 **Open Core** 模型,OSS 覆盖信任根基,Pro 覆盖高级自动化。详细模块边界写成 ADR-010。
8. D-1 + D-3 拍板:采纳 **A+A 路径** — 小步快发(4 周发 v1.0)+ 开发者先行(Cursor/Copilot/Codex 用户)。
9. 用户首次接触"发布"与"开源社区"概念,补充一段完整的"发布会发生什么"说明,便于心理预设。

## 本次最终结论

### 1. 采纳 Universal Capture Stack (UCS) 作为捕获层北极星架构

UCS 是本次讨论产出的核心架构成果。它不是短期 sprint 的产物,而是 PCE 捕获层未来 6-12 个月的演化蓝本。

核心设计:

- **10 形态分类**:所有 AI 产品都归入 10 种规范形态之一(F1 ~ F10)
- **5 层能力栈**:L0 Kernel Redirector / L1 TLS MITM / L2 Frida SSL Hook / L3 Application Hook(6 子层) / L4 UI Accessibility(3 子层)
- **Capture Supervisor 中枢**:发现 / 策略 / 健康探针 / 动态降级 / 多层去重
- **CaptureEvent v2 统一数据契约**:所有捕获层向 ingest gateway 递交的唯一 envelope
- **Tier 投资纪律**:T1 追结构化(7 个 ~75% 用户)、T2 接受 UI 兜底(2 个 ~15% 用户)、T3 明确不投资(F7 Mobile + F8 OS-Integrated ~10% 用户)

完整设计见 `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`,决策记录见 `ADR-009`。

### 2. 采纳 Open Core 商业模型

**OSS Edition(Apache-2.0)**——公开仓库 `github.com/zstnbb/pce`

覆盖信任根基:
- L1 MITM + CA 向导 + 系统代理开关
- L3a 浏览器扩展(13+ 站点)
- L3d CDP / L3e LiteLLM / L3f OTel
- L4a 剪贴板 / L4c OCR
- L3c VS Code 扩展基础 hook
- `pce_core/`(归一化 / 存储 / 仪表板基础)
- **CaptureEvent v2 schema 与 Ingest Gateway**

**Pro Edition(专有)**——私有仓库 `github.com/zstnbb/pce-pro`

覆盖高级差异化:
- L0 Kernel Redirector(Windows WFP / macOS NE / Linux eBPF)
- L2 Frida SSL Hook(含签名脚本)
- L3b Electron preload 注入
- L4b Accessibility 桥(macOS AX / Windows UIA)
- Capture Supervisor 自动调度引擎
- VS Code 扩展 Pro 增值 + JetBrains 扩展
- 仪表板高级版(搜索 / 回放 / 分享 / 导出)

核心设计原则:
- **依赖方向** `pce-pro ──▶ pce`,OSS 永不 import Pro
- **CaptureEvent v2 是唯一公共契约**,Pro 通过本地 HTTP 发事件,不进程内直调
- **CI 硬规则**:`pce` 仓库自动扫 import,出现 Pro 依赖即 fail

完整模块边界见 `ADR-010`。

### 3. 采纳 A+A 发布路径

**D-1 = A · 小步快发**:4 周后发 v1.0(L1 + L3a 够用即发),不等到 L3b 完成

**D-3 = A · 开发者先行**:瞄准 Cursor / Copilot / Codex / Cline 用户,不瞄准 ChatGPT Plus 大众

理由:
- PCE v1.x 需要装 CA + 代理 + 扩展,技术门槛只适合开发者种子用户
- 开发者反馈质量高,能帮调试;大众用户容易"装不上就卸载"
- 与 Open Core 天然协同 — OSS 社区本来就是开发者
- Pro 变现点(L0 + L2 解决 pinning)正好是 Cursor/Claude Desktop 用户的真实痛点

D-1 推荐从"~~A~~ → **Decided A**";D-3 早先草稿推荐 B,深入讨论后确认 **Decided A**。

### 4. 完整阶段路线图

| 阶段 | 版本 | 切片内容 | 工时估 |
|---|---|---|---|
| P5.A | v1.0 Subscription Capture | L1 收尾 + L3a 扩展 F2 站点 | 3–4 周 |
| P5.B | v1.1 IDE & Electron | L3b Electron preload + L3c VS Code 扩展 | 4–5 周 |
| P6 | v1.2 Pinning-Proof | L2 Frida SSL hook(跨平台) | 6–8 周 |
| P7 | v1.3 Force Capture + Fallback | L0 Kernel + L4b AX + L4c OCR + JetBrains | 8–10 周 |
| P8 | v2.0 Full Supervisor | 完整调度 / 去重 / 自动降级 | 4–6 周 |

每阶段都是**可发布的产品切片**,验收不过不允许放行。详细任务单与验收门槛见 UCS §9/§10。

### 5. 10 形态投资纪律

- **Tier 1(追结构化,~75% 用户)**:F1 Web Chat / F3 Browser-Ext AI / F5 IDE AI / F6 CLI / F9 Local / F10 Headless Agent / F4 部分
- **Tier 2(接受 UI 兜底,~15% 用户)**:F2 Web-Embedded / F4 pinning 部分
- **Tier 3(明确不投资,~10% 用户)**:F7 Mobile(pinning + 越狱不现实)/ F8 OS-Integrated(Apple Intelligence / Windows Copilot NPU 不允许第三方 hook)

**不追求"覆盖所有产品"** — 用物理可及性作为边界,接受不可及的部分。

### 6. 发布过程心理预设

用户首次面对开源发布,特别澄清:
- "发布"不是一个瞬间,是 6+ 周的旅程(准备 → 商店提交 → 发布日 → 第一波 → 稳定节奏)
- Chrome Web Store 审核(尤其 `<all_urls>`)是发布路径上最大的瓶颈,需倒排时间
- Show HN / Reddit / Twitter 的典型流量与 issue 分布有经验公式可参考
- 60% 首 issue 是安装问题,不是 bug
- 第一个 security researcher 出现应视为礼物而非危机
- Maintainer burnout 是真实风险,需要 SLA 与边界
- 关键心态:**"10 个 star 就很开心"** — 这种心态反而让项目能走远

## 当前明确不做的事

P5 阶段明确**不**纳入:

- F7 Mobile 平台原生捕获(CA + PAC 向导已是极限)
- F8 OS-Integrated(Apple Intelligence / Windows Copilot 的 NPU 调用)
- AI 自动分析 / 摘要 / 评分(属于"理解"层,不是"抓"层)
- 云同步 / 多设备
- 团队协作 / 企业多租户
- 为每个新出现的 AI 产品单独写适配(要先看能不能归到 10 形态之一)

## 待后续决策的问题

在 UCS §12 中列出 9 项 D-point,本轮已解:
- D-1 发布路径 ✅ Decided: A
- D-2 开源边界 ✅ Decided: B Open Core
- D-3 用户优先级 ✅ Decided: A

未解,不阻塞 P5.A,但 v1.0 发布前必须定:
- D-4 L2 Frida 脚本分发(静态打包 / 按需下载)— 建议 B
- D-5 移动端投资程度 — 建议 B(只做 PAC 兜底)
- D-6 OTel 二级通道默认行为 — 建议 A(默认关)
- D-7 CA 安装合规声明(需法务)
- D-8 L2 Frida 与目标应用 ToS(需法务)
- D-9 数据所有权 / 免责(需法务)

## 这份决议的用途

这份文档用于替代本轮长聊天,作为 **P5.A 开工的上下文锚点**。

本轮决议同时产生了以下具体落盘文件:

- `docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`(新建,1340 行)
- `docs/engineering/adr/ADR-009-universal-capture-stack.md`(新建)
- `docs/engineering/adr/ADR-010-open-core-module-boundary.md`(新建)
- `docs/PROJECT.md`(升级:反映 P5 阶段 + UCS 架构 + Open Core)
- `tasks/TASK-006-P5A-subscription-capture.md`(新建)
- `handoff/HANDOFF-P5A-KICKOFF.md`(新建)
- `Docs/README.md`(入口文档地图更新)

## 下一步

1. **仓库拆分与开源门面准备(R1–R7)** — P5.A 编码前必完,约 0.5–1 day:
   - R1 当前仓库加 `LICENSE`(Apache-2.0)+ `NOTICE` + `CONTRIBUTING.md` + `SECURITY.md` + `CODE_OF_CONDUCT.md`
   - R2 所有 `.py` / `.ts` / `.js` 源文件加 SPDX header
   - R3 创建 `github.com/zstnbb/pce-pro` 私有仓库 + 专有 LICENSE
   - R4 `pce` CI 加 import-direction 检查(禁止 import `pce_agent_*` / `pce_core.capture_supervisor`)
   - R5 `pce-pro` CI 设置 secrets 存放占位(签名密钥晚点配)
   - R6 更新 `README.md` 说明 OSS vs Pro 功能矩阵
   - R7 仓库改名 `PCE-Core` → `pce`
2. **本地 agent 按 `handoff/HANDOFF-P5A-KICKOFF.md` 开始 P5.A**
3. **P5.A 完成后产出 `docs/decisions/2026-05-??-P5.A-completion.md`**,供 P5.B 开工参考
