# Wave 5 — Phase A Mid-gate + Phase B Kickoff (Day 5, ~12h, STRICT MODE)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.1
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §5
>
> **目标**: G3 落地 — 让本冲刺的成果**自己监控自己**, 否则一周后回到
> 不可知状态. **Wave 5 不发 v1.1.6 tag** — STRICT MODE 13/13 才发,
> tag 推到 [`09-wave9-final-closure.md`](09-wave9-final-closure.md).
> 本 wave 只做 Phase A 关闭 + Phase B 三 track kickoff.

---

## 1 · 任务矩阵

| 任务 ID | 工时 | 内容 |
|---|:---:|---|
| **W5-T1** | 2h | `tools/render_redundancy_matrix.py` — 读 supervisor status (13 场景) + REDUNDANCY-AUDIT §3 → 生成 `Docs/stability/REDUNDANCY-MATRIX.svg`; 同 P5.C.3 nightly workflow 提交回仓 |
| **W5-T2** | 1h | `.github/workflows/nightly-probe.yml` 增 1 个 step: 跑 supervisor `/status` 检查所有 P0 redundancy_target 是否满足; 不满足 → auto-issue 标 `redundancy-degraded` |
| **W5-T3** | 1h | `.github/ISSUE_TEMPLATE/redundancy-degraded.yml` 新增 |
| **W5-T4** | 2h | `tools/repair_adapter.py` 兼容 supervisor — 当 status=impaired 时优先建议哪条 leg 修 |
| **W5-T5** | 2h | `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3 主表统一更新 (13 行); §4 汇总表刷新; §7.3 cadence 加一条 "本冲刺新增 nightly 自动核对" |
| **W5-T6** | 2h | Phase A mid-gate handoff `HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md`; Phase B 3 track kickoff handoff (W6/W7/W8 各一份) |
| **W5-T7** | 2h | CHANGELOG 一段 "P5.D.1 Phase A — Tier-α Closed" 内部 milestone (注: **不 tag**, 等 13/13 全绿) |

**总:** 12h.

---

## 2 · W5-T1 — REDUNDANCY-MATRIX.svg 渲染 (13 cards)

### 2.1 工具

`tools/render_redundancy_matrix.py` (~180 LOC).

输入:
- `GET /api/v1/supervisor/status` (运行时数据, 13 scenarios)
- `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3 (静态权威)

输出: `Docs/stability/REDUNDANCY-MATRIX.svg`.

### 2.2 SVG 布局 (13 卡片)

```
┌──────────────────────────────────────────────────────────────┐
│  PCE Redundancy Matrix — 2026-05-19 14:30                    │
├──────────────────────────────────────────────────────────────┤
│  P0 Scenarios (target ≥3 legs)                               │
│                                                              │
│  Web 5:                                                      │
│    ChatGPT Web         L1●  L3a●  L3d●           🟢 redundant│
│    Claude Web          L1●  L3a●  L3d●           🟢 redundant│
│    Gemini Web          L1●  L3a●  L4a●           🟢 redundant│
│    Google AI Studio    L1●  L3a●  L4a●           🟢 redundant│
│    Grok Web            L1●  L3a●  L4a●           🟢 redundant│
│                                                              │
│  Desktop 8 (P1-P8):                                          │
│    P1 Claude Desktop   L1●  L3g●  L3f●           🟢 redundant│
│    P2 ChatGPT Desktop  L1●  A2◐  L4b○            🟠 impaired│  ← Phase B-1
│    P3 Cursor           L1○  L3f○  L4a○           🔴 down     │  ← Phase B-2
│    P4 Windsurf         L1●  L3f●  L4a●           🟢 redundant│
│    P5 GitHub Copilot   L1○  L3c○  L3f○           🔴 down     │  ← Phase B-3
│    P6 Claude Code CLI  L1●  L3g●  L3h●           🟢 redundant│
│    P7 Codex CLI        L1●  L3g●  L3h●           🟢 redundant│
│    P8 Gemini CLI       L1●  L3g●  L3h●           🟢 redundant│
│                                                              │
│  Phase A status: 10/13 redundant. Phase B in progress.      │
│  Legend: ● green  ◐ degraded  ○ down  · not configured      │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 nightly workflow 集成

`.github/workflows/nightly-probe.yml` 加 step:

```yaml
- name: Render redundancy matrix
  run: |
    python tools/render_redundancy_matrix.py \
      --status-url http://127.0.0.1:9800/api/v1/supervisor/status \
      --audit-md Docs/stability/REDUNDANCY-AUDIT-MATRIX.md \
      --out Docs/stability/REDUNDANCY-MATRIX.svg

- name: Commit matrix back to repo
  uses: stefanzweifel/git-auto-commit-action@v5
  with:
    commit_message: "chore: nightly redundancy matrix update"
    file_pattern: Docs/stability/REDUNDANCY-MATRIX.svg
```

---

## 3 · W5-T2 — Auto-issue on Redundancy Degradation

### 3.1 trigger 条件

任一 P0 场景 `legs_active < redundancy_target`:

- `legs_active = 2` (target=3) → status=`minimal` → **YELLOW**, 不开 issue, SVG 标黄
- `legs_active ≤ 1` (target=3) → status=`impaired` 或 `down` → **RED**, 开 issue

**Phase B 期间例外**: P2 / P3 / P5 三场景在 Phase B 关闭前预期为 down /
impaired, 不触发 auto-issue (用 `phase_b_in_progress` flag 抑制).
Phase B 关闭后此 flag 清除.

### 3.2 nightly workflow step

```yaml
- name: Check redundancy targets
  id: redundancy_check
  run: |
    python tools/check_redundancy_targets.py \
      --status-url http://127.0.0.1:9800/api/v1/supervisor/status \
      --threshold red \
      --suppress-phase-b-scenarios f4_p2_chatgpt_desktop,f5_p3_cursor,f5_p5_github_copilot \
      --output redundancy_alerts.json
  continue-on-error: true

- name: Open auto-issues for redundancy degradation
  if: steps.redundancy_check.outputs.alert_count > 0
  run: |
    python tools/auto_issue_on_redundancy_degraded.py \
      --alerts redundancy_alerts.json \
      --label redundancy-degraded
```

`tools/check_redundancy_targets.py` (~100 LOC) — 输出 JSON 给下一步.

`tools/auto_issue_on_redundancy_degraded.py` (~120 LOC) — 复用 P5.C.3
auto_issue_on_fail.py 的 dedup 逻辑 (24h 内同场景不重复开 issue).

---

## 4 · W5-T3 — `redundancy-degraded.yml` Issue Template

```yaml
# .github/ISSUE_TEMPLATE/redundancy-degraded.yml
name: 🔴 Redundancy Degradation
description: A P0 scenario has fewer V-GREEN legs than its redundancy target.
title: "[redundancy-degraded] {scenario_id}: {legs_active}/{redundancy_target} legs"
labels: ["redundancy-degraded", "auto-detected"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        Auto-filed by `tools/auto_issue_on_redundancy_degraded.py`.
        See `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` for canonical state.
  - type: input
    id: scenario_id
    attributes:
      label: Scenario ID
    validations:
      required: true
  - type: dropdown
    id: status
    attributes:
      label: Current status
      options: [redundant, minimal, impaired, down]
    validations:
      required: true
  - type: input
    id: legs_active
    attributes:
      label: legs_active / redundancy_target
    validations:
      required: true
  - type: textarea
    id: failed_legs
    attributes:
      label: Which legs are failing?
      description: per-leg health snapshot
    validations:
      required: true
  - type: textarea
    id: last_pass_per_leg
    attributes:
      label: Last PASS per leg
    validations:
      required: false
  - type: textarea
    id: recommended_repair
    attributes:
      label: Recommended repair (auto-generated)
      description: tools/repair_adapter.py output if applicable
    validations:
      required: false
```

---

## 5 · W5-T4 — repair_adapter.py 兼容 supervisor

### 5.1 改动

P5.C.4.3 的 `tools/repair_adapter.py` 当前从 conductor 失败 run 出发.
W5-T4 加一条新路径:

```bash
python -m tools.repair_adapter --redundancy-degraded f4_p1_claude_desktop --json
```

效果:
- 查 supervisor /status 拿到 f4_p1_claude_desktop 的 fail leg
- 把"哪条 leg 挂了"映射到该 leg 对应的 conductor case (per scenarios.yaml)
- 触发现有 LLM repair 流程

`tools/repair_adapter.py` 新增 ~60 LOC.

---

## 6 · W5-T5 — REDUNDANCY-AUDIT-MATRIX 主表统一更新 (13 行)

### 6.1 §3 主表行格式

每行末尾加列 `Sprint Reference`:

```markdown
| # | Path | 依赖基底 | 代码 | 最新 Live 验证 | 等级 | Sprint Ref |
|---|---|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | 页面 DOM + fetch shim | ... | ... | V-GREEN | (existing) |
| 2 | L1 MITM | 系统代理 + CA | ... | HANDOFF-W1-T4 2026-05-14 | V-GREEN | **W1-T4** |
| 3 | L3d CDP | Chromium Debug | ... | HANDOFF-W4-T1 2026-05-14 | V-GREEN | **W4-T1** |
```

### 6.2 §4 汇总表 (13 行)

```markdown
| 场景 | V-GREEN 数 (pre-sprint) | V-GREEN 数 (Phase A end) | V-GREEN 数 (Phase B end) | 达标? |
|---|:---:|:---:|:---:|:---:|
| F1 ChatGPT Web              | 2 | 3 | 3 | ✅ |
| F1 Claude Web               | 2 | 3 | 3 | ✅ |
| F1 Gemini Web               | 1 | 3 | 3 | ✅ |
| F1 GAS                      | 1 | 3 | 3 | ✅ |
| F1 Grok Web                 | 1 | 3 | 3 | ✅ |
| F4 P1 Claude Desktop        | 2 | 3 | 3 | ✅ |
| F4 P2 ChatGPT Desktop       | 1 | 1 (+A2) | 3 (+L4b) | ⏳ Phase B |
| F5 P3 Cursor                | 0 | 1 (MCP) | 3 (+protobuf+L4a) | ⏳ Phase B |
| F5 P4 Windsurf              | 1 | 3 | 3 | ✅ |
| F5 P5 GitHub Copilot        | 0 | 0 | 3 (+L1+L3c+L3f) | ⏳ Phase B |
| F6 P6 Claude Code CLI       | 1 | 3 | 3 | ✅ |
| F6 P7 Codex CLI             | 0 | 3 | 3 | ✅ |
| F6 P8 Gemini CLI            | 0 | 3 | 3 | ✅ |
```

### 6.3 §7.3 cadence 加新条目

```markdown
- **每夜 02:00 UTC** → nightly workflow 跑 supervisor /status check, 自动核对
  P0 13 场景 ≥ redundancy_target; 不达标自动开 redundancy-degraded issue;
  Phase B 期间 P2/P3/P5 抑制
```

---

## 7 · W5-T6 — Phase A mid-gate handoff + Phase B kickoff

### 7.1 Phase A mid-gate handoff

`Docs/handoff/HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md`:

```markdown
---
title: "P5.D.1 Phase A Mid-gate — Tier-α 10/10 V-GREEN"
status: PASS
date: 2026-05-19
predecessor: HANDOFF-P5D1-KICKOFF-2026-05-14.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
artifacts:
  - 14 handoff files (W1×11, W2×1, W3×1, W4×8, W5×1)
  - ~2000 LOC new code
  - 47+ new tests
  - ADR-021 Adopted, ADR-018 Phase 5 Closed
  - **NO tag yet (waiting on Phase B 13/13)**
---

## TL;DR
P5.D.1 Phase A closed. 10/13 P0 scenarios ≥ 3 V-GREEN (Tier-α).
Capture Supervisor v1 alive (13 scenarios in /status).
Phase B kicked off — 3 tracks running parallel for P2/P3/P5.

## Phase A Goals (G1/G2/G3 partial)
- G1 Tier-α: 10/10 ✅
- G1 Tier-β: 0/3 (Phase B in progress)
- G2: /api/v1/supervisor/status returns 13 scenarios
- G3: REDUNDANCY-MATRIX.svg renders 13 cards (3 显黄/红 by design)

## What's NOT done (carry to Phase B)
- F4 P2 ChatGPT Desktop → L4b UIA (Wave 7, 3-4 周)
- F5 P3 Cursor → protobuf + L4a (Wave 8, 4-6 周)
- F5 P5 GitHub Copilot → L1 + L3c + L3f (Wave 6, 1-2 周)

## ADR closures (this phase)
- ADR-021 Adopted (capture_supervisor OSS boundary)
- ADR-018 Phase 5 Closed (SSLKEYLOGFILE production)

## ADR pending Phase B
- ADR-022 (UIA accessibility OSS boundary)
- ADR-023 (gRPC-web protobuf decoder)

## Verification
git log <kickoff>..<midgate> --oneline
python -m pytest tests/test_capture_supervisor.py tests/test_keylog_mode.py -v
curl http://127.0.0.1:9800/api/v1/supervisor/status | jq '[.scenarios[] | select(.status == "redundant")] | length'
# Expected: 10
```

### 7.2 Phase B kickoff handoffs (3 份)

- `Docs/handoff/HANDOFF-W6-COPILOT-KICKOFF-2026-05-19.md` →
  `06-wave6-copilot-l3c.md` 任务展开
- `Docs/handoff/HANDOFF-W7-CHATGPT-DESKTOP-UIA-KICKOFF-2026-05-19.md` →
  `07-wave7-chatgpt-desktop-uia.md`
- `Docs/handoff/HANDOFF-W8-CURSOR-PROTOBUF-KICKOFF-2026-05-19.md` →
  `08-wave8-cursor-protobuf.md`

每份含: track owner / 周历 / 风险 / acceptance link.

---

## 8 · W5-T7 — CHANGELOG mid-milestone (NOT tag)

### 8.1 CHANGELOG entry (Phase A end, internal milestone)

```markdown
## [Unreleased] 2026-05-19 P5.D.1 Phase A — Tier-α Closed

> Internal milestone, NOT a release tag. Tag v1.1.6 will be issued
> only after Phase B 13/13 closure (see HANDOFF-P5D1-FINAL-CLOSING-...).

### Added
- `pce_core/capture_supervisor/` — dedup, policy, status, api
  (ADR-021 Adopted)
- `pce_proxy/keylog_mode.py` + `pce_core/cert_wizard/keylog.py` —
  SSLKEYLOGFILE production (ADR-018 Phase 5 closed)
- 13 P0 scenarios in `scenarios.yaml`; 10 reaching ≥ 3 V-GREEN
  (Tier-α complete)
- `tools/render_redundancy_matrix.py` + `auto_issue_on_redundancy_degraded.py`
- `pce_core/normalizer/openai.py` (Codex CLI host)
- `pce_core/normalizer/google_genai.py` (Gemini CLI host)
- 47+ new unit tests
- Nightly workflow step: redundancy check + auto-issue (Phase B
  scenarios suppressed)
- `.github/ISSUE_TEMPLATE/redundancy-degraded.yml`

### Changed
- `scripts/check_import_direction.py` — `pce_core.capture_supervisor`
  removed from PRO_MODULES (per ADR-021)
- `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3 — 13 rows;
  10 upgraded to V-GREEN; §4 summary table refreshed; §7.3 cadence updated

### Pending Phase B
- F4 P2 ChatGPT Desktop L4b UIA (Wave 7, ADR-022 pending)
- F5 P3 Cursor protobuf decoder (Wave 8, ADR-023 pending)
- F5 P5 GitHub Copilot L1 + L3c + L3f (Wave 6)

### Phase A metrics
- ~88 hours (estimated) / 6 working days
- 10/13 P0 scenarios reach G1 acceptance
- G2 runtime: dedup window 30s, degrade detection ≤30s
- G3 visibility: nightly SVG (13 cards) + auto-issue
```

### 8.2 NOT git tag

```
DO NOT tag v1.1.6 here. STRICT MODE — 13/13 全绿才发.
Tag 在 09-wave9-final-closure.md §8.2 完成.
```

---

## 9 · 验收门 (Wave 5 结束 = Phase A end)

```
[ ] nightly workflow 跑过 1 轮 (手触发) → REDUNDANCY-MATRIX.svg
    生成 + 13 张卡片显示 (10 绿 / 3 黄红)
[ ] 模拟一条 leg degraded → auto-issue 自动开 (非生产仓内 dry-run)
[ ] CHANGELOG + 本文件 + REDUNDANCY-AUDIT §3 三处一致
[ ] HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md 写好待 commit
[ ] Phase B 3 个 kickoff handoff (W6/W7/W8) 写好待 commit
[ ] **NO git tag** — STRICT MODE 13/13 才发
```

完成后:
- 进入 Phase B 三 track 并行: [`06-wave6-copilot-l3c.md`](06-wave6-copilot-l3c.md) +
  [`07-wave7-chatgpt-desktop-uia.md`](07-wave7-chatgpt-desktop-uia.md) +
  [`08-wave8-cursor-protobuf.md`](08-wave8-cursor-protobuf.md)
- Phase B 全闭后进入 [`09-wave9-final-closure.md`](09-wave9-final-closure.md)
- 最终对照 [`99-acceptance-gate.md`](99-acceptance-gate.md) 做 13/13 核对
