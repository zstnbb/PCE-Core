# Wave 9 — Final Closure + tag v1.1.6 (Phase C, ~16h, 2 工日)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.3
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §6
>
> **目标**: STRICT MODE 13/13 P0 场景全严格 ≥3 V-GREEN. **此前不发
> v1.1.6 tag**. 本 Wave 全量验收 + 关闭 sprint + tag.
>
> **触发条件**: Phase B 三 track (W6 / W7 / W8) 全部 PASS.
>
> **不允许**: 12/13 + carry-forward 妥协. 任一场景未达 → release 顺移.

---

## 1 · 任务矩阵

| 任务 ID | 工时 | 内容 |
|---|:---:|---|
| **W9-T1** | 2h | 全量 13/13 supervisor /status verify; 截图存 `Docs/handoff/_evidence_W9_2026-07-06/status_13_redundant.png` |
| **W9-T2** | 2h | 跑 nightly workflow 1 轮手触发: SVG 生成 + 13 全绿 + 0 redundancy-degraded auto-issue |
| **W9-T3** | 2h | REDUNDANCY-AUDIT-MATRIX §3 §4 全表最终对齐 (13 行全 V-GREEN) |
| **W9-T4** | 2h | ADR-022 / ADR-023 status: Proposed → Adopted (owner sign-off) |
| **W9-T5** | 2h | UCS 附录 B 口径声明 finalize (Pre-flight §2.2 准备的编辑) |
| **W9-T6** | 2h | CHANGELOG `## [v1.1.6] 2026-07-06` 完整 entry; 含 Phase A + Phase B 全部成果 |
| **W9-T7** | 2h | `HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md` 写好 |
| **W9-T8** | 2h | git tag v1.1.6 (注: 必须 13/13 全绿, 由 W9-T1 验过) |

**总:** 16h, 2 工日.

---

## 2 · W9-T1 — 全量 13/13 verify

### 2.1 SOP

```powershell
# 1. 启动完整环境
python -m pce_core.server --port 9800

# 2. 启动所有 leg
Start-Process pce_proxy -ArgumentList @("--port=8080")
Start-Process pce_persistence_watcher
Start-Process pce_mcp_proxy
# ... + Wave 6/7/8 新增的 leg (L3c VS Code ext / UIA driver / Cursor protobuf)

# 3. 触发每个场景 1 个最小 prompt
foreach ($scenario in $scenarios_13) {
    Invoke-MinimalPrompt -Scenario $scenario
}

# 4. 等 30s

# 5. 拉 supervisor /status
$status = curl http://127.0.0.1:9800/api/v1/supervisor/status | ConvertFrom-Json

# 6. 验 13 全 redundant
$redundant = ($status.scenarios | Where-Object { $_.status -eq "redundant" }).Count
if ($redundant -ne 13) {
    Write-Error "Only $redundant/13 redundant — Phase B not closed, ABORT release"
    exit 1
}

# 7. 截图存证
Save-Screenshot -Path Docs/handoff/_evidence_W9_2026-07-06/status_13_redundant.png

# 8. SQLite 全量快照
Copy-Item <pce_data_dir>\pce.db Docs/handoff/_evidence_W9_2026-07-06/final.db
```

### 2.2 验收

- 13 个 scenarios 全部 status=`redundant` color=`green`
- 截图 + SQLite snapshot 进 `_evidence_W9_2026-07-06/`

---

## 3 · W9-T2 — Nightly workflow 1 轮跑过

```powershell
# 触发 nightly workflow 手动 run
gh workflow run nightly-probe.yml

# 等待完成 (5-10 min)
gh run watch

# 验:
# - REDUNDANCY-MATRIX.svg commit 回仓 (13 全绿)
# - 0 个新 redundancy-degraded issue 开
# - check_import_direction.py 跑过 (含 capture_supervisor 在 OSS)
```

---

## 4 · W9-T3 — REDUNDANCY-AUDIT-MATRIX 最终对齐

### 4.1 §3 主表 13 场景行验证

每场景行 §3 必须含:

```markdown
| 场景 | Leg 1 | Leg 2 | Leg 3 | 等级 | Sprint Ref |
|---|---|---|---|---|---|
| F1 ChatGPT Web | L1 V-GREEN | L3a V-GREEN | L3d V-GREEN (W4-T1) | ✅ 3/3 | (chain) |
| F1 Claude Web | ... | ... | ... (W4-T2) | ✅ 3/3 | |
| F1 Gemini Web | ... | ... | L4a V-GREEN (W4-T3) | ✅ 3/3 | |
| F1 GAS | ... | ... | L4a V-GREEN (W4-T3) | ✅ 3/3 | |
| F1 Grok Web | ... | ... | L4a V-GREEN (W4-T3) | ✅ 3/3 | |
| F4 P1 Claude Desktop | L1 V-GREEN | L3g V-GREEN | L3f V-GREEN (W1-T1) | ✅ 3/3 | |
| F4 P2 ChatGPT Desktop | L1 V-GREEN | A2 V-GREEN (corrob.) | L4b V-GREEN (W7-T2) | ✅ 3/3* | * A2 算 0.5, 但 L4b 是真第 3 |
| F5 P3 Cursor | L1 V-GREEN (W8-T1) | L3f V-GREEN (W8-T2) | L3b/L4a V-GREEN (W8-T3) | ✅ 3/3 | |
| F5 P4 Windsurf | L1 V-GREEN | L3f V-GREEN (W1-T7) | L4a V-GREEN (W4-T5) | ✅ 3/3 | |
| F5 P5 GitHub Copilot | L1 V-GREEN (W6-T1) | L3c V-GREEN (W6-T2) | L3f V-GREEN (W6-T3) | ✅ 3/3 | |
| F6 P6 Claude Code CLI | L1 V-GREEN (W4-T6) | L3g V-GREEN | L3h V-GREEN (W1-T3) | ✅ 3/3 | |
| F6 P7 Codex CLI | L1 V-GREEN (W4-T7) | L3g V-GREEN (W1-T9) | L3h V-GREEN (W1-T8) | ✅ 3/3 | |
| F6 P8 Gemini CLI | L1 V-GREEN (W4-T8) | L3g V-GREEN (W1-T11) | L3h V-GREEN (W1-T10) | ✅ 3/3 | |
```

### 4.2 §4 汇总表 final 列

```markdown
| 场景 | pre | Phase A | Phase B | final | 达标 |
|---|:---:|:---:|:---:|:---:|:---:|
| 13 行同 W5-T5 §6.2 | ... | ... | ... | 3 | ✅ |
```

13/13 ✅.

---

## 5 · W9-T4 — ADR-022 / ADR-023 Adopted

```diff
# ADR-022-uia-accessibility-oss-boundary.md
- Status: Proposed
+ Status: Adopted (2026-07-XX, owner sign-off, MVP shipped W7)

# ADR-023-grpc-web-protobuf-decoder.md
- Status: Proposed
+ Status: Adopted (2026-07-XX, owner sign-off, MVP shipped W8)
```

owner 在 PR 上 review 后合.

---

## 6 · W9-T5 — UCS 附录 B 口径声明 finalize

`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` 附录 B 顶
部加:

```markdown
> **口径声明 (2026-07-XX, P5.D.1 STRICT MODE 关闭锚定):**
> 本表回答"设计上能抓", 不回答"验证过能抓". 后者请回
> `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.
> 任何"PCE 已覆盖 X 场景"的对外陈述以 §3 V-GREEN 数为准.
> P5.D.1 13 P0 场景全部 ≥ 3 V-GREEN, 由 v1.1.6 release 锚定.
```

---

## 7 · W9-T6 — CHANGELOG v1.1.6 完整 entry

```markdown
## [v1.1.6] 2026-07-06 — P5.D.1 Redundancy Sprint Complete (STRICT MODE)

### 概要

13 P0 场景 (5 Web + 8 Desktop) 全部严格 ≥3 V-GREEN 不相互依赖路线.
Capture Supervisor v1 alive (dedup + status + policy + degrade).
Nightly auto-redundancy-check operational. 不接受 12/13 妥协.

### Phase A (Tier-α 10 场景, 2026-05-14 → 2026-05-19)

#### Added
- `pce_core/capture_supervisor/` — dedup, policy, status, api
  (ADR-021 Adopted)
- `pce_proxy/keylog_mode.py` + `pce_core/cert_wizard/keylog.py`
  (ADR-018 Phase 5 Closed)
- `pce_core/normalizer/openai.py` (Codex CLI host)
- `pce_core/normalizer/google_genai.py` (Gemini CLI host)
- 13 P0 scenarios in `scenarios.yaml`
- `tools/render_redundancy_matrix.py` +
  `auto_issue_on_redundancy_degraded.py`
- 47+ new unit tests
- migrations 0014 (clipboard) + 0015 (codex/gemini source)

### Phase B (Tier-β 3 场景, 2026-05-19 → 2026-07-06)

#### Track-1 GitHub Copilot
- `pce_core/normalizer/github_copilot.py` — L1 host normalizer
- `vscode_ext/copilot/` — L3c VS Code 扩展 (ADR-022 referenced)
- migration 0016 (VS Code Copilot source)
- 20 new unit tests

#### Track-2 ChatGPT Desktop UIA (ADR-022 Adopted)
- `pce_core/uia_driver/` — Windows UIA accessibility driver
- migration 0017 (UIA source)
- 28 new unit tests

#### Track-3 Cursor protobuf (ADR-023 Adopted)
- `pce_core/normalizer/protobuf/` — generic gRPC-web framing + decoder
- `pce_core/normalizer/cursor_protobuf/` — Cursor-specific schema
- L3b preload OR L4a clipboard for Cursor leg 3
- migration 0018 (Cursor source)
- 60+ new unit tests

### Phase C (Final closure, 2026-07-06)

#### Closed
- 4 new ADRs: ADR-021 + ADR-022 + ADR-023 Adopted; ADR-018 Phase 5
  Closed
- ~30 handoff files committed under `Docs/handoff/HANDOFF-W*-2026-*`
  + `_evidence_W*_2026-*/` SQLite snapshots
- `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3: 13 行全 V-GREEN
- `Docs/stability/REDUNDANCY-MATRIX.svg`: 13 张卡片全绿 nightly
  rendered

### Sprint metrics

- 总 wall-clock: ~7-8 周 (Phase A 1 周 + Phase B 6 周 + Phase C
  紧贴)
- 总工时 estimated: ~250h
- ~5000 LOC new code
- 70+ new unit tests
- G1 13/13 ≥ 3 V-GREEN (STRICT)
- G2 dedup proven, degrade ≤30s, 13 scenarios in supervisor /status
- G3 nightly SVG (13 cards) + auto-issue channel alive

### 标杆性

P5.D.1 是 PCE redundancy 体系的首个完整对齐标杆. 后续每个新场景
(P5.D.2 / P6 / ...) 接入按本 release 立的"3 leg + 独立 basis +
dedup + status + nightly check"模板执行.
```

---

## 8 · W9-T7 — Final closing handoff

`Docs/handoff/HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md`:

```markdown
---
title: "P5.D.1 Redundancy Sprint — FINAL CLOSING (STRICT MODE)"
status: PASS
date: 2026-07-06
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
release_tag: v1.1.6
artifacts:
  - 30+ handoff files (W1×11, W2×1, W3×1, W4×8, W5×1, W6×3, W7×2, W8×3, W9×1, midgate, kickoff×4)
  - ~5000 LOC new code
  - 70+ new tests
  - 4 ADR closures: ADR-018 Phase 5, ADR-021, ADR-022, ADR-023
  - tag v1.1.6 issued
---

## TL;DR
P5.D.1 STRICT MODE closed. 13/13 P0 scenarios ≥ 3 V-GREEN. v1.1.6
released.

## G1 (Coverage 13/13) ✅
<table from W9-T3 §4.1>

## G2 (Runtime) ✅
- /api/v1/supervisor/status returns 13 scenarios, all redundant
- dedup proven on multiple live tests (W4-T4, W7-T2, W8 live)
- degrade detection ≤ 30s verified

## G3 (Visibility) ✅
- REDUNDANCY-MATRIX.svg renders 13 cards nightly
- redundancy-degraded auto-issue channel live (0 false positives
  in 7-day window)
- CONTRIBUTING.md links to REDUNDANCY-AUDIT §7

## ADR closures
- ADR-018 Phase 5 Closed (SSLKEYLOGFILE production)
- ADR-021 Adopted (capture_supervisor OSS boundary)
- ADR-022 Adopted (UIA accessibility OSS boundary)
- ADR-023 Adopted (gRPC-web protobuf decoder)

## Carry-forwards (P5.D.2 / P6)
全部见 `SCOPE-LOCK-2026-05-15.md` §4 不变 — 显式不在本 release 范围.

## Verification (next-shift)
git log v1.1.5..v1.1.6 --oneline
python -m pytest tests/test_capture_supervisor.py tests/test_keylog_mode.py tests/test_uia_driver_unit.py tests/test_normalizer_protobuf_*.py -v
curl http://127.0.0.1:9800/api/v1/supervisor/status | jq '[.scenarios[] | select(.status == "redundant")] | length'
# Expected: 13
```

---

## 9 · W9-T8 — git tag v1.1.6

```powershell
# 1. 最后一次确认 13/13 (W9-T1 已做)
$status = curl http://127.0.0.1:9800/api/v1/supervisor/status | ConvertFrom-Json
$redundant = ($status.scenarios | Where-Object { $_.status -eq "redundant" }).Count
if ($redundant -ne 13) {
    Write-Error "ABORT — only $redundant/13 redundant"
    exit 1
}

# 2. 打 tag
git tag -a v1.1.6 -m "P5.D.1 Redundancy Sprint complete (STRICT MODE).
13/13 P0 scenarios reach >=3 V-GREEN.
5 Web (ChatGPT/Claude/Gemini/GAS/Grok) + 8 Desktop (P1-P8 per
DESKTOP-PRODUCT-MATRIX.md). Capture Supervisor v1 alive.
4 ADR closures: ADR-018 Phase 5 + ADR-021/022/023 Adopted."

# 3. 推 tag (待 owner 推, 本 task 只准备好 tag)
# git push --tags
```

---

## 10 · 验收门 (Wave 9 = sprint final)

```
[ ] W9-T1: /api/v1/supervisor/status 13 全 redundant 截图存证
[ ] W9-T2: nightly workflow 1 轮 GREEN, REDUNDANCY-MATRIX.svg 13 全绿
[ ] W9-T3: REDUNDANCY-AUDIT §3 §4 13 行全部 V-GREEN
[ ] W9-T4: ADR-022 + ADR-023 Adopted
[ ] W9-T5: UCS 附录 B 口径声明 finalize
[ ] W9-T6: CHANGELOG v1.1.6 entry 完整
[ ] W9-T7: HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md 写好
[ ] W9-T8: git tag v1.1.6 候选放在那里, 等 owner 推
```

完成后:
- 全量 sprint acceptance gate 见 [`99-acceptance-gate.md`](99-acceptance-gate.md)
- Post-sprint 7-day 监控开始 (per 99 §"Post-sprint review")
