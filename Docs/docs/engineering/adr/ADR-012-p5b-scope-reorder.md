# ADR-012: P5.B 范围重排 — L3f/L3b 先行, L3c 移至 P5.C

- Status: Accepted
- Date: 2026-05-08
- Related: ADR-009 (Universal Capture Stack), ADR-010 (Open Core boundary), `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md`
- Resolves: 与 2026-04-18 决议中 P5.B scope 之间的张力
- References: `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §8 (attack sequence), `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §7 (implementation phasing)

## Context

2026-04-18 的发布策略决议(`ucs-and-release-strategy.md`)将 P5.B 定义为 "v1.1 IDE & Electron"，包含两条主路径：

- **L3b** Electron preload 注入(Claude Desktop / Cursor / Windsurf)
- **L3c** VS Code Extension API 原生 hook(Copilot 寄生插件)

之后 2026-05-08 编写的桌面端捕获认知框架(`DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`)在四个捕获面(N/H/M/U)的基础上提炼出 §8 攻击顺序：

1. **M 面 / L3f** — MCP middleware(新引入的捕获层)— 一处接口解锁 6+ 产品的工具调用语义
2. **H 面 / L3b** — Electron preload — 解锁桌面聊天 + IDE-class
3. **N 面 / L1 + L3e gateway** — 系统代理 + CLI gateway — 解锁 Copilot / CLI agents
4. ... 后续

而 L3c 在框架中的位置：**寄生插件类型**(框架 §3 类型 3)。它的捕获面主要是 N(API 流量经厂商中继)；L3c 提供的本质是 *观察接口*，而不是 *额外的网络流量来源*。在框架视角下，L3c 提供的覆盖**完全可以由 L1 + L3e gateway 覆盖**(代价是不能拿到 IDE 上下文)。

这创造了一个 scope 张力：

- 4-18 决议把 L3c 列在 P5.B
- 5-8 框架明确指出 L3f 是更基础的层，且 L3f 解锁的产品集(类型 1b/2/4)显著大于 L3c(类型 3)
- 同时纳入 L3f + L3b + L3c 会让 P5.B 工时膨胀至 ~10 周；按 ADR-009 的"小步快发"原则，应该控制单阶段在 5-7 周

## Decision

**重排 P5.B scope 如下**：

### 进入 P5.B(v1.1, 2026-05-08 ~ 2026-06-26 估)

| Sub-phase | 内容 | UCS 层 | 解锁的目标产品 |
|---|---|---|---|
| **P5.B.0** | `pce_mcp` 资产正名 | 现有 | P1/P3/P4/P6/P7/P8 自报告 |
| **P5.B.1** | MCP middleware proxy | **L3f**(新增) | P1/P6/P7/P8 工具调用 |
| **P5.B.2** | Electron preload — Claude Desktop | L3b | P1 全 D-cases |
| **P5.B.3** | Electron preload — Cursor + Windsurf | L3b | P3/P4 全 D-cases |
| **P5.B.4** | L1 proxy + L3e gateway + ChatGPT Desktop probe | L1, L3e, L3b(尝试) | P5/P7/P8/P2 |

### 移出 P5.B、新增 P5.C(v1.1.1)

- **L3c VS Code Extension API** 原生 hook 移至 **P5.C**
- P5.C 单独 3-4 周，专注 IDE 插件生态：Copilot 高级功能、JetBrains 探索、VS Code Marketplace 上架准备

### 本次重排的合理性来源

1. **更高 ROI**：L3f 一次实现解锁 6 个产品(P1/P3/P4/P6/P7/P8 都使用 MCP)；L3c 只解锁 1 个产品(P5 Copilot)
2. **更低风险**：L3f 是协议层透明转发，无 vendor 反制风险；L3c 依赖 Microsoft Extension API 表面，长期不稳定
3. **框架自洽**：把 P5.B 的攻击顺序锁定为"M → H → N"三面渐进，每一面的锁解开都是一份可发布切片；这与 ADR-009 的小步快发原则严格对齐
4. **P5 总时长不变**：移到 P5.C 的工作量没有消失，只是被合理后置；总 P5(.A+.B+.C)时长仍在 13-15 周区间

## Consequences

### 正面

- P5.B 工时收敛至 5-7 周，与 ADR-009 小步快发原则严格对齐
- L3f 作为新增 UCS 层会在 P5.B.1 期间通过单独 ADR(暂定 ADR-015)正式纳入 UCS 设计文档 v0.3
- DESKTOP-PRODUCT-MATRIX.md §7 实施分期可作为本 ADR 的执行单照搬

### 负面 / 已知代价

- Copilot 在 P5.B 期间只能通过 L1 proxy 路径(D2 best-effort 等级)被覆盖；其 D-case 通过率受厂商中继 + 不可控更新影响
- 4-18 决议中"v1.1 = IDE & Electron"的简洁表述被打散；新表述"v1.1 = IDE & Desktop & MCP"略冗长，但更准确反映 scope

### 中性 / 不变

- ADR-010 的 Open Core 模块边界完全不变：L3f 是 OSS(由 ADR-013 单独裁定)、L3b 是 Pro、L3c 仍是 OSS(基础版)
- CaptureEvent v2 契约不变；新增的 source_type 枚举值(`mcp_proxy` / `desktop_electron` / `cli_gateway`)走的是 schema 加字段(non-breaking)路径
- v1.0 → v1.1 → v1.1.1 → v1.2 的 SemVer 节奏严格保持

## 跟进项

- **ADR-015**(将在 P5.B.1 期间落地)：UCS 增设 L3f 层的设计与边界
- **PROJECT.md** §11 阶段表已同步更新(P5.B / P5.C 分列)
- **HANDOFF-IDE-DESKTOP-KICKOFF.md** §4 已按本 ADR 排布 P5.B.0 ~ P5.B.4 子阶段
- **DESKTOP-PRODUCT-MATRIX.md** §3.1 + §7 已依此 ADR 划分 Tier-D 与子阶段 unlock 矩阵

## 参考来源

- `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md` — 原 P5.B scope 来源
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §8 — 重排的证据基础
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §7 — 重排后的执行方案

---

*本 ADR 于 P5.B.0 阶段(2026-05-08)起草并接受。重排不需要回滚 4-18 决议中的其他条款(D-1 = A 小步快发, D-2 = B Open Core, D-3 = A 开发者先行)。*
