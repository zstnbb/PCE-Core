# ADR-009: 采纳 Universal Capture Stack (UCS) 作为捕获层北极星架构

- Status: Proposed
- Date: 2026-04-18
- Related: ADR-001 (record-not-intervention), ADR-002 (local-proxy-as-core), ADR-003 (dual-install-modes), ADR-006 (WXT), ADR-007 (OTLP optional channel)
- Follow-up: ADR-010 (Open Core module boundary — resolves D-2 of this ADR)
- Full design: `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`

## Context

P4 落幕后产品进入下一阶段规划。随之暴露出三个不能再回避的问题:

1. **PCE 的捕获能力是否还有独家价值?** 订阅类桌面 AI(Copilot/Cursor/ChatGPT Desktop/Claude Desktop/Codex)的鉴权都锁死在厂商 OAuth,BYOK 路径走不通,浏览器扩展也够不到 — 只有网络代理能覆盖。
2. **全品类 AI 的捕获应该怎么分层?** AI 产品爆炸,但按"用户位置 × LLM 位置"收敛后其实只有 10 种规范形态,不需要为每种形态造一个 PCE。
3. **终极架构与产品化如何不冲突?** 朝终极爬 vs 小步快发 ,必须有一个能同时满足两者的架构决策。

这三个问题不早答,后续每个 sprint 都在"修 L1 / 加浏览器扩展 / 补某个应用"之间打转,无法形成系统化产品。

## Decision

采纳 **Universal Capture Stack (UCS)** 作为"抓"层的北极星架构:

### 结构

- **10 形态分类**:F1 Web Chat / F2 Web-Embedded / F3 Browser-Ext / F4 Desktop Chat / F5 IDE AI / F6 CLI / F7 Mobile (T3) / F8 OS-Integrated (T3) / F9 Local / F10 Headless Agent
- **5 层能力栈**:L0 Kernel Redirector / L1 TLS MITM / L2 Frida SSL Hook / L3 Application Hook(6 子层) / L4 UI Accessibility
- **Capture Supervisor 中枢**:发现 / 策略 / 健康 / 降级 / 去重
- **统一数据契约 CaptureEvent v2**:所有层向 ingest gateway 递交的唯一 envelope
- **Tier 投资纪律**:T1 追结构化(~75% 使用量)、T2 接受 UI 级兜底(~15%)、T3 明确不投资(~10%)

### 渐进发布策略

朝终极架构爬,每爬稳一段就切片打包发出去,绝不为产品化阉割架构:

- v1.0(P5.A)= L1 + L3a 全面(Subscription Capture)
- v1.1(P5.B)= + L3b/L3c(IDE & Electron)
- v1.2(P6)= + L2 Frida(Pinning-Proof)
- v1.3(P7)= + L0 Kernel + L4b AX(Force Capture + Fallback)
- v2.0(P8)= + Supervisor 全自动(Full Supervisor)

### 投资边界

- **投资**:L0~L4 全部 5 层 + Supervisor + 10 形态中 7 个 T1 + 2 个 T2
- **不投资**:F7 Mobile(cert pinning + 越狱不现实)、F8 OS-Integrated(Apple Intelligence / Windows Copilot NPU 不允许第三方 hook)

## Rationale

1. **10 形态是物理事实,不是人为切分**。全品类 AI 在"用户位置 × LLM 位置"两个坐标下自然收敛到 10 种形态,不是我们主动简化 — 是市场结构使然。任何捕获方案都必须与这个结构对齐。
2. **5 层分工源自捕获物理**。TLS 层、进程层、应用层、UI 层这 4 种钩点是物理独立的;L0 内核层作为强制入口补充。不分层就没有兜底路径。
3. **PCE 在 L1/L3 已有独一份地基**。L1 的 mitmproxy addon + 跨平台 CA 向导 + 系统代理开关 + PAC,L3 的 13 站点浏览器扩展 + CDP + LiteLLM + OTel,这些已经是产品级。把它们组织进 UCS 框架,比推翻重写更快更稳。
4. **单层永远到不了 100%**。只做代理 → 被 pinning 和 gRPC 卡住;只做 Frida → 合规/签名/反调试;只做 UI → 拿不到结构化。终极方案必须是"每应用选它能支撑的最强层 + 降级"。
5. **渐进切片不冲突**。由于各层本身就在演进,按层切片(先 L1+L3a 稳定 → 再加 L3b → 再加 L2 → 再加 L0)天然对应产品里程碑,不用为了 release 人为切。
6. **避免与 ADR-001/002 冲突**。UCS 依然只记录不改写(ADR-001),依然 local-first(ADR-002),只是把捕获面从单一代理扩展到五层栈。

## Consequences

### Positive

- 捕获层路线图从"零散补丁"变成可审计的阶段性架构
- 新形态出现时先归到 10 种中最接近的,架构无需改
- 每个 P 阶段都有明确的 release 候选,市场反馈驱动优先级
- 投资边界清晰:F7/F8 不投入,避免拖死项目
- 技术选型(Frida / WinDivert / NetworkExtension / eBPF / preload / Accessibility)有统一去处

### Negative

- 引入大量新代码:`pce_agent_kernel/`、`pce_agent_frida/`、`pce_agent_electron/`、`pce_agent_ax/`、`pce_ide_vscode/`、`pce_ide_jetbrains/`、`pce_core/capture_supervisor/` 共 7 个新模块
- L0/L2 涉及系统内核/进程注入,合规与签名成本高(EV 证书 / Apple Developer / 脚本签名私钥保管)
- CaptureEvent v2 是 schema 断点,需要 migration + 向后兼容中间件
- Supervisor 成为新的关键路径,其稳定性直接影响整个 UCS

### Neutral

- 10 形态清单需要每季度回顾,新兴 AI 产品可能引发扩展(但历史表明 6 个月内大致稳定)
- 浏览器扩展的 Chrome Web Store 政策(尤其 `<all_urls>`)在未来可能变化 — 已由 ADR-003 双模式兜底
- 部分层依赖 Apple / Microsoft / Frida 等外部生态

## Guardrails

- CaptureEvent v2 schema 冻结后,新字段只能加 optional,不能改/删;字段语义变更必须走 v3 路径
- Supervisor 对事件**无改写权**,只加 `deduped_by` / `quality_tier` 元标签
- L0 默认关闭,仅在用户显式"强制捕获 X 应用"时启用,并维护 `L0_FORCE_BYPASS_LIST` 黑名单(银行/支付/VPN 永不导流)
- L2 Frida 脚本必须 Ed25519 签名,校验失败直接拒绝加载
- 每层暴露标准指标 `capture.rate` / `capture.failure` / `capture.latency_p95`;未达 NFR 性能预算(整机 ≤ 12% CPU / ≤ 1.2GB RAM)阻止发版
- T3 形态(F7/F8)在仪表板明确标记"不支持",避免用户误以为是 bug 而反复报告

## Review Trigger

以下情况必须回来评估本 ADR:

- 新出现的 AI 产品形态**无法**归类到现有 10 种中任一个(例如某种完全绕开 TCP/IP 的端侧 AI)
- F7 或 F8 出现技术突破(例如 Apple 开放 Intelligence API、Android 放松 cert pinning)— 重新评估 T3 投资纪律
- Frida / WinDivert / NetworkExtension 等关键依赖的生态发生断代(licensing / EOL)
- 行业出现比 `CaptureEvent v2` 更标准的事件 envelope(例如 OTel GenAI semconv 演进到能直接当数据契约)
- 终极架构完成(v2.0 发布)后,回顾 Tier 覆盖率实际数据,调整下一阶段投资优先级
