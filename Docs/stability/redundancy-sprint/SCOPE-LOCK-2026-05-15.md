# P5.D.1 Scope Lock — 13 P0 Scenarios (STRICT MODE)

> **Status:** canonical — 本文件是 P5.D.1 冲刺的**唯一范围权威**.
> 任何 wave doc / handoff / audit 行涉及"哪些场景在范围内", 必须回此
> 文件核对.
>
> **Adopted:** 2026-05-15
>
> **Authority:** 项目所有者 2026-05-15 决策 — "这是标杆, 必须严格执行"
>
> **Refines (not supersedes):**
> - `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §0
>   原 7 P0 范围 → 本文件 13 P0
> - `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3 主表
>
> **Audience:** 冲刺执行 agent / owner / 后续审计者

---

## 1 · 决策一句话

**P5.D.1 锁定 13 个 P0 场景 (5 Web + 8 已有应用端) 严格执行 ≥3 V-GREEN
不相互依赖路线. 不达 13 全绿不发 v1.1.6 tag. 其它所有场景 (Perplexity /
SaaS-embedded / 本地模型 / Headless / 浏览器扩展 AI / 其它桌面 app)
推 P5.D.2 或更后期, 本冲刺显式不在范围内.**

---

## 2 · 13 个 P0 场景

### 2.1 Web 5

| ID | 站点 | Tier | 当前 V-GREEN | 缺口 |
|---|---|:---:|:---:|:---:|
| `f1_chatgpt_web` | chatgpt.com | S0 | 2 | -1 |
| `f1_claude_web` | claude.ai | S0 | 2 | -1 |
| `f1_gemini_web` | gemini.google.com | S1 | 1 | -2 |
| `f1_gas` | aistudio.google.com | S1 | 1 | -2 |
| `f1_grok_web` | grok.com | S2 | 1 | -2 |

### 2.2 已有应用端 8 (= `DESKTOP-PRODUCT-MATRIX.md` §2 P1–P8)

| ID | 产品 | Type | Tier-D | 当前 V-GREEN | 缺口 | 路径性质 |
|---|---|---|:---:|:---:|:---:|---|
| `f4_p1_claude_desktop` | Claude Desktop (Win/Mac) | 1a/1b | D0 | 2 | -1 | 低门槛 |
| `f4_p2_chatgpt_desktop` | ChatGPT Desktop (Win/Mac) | 1c | D2 | 1 | -2 | **结构性** |
| `f5_p3_cursor` | Cursor | 2a | D0 | 0 | -3 | **结构性** |
| `f5_p4_windsurf` | Windsurf | 2a | D1 | 1 | -2 | 低门槛 |
| `f5_p5_github_copilot` | GitHub Copilot | 3 | D2 | 0 | -3 | **结构性** |
| `f6_p6_claude_code_cli` | Claude Code CLI | 4 | D0 | 1 | -2 | 低门槛 |
| `f6_p7_codex_cli` | Codex CLI | 4 | D1 | 0 | -3 | 中门槛 |
| `f6_p8_gemini_cli` | Gemini CLI | 4 | D1 | 0 | -3 | 中门槛 |

### 2.3 总缺口

**26 条 V-GREEN 路线待签字** (原冲刺只 11 条)

---

## 3 · 路径性质分层

按"补到 3 条 V-GREEN 需要的工作类型"分 3 层. 这个分层只服务**调度**,
不影响验收 — 13 个场景的 ≥3 V-GREEN 标准都一样硬.

### 3.1 Tier-α · 低门槛场景 (10 个)

补的腿是"代码已就绪 + 只差 live sign-off"或"≤ 2 天小工作"型. 走原
Wave 1 / Wave 4 路径, 1 周内完成.

```
f1_chatgpt_web              已 2/3 → +L3d CDP live → 3/3
f1_claude_web               已 2/3 → +L3d CDP live → 3/3
f1_gemini_web               1/3 → +L1 live + L4a clip → 3/3
f1_gas                      1/3 → +L1 live + L4a clip → 3/3
f1_grok_web                 1/3 → +L1 live + L4a clip → 3/3
f4_p1_claude_desktop        已 2/3 → +L3f MCP live → 3/3
f4_p4_windsurf              1/3 → +L3f MCP live + L4a clip → 3/3
f6_p6_claude_code_cli       1/3 → +L3h CLI wrap live + L1 live → 3/3
f6_p7_codex_cli             0/3 → L3h CLI wrap + L3g local + L1 host norm → 3/3
f6_p8_gemini_cli            0/3 → L3h CLI wrap + L3g local + L1 host norm → 3/3
```

P7 / P8 因 0/3 起点稍重, 但 normalizer 复用 P6 的 anthropic.py 模式,
2-3 天内可达标. 列入 Tier-α.

### 3.2 Tier-β · 结构性场景 (3 个)

需要新建子系统才能补到 3 条. 走 Phase B 并行 track.

```
f4_p2_chatgpt_desktop       1/3 → +A2 SSLKEYLOGFILE corroboration (W2)
                                → +L4b Accessibility (UIA) (Phase B-track-1, 3-4 周)
                                → 3/3

f5_p3_cursor                0/3 → +L3f MCP live (核心冲刺签 1)
                                → +L1 protobuf decoder (Phase B-track-2, 4-6 周)
                                → +L3b preload OR L4a clipboard (Phase B-track-2)
                                → 3/3

f5_p5_github_copilot        0/3 → +L1 host JSON normalizer (Phase B-track-3, ~3 天)
                                → +L3c VS Code extension (Phase B-track-3, ~1-2 周)
                                → +L3f MCP OR L4a clipboard
                                → 3/3
```

---

## 4 · 显式移出本冲刺 (推后期)

下列**不是缩水**, 是显式延后到 P5.D.2 / P6 / 或更后. 任何"PCE 已覆盖
X"陈述在 v1.1.6 release 之前不能包括它们.

### 4.1 站点 (Web) 移出

- `f1_perplexity` (perplexity.ai) — 推 P5.D.2
- `f1_copilot_microsoft` (copilot.microsoft.com) — 推 P5.D.2
- `f1_deepseek` (chat.deepseek.com) — 推 P5.D.2
- F1 S3: HuggingFace / Poe / Kimi / Zhipu / Mistral / Manus — 推 P6
- F1 SX: Notion AI / M365 Copilot / Gmail AI / Figma AI — 推 P6
  (账号 + DOM probe 阻塞)

### 4.2 应用端 (Desktop) 移出

- F3 浏览器扩展 AI: Monica / MaxAI / Merlin / Sider /
  ChatGPT-for-Google — 推 P6
- F4 其它桌面: Poe Desktop / DeepSeek Desktop / Kimi Desktop /
  豆包 Desktop / Claude Desktop macOS-DMG / Claude Desktop
  Windows-Squirrel — 推 P6
- F5 IDE 其它: Cline / Continue / Cody / Amazon Q / Roo Cline /
  Codeium / Tabnine / JetBrains plugin — 推 P6
- F6 其它 CLI: Aider CLI — 推 P5.D.2

### 4.3 框架 / 模型层 移出

- F2 Web-Embedded AI 全部 — 推 P6
- F9 Local / Self-Hosted Model (Ollama / LM Studio / vLLM 等) — 推 P5.D.2
- F10 Headless / Framework (LangChain / LlamaIndex / CrewAI / 等) —
  推 P5.D.2

### 4.4 OS-Integrated AI

- F7 / F8 Mobile / OS-Integrated (Apple Intelligence / Windows
  Copilot) — `PROJECT.md` §1.1 FR-1 显式 T3 非目标, 永久不在范围

---

## 5 · 时间窗 (sprint 重新预估)

13 全严格 ≥3 V-GREEN, 总工时:

| Phase | 内容 | 工时 | 工日 |
|---|---|:---:|:---:|
| **Phase A — 核心冲刺** | Pre-flight + W1 + W2 + W3 + W4 + W5 (Tier-α 10) | ~120h | 8 |
| **Phase B — 结构性建设** | W6 (Copilot) + W7 (P2 UIA) + W8 (Cursor protobuf) 并行 | wall-clock ~6 周 | — |
| **Phase C — 全量关闭** | W9 final 13/13 verification + tag v1.1.6 | ~16h | 2 |

**总 wall-clock: ~7-8 周** (Phase A 1 周 + Phase B 6 周 wall-clock,
Phase C 与 Phase B 末尾重叠 / 紧跟).

并行调度:
- Phase A 单人 8 工日; 3 人并行 ~3 工日
- Phase B 3 个 track 必须不同人, 否则 wall-clock 仍是各 track 工时之和
- Track-2 (Cursor protobuf) 是 longest pole, 决定关闭日期

---

## 6 · 严格验收门 (不可松)

冲刺 v1.1.6 release 必须**同时满足**:

```
[ ] G1 Coverage  — 13/13 P0 scenario 严格 ≥ 3 V-GREEN per
                   REDUNDANCY-AUDIT-MATRIX §1 严格标准
[ ] G2 Runtime   — /api/v1/supervisor/status 暴露 13 个 scenario;
                   dedup 在 ≥ 1 真三 leg 场景实战验证;
                   degrade ≤ 30s
[ ] G3 Visibility — REDUNDANCY-MATRIX.svg 渲染 13 张卡片 nightly;
                   redundancy-degraded auto-issue 通道 alive
[ ] Governance   — ADR-021 Adopted; ADR-018 Phase 5 Closed;
                   ADR-022 (本冲刺新增, L4b UIA OSS 边界) Adopted;
                   ADR-023 (本冲刺新增, gRPC-web protobuf decoder
                   开闭) Adopted
[ ] Hygiene      — ≥ 70 新单测 GREEN, 0 flake;
                   13 场景全部有 SQLite snapshot 入
                   _evidence_W*_2026-05-14/
```

**13 全绿前不发 v1.1.6 tag.** Tier-β 其中 1 个滑点 → 整个 release
窗口顺移, 不允许"12/13 + 1 个 carry-forward"妥协.

---

## 7 · 标杆性 (Why STRICT)

本冲刺是 P5.D 起的 redundancy 体系**首个完整对齐的标杆**:

1. **Web 5** 锁住"PCE 抓 LLM 网页"的最小一致性 — chatgpt / claude /
   gemini / gas / grok 是 v1.1 用户每天都用的 5 个站, 任一站缺腿,
   "PCE 覆盖率"对外叙事就破
2. **Desktop 8 (P1-P8)** 锁住"PCE 抓 LLM 桌面/IDE/CLI"的最小一致性 —
   `DESKTOP-PRODUCT-MATRIX.md` 已为这 8 个产品做了完整 D-case 标准 +
   tier 分配, 它们是 v1.1 desktop release gate
3. 后续每个新场景 (P5.D.2 / P6 / ...) 接入时, 都按本冲刺立的"3 leg +
   独立 basis + dedup + status + nightly check" 模板执行. 不再有"2 条
   腿凑数"的妥协可能

**这就是为什么本冲刺必须严格 — 标杆松, 后续全松.**

---

## 8 · 文档反向引用清单

下列文档已或将更新, 锚定本 SCOPE-LOCK 作为范围权威:

| 文件 | 改动状态 | 关键变化 |
|---|---|---|
| `REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` | ⏳ 待改 | §0 7→13; §1 时序加 Phase A/B/C |
| `redundancy-sprint/00-pre-flight.md` | ✅ 不改 | 治理决策不变 |
| `redundancy-sprint/01-wave1-live-evidence.md` | ⏳ 待改 | 任务 8→11, 移除 perplexity/ollama/langchain, 加 grok/windsurf/codex/gemini-cli |
| `redundancy-sprint/02-wave2-sslkeylogfile.md` | ✅ 不改 | A2 仍按 ADR-018 Phase 5 |
| `redundancy-sprint/03-wave3-supervisor-v1.md` | ⏳ 待改 | scenarios.yaml 7→13 |
| `redundancy-sprint/04-wave4-third-leg.md` | ⏳ 待改 | 任务 5→8, 加 grok L4a / windsurf L4a / codex L1 / gemini L1 |
| `redundancy-sprint/05-wave5-nightly-closure.md` | ⏳ 待改 | nightly check 7→13; v1.1.6 候选条件加 Phase B 完成 |
| `redundancy-sprint/06-wave6-copilot-l3c.md` | ⏳ **新建** | P5 GitHub Copilot 结构性 |
| `redundancy-sprint/07-wave7-chatgpt-desktop-uia.md` | ⏳ **新建** | P2 ChatGPT Desktop L4b UIA |
| `redundancy-sprint/08-wave8-cursor-protobuf.md` | ⏳ **新建** | P3 Cursor protobuf decoder |
| `redundancy-sprint/09-wave9-final-closure.md` | ⏳ **新建** | Phase C 全量验收 + tag v1.1.6 |
| `redundancy-sprint/99-acceptance-gate.md` | ⏳ 待改 | G1 7→13 行 |
| `REDUNDANCY-AUDIT-MATRIX.md` | ⏳ 待改 | §3 加 Grok 行 + §4 汇总 7→13; §5.C 结构性缺口移到 Phase B |
| `Docs/handoff/HANDOFF-P5D1-KICKOFF-2026-05-14.md` | ⏳ 待改 | §2 G1 改 13; §7 sprint state pre-table 13 行 |
| `Docs/handoff/_evidence_W*/README.md` | ⏳ 待改 | 文件清单扩展 |
| `Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md` | ⏳ **新建** | Phase B-track-1 落地依据 |
| `Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md` | ⏳ **新建** | Phase B-track-2 落地依据 |

---

## 9 · 一句话锚点

**13 个场景, 5 + 8 = 标杆. Tier-α 1 周内做完, Tier-β 6 周内并行做完,
Phase C 同步关闭. 13 全绿才发 v1.1.6 tag — 这条线在本文件 §6 锁住,
任何后续 agent / handoff / wave doc 不可松绑, 只能新 ADR 显式 supersede
本文件.**
