# Wave 3 — Capture Supervisor v1 (Day 3, ~19h, STRICT MODE)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.1
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
> + `@Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` §4
> + 待批 `ADR-021` (本冲刺新增, capture_supervisor OSS boundary)
>
> **依赖**: Pre-flight §1 决策 A 已通过 (ADR-021 Adopted).
>
> **STRICT MODE 改动**: scenarios.yaml 从 7 场景扩到 13 场景 (5 Web +
> 8 Desktop, 含 Tier-β 3 个 Phase B 场景但初始状态 down/impaired,
> 由 Wave 6/7/8 在 Phase B 期间填腿).

---

## 1 · 目标

G2 落地 — 3 条腿在运行时**真互为冗余**:

- 多条 leg 同时抓时, 按 `pair_id + fingerprint` 30s 滑窗 dedup
- 路径优先级 + warm-standby + 自动降级决策
- `/api/v1/supervisor/status` 暴露 13 场景 leg health
- dashboard 卡片让用户看见当前哪条腿在跑、哪条 degraded

**没有这一步, 前 N 条 V-GREEN 都是纸面冗余.**

---

## 2 · 包结构 (OSS 落点)

```
pce_core/capture_supervisor/                ← 新 (按 ADR-021 通过后的 OSS 边界)
├── __init__.py
├── dedup.py                  ← (pair_id, fingerprint) 30s 滑窗 LRU
├── policy.py                 ← scenarios.yaml 加载 + 路径优先级 + degrade 决策
├── status.py                 ← in-memory health by scenario × leg
├── scenarios.yaml            ← P0 7 场景 × leg 配置 (数据, 不是代码)
└── api.py                    ← /api/v1/supervisor/* 路由

tests/test_capture_supervisor.py            ← 新, 目标 ≥ 31 单测
scripts/check_import_direction.py           ← 改 allowlist (移除限制)
Docs/docs/engineering/adr/ADR-021-*.md      ← 新 (Pre-flight 已 draft)

pce_core/dashboard/redundancy.html          ← 新, ~150 LOC
pce_core/dashboard/static/redundancy.js     ← 新
pce_core/dashboard/static/redundancy.css    ← 新
```

---

## 3 · 4 个最小契约

### 3.1 Dedup (最关键)

```python
# pce_core/capture_supervisor/dedup.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClaimResult:
    is_primary: bool
    primary_source: Optional[str]   # if duplicate, the original source


class CaptureDedup:
    """30s sliding-window LRU on (pair_id, fingerprint).
    
    Contract: when 2 legs report SAME pair (e.g. L1 + L3a both saw the
    user→ChatGPT request), we keep ONE primary row and write the other
    leg's source into raw_captures.deduped_by JSON array.
    
    fingerprint = sha256(method + host + path + body_canon[:1024])
    """
    
    def __init__(self, window_s: float = 30.0, max_entries: int = 10000):
        ...
    
    def claim(
        self,
        pair_id: str,
        fingerprint: str,
        source: str,
    ) -> ClaimResult:
        """
        Returns:
          - is_primary=True: caller inserts new raw_captures row
          - is_primary=False, primary_source=<src>: caller appends source
            to primary's deduped_by JSON array
        """
        ...
```

利用 migration 0006 已建的 `idx_rc_fingerprint` 做查询; 不重建索引.

#### 3.1.1 fingerprint 算法

```python
def compute_fingerprint(
    method: str,
    host: str,
    path: str,
    body: bytes | None,
    ts_bucket_5min: int,    # ⚠️ 加 5min 桶防假阳 (R3 mitigation)
) -> str:
    """SHA-256 over canonicalized request fields.
    
    ts_bucket_5min = int(ts // 300) — same request 5 min apart
    won't collide as duplicate.
    """
    body_canon = (body or b"")[:1024]
    return sha256(
        f"{method}|{host}|{path}|{ts_bucket_5min}|".encode() + body_canon
    ).hexdigest()
```

### 3.2 scenarios.yaml (数据驱动)

```yaml
# pce_core/capture_supervisor/scenarios.yaml
schema_version: 1
scenarios:
  # ============== Web 5 ==============
  - id: f1_chatgpt_web
    label: "ChatGPT Web (chatgpt.com)"
    tier: S0
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3a_browser_ext
        priority: 2
        independent_basis: page_dom
      - source: L3d_cdp
        priority: 3
        independent_basis: chrome_devtools_protocol
      - source: L4a_clipboard
        priority: 4
        independent_basis: user_action
    redundancy_target: 3
  
  - id: f1_claude_web
    label: "Claude Web (claude.ai)"
    tier: S0
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3a_browser_ext
        priority: 2
        independent_basis: page_dom
      - source: L3d_cdp
        priority: 3
        independent_basis: chrome_devtools_protocol
    redundancy_target: 3
  
  - id: f1_gemini_web
    label: "Gemini Web (gemini.google.com)"
    tier: S1
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3a_browser_ext
        priority: 2
        independent_basis: page_dom
      - source: L4a_clipboard
        priority: 3
        independent_basis: user_action
    redundancy_target: 3
  
  - id: f1_gas
    label: "Google AI Studio (aistudio.google.com)"
    tier: S1
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3a_browser_ext
        priority: 2
        independent_basis: page_dom
      - source: L4a_clipboard
        priority: 3
        independent_basis: user_action
    redundancy_target: 3
  
  - id: f1_grok_web
    label: "Grok Web (grok.com)"
    tier: S2
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3a_browser_ext
        priority: 2
        independent_basis: page_dom
      - source: L4a_clipboard
        priority: 3
        independent_basis: user_action
    redundancy_target: 3
  
  # ============== Desktop 8 (P1-P8) ==============
  - id: f4_p1_claude_desktop
    label: "Claude Desktop (Win MSIX/Squirrel + macOS)"
    tier: D0
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: L3g_local_persistence
        priority: 2
        independent_basis: filesystem
      - source: L3f_mcp_proxy
        priority: 3
        independent_basis: mcp_jsonrpc
    redundancy_target: 3
  
  - id: f4_p2_chatgpt_desktop
    label: "ChatGPT Desktop (Win MSIX + macOS)"
    tier: D2
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: chromium_net_stack
      - source: A2_keylog                    # 注: 与 L1 算 1.5 条 (同 net stack)
        priority: 2
        independent_basis: chromium_keylog   # corroboration only
      - source: L4b_uia_accessibility        # Wave 7 落地, 真独立 leg
        priority: 3
        independent_basis: ui_automation_tree
    redundancy_target: 3
  
  - id: f5_p3_cursor
    label: "Cursor (IDE-class MCP-aware)"
    tier: D0
    legs:
      - source: L1_mitm                      # gRPC-web protobuf, Wave 8 normalizer
        priority: 1
        independent_basis: cursor_grpc_protocol
      - source: L3f_mcp_proxy
        priority: 2
        independent_basis: mcp_jsonrpc
      - source: L4a_clipboard                # OR L3b preload, Wave 8 决策
        priority: 3
        independent_basis: user_action
    redundancy_target: 3
  
  - id: f5_p4_windsurf
    label: "Windsurf (IDE-class MCP-aware)"
    tier: D1
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: codeium_grpc_protocol
      - source: L3f_mcp_proxy
        priority: 2
        independent_basis: mcp_jsonrpc
      - source: L4a_clipboard
        priority: 3
        independent_basis: user_action
    redundancy_target: 3
  
  - id: f5_p5_github_copilot
    label: "GitHub Copilot (VS Code)"
    tier: D2
    legs:
      - source: L1_mitm                      # Wave 6 host JSON normalizer
        priority: 1
        independent_basis: copilot_https_api
      - source: L3c_vscode_ext               # Wave 6 落地
        priority: 2
        independent_basis: vscode_extension_host
      - source: L3f_mcp_proxy                # OR L4a clipboard
        priority: 3
        independent_basis: mcp_jsonrpc
    redundancy_target: 3
  
  - id: f6_p6_claude_code_cli
    label: "Claude Code CLI (@anthropic-ai/claude-code)"
    tier: D0
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: anthropic_https_api
      - source: L3g_local_persistence
        priority: 2
        independent_basis: filesystem
      - source: L3h_cli_wrap
        priority: 3
        independent_basis: stdio_shim
    redundancy_target: 3
  
  - id: f6_p7_codex_cli
    label: "Codex CLI (OpenAI)"
    tier: D1
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: openai_https_api
      - source: L3g_local_persistence
        priority: 2
        independent_basis: filesystem
      - source: L3h_cli_wrap
        priority: 3
        independent_basis: stdio_shim
    redundancy_target: 3
  
  - id: f6_p8_gemini_cli
    label: "Gemini CLI (Google)"
    tier: D1
    legs:
      - source: L1_mitm
        priority: 1
        independent_basis: google_genai_https_api
      - source: L3g_local_persistence
        priority: 2
        independent_basis: filesystem
      - source: L3h_cli_wrap
        priority: 3
        independent_basis: stdio_shim
    redundancy_target: 3
```

### 3.3 Status API

```http
GET /api/v1/supervisor/status
→ 200 OK
{
  "computed_at": 1747123456.0,
  "scenarios": [
    {
      "id": "f4_p1_claude_desktop",
      "label": "Claude Desktop (Win MSIX/Squirrel + macOS)",
      "tier": "D0",
      "redundancy_target": 3,
      "legs_active": 3,
      "legs_degraded": 0,
      "status": "redundant",
      "color": "green",
      "legs": [
        {
          "source": "L1_mitm",
          "priority": 1,
          "independent_basis": "chromium_net_stack",
          "health": "green",
          "last_pass_ts": 1747123440.0,
          "last_fail_ts": null
        },
        {
          "source": "L3g_local_persistence",
          "priority": 2,
          "independent_basis": "filesystem",
          "health": "green",
          "last_pass_ts": 1747123450.0,
          "last_fail_ts": null
        },
        {
          "source": "L3f_mcp_proxy",
          "priority": 3,
          "independent_basis": "mcp_jsonrpc",
          "health": "green",
          "last_pass_ts": 1747123430.0,
          "last_fail_ts": null
        }
      ]
    },
    ...   # 共 13 个 scenarios
  ]
}

GET /api/v1/supervisor/scenario/<id>     # 单场景详情, 同上 + 24h 时序
```

#### 3.3.1 status 状态机

| Active legs | Status | Color |
|---:|---|---|
| ≥ redundancy_target (3) | `redundant` | green |
| 2 | `minimal` | yellow |
| 1 | `impaired` | orange |
| 0 | `down` | red |

`legs_active` 由 status.py 查 `health_beacons` 表 24h 内 PASS 数推断
(直接复用 P5.C.1 已有基础设施).

### 3.4 dashboard 卡片

`pce_core/dashboard/redundancy.html` (~200 LOC):

13 张卡片, 每张显示:
- Scenario label
- 大色块: redundant / minimal / impaired / down
- 3 个 leg 子条 (绿/黄/灰)
- 24h trend mini-chart (复用 health_beacon 数据)
- "已合并自 N 个 source" badge (来自 dedup `deduped_by` 数组统计)

样式参考 `Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md` §6.1 的字符画.

布局: 上行 5 张 (Web 5), 下行 8 张分两行 (P1-P4, P5-P8) 各 4 张.

---

## 4 · 落地清单

| 文件 | LOC | 工时 |
|---|:---:|:---:|
| `dedup.py` + 12 单测 | 200 | 4h |
| `policy.py` + scenarios.yaml + 8 单测 | 180 | 3h |
| `status.py` + health 集成 + 5 单测 | 120 | 2h |
| `api.py` + `pce_core/server.py` 注册 + 6 单测 | 150 | 2h |
| ingest 端 dedup 接入: `pce_core/ingest.py` 调 `CaptureDedup.claim()` | 30 | 1h |
| `dashboard/redundancy.html` + `.js` + `.css` | 150 | 2h |
| ADR-021 草稿 (Pre-flight 已完成) → finalize | — | 1h |
| `check_import_direction.py` allowlist 改 | 5 | 0.2h |
| **handoff** `HANDOFF-W3-SUPERVISOR-V1-2026-05-14.md` | — | 2h |

**总:** ~19h, 1 工日 (含 13 场景 scenarios.yaml 与原 7 场景的工时差).

---

## 5 · 测试矩阵 (31 单测)

### 5.1 dedup (12)

| # | 名称 | 验证 |
|---|---|---|
| 1 | `test_first_claim_is_primary` | 第一次 claim → is_primary=True |
| 2 | `test_second_claim_is_duplicate` | 同 pair+fingerprint 第二次 → is_primary=False, primary_source 准确 |
| 3 | `test_different_pair_not_dedupe` | 不同 pair_id → 都 primary |
| 4 | `test_different_fingerprint_not_dedupe` | 同 pair, 不同 fingerprint → 都 primary (实际是 follow-up request) |
| 5 | `test_window_expiry_30s` | 30s 后同 pair+fingerprint → primary again |
| 6 | `test_lru_cap_at_10000` | 第 10001 个 entry 入, 最旧出 |
| 7 | `test_thread_safety` | 多线程并发 claim 不假阳 |
| 8 | `test_compute_fingerprint_deterministic` | 同输入同输出 |
| 9 | `test_compute_fingerprint_5min_bucket` | 同请求 5 分钟内同 fingerprint, 5+ 分钟外不同 |
| 10 | `test_fingerprint_body_truncate_1024` | body > 1024 字节, 只 hash 前 1024 |
| 11 | `test_no_dedup_flag_disable` | `--no-dedup` flag 时, 永远 primary |
| 12 | `test_dedup_metrics_emitted` | 每次 claim 触发 metric (rate / hit_ratio) |

### 5.2 policy (8)

| # | 名称 | 验证 |
|---|---|---|
| 1 | `test_load_scenarios_yaml` | 13 个 P0 场景全部加载 |
| 2 | `test_redundancy_target_default_3` | 缺 redundancy_target → 默认 3 |
| 3 | `test_independent_basis_must_unique` | 同场景内 leg 的 basis 重复 → 抛 ValueError |
| 4 | `test_priority_order_respected` | 多 leg 都 active, 主推荐 priority=1 |
| 5 | `test_degrade_to_priority_2` | priority=1 leg fail, 主推荐 priority=2 |
| 6 | `test_unknown_source_rejected` | scenarios.yaml 含未注册 source → 加载失败 |
| 7 | `test_legs_count_independent` | 3 条 leg 都 active 时 legs_active=3 |
| 8 | `test_legs_count_excludes_failed` | 1 条 fail, legs_active=2 |

### 5.3 status (5)

| # | 名称 | 验证 |
|---|---|---|
| 1 | `test_status_redundant_3_active` | 3 leg green → redundant / green |
| 2 | `test_status_minimal_2_active` | 2 leg green → minimal / yellow |
| 3 | `test_status_impaired_1_active` | 1 leg green → impaired / orange |
| 4 | `test_status_down_0_active` | 0 leg green → down / red |
| 5 | `test_status_24h_window_for_health` | 24h 前的 PASS 不计入当前 health |

### 5.4 api (6)

| # | 名称 | 验证 |
|---|---|---|
| 1 | `test_get_status_returns_13_scenarios` | /api/v1/supervisor/status 含 P0 13 场景 |
| 2 | `test_get_scenario_detail` | /api/v1/supervisor/scenario/<id> 含 24h 时序 |
| 3 | `test_get_scenario_404` | 不存在 id → 404 |
| 4 | `test_status_response_schema_v1` | response 含 schema_version=1 |
| 5 | `test_status_no_pii` | response 不含 raw_captures.body / user 内容 |
| 6 | `test_status_perf_under_50ms` | P95 ≤ 50ms (NFR-3) |

---

## 6 · scenarios.yaml 加载流程图

```
启动 pce_core server
  ↓
api.py: from .policy import load_scenarios
  ↓
policy.load_scenarios("pce_core/capture_supervisor/scenarios.yaml")
  ↓ (validate independent_basis uniqueness per scenario)
  ↓ (validate every source matches CaptureSource enum from capture_event.py)
return Dict[str, Scenario]
  ↓
api.py: register routes /api/v1/supervisor/*
status.py: subscribe to health_beacon updates
dedup.py: instantiate global CaptureDedup
  ↓
ingest.py: on each new capture, dedup.claim() before insert
```

---

## 7 · handoff `HANDOFF-W3-SUPERVISOR-V1-2026-05-14.md` 模板

```markdown
---
title: "W3 — Capture Supervisor v1 Live"
status: PASS
date: 2026-05-16
session: HH:MM-HH:MM
operator: <name>
artifacts:
  - commit <sha>
  - ADR-021 Adopted
---

## TL;DR
Capture Supervisor v1 delivered. 7 P0 scenarios reporting via
/api/v1/supervisor/status. Dedup proven on 1 dual-leg session
(L1 + L3g for Claude Desktop). dashboard /redundancy renders 7
cards.

## Live evidence — dedup proof
<table: pair_id | primary source | deduped_by[]>

## Live evidence — degrade detection
<sequence: kill mitmdump → wait 30s → /status reflects L1=fail>

## REDUNDANCY-AUDIT update
§3 N/A (no leg upgrade) — but G2 acceptance ticked.
```

---

## 8 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W3-R1 | dedup 假阳删了真不同的请求 | fingerprint 含 5min bucket; `--no-dedup` flag 给 debug; raw_captures 仍保留 dedup 前的所有行 (只是被标记) |
| W3-R2 | scenarios.yaml schema 演进 | schema_version 字段; 加载时检查 |
| W3-R3 | ADR-021 未通过, 无法 OSS 落地 | fallback: W3 落到 PCE-pro, OSS 暴露 read-only proxy (本冲刺范围不变) |
| W3-R4 | health_beacon 表无数据 (P0 场景无 nightly probe) | scenarios.yaml 加 `health_unknown` 状态; dashboard 显示灰 "no data yet" |

---

## 9 · 验收门

```
[ ] 31 单测全 GREEN (dedup 12 + policy 8 + status 5 + api 6)
[ ] 杀掉 mitmdump 后 30s 内 /api/v1/supervisor/status 反映
    L1 → degraded
[ ] 同一 pair 由 L1 + L3a 双发 → raw_captures 只有 1 主行
    + deduped_by=["L3a_browser_ext"]
[ ] dashboard /redundancy 7 张卡片渲染
[ ] ADR-021 Adopted (owner 签字)
[ ] check_import_direction.py 跑过 (CI green)
[ ] handoff 含 dedup proof + degrade 30s 证据
```

完成后进入 [`04-wave4-third-leg.md`](04-wave4-third-leg.md).
