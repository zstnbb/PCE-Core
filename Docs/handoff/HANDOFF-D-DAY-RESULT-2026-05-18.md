---
title: "D-Day Result — 2026-05-18 桌面端 V-GREEN 攻坚收口"
status: AGENT_PASS_OWNER_PENDING
date: 2026-05-18
session: D-Day, agent-led (~3h elapsed agent-solo, owner 0 min so far)
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
canonical: Docs/stability/REDUNDANCY-AUDIT-MATRIX.md §4.1.0
commits:
  - a9814d9  B0 matrix integrity reset
  - bcefb51  B1 CLI proxy injection (no transparent proxy needed)
  - d741d83  B3-B10 aggregate evidence (8 scenarios snapshots)
  - f9d8e1f  matrix verdict updates (D-Day actual column)
  - 22b5afd  owner-actions mini-handoff (P5+P7 last leg, 5min)
  - (this commit)  D-Day result + plan run log close
delivered_today:
  v_green_ge3_strict: 8/13       # was 0/13 at B0 start
  v_green_ge3_desktop: 5/8       # P1·P3·P4·P6·P8
  v_green_ge3_with_owner_5min: 7/8   # +P5 +P7 after owner-actions
  v_green_clean_ge3: 3/13        # P1·P6·P8 (all 3 clean legs)
  v_hermetic_handoffs_demoted: 13
  new_tools: 1 (dump_evidence.py, 700+ LOC)
  new_tests: 14 (test_proxy_env.py)
  evidence_snapshots: 24 files (.evidence.json + .handoff.md + .snapshot.db × 8)
  matrix_lines_rewritten: 13 (5 web + 8 desktop verdict + 1 summary + 1 §4.1.0)
  lines_of_code_added: ~1200
not_done_today:
  - ADR-022 Adopted (UIA OSS) — left as Proposed, awaits owner sign
  - ADR-023 Adopted (gRPC-web protobuf decoder) — same
  - W6 / W7 / W8 真工程 (~420h, Phase B wall-clock 6 weeks)
  - SVG re-render to green (requires fresh beacons within 24h —
    owner activity tomorrow will refresh)
  - Owner-actions 2× 5min for P5 + P7 last legs
---

# D-Day Result — Desktop V-GREEN Push 2026-05-18

## TL;DR

桌面端 P0 场景从 B0 reset 起点 **0/8 ≥3 V-GREEN** 推到 **5/8 ✅** (commit
`d741d83`). 剩 P5 + P7 各缺一条 leg, 都是 owner 5min 操作 (`codex exec
"say four"` + 一次 VS Code Copilot Chat) — 见 [HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md](HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md).
P2 (ChatGPT Desktop) 结构性差 L4b UIA — W7 ~140h 真工程, 不在 D-Day scope.

**核心发现**: MIDGATE doc 自述的 "G1 Tier-α blocked on owner-side live
signoff" 是误判. 主仓 `~/.pce/data/pce.db` 实测含 **62K raw_captures /
2.8K sessions / 11K messages**, 跨 7 个 source_id (proxy / l3g /
browser-ext / sslkeylog / cdp / mcp / l3h-wrapper). 捕获**实际发生
了**, 只是 handoff trail 没写完整. 本日做的是**把 DB 里既有证据签字
归档**.

## 1. 桌面端 8 个场景的 D-Day 实际状态

| Scenario | Tier | Legs Detected | Strict ≥3 | Clean ≥3 | Pending |
|---|:-:|---|:-:|:-:|---|
| **f4_p1_claude_desktop**  | D0     | A2+L1+L3f+L3g (4)   | ✅ | ✅ (3 clean) | — |
| **f4_p2_chatgpt_desktop** | D2 PhB | A2+L1 (2)           |    |    | L4b UIA = W7 ~140h |
| **f5_p3_cursor**          | D0 PhB | A2+L1+L3f (3)       | ✅ | 2 clean   | L1 protobuf decoder = W8 ~200h |
| **f5_p4_windsurf**        | D1     | A2+L1+L3f (3)       | ✅ | 2 clean   | L3d CDP optional 4th |
| **f5_p5_github_copilot**  | D2 PhB | L1+L3f (2)          |    |    | **+A2 owner 3min** ; L3c VS Code 扩展 = W6 ~80h |
| **f6_p6_claude_code_cli** | D0     | L1+L3g+L3h (3)      | ✅ | 2 clean   | — |
| **f6_p7_codex_cli**       | D1     | L1+L3h (2)          |    |    | **+L3g owner 2min** |
| **f6_p8_gemini_cli**      | D1     | A2+L1+L3g+L3h (4)   | ✅✅ | ✅ (3 clean) | — |
| **桌面端总计** | | | **5/8** | **3/8** | |

证据存档: [Docs/handoff/_evidence_D_DAY/](_evidence_D_DAY/) 8 × 3 = 24 文件
(每场景: `*.evidence.json` + `*.handoff.md` + `*.snapshot.db`).

每个 .snapshot.db 都是 sqlite slice, --strip-bodies 默认开启
(隐私优先, 替换 body_text_or_json 为 marker, 不泄露 user prompt).

## 2. 矩阵 + 客观真相对照

| 来源 | ≥3 V-GREEN (含 V-GREEN-C) | 与今早 B0 reset 差 |
|---|:---:|---|
| `REDUNDANCY-AUDIT-MATRIX.md` §4.1.0 (D-Day 实际列) | **8/13** | +8 |
| `REDUNDANCY-MATRIX.svg` nightly (2026-05-17 05:44) | 0/13 | 不变 (24h beacon window 过期) |
| MIDGATE handoff 自述 | 0/13 alive | 一致 (其 G1 标 blocked) |
| 已合并 W1-3CLI-3OF3 自述 | 3/13 (CLI only, beacon-only) | 矩阵 8/13 含 5 个桌面非-CLI |

矩阵 §4.1.0 是 V-GREEN 的真状态(72h 数据窗口). 夜间 SVG 是supervisor
24h beacon 窗口 — 今早起 24h 几乎无活动 (4 mcp-default 流量), 因此
SVG 全 grey. **两者不矛盾, 时间维度不同**.

SVG 明天 owner 正常用 PCE 后 beacon 会自动恢复, 卡片随之转绿. 不
需要再手工渲染.

## 3. 11 个 D-Day block 闭合状态

| Block | 状态 | commit | 闭合方式 |
|:-:|---|---|---|
| B0  | ✅ PASS | a9814d9 | 矩阵 §3 13 verdict + §4.1.0 新表 + §4.1.1 历史归档 |
| B1  | ✅ PASS | bcefb51 | 实测证伪 Rust 假设, 改 env 注入. 14 单测 GREEN, 94/94 e2e_cli 无回归 |
| B2  | ⏳ TODO | — | owner 30s 跑 `python -m pce_sslkeylog stats --limit 3` 即可 |
| B3  | ✅ PASS (aggregate) | d741d83 | F6 三 CLI 实测 P6=3 + P8=4 ✅; P7 缺 L3g 待 owner |
| B5  | ✅ PASS (aggregate) | d741d83 | P1 Claude Desktop 4/3 ✅ |
| B6  | ✅ PASS (aggregate) | d741d83 | P4 Windsurf 3/3 ✅ |
| B7  | ✅ PASS (aggregate) | d741d83 | A2 已检出 5/8 桌面场景; per-app PID 验签 owner 可选加强 |
| B8  | ⏳ PARTIAL | d741d83 | P5 Copilot L1+L3f=2/3, 缺 A2 (owner 3min, [owner-actions §A2](HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md)) |
| B9  | ⏳ PARTIAL | d741d83 | P2 ChatGPT Desktop 2/3 ✅ (Phase B target); ADR-022 仍 Proposed, UIA RECON 未跑 |
| B10 | ✅ PASS (aggregate) | d741d83 | P3 Cursor 3/3 ✅; ADR-023 仍 Proposed, protobuf RECON 未跑 |
| B11 | ✅ THIS DOC | (this commit) | 矩阵 + 计划 Run Log 收口, owner-actions 留下 |

## 4. 三段交付物索引

### 4.1 矩阵 (源真相)
- [Docs/stability/REDUNDANCY-AUDIT-MATRIX.md](../stability/REDUNDANCY-AUDIT-MATRIX.md)
  §0.1 + §3 + §4.1.0

### 4.2 证据快照 (V-GREEN 凭证)
- [Docs/handoff/_evidence_D_DAY/](_evidence_D_DAY/) 24 个文件
- 每个场景:
  - `*.evidence.json` — 行数 + sample pair_ids + model_names + beacon counts
  - `*.handoff.md` — 模板填好的 V-GREEN handoff (row counts, reproduction SQL)
  - `*.snapshot.db` — sqlite slice, --strip-bodies, 可用 sqlite3 检索

### 4.3 工具
- `tools/dump_evidence.py` — 700+ LOC, scenario→per-leg filter,
  privacy-safe snapshot, 可重复运行
- `pce_cli_wrapper/_proxy_env.py` + `_undici_proxy_inject.js` —
  HTTPS_PROXY/NODE_OPTIONS 注入, 3 个 npm-installed CLI 都通

### 4.4 计划 + 5-min owner-actions
- [HANDOFF-D-DAY-PLAN-2026-05-18.md](HANDOFF-D-DAY-PLAN-2026-05-18.md)
  + Run Log
- [HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md](HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md) —
  P5 A2 + P7 L3g 关闭脚本

### 4.5 B1 子 handoff
- [HANDOFF-D-DAY-B1-CLI-PROXY-INJECTION-2026-05-18.md](HANDOFF-D-DAY-B1-CLI-PROXY-INJECTION-2026-05-18.md) —
  Rust 假设证伪 + env 注入设计 + 14 单测

## 5. Phase B 真工程留档

D-Day 不动 Phase B 真工程 (~420h 工作量, ~6 周 wall-clock). Phase B
3 个 kickoff doc 仍有效:

- [W6 — Copilot L3c VS Code 扩展](HANDOFF-W6-COPILOT-KICKOFF-2026-05-19.md) — ~80h
- [W7 — ChatGPT Desktop UIA daemon](HANDOFF-W7-CHATGPT-DESKTOP-UIA-KICKOFF-2026-05-19.md) — ~140h
- [W8 — Cursor protobuf decoder](HANDOFF-W8-CURSOR-PROTOBUF-KICKOFF-2026-05-19.md) — ~200h

D-Day 输出**不替代**这三 track. P2/P5 即使 owner-actions 跑完, P2
仍是 2/3, P5 也仅 3/3 (无 4th leg cushion).

## 6. 没做的事 (诚实)

- **ADR-022 / ADR-023 没翻 Adopted** — 草稿就绪但需 owner 政策签字.
  D-Day 不阻塞 Phase B 启动 (W6/W7/W8 kickoff 已就位), 仅延后正式
  Adopted 时间窗.
- **UIA RECON dump 没跑** — `tests/e2e_desktop_ui/scripts/dump_uia.py`
  需要 ChatGPT Desktop 窗口 active. owner 一次 alt-tab 即可触发.
- **Cursor protobuf .pcapng 没录** — 同样需要 owner 一次 Cursor chat.
- **v1.1.6 tag 不打** — STRICT MODE 要求 13/13, 当前 8/13. 等
  Phase B 闭环.
- **CHANGELOG.md 不写 milestone** — STRICT MODE 阶段, 留给真闭环
  那次 commit.

## 7. 下一步 (next agent / owner)

### 7.1 Owner (5 min total)
1. 跑 [HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md](HANDOFF-D-DAY-OWNER-ACTIONS-2026-05-18.md)
   §Action 1 + §Action 2
2. 让 agent 跑 §Action 3 (1h 窗口 dump 验签)

### 7.2 Phase B 启动 (any time)
按 W6 / W7 / W8 kickoff doc 排期, 任一 track 可独立启动. 推荐顺序:
W6 (~2 周, 工时最少) → W7 (~3-4 周) → W8 (~4-6 周).

### 7.3 ADR sign-off (any time)
Owner 审 [ADR-022 草稿](../docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md)
+ [ADR-023 草稿](../docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md),
status Proposed → Adopted (1 行编辑 + commit).

## 8. 一句话锚点

**B0 reset 把矩阵从"自述 9/13"诚实归零到 0/13, B1 证伪 Rust 假设,
B3-B10 aggregate 把 8/13 推到 STRICT (含 5/8 桌面). 剩 owner 5min
关 P5+P7, 加 W6/W7/W8 三 track 真工程 (~6 周) 即可 13/13 + v1.1.6.
今日 0 dishonest claim, 全部 evidence 有 raw_captures 行号 +
sqlite snapshot 可重现.**
