# Redundancy Implementation Plan — P5.D.1 Sprint (2026-05-14, STRICT MODE)

> **Status:** canonical — 本文件是本冲刺的唯一真相来源。所有 sub-document
> (`redundancy-sprint/0x-*.md`) 都是本文件的展开，不可独立解读。
>
> **Scope authority:** `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
> (2026-05-15 owner 决策锁定 13 个 P0 场景严格 ≥ 3 V-GREEN). 本文件 §0
> 范围 / 时序均按 SCOPE-LOCK 重写.
>
> **Sprint window:** 2026-05-14 → ~2026-07-06 (Phase A 1 周 + Phase B 6 周
> wall-clock + Phase C 关闭周; 单人或并行 3 人日 Phase A; Phase B 必须 3
> 人并行才能压到 6 周)
>
> **Adopted:** 2026-05-14, **Re-scoped STRICT MODE 2026-05-15**
>
> **Authority:**
> - 上承范围层: `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
>   (13 P0 场景锁定)
> - 上承策略层: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\REDUNDANCY-AUDIT-MATRIX.md`
>   §1 (V-GREEN 阶梯) + §6 (不变式) + §7 (更新协议)
> - 上承设计层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\UNIVERSAL-CAPTURE-STACK-DESIGN.md`
>   §4 (Capture Supervisor) + `@Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
>   §2 (8 desktop products P1-P8)
> - 上承决策层: `ADR-018` (MSIX strategy) + `ADR-019` (maintenance) +
>   待批 `ADR-021` (capture_supervisor OSS boundary) +
>   待批 `ADR-022` (UIA accessibility OSS boundary, 本冲刺新增) +
>   待批 `ADR-023` (gRPC-web protobuf decoder, 本冲刺新增)
> - 下指执行层: 每个 wave 详细 sub-doc (W1-W9)
>
> **Audience:** 本冲刺的执行 agent / owner / 后续审计者

---

## 0 · 顶层目标 / 非目标 / Definition-of-Done

### 0.1 Goal

冲刺结束时**同时成立**:

- **G1 (覆盖)**: 13 个 P0 场景每个有 ≥ 3 条 V-GREEN 路线
  - **Web 5**: F1 ChatGPT Web · F1 Claude Web · F1 Gemini Web ·
    F1 GAS · F1 Grok Web
  - **Desktop 8 (P1-P8 per `DESKTOP-PRODUCT-MATRIX.md` §2)**:
    F4 P1 Claude Desktop · F4 P2 ChatGPT Desktop ·
    F5 P3 Cursor · F5 P4 Windsurf · F5 P5 GitHub Copilot ·
    F6 P6 Claude Code CLI · F6 P7 Codex CLI · F6 P8 Gemini CLI
- **G2 (运行时)**: 3 条腿在运行时真互为冗余 — `pair_id + fingerprint`
  30s 滑窗 dedup 上线; `/api/v1/supervisor/status` 暴露 13 场景 leg
  health; 任一条腿挂了 ≤ 30s 内 dashboard 显示降级
- **G3 (可见)**: `REDUNDANCY-AUDIT-MATRIX.md` §3 主表里所有 13 P0 场景
  行升级到 ≥ 3 条 V-GREEN, 每条都引用本冲刺新增的 handoff;
  `Docs/stability/REDUNDANCY-MATRIX.svg` 13 张卡片 nightly 渲染

### 0.2 Non-Goal (显式不做; 见 SCOPE-LOCK §4 完整清单)

- ❌ F1 Perplexity / Copilot MS / DeepSeek / S3 / SX 站点 — 推 P5.D.2 / P6
- ❌ F2 SaaS-embedded (Notion / M365 / Gmail / Figma) — 推 P6
  (受账号 + DOM probe 阻塞)
- ❌ F3 浏览器扩展 AI / F4 其它桌面 / F5 IDE 其它 / F6 Aider — 推 P6
- ❌ F9 Local Model / F10 Headless / Framework — 推 P5.D.2
- ❌ F7/F8 OS-Integrated AI — `PROJECT.md` §1.1 永久 T3 非目标
- ❌ Frida / L0 kernel — ADR-018 §3.7 永久 Pro 渠道
- ❌ "新场景接入" — 不在本冲刺, 专心冗余化已锁定的 13 个

### 0.3 Definition of Done (per P0 scenario)

```
[ ] Leg 1 V-GREEN  ← 已在 REDUNDANCY-AUDIT-MATRIX §3 标 V-GREEN
[ ] Leg 2 V-GREEN  ← 已在或本冲刺新签字
[ ] Leg 3 V-GREEN  ← 本冲刺新签字
[ ] 三条 leg 通过依赖隔离规则 (REDUNDANCY-AUDIT §1) — 不允许
    "L1 + A2" 算两条 (同 Chromium 网络栈)
[ ] 三条 leg 在 raw_captures 同一 pair_id 写入时被 dedup 到主行
    + deduped_by 数组
[ ] /api/v1/supervisor/status?scenario=<id> 返回三条 leg 各自的 health_color
[ ] handoff 按 §7.2 协议记录证据: raw_captures 行数 + messages 行数 +
    session_id
[ ] REDUNDANCY-AUDIT-MATRIX §3 对应行更新到 V-GREEN
[ ] 一条 PRE-MERGE 测试用 nightly probe 跑完且 GREEN
```

---

## 1 · 冲刺时序总览 (Phase A + B + C)

### 1.1 Phase A — 核心冲刺 (Tier-α 10 场景, 1 周)

| Day | Wave | 内容 | 工时 | 详细 sub-doc |
|---|---|---|:---:|---|
| 0 | Pre-flight | 5 治理决策 (OSS 边界 / 口径统一 / 隔离规则 / UIA OSS / Protobuf 决策) | 1h | [`redundancy-sprint/00-pre-flight.md`](redundancy-sprint/00-pre-flight.md) |
| 1 | Wave 1 — Live Evidence | 11 任务并行: V-HERMETIC → V-GREEN (Tier-α 10 场景中 8 个低门槛 leg) | 24h | [`redundancy-sprint/01-wave1-live-evidence.md`](redundancy-sprint/01-wave1-live-evidence.md) |
| 2 | Wave 2 — A2 SSLKEYLOGFILE | ADR-018 Phase 5 生产链路落地 (P1 + P2 corroboration) | 14.5h | [`redundancy-sprint/02-wave2-sslkeylogfile.md`](redundancy-sprint/02-wave2-sslkeylogfile.md) |
| 3 | Wave 3 — Capture Supervisor v1 | dedup + policy + status + dashboard (13 场景 scenarios.yaml) | 19h | [`redundancy-sprint/03-wave3-supervisor-v1.md`](redundancy-sprint/03-wave3-supervisor-v1.md) |
| 4 | Wave 4 — Third Leg per Tier-α | 8 任务补足第三条独立 leg (Tier-α 10 全 ≥3) | 18h | [`redundancy-sprint/04-wave4-third-leg.md`](redundancy-sprint/04-wave4-third-leg.md) |
| 5 | Wave 5 — Phase A Mid-gate | nightly check 接入 13; Tier-α 10 全绿 mid-gate; Phase B kickoff | 12h | [`redundancy-sprint/05-wave5-nightly-closure.md`](redundancy-sprint/05-wave5-nightly-closure.md) |

Phase A 合计: ~88h ≈ 6 工人日 (单人); 3 人并行 ~2.5 工日.

### 1.2 Phase B — 结构性建设 (Tier-β 3 场景, 6 周 wall-clock 并行)

3 个 track 必须不同人做, wall-clock 才能压到 6 周; 单人串行约 12 周.

| Track | Wave | 场景 | 内容 | 工时 | 详细 sub-doc |
|---|---|---|---|:---:|---|
| B-1 | Wave 6 — GitHub Copilot | F5 P5 | L1 host JSON normalizer + L3c VS Code 扩展 + L3f MCP | ~80h (~2 周) | [`redundancy-sprint/06-wave6-copilot-l3c.md`](redundancy-sprint/06-wave6-copilot-l3c.md) |
| B-2 | Wave 7 — ChatGPT Desktop UIA | F4 P2 | L4b Accessibility (UIA) 兜底; ADR-022 落地 | ~140h (~3-4 周) | [`redundancy-sprint/07-wave7-chatgpt-desktop-uia.md`](redundancy-sprint/07-wave7-chatgpt-desktop-uia.md) |
| B-3 | Wave 8 — Cursor protobuf | F5 P3 | gRPC-web protobuf decoder + L3b preload OR L4a; ADR-023 落地 | ~200h (~4-6 周) | [`redundancy-sprint/08-wave8-cursor-protobuf.md`](redundancy-sprint/08-wave8-cursor-protobuf.md) |

Phase B 合计 wall-clock: 由 Track B-3 决定, ~6 周.
Track B-3 是 longest pole — 它的关闭时间决定 v1.1.6 release 日期.

### 1.3 Phase C — 全量关闭 (1 周)

| Day | Wave | 内容 | 工时 | 详细 sub-doc |
|---|---|---|:---:|---|
| 末周 | Wave 9 — Final Closure | 13/13 全量验收 + ADR-022/023 Adopted + tag v1.1.6 + closing handoff | 16h | [`redundancy-sprint/09-wave9-final-closure.md`](redundancy-sprint/09-wave9-final-closure.md) |

Phase C 工时 ~16h, 与 Phase B 末尾紧贴或微重叠. 不允许在 Phase B 任一
track 未完成时进入 Phase C — 13 全绿才能 tag.

### 1.4 总时间窗

- **Phase A**: 6-7 工人日 (1 工作周, 多人并行可压到 ~3 工日)
- **Phase B**: 6 周 wall-clock (3 track 并行)
- **Phase C**: 2 工人日

**Sprint window: ~7-8 周 wall-clock**, 2026-05-14 → ~2026-07-06 (取决于
Track B-3 实际关闭日).

---

## 2 · 关键交付物总览

冲刺产出 (cumulative):

- **30+ 份新 handoff** — Docs/handoff/HANDOFF-W*-*-2026-05-14.md
  (Phase A 14 + Phase B 14 + Phase C 2)
- **~5000 LOC 新代码** — pce_core/capture_supervisor/* +
  pce_proxy/keylog_mode.py + cert_wizard/keylog.py +
  pce_core/uia_driver/* (P2 ChatGPT Desktop) +
  pce_core/normalizer/cursor_protobuf/* +
  vscode_ext/copilot/* + dashboard/redundancy.{html,js}
- **70+ 新单测**
- **REDUNDANCY-AUDIT §3 主表 13 行升级到 ≥ 3 V-GREEN**
- **4 份新 ADR** — ADR-021 (capture_supervisor OSS) + ADR-018 Phase 5
  closure + ADR-022 (UIA OSS boundary) + ADR-023 (gRPC-web protobuf)
- **1 个 nightly auto-issue 通道** — redundancy-degraded label
- **1 个 v1.1.6 release tag** (13/13 全绿才发)

---

## 3 · 关联 / 不变式

### 3.1 上游不变式 (本冲刺不可改)

来自 `REDUNDANCY-AUDIT-MATRIX.md` §6:

1. 任何"PCE 已覆盖 X 场景"陈述必须回 §3 V-GREEN 数核对
2. V-HERMETIC → V-GREEN 升级只能通过 handoff 签字
3. V-DEAD 不得被静悄悄翻案
4. 冗余定义的"不相互依赖"规则不许松绑 — L1 + A2 仍算 1.5 条
5. 本文件只记录客观状态, 不替项目决定优先级

### 3.2 本冲刺新增不变式

6. **dedup 假阴优于假阳**: 当 fingerprint 不确定时, 默认保留所有行,
   不删多算; 由 dashboard `/redundancy` 显示 "已合并自 N 个 source"
   角标, 不删历史行
7. **leg 独立性硬约束**: scenarios.yaml 里每条 leg 的 `independent_basis`
   字段值必须不同, 否则 redundancy_target 不计入
8. **handoff 必须含 SQLite snapshot pointer**: 每份 handoff 引用
   `_evidence_W*_2026-05-14/<id>.db`, 否则不算 V-GREEN

---

## 4 · 风险登记

| ID | 风险 | 触发条件 | 缓解 |
|---|---|---|---|
| R1 | W1-T1/T2 真 Claude Desktop 不响应 MCP | 用户没装 / Anthropic 改协议 | fallback: posture B (mcp_proxy) 替代 posture A, 2 条都试 |
| R2 | W2 keylog 路径权限问题 (MSIX user space 写入失败) | `%LOCALAPPDATA%\pce\` 写不动 | fallback: `%TEMP%\pce_keylog.txt` |
| R3 | W3 dedup 假阳: 把不同 pair 的相似 fingerprint 误删 | fingerprint 算法太宽 | 1) fingerprint 含 timestamp 5min 桶; 2) 提供 `--no-dedup` flag debug |
| R4 | ADR-021 owner 不批 OSS 边界放宽 | 30 min decision 卡住 | fallback: W3 supervisor 暂落 pce-pro; OSS 侧只暴露 status read-only proxy |
| R5 | W4-T1/T2 CDP launcher 在用户 Chrome 上失败 | Chrome 已运行旧实例 | SOP 第 1 步加 "kill 所有 chrome 进程" + warning |
| R6 | nightly workflow 在 GitHub Actions runner 上无法跑真产品 | runner 没装 Claude Desktop | nightly 只跑 hermetic + supervisor /status check; live handoff 仍人工 |
| R7 | 5 天窗口内 owner 不可用做 live test | 无法安排账号 / 桌面 | 把 W1-T4/T5/T6 推后; 先做 P1 Claude Desktop + supervisor (不依赖 live) |
| R8 | dedup 上线导致 dashboard 历史会话计数突然减少 | 用户感知数据丢失 | dashboard `/sessions` 显示 "已合并自 N 个 source" 角标, 不删历史行 |

---

## 5 · 一页验收门 (sprint closing)

完整 checklist 见 [`redundancy-sprint/99-acceptance-gate.md`](redundancy-sprint/99-acceptance-gate.md).

简版 (STRICT MODE — 13 全绿才发 v1.1.6):

```
[ ] G1: 13/13 P0 scenarios ≥ 3 V-GREEN (5 web + 8 desktop)
[ ] G2: /api/v1/supervisor/status alive (13 scenarios) +
        dedup proven on ≥ 1 真三 leg + degrade ≤30s
[ ] G3: REDUNDANCY-MATRIX.svg renders 13 cards +
        nightly auto-check daily
[ ] Governance: ADR-021 + ADR-022 + ADR-023 Adopted +
        ADR-018 Phase 5 Closed
[ ] Hygiene: 70+ tests GREEN + CHANGELOG +
        git tag v1.1.6 only after 13/13 green
```

**Tier-β (P2 / P3 / P5) 任一未达 ≥3 V-GREEN → 整个 release 顺移.
不接受 12/13 + carry-forward 妥协.**

---

## 6 · 后续 (本冲刺外, 全部移到 P5.D.2 / P6)

完整列表见 `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §4.

简表:

| 类别 | 内容 | 推到 |
|---|---|---|
| Web 站点 | F1 Perplexity / Copilot MS / DeepSeek / S3 / SX | P5.D.2 / P6 |
| F2 SaaS-embedded | Notion / M365 / Gmail / Figma | P6 (账号阻塞) |
| F3 浏览器扩展 AI | Monica / MaxAI / Merlin / Sider 等 | P6 |
| F4 其它桌面 | Poe / DeepSeek / Kimi / 豆包 / mac-DMG / Squirrel | P6 |
| F5 IDE 其它 | Cline / Continue / Cody / Amazon Q / Roo / Codeium / Tabnine / JetBrains | P6 |
| F6 其它 CLI | Aider | P5.D.2 |
| F9 Local Model | Ollama / LM Studio / vLLM / llama.cpp | P5.D.2 |
| F10 Headless | LangChain / LlamaIndex / CrewAI / 等 | P5.D.2 |
| F7/F8 OS-Integrated | Apple Intelligence / Windows Copilot | 永久 T3 非目标 |

---

## 7 · 一句话锚点

**冗余性不是更多的代码, 是更多互不依赖的失败模式. 本冲刺把"已有
代码 + 已写的设计 + 已批的 ADR"翻译成"3 条独立失败模式 + 运行时
仲裁 + 可见的健康度". 13 个 P0 场景 (5 Web + 8 Desktop) 是 v1.1
"PCE 抓 LLM" 标杆 — 13 全严格 ≥3 V-GREEN 才发 v1.1.6 tag, 任何场景
滑点整个 release 顺移. 完成后, 任何一条腿挂掉, 用户感知不到; 任何
一条腿降级, dashboard 立刻知道; 后续每个新场景接入按本冲刺立的
"3 leg + 独立 basis + dedup + status + nightly check" 模板执行.**
