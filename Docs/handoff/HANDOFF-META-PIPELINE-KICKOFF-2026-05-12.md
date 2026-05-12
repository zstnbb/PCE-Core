# HANDOFF: P5.C — Meta-Pipeline & Maintainability (post-Claude-Desktop)

> **Status**: active kickoff — effective 2026-05-12.
>
> **Supersedes (active position)**: `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-IDE-DESKTOP-KICKOFF.md` 不取消, 但其中 P5.B.0–P5.B.5 已经把 D0 释放门 (Claude Desktop chat-region 19/22 PASS) 走通; cowork-region (P5.B.5) 与 code-region (P5.B.6) 仍属 P5.B 余项, 在 P5.C 期间作为 **并行轨道** 推进, 不阻塞本 handoff.
>
> **Predecessors (read order)**:
> 1. `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` — Claude Desktop chat 19/22 PASS 闭环
> 2. `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` — cowork-region standard-down (P5.B.5 待实施)
> 3. `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md` ⭐ — 本 handoff 的决议依据
>
> **Framework authority**: 所有 "为什么" 问题的 truth source 是
> `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` (本 commit 同步交付).
>
> **Validation authority**: 所有 "什么算做完" 的 truth source 是
> `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md` 与
> `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` (本 commit 同步交付),
> 配合既有的
> `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` (浏览器 T-case) 和
> `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` (产品视角).
>
> **Audience**: Cascade、后续接手 agent、项目所有者.

---

## 0. TL;DR — P5.C 6 个 sub-phase

| Sub-phase | 时长 | 主交付 | 是否有代码 |
|---|---|---|---|
| **P5.C.0 契约冻结** | 1 周 | 4 份 markdown (本 handoff 已包含 framework + 2 份 standard, 其余在本 commit) | 0 行代码 |
| **P5.C.1 健康度骨架** | 1 周 | `pce_core/health.py` + 4 lane 接入 + dashboard "适配健康"视图 | ~600 行 + 1 migration |
| **P5.C.2 Test Conductor MVP** | 1 周 | `pce_test_conductor/` 包 + 8 MCP tool + canary + classifier (无 LLM) | ~1500 行 |
| **P5.C.3 Nightly CI** | 1 周 | `.github/workflows/nightly-probe.yml` + auto-issue + 健康矩阵 SVG | 0 行代码, ~200 行 YAML |
| **P5.C.4 配置化 + AI 修复** | 2 周 | YAML adapter (3 站) + `propose_patch` LLM 调用 + `tools/repair_adapter.py` | ~800 行 |
| **P5.C.5 治理收口 + Cleanup** | 2 周 | CONTRIBUTING.md / CODEOWNERS / issue+PR templates / 14 个 .diag_*.py 归档 | 0 行代码, 文档 + 配置 |

**释放门(D0)**: 见 §6. 本 handoff 不要求一次跑完全部 sub-phase, 每个 sub-phase 都是可发布切片.

---

## 1. Phase transition summary

```
P5.A Subscription Capture (v1.0)              ✅ 浏览器切片 2026-05-08 硬冻结
    14 sites × T00-T20 / S0 三站稳态

P5.B IDE & Desktop & MCP (v1.1)               🟢 chat-region D0 释放门 ✅ 已过
    P5.B.0 MCP 资产 ✅
    P5.B.1 MCP middleware (L3f) ✅ alpha.1+2
    P5.B.2 CDP launcher (L3d) ✅ alpha.6
    P5.B.3 三主轴 alpha.8 ✅ (L3g 持久化 + L3h CLI wrap)
    P5.B.4 Claude Desktop chat 19/22 PASS ✅
    P5.B.5 Claude Desktop cowork    ✅ alpha.11 12/17 PASS (gate ≥80% met)
    P5.B.7 Claude Desktop code      ✅ alpha.14 + alpha.15 empirical 25/26 PASS

P5.C Meta-Pipeline & Maintainability (v1.1.5)  🟢 ACTIVE (本 handoff)
    P5.C.0 契约冻结 (本 commit)
    P5.C.1 健康度骨架
    P5.C.2 Test Conductor MVP (激活 ADR-017)
    P5.C.3 Nightly CI + auto-issue (激活 ADR-011 G3)
    P5.C.4 配置化 + AI 辅助修复 (激活 ADR-011 G9 / ADR-017 §3.5)
    P5.C.5 治理收口

P6 Coverage Polish (v1.2)                     ⏸ future (P5.C 后)
    L2 Frida + L4b AX + 剩余 D2 产品

P7+ Force Capture & Full Supervisor           ⏸ future (Pro)
```

**P5.B Claude Desktop 三 sub-region (chat / cowork / code) 全部已闭环** (chat 19/22, cowork 12/17, code 25/26, 三个 region 释放门全过). P5.C 把"基础设施治理"做扎实, P5.B 余项 (E10 default-mode UIA / E04 cancel-mid-stream normaliser bug) 收口归 P5.C.5.

> **Note (2026-05-12 14:00 update)**: 本 handoff §1 表格、§7.3、§7.4 在初稿时未追上 P5.B.5.5c (`267e8b1`...`0e7a50a`) 与 P5.B.7 alpha.14/.15 (`e3a2261`...`fe364f6`) 的最新 commit; 已在 kickoff commit 内同步修正. P5.B 余项轨道实质上已合流, P5.C 是后续唯一活跃阶段.

---

## 2. 接手 agent 阅读顺序 (~2.5 小时)

### Tier 1 必读 — 决议与战略 (45 min)

1. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md` — 本阶段的 "为什么". 9 章, ~10 min.
2. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` — 三层抽象 (capture / verification / health-as-data) 的工程级定义.
3. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` — 治理优于功能的决策固化.

### Tier 2 必读 — 跨 lane 标尺 (30 min)

4. `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` — 浏览器 T-case 标准 (已存在, 无改动).
5. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` — 桌面 D-case 标准 (本 commit 新增).
6. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md` — 健康度契约 (本 commit 新增).

### Tier 3 必读 — 已有 ADR 待激活 (45 min)

7. ⭐ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-017-test-conductor-cross-lane-agent-contract.md` — Test Conductor 8 MCP tool. **status 由 P5.C.2 推到 Adopted**.
8. `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-011-probe-remote-agent-autonomous-testing.md` — G3 (DOM watcher) + G9 (LLM auto-fix). **由 P5.C.3 + P5.C.4 落地**.
9. `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-018-msix-store-app-capture-strategy.md` — MSIX 三主轴模型, P5.C.1 健康度数据接入它的 raw_captures 流.

### Tier 4 跳读 — 历史预备 (30 min)

10. `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-IDE-DESKTOP-KICKOFF.md` §4-§11 — P5.B sub-phase 完成情况.
11. `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` — chat-region 19/22 evidence (D04 known bug + D08 deferred 是 P5.C.5 的 follow-up #1 / #2).
12. `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §2 (4 plane) — 跨 lane 公共词汇.

### Tier 5 实施时再查

- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py` — `BaseProbeSiteAdapter` (浏览器 lane adapter 雏形)
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\base.py` — `DesktopDriver` (桌面 lane driver 雏形)
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\execution_standard.py` — T-case 机器可读契约 (Python dataclass)
- `@f:\INVENTION\You.Inc\PCE Core\pce_core\db.py` — `raw_captures / sessions / messages / pipeline_errors` 表 (健康度数据底座)
- `@f:\INVENTION\You.Inc\PCE Core\pce_core\dashboard\` — dashboard 现状 (P5.C.1 加新视图的扩展点)

---

## 3. Target mental model — 从 plane-first 转到 lane-first

P5.B 时代的认知模型是 **plane-first** (来自 `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §2 的 4 plane × 6+1 type). 它解决的是 "PCE 在哪儿插管子".

**P5.C 时代要再加一个正交轴: lane-first**.

```
┌─────────────────────────────────────────────────────────────────┐
│  Plane axis   N (网络)  H (进程)  M (MCP)   U (UI)              │
│  (来自 P5.B)                                                    │
│                                                                  │
│  Lane axis    Browser   Desktop   CLI    MCP                    │
│  (P5.C 新增)  (L3a +    (L3b/d/g  (L3h)  (L3f)                  │
│               L1)       + L4b)                                  │
│                                                                  │
│  每个 lane 必须实现的同形契约 (Meta-Pipeline Framework §3):     │
│  - capture-as-data: CaptureEvent v2 (已存在, 不变)             │
│  - verification-as-data: T-case/D-case/C-case 标准 (本 commit)  │
│  - health-as-data: HealthBeacon v1 (P5.C.1 新增)                │
└─────────────────────────────────────────────────────────────────┘
```

**这意味着接手 agent 要问的问题不再是 "下一个产品是什么", 也不仅仅是 "下一个 plane 是什么"; 还要问 "下一个 lane 的 verification + health 契约是否齐了"**. 三件套不齐, 这个 lane 不算被纳入 Meta-Pipeline.

P5.B 已经把 plane × product 走通; P5.C 是把 lane × contract 显式化的阶段.

---

## 4. Sub-phases 详细

每个 sub-phase 都是 **releasable slice**. 完成一个 sub-phase 后, 项目能力会增加一个用户/维护者可感知的能力.

### P5.C.0 — 契约冻结 (1 周, 0 代码)

**Goal**: 把跨 lane 的所有公共契约从 "隐式存在于代码里" 转为 "显式存在于文档里", 让任何 agent 读完 Tier 1+2 文档就能开工.

**Deliverables (本 commit 已交付)**:
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` (本文件)
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md`
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\PROJECT.md` §11/§12 加 P5.C 段
- ✅ `@f:\INVENTION\You.Inc\PCE Core\Docs\README.md` 阶段对照表加 P5.C 行

**Acceptance**:
- [ ] 接手 agent 读完 Tier 1+2 (≤75 min) 能在 5 分钟内回答以下 4 题:
  - Q1: PCE 当前 4 lane 是哪 4 个? 每个 lane 的 plane 归属?
  - Q2: 一个 lane 要被纳入 Meta-Pipeline 必须实现哪 3 件契约?
  - Q3: 当 ChatGPT UI 改了, PCE 怎么知道 broken 了?
  - Q4: 下一个外部贡献者要修复一个 broken 浏览器适配器, 要按什么顺序操作?

**Non-goals**:
- 不动代码
- 不实现 health beacon 调用点 (那是 P5.C.1)
- 不实现 Test Conductor (那是 P5.C.2)

### P5.C.1 — 健康度骨架 (1 周)

**Goal**: 每个 lane 都开始上报健康度. dashboard 能看到 4 lane 的颜色.

**Deliverables**:
- `pce_core/health.py` (NEW) — `HealthBeacon` dataclass + `record_beacon()` + 聚合查询 (`current_health()`, `health_history(lane)`)
- `pce_core/db.py` — migration 0013: `health_beacons` 表 (`lane TEXT, layer TEXT, target TEXT, case_id TEXT, status TEXT, ts REAL, meta_json TEXT, dom_selector_hits TEXT`)
- `pce_core/server.py` — `POST /api/v1/health/beacon` 端点 + `GET /api/v1/health/matrix`
- `pce_core/dashboard/` — 新增 "适配健康" 页 (`health.html` + `health.js` + 接 `/api/v1/health/matrix`)
- 4 lane 接入点:
  - **Browser lane**: `pce_browser_extension_wxt/utils/capture-runtime.ts` 在 capture 成功/失败时 POST beacon
  - **Desktop lane**: `tests/e2e_desktop_ui/drivers/base.py` 在每个 D-case 开始/结束时 POST beacon
  - **CLI lane**: `pce_cli_wrapper/capture.py` 在每次 wrap 调用时 POST beacon
  - **MCP lane**: `pce_mcp_proxy/capture.py` 在每个 JSON-RPC frame 转发时 POST beacon
- `tests/test_health_beacon.py` (NEW) — 至少 8 测试 (CRUD + 聚合 + 4 lane 接入冒烟)

**Acceptance**:
- [ ] `migrate.py` 跑过, 表存在
- [ ] `POST /api/v1/health/beacon` 能接收 4 lane 的 beacon 写入
- [ ] dashboard `/dashboard/health` 显示 4 lane × 至少 1 target 的颜色矩阵
- [ ] 浏览器扩展 + 桌面 driver 至少各能产生 1 个真实 beacon
- [ ] 8 测试 GREEN

**Non-goals**:
- 不要求所有 case 接入 (P5.C.3 nightly CI 时再补全)
- 不实现 Sentry-style telemetry 上报到外部 (永远不做)

### P5.C.2 — Test Conductor MVP (1 周)

**Goal**: 激活 ADR-017. 任何 agent 通过 8 MCP tool 跑跨 lane 测试 + 拿失败分类.

**Deliverables (按 `ADR-017 §3` 三层契约实现)**:
- `pce_test_conductor/` (NEW package, OSS, Apache-2.0)
  - `__main__.py` — MCP stdio server entry
  - `server.py` — 8 个 tool 实现
  - `targets/` — 每个 target 一份 manifest (`browser_chatgpt.yaml` / `desktop_claude_chat.yaml` / `cli_claude_code.yaml` / `mcp_filesystem.yaml`)
  - `canaries/` — JSON Schema 快照存仓 (genson 生成 + jsonschema-diff 对比)
  - `classifier.py` — `FailureKind` 9 值 enum + 推断
  - `replay.py` — 离线 fixture 回放 (0.5s vs live 30s)
- `tests/test_conductor.py` (NEW) — 至少 16 测试 (8 tool × 2 path: success + fail)

**8 MCP tool (来自 ADR-017 §3.2)**:
1. `list_targets()` — 返回 `[{lane, target_id, plane}]`
2. `list_cases(target)` — 返回 `[{case_id, name, plane, applicability}]`
3. `run_case(target, case_id, mode="live"|"replay")` — 跑单 case, 返回 `run_id` + evidence paths
4. `get_run(run_id)` — 拿历史 run 详情
5. `diff_canary(target, case_id)` — JSON Schema diff vs 最近 known-good
6. `classify_failure(run_id)` — `FailureKind` enum + field_path + severity
7. `propose_patch(run_id)` — 返回 `list[PatchProposal]` (P5.C.2 阶段返回 stub, P5.C.4 实现 LLM 调用)
8. `verify_patch(target, case_id)` — 假设外部 agent 已落 diff, 重跑 case

**Acceptance**:
- [ ] Conductor MCP server 能被 Claude Desktop / Cascade 接入并列出 8 tool
- [ ] `run_case("browser_chatgpt", "T01")` 能跑通 `tests/e2e_probe/test_matrix.py::test_chatgpt[T01]` 并返回结构化结果
- [ ] `run_case("desktop_claude_chat", "D01")` 能跑通 `tests/e2e_desktop_ui/cases/p1_chat_window_a.py` (最小化变形)
- [ ] `classify_failure` 至少能正确分类 4 类 (LOGIN_WALL / UI_SELECTOR_MISS / SCHEMA_DRIFT / RACE_TIMEOUT)
- [ ] `diff_canary` 能在 ChatGPT response schema 发生 `enum_extension` 时输出 `severity=soft`
- [ ] 16 测试 GREEN

**Non-goals**:
- 不实现 `propose_patch` 的 LLM 调用 (P5.C.4)
- 不重写 `tests/e2e_probe/` 或 `tests/e2e_desktop_ui/` 任何现有 case (Conductor 是上层编排, 不替换底层)
- 不接入 P5.B 余项 (cowork-region C-case + code-region) — 那是 P5.B.5/.6 的事, Conductor 接入 case 列表会自动扩展

### P5.C.3 — Nightly CI + auto-issue (1 周)

**Goal**: GitHub Actions 每天跑 4 lane 核心 case, 失败自动开 issue. 项目所有者不主动看, 也能在客户报警前知道 broken.

**Deliverables**:
- `.github/workflows/nightly-probe.yml` (NEW) — UTC 凌晨 2 点 trigger, 跑 4 lane 核心 case, ~30 min budget
- `tools/render_health_matrix.py` (NEW) — 读 `health_beacons` 表 + canary diff 历史, 输出 `Docs/stability/HEALTH-MATRIX.svg` (commit 回仓)
- `tools/auto_issue_on_fail.py` (NEW) — Conductor 结果 → GitHub Issue (用 `gh issue create --label broken-adapter --assignee @CODEOWNERS`)
- `Docs/stability/HEALTH-MATRIX.svg` 在 README.md 顶部展示 (P5.C.5 时再加 README 链接)

**Acceptance**:
- [ ] Nightly workflow 在 GitHub Actions 跑过至少 1 次, 不报错
- [ ] 故意制造 1 个 broken case (改 ChatGPT 一个 selector), nightly 跑后能自动开 1 个 issue
- [ ] 健康矩阵 SVG 渲染正常 (能看到 4 lane × 至少 3 target 的颜色)
- [ ] Canary diff 在 ChatGPT 真实响应 enum 扩展时输出 PR review request

**Non-goals**:
- 不要求覆盖所有 case (S3 站 / D2 产品可以不在 nightly)
- 不要求实现 Sentry-style 客户端 telemetry (永远不做)

### P5.C.4 — 配置化 + AI 辅助修复 (2 周)

**Goal**: 把 selector 从代码抽到 YAML; 实现 `propose_patch` 的 LLM 调用. 单次维护成本从 30min 砍到 5min.

**Deliverables**:
- `pce_core/adapters/` (NEW) — YAML adapter 配置 (`chatgpt.yaml`, `claude.yaml`, `gemini.yaml`)
- `pce_core/adapter_loader.py` (NEW) — YAML → adapter Python 实例 (向后兼容现有 `BaseProbeSiteAdapter` 类属性)
- `tests/e2e_probe/sites/chatgpt.py` 等 3 个 — 重构为 "从 YAML 加载" + 站点特有逻辑 (~50 行/站, 从原 ~150-450 行降下来)
- `pce_test_conductor/patches/` — patch 模板 (selector_drift / new_content_block / sse_event_added 等 5 类)
- `pce_test_conductor/llm_repair.py` (NEW) — Anthropic / OpenAI API 调用 (用户 opt-in, API key 走 env), 输入 = (run evidence + DOM diff + 旧 selector), 输出 = unified diff + rationale + confidence
- `tools/repair_adapter.py` (NEW) — 本地 CLI 入口 `python -m tools.repair_adapter --target browser_chatgpt --case T01`

**Acceptance**:
- [ ] ChatGPT/Claude/Gemini 的 selector 完全从 YAML 加载, 删除 Python 类属性
- [ ] 故意改一个 ChatGPT selector, `propose_patch` 能输出至少 3 个 selector 候选 with confidence ≥ 0.7
- [ ] 所有现有 e2e_probe 测试在 YAML 化后仍 GREEN (无回归)

**Non-goals**:
- 不要求把 14 站全 YAML 化 (S3 站 P5.C.4 不动, 留 P5.C.5 或 P6)
- 不要求 LLM 100% 准确 — 只要 confidence + diff data 让 agent 能快速 review 即可
- 不要求把桌面端 UIA selector YAML 化 (那是 P6 范围)

### P5.C.5 — 治理收口 + Cleanup (2 周)

**Goal**: 让"任何贡献者都能修复一个 broken adapter"成为可执行流程, 并清理脆性残留.

**Deliverables**:
- `CONTRIBUTING.md` 重写 (重点: §"Fix a broken adapter" 章节, 含 step-by-step + 可粘贴命令)
- `CODEOWNERS` (NEW) — 每个 lane / 每个 D0/S0 target 一个 owner (即使 owner 是 @zstnbb 也比 nobody 强)
- `.github/ISSUE_TEMPLATE/` — `broken-adapter.yml` / `new-site-request.yml` / `health-degradation.yml`
- `.github/PULL_REQUEST_TEMPLATE.md` — 强制勾选 (跑了相关 lane 测试 / 提交了 canary 更新 / 更新了 owner)
- 根目录清理:
  - 14 个 `.diag_*.py` 全部归档到 `Docs/archive/diagnostics/2026-05-claude-desktop/` 或直接删除 (按 §7.1 清单)
  - `_inspect_window_*.py` 等 P5.B 残留迁到 `tests/e2e_desktop_ui/inspectors/`
  - `.tmp_*.py` / `.pytest_*.log` / `.triage_*.txt` / `tmp_c*_body.txt` 全部 gitignore 或删除
- `Docs/handoff/HANDOFF-P5C-COMPLETION-<date>.md` — 完工 handoff, 把所有 sub-phase 的 evidence 收口

**Acceptance**:
- [ ] 任何外部贡献者按 CONTRIBUTING.md "Fix a broken adapter" 章节, 在 ≤30min 内修复一个本地复现的 broken case
- [ ] 根目录 0 个 `.diag_*.py` / `.tmp_*.py` / `.triage_*.txt`
- [ ] CODEOWNERS 覆盖所有 D0/S0 target
- [ ] 所有 P5.C.0-.4 的 acceptance 项都已勾选
- [ ] D04 known bug (chat-region cancel-mid-stream, 来自 `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` §3) 修复或显式延后到 P6

---

## 5. 决策矩阵 — 接手 agent 第一周内必须读完, 拍板需要找项目所有者

来自决议 §6, 4 个决策点不阻塞 P5.C.0 (本 commit 已落), 但影响 P5.C.1+:

| 决策点 | 推荐 | 影响 | 拍板时机 |
|---|---|---|---|
| D-1: 健康度 dashboard 现在做 vs P5.C.1 才做 | **现在做(已经在 P5.C.1 deliverable 里)** | 决定 nightly probe 报警去哪儿被看到 | P5.C.1 启动前 |
| D-2: CONTRIBUTING + CODEOWNERS 现在 vs P5.C.5 | **P5.C.0 写 stub, P5.C.5 完善** | 决定社区贡献接入门槛 | P5.C.5 启动前 |
| D-3: "我有 API key" 兜底通道 | **加 (P5.C.4)** | 决定是否承认"无痕抓取不是唯一形态" | P5.C.4 启动前 |
| D-4: 配置化 (YAML) 优先级 | **P5.C.4 (不提前)** | 决定重构 3 个 S0 站点适配器的工作量 | 默认 |

接手 agent 接手第一周建议: 跑完 Tier 1+2 阅读 + Q1-Q4 自测 → 拿 D-1/D-3 给项目所有者拍板 → 启动 P5.C.1.

---

## 6. P5.C 整体释放门 (D0)

**整阶段 D0 释放门 (≥6/8 子项 PASS)**:

- [ ] 4 lane 全部接入 Test Conductor 8 MCP tool
- [ ] Nightly CI 连续 7 天 GREEN (允许 ≤2 day SKIP for known infra)
- [ ] 4 lane 全部上报 health beacon, dashboard 4 lane 颜色齐
- [ ] 至少 3 个站点 (ChatGPT/Claude/Gemini) selector 已 YAML 化
- [ ] 根目录 0 个 `.diag_*.py` / `.tmp_*.py`
- [ ] CONTRIBUTING.md "fix-a-broken-adapter" 至少被 1 个外部 reviewer 验证可读
- [ ] CODEOWNERS 覆盖所有 D0/S0 target
- [ ] ADR-017 status: Proposed → Adopted

D0 ≥6/8 通过则发 v1.1.5.

**整阶段 D1 释放门 (8/8 全过)**: 锁 v1.2 OK 启动. 否则 v1.2 用 v1.1.5 基线启动, 余项 carry forward.

---

## 7. Open follow-ups (priority-ordered)

### 7.1 P5.B 余项追溯 (P5.C.5 处理)

- **D04 known bug** (Claude Desktop chat cancel-mid-stream) — 来自 `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` §3. 1-2 天工作量, P5.C.5 修或显式延后到 P6.
- **D08 deferred** (Claude Desktop chat MCP tool) — 已并入 P5.B.5 cowork-region scope.

### 7.2 .diag_*.py 归档清单 (P5.C.5)

仓库根目录现有以下临时文件, 全部需要归档/删除:

```
.diag_auto_loop.py
.diag_chatgpt_chip.py / .log
.diag_chatgpt_chip2.py
.diag_chatgpt_chip3.py / .log
.diag_chatgpt_chip4.py / .log
.diag_chatgpt_chip5.py / .log
.diag_chatgpt_paste_main.py
.diag_chatgpt_upload_mark.log
.diag_claude_human_turn_dom.py
.diag_claude_image_chip.py
.diag_claude_input.py
.diag_claude_paste.py
.diag_grok_chip.py
.diag_main_world_steps.py
.diag_pce_core_server.err.log / .out.log
.diag_pce_core_state.py
.diag_pipeline_state.py
.diag_probe_attach.py
.diag_profile_state.py / .diag_profile_state_v2.py
.diag_run_chatgpt.log / _v2.log
.diag_run_claude.log / _v2.log
.diag_smoke_paste.log
.pytest_gas_t10_retry.err.log / .log
.pytest_gas_t10_t11.err.log / .log
.pytest_t00_first_light.log
.pytest_t01_first_light.log
.sessions_openai.json
.tmp_dump_summary.py
.tmp_inspect_gas.py
.triage_t01.txt
tmp_c06_completion_body.txt
tmp_c15_completion_body.txt
tmp_c17_body.txt
```

策略:
- 可重用诊断逻辑 → 迁到 `tools/` 或 `tests/e2e_desktop_ui/inspectors/`
- 一次性日志 → 加入 `.gitignore` (如 `.diag_*` / `.pytest_*.log` / `.triage_*.txt`) 然后删
- 历史 evidence 想留 → 移到 `Docs/archive/diagnostics/2026-05-claude-desktop/`

### 7.3 P5.B.5 cowork-region 已闭环 (closed 2026-05-11 prior to this kickoff)

P5.B.5.5c alpha.11 (`267e8b1`...`0e7a50a`) 已交付: cowork PASS 12 / SKIP 5 / FAIL 0 across C00-C16 (gate ≥80% 已过). 17 个 C-case 的 verification 实现已落 `tests/e2e_desktop_ui/run_p2_cowork_sweep.py` + walker. 在 P5.C.2 Test Conductor 落地后**自动**纳入 `list_cases` 输出 (因为 conductor 调用 pytest collection API).

已知 carry-forward 到 P5.C.5: 5 个 SKIP 中包含 C13 / C14 / C16 etc. 的环境/账号约束, 不算 capture-pipeline FAIL.

### 7.4 P5.B.7 Claude Desktop code-region 已闭环 (closed 2026-05-12 prior to this kickoff)

P5.B.7 alpha.14 (`e3a2261`...`2a71407`, code-region inline Code-tab walker + tests) + alpha.15 empirical live-sweep (`fe364f6`) 已交付: code 25 PASS / 1 SKIP / 0 FAIL across E00-E25 (gate cleared with 13 PASS margin). 26 个 E-case 的 verification 实现已落 `tests/e2e_desktop_ui/run_p1_code_sweep.py`.

> 注: handoff 初稿用 P5.B.6 编号; 实际 git history 用 P5.B.7. P5.B.6 是中间被并入或跳过的 placeholder, 不再使用.

已知 carry-forward 到 P5.C.5:
- **E10**: default-mode permission-dialog UIA names 待 RECON (当前 sweep 用 `permissionMode=acceptEdits` 不触发该弹窗; §5.C.2 Q2 open).
- **E04 / D04 同源 bug**: cancel-mid-stream 的 downstream normaliser 漏处理 request-only 路径, 与 chat-region D04 同源, P5.C.5 一并处理或显式延后到 P6.

### 7.5 桌面 lane Pro modules 治理 (Pro repo)

P5.C 阶段 OSS 侧契约冻结后, Pro repo (`github.com/zstnbb/pce-pro`) 的 L0/L2/L3b/L4b 也必须实现 `AdapterContract` + `HealthBeacon` 才能进 Test Conductor. **CI 边界规则不变**: OSS 永不 import Pro, Pro 可 import OSS.

---

## 8. Cross-references

### 决议链
- 上游决议: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-04-18-ucs-and-release-strategy.md` (UCS + Open Core)
- 本阶段决议: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md`

### ADR 链
- 已存 (本阶段激活): `ADR-011`, `ADR-017`, `ADR-018`
- 本阶段新增: `ADR-019` (本 commit)

### 设计文档
- 既有: `UNIVERSAL-CAPTURE-STACK-DESIGN.md`, `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`, `ARCHITECTURE.md`
- 本阶段新增: `META-PIPELINE-FRAMEWORK.md`

### Standard 文档
- 既有: `PCE-PROBE-E2E-EXECUTION-STANDARD.md`, `DESKTOP-PRODUCT-MATRIX.md`, `SITE-TIER-MATRIX.md`
- 本阶段新增: `DESKTOP-D-CASE-EXECUTION-STANDARD.md`, `PCE-PIPELINE-HEALTH-MATRIX.md`

### Handoff 链 (active position)
- P5.A: `HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md` ✅ frozen
- P5.B: `HANDOFF-IDE-DESKTOP-KICKOFF.md` 🟡 carry on (cowork + code 余项)
- P5.B chat-region 完工: `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` ✅
- P5.B cowork-region kickoff: `HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md` 🟡 standard-down done, impl待启
- **P5.C ACTIVE**: 本 handoff
- P5.C 完工 (待写): `HANDOFF-P5C-COMPLETION-<date>.md`

---

## 9. 一句话锚点 (重申)

**PCE 不是"做更多的捕获前端", 是"在 4 个 lane 上把已经成型的捕获 + 验证 + 健康度三件套显式化为可被任何 agent 调用、可被任何贡献者扩展、可被官方应用迭代而不崩的统一管道". P5.C 是这个目标的实施阶段.**

接手 agent 拿到本 handoff 后的 first action:

```powershell
# 1. 阅读 Tier 1+2 (~75 min)
# 2. 自测 §4.0 P5.C.0 Acceptance 的 Q1-Q4
# 3. 找项目所有者拍 D-1 / D-3 决策
# 4. 启动 P5.C.1 — 创建 pce_core/health.py
```

End of handoff.
