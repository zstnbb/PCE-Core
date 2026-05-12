# 2026-05-12 决议 — Meta-Pipeline 与可维护性战略

> **Status**: adopted (drafted by Cascade after the 2026-05-11 Claude Desktop chat full sweep + cowork kickoff sub-runs landed at 19/22 PASS).
>
> **Trigger**: 项目所有者在 Claude Desktop chat-region 闭环后提出体系化追问 ——
> "一个端一个端做完之后, 后面的标准化抓取框架 + 标准化验证框架怎么搭?
> 我没办法作为这个项目的主要维护者, 但官方应用迭代会必然破坏适配, 怎么办?"
>
> **Audience**: 后续接手 agent / 项目所有者 / 任何评估 PCE 长期可维护性的人.
>
> **认知轴归属**: 本决议属于 **maintainability 轴** — 与既有的 strategy 轴
> (`@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`)
> 和 validation 轴 (`@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md`) 平行, 不替代任何一个.

---

## 1. 决议(三句话)

1. **当前"一个端一个端做"方向正确, 但要先冻结跨 lane 公共契约再继续做**, 否则 P5.B 收尾后做体系化时会出现 browser/desktop/cli/mcp 各 lane 重写的成本浪费. 跨 lane 契约的雏形已经存在 (`pce_probe/`、`tests/e2e_desktop_ui/`、`pce_core/normalizer/` 共享 schema), **不是从零搭, 是显式化**.

2. **"用户 dashboard 渲染后续考虑"对, 但 dashboard 必须切两层** —
   用户消息渲染 dashboard 可以延后; **适配健康度 dashboard 必须现在做**, 它是项目所有者无法亲自维护时的"眼睛".

3. **应对官方应用迭代靠的不是"做得更健壮", 是做得更冗余 + 自动告警 + 配置化**. 健壮性有上限. 多通道冗余 (N + H + M 三面至少二选其一) + nightly probe + Test Conductor canary + AI-辅助 selector 修复 才是真解.

---

## 2. 触发本决议的 5 个事实

### 2.1 P5.A + P5.B 已经把"一个端一个端"走通了一半

- **浏览器 lane (P5.A)**: 14 个站点适配器, S0 三站 (ChatGPT/Claude/Gemini) 在 `tests/e2e_probe/test_matrix.py` 上稳态 10P/9S/2F. 已硬冻结 (`@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md`).
- **桌面 lane (P5.B chat-region)**: Claude Desktop 22 个 D-case 中 19 PASS / 1 SKIP / 1 known bug / 1 deferred. **D0 释放门 (≥85%) 已过**. 5 个 sub-run handoff 落地.
- **桌面 lane (P5.B cowork-region)**: standard-down 已落地 (`@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`), 17 个 C-case 已定义但未实现.
- **CLI lane (L3h)**: `pce_cli_wrapper/` alpha.8 落地, 77 hermetic tests GREEN, 但还没有 case-级覆盖标准.
- **MCP lane (L3f)**: `pce_mcp_proxy/` alpha.1+2 落地, 但 MCP 帧的 case-级覆盖矩阵尚未存在.

**关键观察**: 4 个 lane (browser/desktop/cli/mcp) 的"adapter 抽象 / case-as-data / verifier" 三层都已经在各自 lane 里成型, 但**跨 lane 的统一契约还没有显式化**.

### 2.2 ADR-017 已经设计好了"自动化闭环验证框架"但 status=Proposed

`@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-017-test-conductor-cross-lane-agent-contract.md` 提出 `pce_test_conductor/` (OSS, Apache-2.0):

- 8 个 MCP tool: `list_targets / list_cases / run_case / get_run / diff_canary / classify_failure / propose_patch / verify_patch`
- `FailureKind` 9 值闭集: `LOGIN_WALL / UI_SELECTOR_MISS / NETWORK_NOISE_MISS / SCHEMA_DRIFT / URL_PATTERN_DRIFT / CONTENT_BLOCK_UNKNOWN / RACE_TIMEOUT / INFRA / UNKNOWN`
- JSON Schema canary 主动巡检 (genson 推断 + jsonschema-diff)
- Patch-as-data 模板 (conductor 不应用补丁, 调用方 agent 用 edit/apply_patch 自己落)

**这正是用户问的"标准化验证框架"**. 但 status 是 Proposed-but-not-implemented (deferred to Phase 4.D.1–6).

### 2.3 ADR-011 G3 + G9 也是 deferred 半成品

ADR-011 (`@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-011-probe-remote-agent-autonomous-testing.md`) 锁了 PCE Probe 远程化的 9 个 known gap, 其中:

- **G3 DOM baseline watcher** — 把 DOM snapshot 存仓, 每次跑 probe 时 diff. 浏览器 UI 一改就被发现.
- **G9 LLM auto-fix proposal** — UI 改了, 把 DOM diff + 失败 case 喂给 LLM, 让它产出 selector 候选.

两条都是 deferred 6+ 个月. ADR-017 §3.5 把 G9 重新解释为 "patches as data, agent 用自有 edit 工具落地" — 这是更合规的形态, 但仍未实施.

### 2.4 临时诊断脚本污染根目录(脆性具象化)

仓库根目录有 14 个 `.diag_*.py` + `.pytest_*.log` + `.tmp_*.py` (P5.B 期间 Claude Desktop 调试残留). 这本身是"遇到问题靠手写一次性脚本调试, 没有沉淀到框架"的具象信号 — 也是 ADR-017 + 本决议要解决的现象之一. 解决路径: 把可重用部分迁到 `tools/` 或 `tests/e2e_desktop_ui/inspectors/`, 一次性的归档/删除.

### 2.5 项目所有者的灵魂约束 — "我无法作为主要维护者"

这是讨论中最重要的事实. 它不是技术约束, 是工程治理约束. 翻译成可执行的工程目标:

| 所有者陈述 | 工程翻译 | 落点 |
|---|---|---|
| "我没法主体维护" | 单次维护成本 ≤ 30min, 任何贡献者都能做 | 配置化适配 + CONTRIBUTING.md "fix-a-broken-adapter" 章节 + CODEOWNERS |
| "官方一变就 broken" | 多通道冗余, 单层 broken 不影响产品 | 每个 D0/S0 强制 ≥2 plane (N/H/M) 同时在工作 |
| "影响客户服务" | broken 在客户报警前 12h 被发现 | Nightly probe + auto-issue + opt-in 客户端 telemetry |
| "整体体系怎么搭" | 跨 lane 契约 + 健康度面板 + canary + 治理 | 见 §4 Roadmap |

---

## 3. 三层防御策略(应对官方迭代)

### 3.1 防御 1 — 多通道冗余(最重要)

每个 D0/S0 产品必须 ≥2 plane 同时在工作:

```
ChatGPT          = N (L1 mitmproxy) + H (L3a 浏览器扩展)
Claude.ai (web)  = N (L1) + H (L3a)
Claude Desktop   = N (L1, ADR-018 H2 PASS) + L3g 持久化 + M (L3f MCP middleware)
Cursor           = N (L1, github copilot relay) + H (L3d CDP launcher)
ChatGPT Desktop  = N (L1, MSIX H2 待证) + (P6 Frida 兜底)
Claude Code      = H1/L3h CLI wrap + N (L1 anthropic.com)
```

**强制契约**: 任何 D0 产品单 plane 部署不允许 release. 这条要写进 ADR-019 § 决议.

### 3.2 防御 2 — Nightly Synthetic Probe + Test Conductor

**激活 ADR-017 + ADR-011 G3**:

- `.github/workflows/nightly-probe.yml` 每天 UTC 凌晨 2 点跑一次
  - browser lane: `python -m pytest tests/e2e_probe/test_matrix.py -k "chatgpt|claude|gemini"`
  - desktop lane: `python -m pytest tests/e2e_desktop_ui/cases -k "p1_chat"`
  - mcp lane: `python -m pytest tests/e2e_mcpb/`
  - cli lane: `python -m pytest tests/e2e_l3h/`
- 失败 → 自动开 GitHub Issue, 标签 `broken-adapter`, @CODEOWNERS
- Canary diff (ADR-017 §3.4) 写入 `pce_test_conductor/canaries/<target>/<case>.schema.json`, 漂移自动报告
- 输出 SVG 健康矩阵到 `Docs/stability/HEALTH-MATRIX.svg` (README 引用)

### 3.3 防御 3 — 配置化适配器(降单次修复成本)

把 `BaseProbeSiteAdapter` 类属性 (selectors / login_wall_selectors / response_container_selectors 等) 抽到 YAML:

```yaml
# pce_core/adapters/chatgpt.yaml
selectors:
  message_container: ["[data-message-author-role]", "article[data-message]"]
  send_button: ["button[data-testid='send-button']", "button[aria-label='Send']"]
network:
  endpoint_regex: "/backend-api/conversation"
  sse_event_marker: "data: "
```

UI 变了改配置不改代码, 社区可 PR. 桌面端 UIA selector 同理可抽 YAML.

### 3.4 防御 4 — AI-辅助 selector 修复(激活 ADR-017 §3.5 + ADR-011 G9)

`pce_test_conductor` `propose_patch` tool 调用 LLM, 输入 = (failed run evidence + DOM diff + 旧 selector), 输出 = unified diff + rationale + confidence. 调用方 agent (Cascade / Claude Code) 用自有 edit 工具落地, conductor 重跑验证.

把"单次修复"从 30min 砍到 5min.

### 3.5 防御 5 — 官方 API 兜底通道

给愿意提供 API key 的硬核用户一个开关:

```
设置 → 高级 → "我有 API key, 直接调用官方接口"
     → 切到 SDK 模式 (复用 pce_core/sdk_capture_litellm.py)
     → 绕开 DOM/网络抓取
```

API 协议比 UI 稳定 100 倍. 这部分用户最不抱怨, 同时降本.

---

## 4. 反模式清单(必须显式禁止)

| 反模式 | 现状证据 | 要求 |
|---|---|---|
| 临时诊断脚本散根目录 | 14 个 `.diag_*.py` | 立刻归档/删除, 后续诊断走 `tools/` 或 `tests/e2e_desktop_ui/inspectors/` |
| 全部押注 DOM 抓取 | 浏览器扩展只是 L3a | 强制 D0/S0 双 plane, L1 网络层 already there 但要标准化覆盖 |
| dashboard 完全延后 | 项目所有者倾向 | 用户 dashboard 可以延后, **健康度 dashboard 不行** |
| 等所有 lane 做完再标准化 | 当前思路 | 先冻结契约再做下一个 lane |
| 把维护负担留给项目所有者 | 灵魂痛点 | CONTRIBUTING.md + CODEOWNERS + auto-issue 让社区分担 |
| Pro lane 漏掉契约 | ADR-010 boundary | 任何 Pro lane 也必须实现 `AdapterContract` 才能进 Test Conductor |
| Test Conductor 8 tool 漂移 | ADR-017 仍 Proposed | P5.C.0 一次性锁定, 不再增减 |
| 每 lane 自己写 verifier | 当前现状 | 共享 `tests/e2e/capture_verifier.py` (HTTP closed-loop), 不重写 |
| 健康度只是日志 | 当前现状 | 落盘到 SQLite + 上 dashboard |

---

## 5. 90 天 Roadmap — 新增 P5.C 阶段 "Meta-Pipeline & Maintainability"

P5.C 是 P5.B 完成后的桥梁阶段, 把跨 lane 契约 + 健康度 + 治理收口, 然后再继续 P6 Coverage Polish.

### P5.C.0 (W1) — 契约冻结(文档先行, 0 代码)

- 创建 `Docs/docs/engineering/META-PIPELINE-FRAMEWORK.md` (本决议同步交付)
- 创建 `Docs/stability/DESKTOP-D-CASE-EXECUTION-STANDARD.md` (与 `PCE-PROBE-E2E-EXECUTION-STANDARD.md` 平行的桌面端 D-case 标准)
- 创建 `Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md` (健康度矩阵契约)
- 创建 `ADR-019-maintenance-as-first-class-concern.md` (本决议升级为 ADR)
- 完成度: 0 行代码, 4 份文档锁住所有跨 lane 契约

### P5.C.1 (W2) — 健康度骨架

- 新增 `pce_core/health.py` — `HealthBeacon` 数据类 + `POST /api/v1/health/beacon` 端点
- 浏览器扩展 + 桌面 driver + CLI wrapper + MCP proxy 各自加 `emit_health_beacon()` 调用点
- Dashboard 新增 "适配健康" 视图 (一个表格 + 颜色, 复用现有 `pce_core/dashboard/`)
- 新增 `tests/test_health_beacon.py`

### P5.C.2 (W3) — Test Conductor MVP

- 新增 `pce_test_conductor/` 包 (按 ADR-017 §3 三层契约)
- 实现 8 MCP tool (list_targets / list_cases / run_case / get_run / diff_canary / classify_failure / propose_patch / verify_patch)
- 实现 `FailureKind` 9 值 classifier
- 实现 canary schema 生成 + diff (genson + jsonschema-diff)
- 不实现 propose_patch 的 LLM 调用 (放 P5.C.4)

### P5.C.3 (W4) — Nightly CI + Auto-Issue

- `.github/workflows/nightly-probe.yml` 每天跑 4 lane 的核心 case
- 失败自动 `gh issue create --label broken-adapter`
- Canary diff 写入 `pce_test_conductor/canaries/` 并提 PR review
- 输出健康矩阵 SVG 到 `Docs/stability/`

### P5.C.4 (W5–W6) — 配置化 + AI 辅助修复

- 把 ChatGPT / Claude / Gemini 的 DOM selector 抽到 YAML (`pce_core/adapters/<site>.yaml`)
- 实现 `propose_patch` 的 LLM 调用 (Anthropic API key 用户 opt-in)
- 写 `tools/repair_adapter.py` (本地 CLI 入口)
- 写 `CONTRIBUTING.md` 的 "fix-a-broken-adapter" 章节
- 配置 CODEOWNERS

### P5.C.5 (W7–W8) — 治理收口 + Cleanup

- 归档/删除根目录 14 个 `.diag_*.py`
- 把 P5.B Claude Desktop debug 期间产生的 `_inspect_window_*.py` 迁到 `tests/e2e_desktop_ui/inspectors/`
- Issue templates: `broken-adapter` / `new-site-request` / `health-degradation`
- PR templates: 强制跑 `pytest -k <touched_lane>` + 提交 canary update

### Acceptance Criteria(P5.C 释放门)

- [ ] 4 lane (browser/desktop/cli/mcp) 都通过 `pce_test_conductor` 的 8 MCP tool 可调
- [ ] Nightly probe 连续 7 天 GREEN
- [ ] Health beacon 数据落盘 + dashboard 视图能看到 4 lane 颜色
- [ ] 至少 3 个站点 (ChatGPT/Claude/Gemini) selector 已 YAML 化
- [ ] 根目录 0 个 `.diag_*.py` / `.tmp_*.py`
- [ ] CONTRIBUTING.md "fix-a-broken-adapter" 章节存在 + 至少 1 个外部贡献者验证可读
- [ ] CODEOWNERS 标记每个 lane 的 owner
- [ ] ADR-017 status: Proposed → Adopted

---

## 6. 关键决策(待项目所有者拍板, 不阻塞 P5.C.0 文档落地)

以下 4 个决策影响 P5.C.1+ 的实施细节, 但 P5.C.0 (契约冻结) 不依赖它们:

| 决策点 | 推荐 | 影响 |
|---|---|---|
| **D-1**: 健康度 dashboard 现在做 vs P5.C.1 才做 | **现在做** (P5.C.0 同步) | 决定 nightly probe 报警去哪儿被看到 |
| **D-2**: 现在就开 CONTRIBUTING + CODEOWNERS vs P5.C.5 才开 | **P5.C.0 写 stub, P5.C.5 完善** | 决定社区贡献的接入门槛 |
| **D-3**: 加"我有 API key"兜底通道 | **加 (P5.C.4)** | 决定是否承认"无痕抓取不是唯一形态" |
| **D-4**: 配置化适配 (YAML) 优先级 | **P5.C.4** (不提前) | 决定重构 3 个 S0 站点适配器的工作量 |

D-1/D-3 推荐 "yes", 因为它们是降低维护成本的杠杆.
D-2/D-4 推荐 "按 roadmap 节奏走", 因为提前会变成额外负担.

---

## 7. 与既有 ADR 的关系

| ADR | 关系 |
|---|---|
| **ADR-009** UCS canonical | 不变. P5.C 不引入新 UCS 层, 只是把现有 5 层 (L0–L4) 在 4 个 lane 上的覆盖度显式化. |
| **ADR-010** Open Core boundary | 不变. `pce_test_conductor/` 明确归 OSS (复用 ADR-017 §4 论证). 健康度 telemetry 也归 OSS. |
| **ADR-011** PCE Probe remote agent | **激活其 G3 + G9**. G3 → P5.C.3 canary 实现; G9 → P5.C.4 propose_patch LLM 实现. |
| **ADR-017** Test Conductor | **status: Proposed → Adopted**. P5.C.2 实施其 8 MCP tool + 9 FailureKind + canary + patch-as-data. |
| **ADR-019**(本决议升级) | 把"治理优于功能"作为决策固化, 上承 ADR-017, 下指 P5.C 阶段. |

---

## 8. 后续动作 — 同步交付的文档

本决议同 commit 一起落地的文档:

| 文档 | 用途 |
|---|---|
| `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` | 下一个 agent 的接手入口 |
| `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` | 决策固化 |
| `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` | 体系化框架设计(三层 capture/verification/health-as-data 抽象) |
| `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` | 桌面端 D-case 兑现标准 (D00–D22) |
| `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md` | 健康度矩阵契约 |

更新现有文档:

- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\PROJECT.md` §11 加 P5.C 阶段定义, §12 加 ADR-019 引用
- `@f:\INVENTION\You.Inc\PCE Core\Docs\README.md` 阶段对照表加 P5.C 行

---

## 9. 一句话锚点

**PCE 不是"做更多的捕获前端", 是"在 4 个 lane 上把已经成型的捕获 + 验证 + 健康度三件套显式化为可被任何 agent 调用、可被任何贡献者扩展、可被官方应用迭代而不崩的统一管道". P5.C 是这个目标的实施阶段.**
