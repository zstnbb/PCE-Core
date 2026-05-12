# ADR-019: 治理优于功能 — 把可维护性作为产品一等约束

- Status: Adopted (2026-05-12)
- Date: 2026-05-12
- Phase: P5.C (新增, 介于 P5.B 与 P6 之间)
- Related: ADR-009 (UCS canonical), ADR-010 (Open Core boundary), ADR-011 (PCE Probe remote agent — 激活其 G3 + G9), ADR-017 (Test Conductor — status 推到 Adopted), ADR-018 (MSIX 三主轴)
- Supersedes: 无 (首份本主题 ADR)
- Refines: ADR-017 §3 (Test Conductor 三层契约) — 本 ADR 把 conductor 提供的 verification-as-data 升级为 lane 级强制契约的一部分; ADR-011 G3+G9 — 本 ADR 给出明确实施期 (P5.C.3 + P5.C.4) 替代之前的 deferred 状态.

---

## 1. 决议 (一句话)

**PCE 把"可维护性"作为与"功能正确性"同等地位的产品一等约束**, 通过 4 项强制契约 + 3 项治理产物 + 1 个新阶段 (P5.C Meta-Pipeline) 落地. 任何 D0/S0 target 不满足契约不允许 release; 任何外部贡献者不通过治理产物无法接入 — 这是项目所有者无法作为主体维护时, 系统能继续生存的物理前提.

---

## 2. 背景 — 为什么这必须是 ADR 而不只是 process

### 2.1 项目约束的灵魂改变

过去 7 个月 (P0–P5.B), PCE 的隐式假设是 "项目所有者会持续投入 fix-it 工作". 这个假设在 2026-05-11 被显式推翻:

> "我没有办法作为维护这个项目的主体. 但这个项目存在一个必然问题: 官方应用 / 官方 web 会迭代, 一迭代我们的适配如果做得不够健壮, 就有可能阻碍我们服务我们的客户."

这不是性能问题或代码风格问题, 是 **structural risk** — 它决定项目 6-12 个月后还存不存在.

### 2.2 "做得更健壮"不是答案 — 健壮性有上限

PCE 抓取的对象是 **别人的产品**. 别人变 → 我们必须变.

- ChatGPT 几乎每周 UI 调整 (经验事实)
- Anthropic 一次小版本就可能加新 `content_block.type` (ADR-017 §2.2 已点出)
- Cursor / Windsurf 的私有协议是不透明的 gRPC-web, 会随静默升级
- MSIX Electron Fuses 锁死 NODE_OPTIONS 偏好 (ADR-018 H4 LOCKED)

任何对 "把代码做得更健壮" 的押注, 在 12 个月时间窗口内必败.

### 2.3 现状里的脆性具象

不是抽象担忧 — 已有具象证据:

- 14 个 `.diag_*.py` 散在仓库根目录 (P5.B 期间 Claude Desktop 调试残留). **遇到问题 → 写一次性脚本 → 不沉淀到框架 → 下次又重写**, 这是脆性的循环.
- 4 个 lane (browser/desktop/cli/mcp) 的 adapter 抽象在各自 lane 里成型, 但**跨 lane 没有统一契约** — verifier 改进要改 4-6 处.
- ADR-011 G3 (DOM watcher) + G9 (LLM auto-fix) deferred 6+ 个月, 没人推.
- ADR-017 设计完整但 status=Proposed, 等于没有.
- 没有 CODEOWNERS, 没有 issue template, 没有 nightly probe, 没有 health beacon — 所有 broken 全靠用户报警 + 项目所有者亲自查.

**如果不把"治理"显式化为 ADR, 它会永远输给"做新功能"**. 因为做新功能有看得见的 deliverable, 治理只有避免的灾难.

---

## 3. 决议内容

### 3.1 四项强制契约 (CI 硬拦截 + release gate)

**契约 A — 跨 lane 三件套**:

每个 lane 必须实现以下 3 件 (定义见 `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md`):

```
1. capture-as-data       → CaptureEvent v2 (已存在, 不变)
2. verification-as-data  → T-case (browser) / D-case (desktop) /
                           C-case (cowork) / M-case (mcp lane, 待定义) /
                           K-case (cli lane, 待定义)
3. health-as-data        → HealthBeacon v1 (P5.C.1 新增)
```

**禁止**: 任何 lane 跳过 3 件套之一直接进 PCE 主线. CI 拦截规则:

- `pce_core/health.py` 提供 `HealthBeacon` API
- 任何新 lane 的 PR 必须接入 `record_beacon()`, 否则 CI fail
- Test Conductor `list_targets()` 自动列出无 beacon 的 lane 为 `health_unknown`, dashboard 标黄

**契约 B — 多通道冗余 (D0/S0 强制双 plane)**:

D0/S0 target release gate (ADR-018 + DESKTOP-PRODUCT-MATRIX.md 修订):

| Tier | 最低 plane 数 | 要求 |
|---|---|---|
| **D0/S0** | ≥2 plane in PASS state | 一个 broken 不影响产品交付; CI 释放门 |
| **D1/S1** | ≥1 plane in PASS, ≥1 plane in `applicable` 状态 (即使 PENDING) | 提示但不阻塞 release |
| **D2/S2** | ≥1 plane in PASS | 标 best-effort |

具体到当前 D0/S0:

```
ChatGPT (S0)         = N (L1 mitmproxy) + H (L3a 浏览器扩展)
Claude.ai (S0)       = N (L1) + H (L3a)
Gemini (S0)          = N (L1) + H (L3a)
Claude Desktop (D0)  = N (L1, ADR-018 H2 PASS) + L3g 持久化 + M (L3f)
Cursor (D0)          = H (L3d CDP launcher) + N (L1 cursor.sh relay)
Claude Code (D0)     = H1/L3h CLI wrap + N (L1 anthropic.com)
```

**禁止**: D0/S0 单 plane 部署. CI 拦截规则: `release.yml` 检查 `pce_test_conductor` 输出, 任何 D0 target 缺第二 plane → release blocked.

**契约 C — 配置化优先 (selectors / endpoints / regex)**:

任何会随官方 UI / 协议改变的"魔术字符串"必须放在 YAML / JSON 配置文件, 不准放 Python / TypeScript 类属性:

- DOM selector → `pce_core/adapters/<site>.yaml` (P5.C.4 落地)
- 网络 endpoint regex → 同上
- UIA AutomationId / ClassName → `pce_core/adapters/desktop_<product>.yaml` (P6 落地)
- SSE event marker / JSON-RPC method 名 → 配置而非常量

**禁止**: 新增 adapter 时把 selector 写在代码里. PR template 强制勾选 "selector 已 YAML 化".

**契约 D — Patch-as-data, 不直接 push**:

来自 ADR-017 §3.5 的细化: Test Conductor `propose_patch` 返回 `unified_diff` + `rationale` + `confidence`, **conductor 不应用补丁** — 调用方 agent (Cascade / Claude Desktop / Claude Code) 通过自有 edit 工具落地, conductor 重跑 verify.

**禁止**: 任何"AI 自动修复并 push"的实现. 治理边界: AI 是 "副驾", 人 / agent + review 是 "驾驶员".

### 3.2 三项治理产物 (P5.C.5 强制)

**产物 1 — `CONTRIBUTING.md` "Fix a broken adapter" 章节**:

强制包含:
- step-by-step 复现失败 (`pytest -k <target>:<case>`)
- 跑 `tools/repair_adapter.py --target <id> --case <id>` 拿 LLM 候选 diff
- 应用 diff (人工 review) 后 `pytest` 重跑
- canary diff 自动更新, 提交 PR

外部贡献者按此能在 ≤30min 内修复一个 broken case → release gate.

**产物 2 — `CODEOWNERS`**:

每个 lane / 每个 D0/S0 target 至少 1 owner. owner 可以是项目所有者本人 (`@zstnbb`), **但不能是 nobody**. 这个机制配合 auto-issue 让 broken 有 ping 对象.

**产物 3 — Issue + PR templates**:

- `.github/ISSUE_TEMPLATE/broken-adapter.yml` — 强制提交 site/product + case_id + DOM dump (or UIA tree) + 复现命令
- `.github/ISSUE_TEMPLATE/new-site-request.yml` — 强制提供 persona alignment 证据 + 用户体量
- `.github/ISSUE_TEMPLATE/health-degradation.yml` — 用于 health beacon 报警 (auto-issue 也用此 template)
- `.github/PULL_REQUEST_TEMPLATE.md` — 强制勾选 (跑了相关 lane 测试 / 提交了 canary 更新 / 更新了 owner)

### 3.3 一个新阶段 — P5.C Meta-Pipeline & Maintainability

P5.C 是 "把契约 + 产物落到代码 + dashboard + CI 上" 的实施阶段. 详细 sub-phase 见
`@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` §4.

简表:

| Sub-phase | 主交付 | 产出契约 |
|---|---|---|
| P5.C.0 契约冻结 | 5 份 markdown (本 commit) | 文档锁住跨 lane 契约 |
| P5.C.1 健康度骨架 | `pce_core/health.py` + dashboard 视图 | 契约 A 第 3 件 |
| P5.C.2 Test Conductor MVP | `pce_test_conductor/` (8 MCP tool) | 激活 ADR-017 |
| P5.C.3 Nightly CI | GH Actions + auto-issue | 契约 B 释放门生效 |
| P5.C.4 配置化 + AI 修复 | YAML adapter + `propose_patch` LLM | 契约 C + D |
| P5.C.5 治理收口 | CONTRIBUTING / CODEOWNERS / templates / cleanup | 三项治理产物 |

P5.C 完成后, P6 (Coverage Polish) 才有意义启动.

---

## 4. 影响 — 哪些既有规则被改变

### 4.1 Release gate 升级

旧 (P5.B): D0 target ≥85% case PASS → ship.

新 (P5.C 之后): D0 target ≥85% case PASS **AND** ≥2 plane both PASS **AND** health beacon GREEN ≥7 days **AND** canary diff 无 hard severity drift → ship.

短期看慢, 长期看稳.

### 4.2 新 lane 进入门槛升级

旧: 新 lane 只要能 POST `CaptureEvent v2` 到 `/api/v1/captures` 就算 onboard.

新: 新 lane 必须同时 (1) POST CaptureEvent v2 (2) 实现至少 5 个 case 的 verification standard (3) 接入 health beacon. 三件套不全, lane 算 "scaffolding only", 不进 release matrix.

适用对象: P5.B 余项 (cowork-region C-case 已定义, 但 health 未接; code-region 全缺) + 任何未来 lane (如 mobile L4 family).

### 4.3 Pro repo 也必须遵守

`@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-010-open-core-module-boundary.md` 锁了 OSS / Pro 的 import direction. 本 ADR 增加约束: **Pro repo 的 lane (L0/L2/L3b/L4b) 也必须实现三件套契约才能进 Test Conductor**, 通过 conductor 的 `targets/<id>.yaml` manifest 接入 (manifest 只是 metadata, 不违反 import direction).

理由: 如果 Pro 跳过契约, OSS 边的 health dashboard / nightly probe / canary 就有盲区, 客户报 "Pro 模块坏了" 时项目所有者还是要 manual 调试. 治理的整一性高于 OSS/Pro 边界.

### 4.4 临时诊断脚本永久禁止根目录

`.diag_*.py` / `.tmp_*.py` / `.triage_*.txt` / `.pytest_*.log` 全部加入 `.gitignore`. 临时调试要么 `tools/` 沉淀, 要么 `tests/<lane>/inspectors/` 沉淀, 要么 archive 后删. P5.C.5 一次性清理 + 后续 PR review 时拒收.

---

## 5. 例外与边界

### 5.1 P5.B.5/.6 余项不阻塞 P5.C

cowork-region C-case (P5.B.5 standard-down 已落, impl 待启) + code-region (P5.B.6 待规划) 在 P5.C 期间作为**并行轨道**推进, 不阻塞本 ADR 的 sub-phase. P5.C.2 Test Conductor 落地后, 这两个余项的 case 列表会自动被 `list_cases()` 覆盖.

### 5.2 SX 站点不强制契约 C

SX (scaffolding, DOM-unverified) 站点 (M365 Copilot / Notion AI / Gmail / Figma AI) 不要求 YAML 化. 它们没有真实 selector, 只是 stub. 等 promote 到 S2+ 时再补.

### 5.3 健康度 dashboard 不上报到外部

`pce_core/health.py` 的 health_beacons 表完全 local. **永远不实现 Sentry-style 客户端 telemetry 上报到项目方服务器**. 这是 PCE local-first 原则 (PROJECT.md §7.1) 的硬约束.

例外: 用户**主动 opt-in** 把单条 broken-adapter beacon 复制到 GitHub issue (通过 dashboard 上的 "复制诊断信息" 按钮). 这不是上报, 是用户授权的诊断协助.

---

## 6. 后续动作

### 6.1 同 commit 落地

- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md`
- ✅ 本 ADR
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md`

### 6.2 后续 ADR (P5.C 实施过程中可能产生)

- 若 P5.C.4 LLM auto-fix 引入 cost / privacy 问题, 需要 ADR-020 (LLM API key 边界 + opt-in 模型)
- 若 P5.C.5 治理产物迭代后发现需要更细的 contributor tier (如 "trusted reviewer"), 需要 ADR-021

### 6.3 ADR status 推动

- ADR-017: Proposed → Adopted (P5.C.2 落地后)
- ADR-011: G3 + G9 标记 Resolved (P5.C.3 + P5.C.4 落地后)
- ADR-019: Adopted (本次)

---

## 7. 一句话锚点

**功能让 PCE 在某天工作; 治理让 PCE 在 12 个月后还能工作. 两者同等重要 — 这是这份 ADR 的全部内容.**
