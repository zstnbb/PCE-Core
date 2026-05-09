# ADR-014: 桌面应用类型 5 / 6 / 7 在 v1.1 显式延后

- Status: Accepted
- Date: 2026-05-08
- Related: ADR-009 (UCS), ADR-010 (Open Core boundary), ADR-012 (P5.B scope reorder)
- Resolves: 防止 P5.B 执行期间静默吸入 type 5/6/7 范围,造成 scope 漂移
- References: `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3 (六种应用类型 + 一种弃投), `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §10 (out-of-scope 三类详述)

## Context

桌面端捕获认知框架(`DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3)将桌面 AI 应用按 **用户位置 × LLM 位置 × 工具调用形态** 划分为 6+1 类:

| 类型 | 代表 | v1.1 进入 scope? | 本 ADR 处理? |
|---|---|---|---|
| 1a Electron 聊天客户端 | Claude Desktop | ✅ P1 in scope (D0) | 不涉及 |
| 1b MCP-native Electron | Claude Desktop / ChatGPT Desktop | ✅ in scope (D0/D2) | 不涉及 |
| 2 IDE-class | Cursor / Windsurf | ✅ P3/P4 in scope (D0/D1) | 不涉及 |
| 3 IDE 寄生插件 | GitHub Copilot | ✅ P5 in scope (D2) | 不涉及 |
| 4 CLI agent | Claude Code / Codex CLI / Gemini CLI | ✅ P6/P7/P8 in scope (D0/D1) | 不涉及 |
| **5 原生(非 Electron)AI 客户端** | **Raycast AI / BoltAI / MacGPT / Quicker AI** | ❌ **延后** | **本 ADR** |
| **6 本地 LLM 桌面应用** | **LM Studio / Ollama Desktop / Jan / GPT4All** | ❌ **延后** | **本 ADR** |
| **7 OS 集成 AI** | **Apple Intelligence / Windows AI / Galaxy AI / Pixel AI** | ❌ **延后** | **本 ADR** |

DESKTOP-PRODUCT-MATRIX.md §10 已记录这三类的延后理由与重启条件。本 ADR 把决策**正式 ratify**,并明确**重启路径**,防止 P5.B 执行期间因 PR 评审、用户呼声或 agent 误读文档而静默吸入。

## Decision

**Type 5 / 6 / 7 在 v1.1 (P5.B) 与 v1.1.1 (P5.C) 都不进入 scope。重启需通过新的 ADR 显式 supersede 本 ADR。**

### Type 5 — 原生(非 Electron)AI 客户端

**代表产品**: Raycast AI(macOS)、BoltAI / MacGPT / ChatX(macOS)、Quicker AI 插件(Windows)、Alfred AI workflows(macOS)

**延后理由**:

1. **捕获面要求**: Type 5 完全无 N(Web)、无 H(Electron preload)、无 M(MCP)。唯一可用面是 **U/L4b**(macOS Accessibility / Windows UIA)。
2. **UCS 排期**: L4b 在 ADR-009 中归属 Pro Edition,排在 **P6 (v1.2)** 后或 **P7 (v1.3)**。在 P5.B (v1.1) 启动 L4b 会与 ADR-010 §依赖方向相违。
3. **Persona 适配度**: 框架 §3.1 分析显示原生客户端是目标 persona 的**次要入口**,daily AI 暴露主要在浏览器 + Electron + IDE + CLI(均已在 v1.1 / v1.0 覆盖)。

**重启条件**(满足任一即可重新讨论):
- v1.1 上线后用户调研显示**具体的**原生客户端覆盖缺口(数量化反馈,不是泛意见)
- 企业客户付费需求 Raycast / BoltAI 抓取(Pro tier 商业承载)
- L4b 在 Pro 仓库已实现并经 P6 / P7 验证可用

### Type 6 — 本地 LLM 桌面应用

**代表产品**: LM Studio、Ollama Desktop、Jan、GPT4All、Msty、AnythingLLM Desktop、Open WebUI

**延后理由**:

1. **技术上可捕获**: 这一类**完全可以**通过 P5.B.4-B 即将构建的 L3e CLI gateway 路径捕获(用户把 `OPENAI_BASE_URL` 改指 PCE gateway)。从代码角度看延后成本接近零。
2. **Persona 适配度低**: 目标 $50+/mo persona 在云模型(Claude/GPT/Gemini)上花费 OS-X 浮点数倍于本地 LLM。本地 LLM 用户是不同的 persona slice(隐私优先 / 兴趣爱好者)。
3. **Scope 风险**: 在 v1.1 显式宣传"支持 Ollama"会迅速吸引一波本地 LLM 受众,引入与目标 persona 冲突的 backlog;v1.1 的发版叙事("desktop / IDE / MCP")也会被稀释。

**重启条件**(任一):
- v1.1 后 persona 研究指出可量化的"既用云也用本地 LLM 日活"群体
- L3e gateway(P5.B.4-B)上线后,实测发现支持本地 LLM 是 ≤3 天的延伸(此时可作为 v1.1.x bonus 隐式上线,不扩张 acceptance gate)

### Type 7 — OS 集成 AI

**代表产品**: Apple Intelligence(macOS / iOS)、Windows AI(Copilot+ PC)、Galaxy AI(Samsung 设备)、Pixel AI(Google 设备)

**延后理由**:

1. **捕获面要求**: Type 7 完全无 N(系统调用直达 NPU)、无 H(无独立进程可注入)、无 M(无 MCP 接入)、U 也极弱(系统级 API 不暴露 model 调用)。唯一通用路径是 **L0 Kernel 拦截**。
2. **UCS 排期**: L0 排在 **P7+**(v1.3+),而且 Apple Developer ID + Windows EV 代码签名是 ADR-010 §"敏感资产"列出的 Pro 专属凭据。
3. **vendor 政策不可控**: Apple / Microsoft / Google 对 OS 集成层 API 的开放节奏完全由各家路线图决定,PCE 没有杠杆。

**重启条件**(同时满足):
- Apple / Microsoft / Google 中至少**一家**发布 *非厂商锁定的*、可第三方读取本机 AI 调用的 API
- 企业客户付费需求(企业级监管合规通常是 OS 集成 AI 的最早合理需求)

(现实预期:不早于 2027-2028。)

## Consequences

### 正面

- P5.B / P5.C 工时与 ADR-009 小步快发严格一致,无 scope 流失风险
- 三类延后均有**重启路径** + **重启条件**,不是永久封死;留出未来 ADR-supersede 的空间
- v1.1 发版叙事("8 个目标产品全部稳定捕获")清晰、可验证、不被泛化稀释

### 负面 / 已知代价

- v1.1 不能宣传"支持 Raycast / Ollama / Apple Intelligence"。营销侧需准备 FAQ 解释。
- 部分 early users 可能因为不支持 Ollama 而流失;接受这一代价(persona 不匹配)
- Type 5/6/7 用户若申报 issue,关闭模板需引用本 ADR + DESKTOP-PRODUCT-MATRIX §10 的 reopen 条件

### 中性 / 不变

- 三类延后**完全不影响** ADR-010 的 Open Core 边界:Type 5/6 未来若开做,L4b / L3e gateway 都已在 OSS 仓;Type 7 必然走 L0 → Pro 仓
- 现有 8 个目标产品(P1-P8)的 acceptance gates 不受影响
- 当前 release notes / CHANGELOG / README 中可以使用"v1.1 focuses on AI-native desktop / IDE / CLI workflows; native macOS Accessibility integrations and on-device system AI are scheduled for later phases"措辞

## 跟进项

- **`README.md` v1.1 章节**(发版前):FAQ 段落引用本 ADR
- **PCE issue tracker label**:新建 `wontfix-v1.1-type5` / `wontfix-v1.1-type6` / `wontfix-v1.1-type7` 用于关闭对应 feature request 时附带本 ADR 链接
- **DESKTOP-PRODUCT-MATRIX.md §10**:本 ADR 接受后将"ADR-014 待写"改为"ADR-014 已批准"
- **HANDOFF-IDE-DESKTOP-KICKOFF.md §6 + §11**:同上更新

## 参考来源

- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §3.6 + §3.7 + §3.8 — 三类延后的原始论证
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §10.1 / §10.2 / §10.3 — 三类延后理由 + 重启条件
- `Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md` §"OSS Edition / Pro Edition 模块边界" — 解释为什么 Type 7 必然 Pro

---

*本 ADR 于 P5.B.0 阶段(2026-05-08)起草并接受。重启 type 5/6/7 任一类需要新 ADR 显式 supersede 本 ADR;不接受静默扩张。*
