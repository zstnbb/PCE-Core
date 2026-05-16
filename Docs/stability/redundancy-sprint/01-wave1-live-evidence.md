# Wave 1 — Live Evidence Sprint (Day 1, ~24h, STRICT MODE)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.1
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.1
>
> **目标**: 把"代码就绪、只差一次真产品 sign-off"的 V-HERMETIC 路径
> 全部翻成 V-GREEN. 投入产出比最高的一波. **本 wave 只覆盖 Tier-α
> 低门槛场景**, Tier-β 结构性场景 (P2 / P3 / P5) 的腿在 Phase B
> Wave 6/7/8 单独处理.

---

## 1 · 任务矩阵 (Tier-α 8 任务, 共 11 行覆盖 8 场景)

每个任务格式: `任务 ID | 场景 | 工时 | 成功证据 | 落地 handoff`

| 任务 ID | 场景 | 工时 | 成功证据 (handoff 必须包含) | 落地 handoff |
|---|---|:---:|---|---|
| **W1-T1** | F4 P1 Claude Desktop / L3f MCP-proxy | 3h | 1 条真 cowork tool call → `raw_captures.source_id='mcp-proxy-default'` ≥ 1 行 + pair_id 关联 + 1 条 messages | `HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md` |
| **W1-T2** | F4 P1 Claude Desktop / L3f `.mcpb` posture A | 2h | `pce_mcp/mcpb/pce-capture.mcpb` 双击装到真 Claude Desktop → 1 次 `pce_capture` tool 调用落 `source_id='pce-mcp-default'` | `HANDOFF-W1-T2-MCPB-LIVE-2026-05-14.md` |
| **W1-T3** | F6 P6 Claude Code CLI / L3h | 2h | 真 `npm i -g @anthropic-ai/claude-code` + `pce wrap claude` 跑 1 prompt → `source_id='l3h-cli-wrapper-default'` ≥ 1 行 stdin + 1 行 stdout | `HANDOFF-W1-T3-CLI-WRAP-LIVE-2026-05-14.md` |
| **W1-T4** | F1 Gemini Web / L1 | 2h | 真 Gemini 网页发 1 prompt → `raw_captures.host='generativelanguage.googleapis.com'` 或 `gemini.google.com` ≥ 1 pair + 1 messages 行 + model_name 非空 | `HANDOFF-W1-T4-GEMINI-L1-LIVE-2026-05-14.md` |
| **W1-T5** | F1 GAS / L1 | 2h | 同 T4 模板, 对 `aistudio.google.com` | `HANDOFF-W1-T5-GAS-L1-LIVE-2026-05-14.md` |
| **W1-T6** | F1 Grok Web / L1 | 2h | 同 T4 模板, 对 `grok.com` | `HANDOFF-W1-T6-GROK-L1-LIVE-2026-05-14.md` |
| **W1-T7** | F5 P4 Windsurf / L3f MCP live | 3h | Windsurf MCP 配置接入 `pce_mcp_proxy` → 1 cowork tool call → `source_id='mcp-proxy-default'` + meta_json.subsystem=`windsurf` | `HANDOFF-W1-T7-WINDSURF-MCP-LIVE-2026-05-14.md` |
| **W1-T8** | F6 P7 Codex CLI / L3h | 3h | 真 OpenAI codex CLI 安装 + `pce wrap codex` 跑 1 prompt → `source_id='l3h-cli-wrapper-default'` ≥ 1 行 + meta_json.cli_kind=`codex` | `HANDOFF-W1-T8-CODEX-CLI-LIVE-2026-05-14.md` |
| **W1-T9** | F6 P7 Codex CLI / L3g local persistence | 2h | 真 codex CLI 跑 1 prompt → `~/.codex/sessions/*.jsonl` 落盘 → `pce_persistence_watcher` 抓到 `source_id='l3g-local-persistence-default'` + meta_json.cli_kind=`codex` | `HANDOFF-W1-T9-CODEX-L3G-LIVE-2026-05-14.md` |
| **W1-T10** | F6 P8 Gemini CLI / L3h | 3h | 真 Google gemini CLI 安装 + `pce wrap gemini` 跑 1 prompt → `source_id='l3h-cli-wrapper-default'` ≥ 1 行 + meta_json.cli_kind=`gemini` | `HANDOFF-W1-T10-GEMINI-CLI-LIVE-2026-05-14.md` |
| **W1-T11** | F6 P8 Gemini CLI / L3g local persistence | 2h | 真 gemini CLI 跑 1 prompt → `~/.config/gemini-cli/*` 或对应 jsonl 落盘 → watcher 抓到 + meta_json.cli_kind=`gemini` | `HANDOFF-W1-T11-GEMINI-L3G-LIVE-2026-05-14.md` |

**总工时:** ~24h, 单人 1.5 工日; 3 人并行 ~1 工日.

> **不在 Wave 1 范围 (推 Phase B)**:
> - F4 P2 ChatGPT Desktop A2 corroboration → Wave 2
> - F5 P3 Cursor MCP live → Wave 8 (与 protobuf 同 wave 落地)
> - F5 P5 GitHub Copilot L1 host normalizer + L3c → Wave 6

---

## 2 · 标准操作流程 (SOP)

避免 11 份 handoff 写得格式不一, 全部走同一个 SOP:

```
SOP - Live Evidence Capture
1. 启动 pce_core server (端口 9800); 记录进程 PID 入 handoff
2. 启动 pce_proxy on 8080; mode upstream:<本机 VPN 端口>
   (避免改用户系统)
3. 设置系统代理 → 127.0.0.1:8080; 记录原值入 handoff (reset 用)
4. 重启目标产品 (确保走新代理)
5. 记录 baseline 到 _baseline.json:
     raw_captures count
     messages count
     最新 created_at
6. 跑 1 个最小 prompt: 「What is 2+2?」 (永远固定)
7. 等 5s
8. 抓 diff:
   - 新增 raw_captures 行
     SELECT * FROM raw_captures WHERE created_at > <baseline_max>
   - 新增 messages 行
   - 高亮 substantive pair (body_len 显著 > 50)
9. 截图 dashboard `/sessions` 显示新 session
10. handoff 模板填空 (见 §3)
11. 还原系统代理; 停 pce_proxy; 保存 SQLite 备份到
    Docs/handoff/_evidence_W1_2026-05-14/<task-id>.db
```

### 2.1 SOP 适用范围说明

- **W1-T1 / W1-T2 / W1-T7** (MCP) 不需要步骤 2-4, 因为 MCP 走 stdio
  不走代理; 改为: 启动 `pce_mcp_proxy` 或装 `.mcpb` → 触发 1 次
  cowork tool call
- **W1-T3 / W1-T8 / W1-T10** (CLI wrap) 不需要步骤 2-4, 改为:
  `pce wrap <cli>` 启动 shim → 跑 1 prompt
- **W1-T9 / W1-T11** (L3g local persistence) 不需要步骤 2-4, 改为:
  启动 `pce_persistence_watcher --target codex|gemini` → 跑 CLI prompt
  → 验文件落盘 → 验 watcher 入库

---

## 3 · handoff 文件最小骨架 (每个 ≤ 100 行)

```markdown
---
title: "<Task ID> — <Scenario> Live Evidence"
status: PASS | FAIL | DEGRADED
date: 2026-05-14
session: HH:MM-HH:MM <UTC offset>
operator: <name>
predecessor: <none for fresh, or prior handoff>
artifacts:
  - commit <sha>
  - sqlite snapshot: Docs/handoff/_evidence_W1_2026-05-14/<id>.db
---

## TL;DR
<1 sentence>

## Pre-flight state
- proxy chain: ...
- baseline: raw_captures=N, messages=M

## Prompt + capture
<exact prompt sent>
<exact response received>

## Capture evidence (raw_captures diff)
<table from §2 step 8>

## messages rows after normalization
<rows>

## REDUNDANCY-AUDIT-MATRIX update
- §3 row: <which row updated from V-HERMETIC → V-GREEN>
- legs count change: N → N+1
```

骨架文件 W1-T1 已生成在 `Docs/handoff/HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md`,
其他 10 份按相同模板复制即可.

---

## 4 · 并行调度建议

如果 3 人并行 Phase A, 推荐:

- **人 A** (Claude Desktop 机): T1 / T2 / W4-T5 (Day 4) — 单台机
- **人 B** (浏览器系统): T4 / T5 / T6 — 三个 L1 任务共享系统代理,
  切站点即可
- **人 C** (CLI 机): T3 / T8 / T9 / T10 / T11 — 独立机器跑后端服务,
  CLI 任务串联做
- **人 B 收尾** (Day 1 晚): T7 Windsurf MCP

如果 1 人串行:

| 时段 | 任务 | 备注 |
|---|---|---|
| 上午 (3h) | W1-T1 | 最复杂, 头脑清醒时 |
| 上午 (2h) | W1-T2 | 复用 W1-T1 环境 |
| 下午 (2h) | W1-T3 | CLI wrap, 独立环境 |
| 下午 (6h, 并行) | W1-T4 / T5 / T6 | L1 任务共享系统代理 |
| 晚上 (3h) | W1-T7 | Windsurf MCP |
| 次日 (10h) | W1-T8 / T9 / T10 / T11 | Codex + Gemini CLI 4 任务 |

---

## 5 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W1-R1 | Claude Desktop MCP 协议失效 (Anthropic 更新) | T1 fallback to T2 (`.mcpb` posture A); 任一成功即升级 P1 至 3 V-GREEN |
| W1-R2 | Gemini / GAS / Grok 账号未登录 | 提前 24h 让 owner 登录; SOP 加 "verify session cookie" 步骤 |
| W1-R3 | Codex CLI / Gemini CLI 未发布或 API 变更 | 提前确认 npm/pip 包存在; 锁版本; fallback: 本 task 推到 P5.D.2 |
| W1-R4 | Windsurf 不接受外部 MCP server | 提前 RECON 配置点; fallback: T7 推到 Wave 6 (跟 Copilot L3c 同处理) |
| W1-R5 | L3g `~/.codex/` `~/.config/gemini-cli/` 路径未确定 | 任务前先跑 1 次 RECON 确认路径; 加入 `pce_persistence_watcher/discovery.py` |

---

## 6 · 验收门 (Wave 1 结束时)

```
[ ] 11 份 handoff 全部 PASS
[ ] REDUNDANCY-AUDIT-MATRIX §3 至少 11 行升级
[ ] 升级后场景计分:
    - F4 P1 Claude Desktop:    2 → 3 ✅ (G1 第一个达标场景)
    - F5 P4 Windsurf:          1 → 2 (W4 补 L4a 到 3)
    - F6 P6 Claude Code CLI:   1 → 2 (W4 补 L1 host live 到 3)
    - F6 P7 Codex CLI:         0 → 2 (W4 补 L1 host norm 到 3)
    - F6 P8 Gemini CLI:        0 → 2 (W4 补 L1 host norm 到 3)
    - F1 Gemini/GAS/Grok:      1 → 2 (W4 补 L4a 剪贴板到 3)
[ ] 11 份 handoff commit + REDUNDANCY-AUDIT 同 commit 更新 (per §7.2)
[ ] _evidence_W1_2026-05-14/ 目录 11 个 SQLite snapshot
```

完成后进入 [`02-wave2-sslkeylogfile.md`](02-wave2-sslkeylogfile.md).
