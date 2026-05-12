# PCE Pipeline Health Matrix — 跨 lane 健康度矩阵契约

> **Version**: pce-health-matrix-2026-05-12
>
> **Status**: canonical — health-as-data 三件套契约 (per `META-PIPELINE-FRAMEWORK.md` §2.3).
>
> **Adopted**: 2026-05-12 (P5.C.0 契约冻结)
>
> **Authority**:
> - 上承策略层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` §2.3
> - 上承决策层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` §3.1 契约 A
> - 下指执行层: `pce_core/health.py` (P5.C.1 待建)
>
> **Audience**: P5.C.1 实施 agent、各 lane 维护者、dashboard 开发者.
>
> **Scope**: 本文件定义 health beacon 数据契约、4 lane 接入点、SQLite schema、API、dashboard 渲染合约、告警阈值. 不定义具体 UI 实现细节 (那归 P5.C.1 deliverable).

---

## 1. 心智模型

### 1.1 为什么需要健康度矩阵

PCE 项目所有者无法作为主体维护 (per `2026-05-12-meta-pipeline-and-maintenance-strategy.md` §2.5). 在这个约束下, 系统必须通过健康度矩阵替代 "项目所有者亲自看每个 lane" 的角色:

```
旧路径 (不可持续):
  用户报障 → 项目所有者亲自查 → 复现 → 修复

新路径 (P5.C 起):
  pce_core/health beacon → dashboard 健康度页 + nightly probe → auto-issue
                ↓
  任意 agent / 贡献者按 issue 修复 → conductor verify_patch
```

### 1.2 健康度矩阵 ≠ telemetry 上报

**关键约束** (PROJECT.md §7.1 Local-first):
- 健康度数据完全 local, 永不上报到外部服务器
- 用户**主动 opt-in** 把诊断信息复制到 GitHub issue (通过 dashboard "复制诊断信息"按钮)
- 不实现 Sentry-style 自动 telemetry

### 1.3 与 raw_captures 的边界

- `raw_captures` = 业务数据 (用户的 AI 对话内容), 受 PRIVACY.md 保护
- `health_beacons` = 系统状态 (lane 是否健康), **不含**业务内容

两个表分离, 两套权限模型, 两个保留策略.

---

## 2. HealthBeacon Schema (canonical v1)

### 2.1 数据契约

```python
from dataclasses import dataclass
from typing import Literal, Optional
import json

LaneT = Literal["browser", "desktop", "cli", "mcp"]
StatusT = Literal["pass", "fail", "skip", "degraded", "infra_error"]


@dataclass
class HealthBeacon:
    """跨 lane 健康度信号. 一个 beacon = 一次 case 执行 / 一次 layer 心跳."""

    # 必填字段
    lane: LaneT                      # "browser" / "desktop" / "cli" / "mcp"
    layer: str                       # "L1" / "L3a" / "L3d" / "L3f" / "L3g" / "L3h"
    target: str                      # "chatgpt" / "claude_desktop" / "claude_code" / ...
    status: StatusT
    ts: float                        # unix epoch seconds (server-side `time.time()`)

    # 可选字段 (case-bound beacon 提供, layer-heartbeat beacon 留空)
    case_id: Optional[str] = None    # "T01" / "D03" / "C05" / "E12" / None
    elapsed_ms: Optional[int] = None
    
    # Lane-specific metadata (JSON-serialisable)
    meta: dict = None                # 见 §2.4
    
    # 仅 browser/desktop lane
    dom_selector_hits: Optional[dict[str, int]] = None  # selector → 命中次数
    
    # 严禁字段 (会被 server-side 拒绝写入)
    # - content_text / body / response_body / request_body
    # - api_key / token / cookie / authorization
    # - user_email / user_id / pii
```

### 2.2 写入约束

server-side validation (`POST /api/v1/health/beacon`):

| Field | 约束 |
|---|---|
| `lane` | 必须在 4 lane 枚举内 |
| `layer` | 必须在 UCS 5 层 + sub-layer 枚举内 (`L1` / `L3a` / `L3b` / `L3c` / `L3d` / `L3e` / `L3f` / `L3g` / `L3h` / `L4a` / `L4b` / `L4c`) |
| `target` | 字符串长度 ≤ 64, only `[a-z0-9_-]` |
| `case_id` | optional, 但若提供必须匹配 `^[A-Z]\d{2}$` (T-/D-/C-/E-/K-/M- 前缀) |
| `meta` | JSON-serialisable, 序列化后 ≤ 4 KB; 含 PII 字段 → reject |
| `ts` | 与服务端时钟差 ≤ 300s; 否则 reject (防 clock skew) |
| beacon 总频率 | 每 `(lane, target)` 限速 10 beacon/sec; 超过 silently dropped + log warning |

### 2.3 SQLite Schema (migration 0013, P5.C.1 落地)

```sql
CREATE TABLE IF NOT EXISTS health_beacons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lane            TEXT NOT NULL,
    layer           TEXT NOT NULL,
    target          TEXT NOT NULL,
    case_id         TEXT,                    -- nullable
    status          TEXT NOT NULL CHECK(status IN ('pass','fail','skip','degraded','infra_error')),
    ts              REAL NOT NULL,           -- unix epoch
    elapsed_ms      INTEGER,                 -- nullable
    meta_json       TEXT,                    -- nullable, JSON-encoded
    selector_hits_json TEXT,                 -- nullable, JSON-encoded
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_health_beacons_lane_target_ts
    ON health_beacons(lane, target, ts DESC);

CREATE INDEX idx_health_beacons_status_ts
    ON health_beacons(status, ts DESC);

CREATE INDEX idx_health_beacons_case
    ON health_beacons(target, case_id, ts DESC) WHERE case_id IS NOT NULL;
```

**保留策略**: 默认保留 90 天 (`PCE_HEALTH_RETENTION_DAYS`, 复用 `pce_core/retention.py` 模式). 旧 beacon 进入 daily sweep 删除. 健康度趋势依赖于 90 天滚动窗口.

### 2.4 Per-lane meta 字段约定

每个 lane 在 `meta_json` 字段填的内容:

```python
# Browser lane (e.g. ChatGPT T01 PASS)
meta = {
    "url": "https://chatgpt.com/c/<uuid-redacted>",
    "user_agent": "Chrome/137.0.0.0",
    "extension_version": "1.0.0",
    "selectors_used": ["[data-message-author-role]"],  # 命中的 selector list
    "pce_session_id": "sess_xxxx",                     # 关联 capture
}

# Desktop lane (e.g. Claude Desktop D03 PASS)
meta = {
    "product_version": "1.6608.2.0",                   # MSIX version
    "os": "windows-10.0.22631",
    "uia_tree_size": 287,                              # 弹窗发现深度参考
    "axes_pass": ["L1", "L3g"],                        # 多通道证据
    "pce_session_id": "sess_yyyy",
}

# CLI lane (e.g. Claude Code K01 PASS)
meta = {
    "product_version": "0.5.12",
    "wrap_mode": "shim",                               # H1 PATH-priority shim 模式
    "anthropic_api_host": "api.anthropic.com",         # 网络 axis
    "pce_session_id": "sess_zzzz",
}

# MCP lane (e.g. filesystem M01 PASS)
meta = {
    "upstream_server": "filesystem",
    "mcp_protocol_version": "2024-11-05",
    "frame_count": 17,
    "pce_session_id": "sess_wwww",
}
```

`pce_session_id` 让健康度可以**反查**到 raw_captures + sessions, 但 health_beacons 表本身不存储用户内容.

---

## 3. Lane 接入点矩阵 (P5.C.1)

每个 lane 必须在以下点位调用 `record_beacon()`:

| Lane | 接入点 | 文件 | 触发时机 | status 取值 |
|---|---|---|---|---|
| **Browser** | capture-runtime | `pce_browser_extension_wxt/utils/capture-runtime.ts` | 每个 capture 成功/失败 emit | `pass` / `fail` / `degraded` |
| **Browser** | T-case 测试 | `tests/e2e_probe/conftest.py` | 每个 T-case 结束 | `pass` / `fail` / `skip` |
| **Desktop** | driver setup/teardown | `tests/e2e_desktop_ui/drivers/base.py` | driver lifecycle | `infra_error` / `pass` |
| **Desktop** | D/C/E case 测试 | `tests/e2e_desktop_ui/cases/__init__.py` (待建) | 每个 case 结束 | `pass` / `fail` / `skip` |
| **Desktop** | L3g 持久化 watcher | `pce_persistence_watcher/__main__.py` | 每个 scan tick | `pass` / `degraded` (degrade if scan latency > threshold) |
| **CLI** | wrap entry | `pce_cli_wrapper/capture.py` | 每次 wrap 调用 | `pass` / `fail` / `degraded` |
| **MCP** | proxy frame relay | `pce_mcp_proxy/capture.py` | 每个 JSON-RPC frame 转发 | `pass` / `fail` |
| **MCP** | server tool invocation | `pce_mcp/server.py` | 每个 tool 调用 | `pass` / `fail` |

### 3.1 减负 — beacon 限速与采样

- **Layer-heartbeat beacon** (无 case_id) → 限速 1/min (即使代码层每秒触发, server 端去重)
- **Case-bound beacon** (有 case_id) → 不限速, 每个 case 必须单独 beacon
- **Network-noise beacon** (filtered out by `_NOISE_PATH_PATTERNS`) → 不发, 避免 dashboard 噪音

### 3.2 失败重试

- HTTP POST 失败 → 本地 in-memory ring buffer (≤ 100 beacon) → 下次成功时批量补发
- 超过 ring buffer 容量 → silently dropped + log warning (优先保住产品体验, 健康度可丢)

---

## 4. API 契约

### 4.1 Ingest

```http
POST /api/v1/health/beacon
Content-Type: application/json

{
  "lane": "browser",
  "layer": "L3a",
  "target": "chatgpt",
  "case_id": "T01",
  "status": "pass",
  "ts": 1747095600.123,
  "elapsed_ms": 2341,
  "meta": {"url": "...", "extension_version": "1.0.0"}
}

→ 200 OK {"id": 12345, "accepted": true}
→ 400 Bad Request {"error": "invalid_lane" | "pii_detected" | "ts_skew" | ...}
→ 429 Too Many Requests (rate limit)
```

### 4.2 Aggregated query

```http
GET /api/v1/health/matrix
→ 200 OK
{
  "lanes": {
    "browser": {
      "targets": {
        "chatgpt": {
          "tier": "S0",
          "plane_count": 2,                        # required ≥2 for D0/S0
          "last_pass_ts": 1747095600.123,
          "pass_rate_24h": 0.94,                   # 24h 滚动窗口 PASS 占比
          "fail_count_24h": 3,
          "skip_count_24h": 18,
          "case_breakdown": {
            "T01": {"status": "pass", "ts": 1747095600.123},
            "T02": {"status": "pass", "ts": 1747095601.234},
            ...
          },
          "color": "green"                         # 见 §5
        },
        "claude_ai": {...},
        "gemini": {...}
      }
    },
    "desktop": {...},
    "cli": {...},
    "mcp": {...}
  },
  "computed_at": 1747095700.0,
  "schema_version": 1
}
```

### 4.3 Time-series query (dashboard charting)

```http
GET /api/v1/health/timeseries?lane=browser&target=chatgpt&hours=24
→ 200 OK
{
  "lane": "browser", "target": "chatgpt",
  "buckets": [
    {"ts": 1747009200.0, "pass": 47, "fail": 0, "skip": 8, "degraded": 0},
    {"ts": 1747012800.0, "pass": 51, "fail": 1, "skip": 7, "degraded": 0},
    ...
  ],
  "bucket_size_s": 3600
}
```

### 4.4 Single beacon detail

```http
GET /api/v1/health/beacon/{id}
→ 200 OK
{
  "id": 12345, "lane": "browser", ..., "meta": {...}
}
```

---

## 5. 颜色 / 告警阈值 (canonical)

### 5.1 Per-target 颜色规则

dashboard 渲染颜色, 也是 nightly probe 报警阈值:

| Color | Status 推断规则 (优先级从上到下) |
|---|---|
| **🔴 RED** | 24h 内 ≥3 个 D0/S0 case `fail`; OR 6h 内 0 `pass`; OR plane_count < tier 要求 (D0/S0 < 2) |
| **🟡 YELLOW** | 24h 内 1-2 个 D0/S0 case `fail`; OR 24h pass_rate < 80%; OR canary diff 含 1+ `enum_extension` (soft severity) |
| **🟢 GREEN** | 24h pass_rate ≥ 90% AND canary 无 hard severity drift AND plane_count ≥ tier 要求 |
| **⚪ GREY** | 24h 内无 beacon (`health_unknown`); 视为 degraded, 但不报警 (可能是 lane 暂时 offline) |

### 5.2 Per-lane 聚合颜色

lane 颜色 = max(target colors) 按严重度 (RED > YELLOW > GREY > GREEN).

### 5.3 自动 issue 触发条件 (P5.C.3 落地)

任一 D0/S0 target 颜色变 RED → auto-issue:

```
Title: [broken-adapter] {lane}/{target} {case_id|all} RED for {hours_red}h
Labels: broken-adapter, auto-detected, {lane}, {tier}
Assignee: @CODEOWNERS
Body:
  - Last PASS: {timestamp}
  - Recent failures: {bullet list of last 3 fail beacons}
  - Canary diff (if any): {jsonschema-diff output}
  - Reproduction: `python -m pytest {runner_args}`
  - Linked health page: http://127.0.0.1:9800/dashboard/health?lane={lane}&target={target}
```

YELLOW → 不自动开 issue, 但写入 `Docs/stability/HEALTH-MATRIX.svg` 标黄.

---

## 6. Dashboard 渲染合约 (P5.C.1)

### 6.1 视图 1: 矩阵概览 (`/dashboard/health`)

```
┌─────────────────────────────────────────────────────────────┐
│  PCE Pipeline Health  (computed at 2026-05-12 13:21:00)    │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  Lane    │  Browser │  Desktop │  CLI     │  MCP            │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│  Status  │   🟢     │   🟢     │   🟡     │   🟢            │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│  Targets │ ChatGPT🟢│ Claude🟢 │ Code🟢   │ Filesystem🟢   │
│          │ Claude🟢 │ Cursor🟡 │ Codex🟡  │ Git🟢           │
│          │ Gemini🟢 │ Windsurf?│ Gemini?  │ Puppeteer🟢    │
└──────────┴──────────┴──────────┴──────────┴─────────────────┘
[Click any cell → drill down to per-case timeline]
```

### 6.2 视图 2: 单 target detail (`/dashboard/health/<lane>/<target>`)

```
┌─────────────────────────────────────────────────────────────┐
│  ChatGPT (browser, S0)                  Plane: N + H        │
│  Last PASS: 2 minutes ago                                   │
│  24h pass rate: 94.5% (47/52)                               │
├─────────────────────────────────────────────────────────────┤
│  Case timeline (24h):                                       │
│  T00 ████████████████████████░ 23/24 PASS                   │
│  T01 ████████████████████████  24/24 PASS                   │
│  T02 ████████████████████████  24/24 PASS                   │
│  ...                                                         │
│  T10 ████████████░░░░░░░░░░░░ 12/24 SKIP (extractor gap)   │
│  T11 ████████████░░░░░░░░░░░░ 12/24 SKIP                    │
├─────────────────────────────────────────────────────────────┤
│  [📋 复制诊断信息]  [🔄 Run case live]  [📊 30-day trend]   │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 视图 3: case-bound history (`/dashboard/health/<lane>/<target>/<case_id>`)

时序图 + recent failures 列表 + canary diff (若有).

### 6.4 "复制诊断信息" 按钮

用户主动 opt-in 把诊断信息复制到剪贴板 (后续粘贴到 GitHub issue):

```
PCE Health Diagnostic — 2026-05-12 13:21
Lane: browser, Target: chatgpt, Case: T10
Last PASS: 2026-05-11 09:14 (28h ago)
Last FAIL: 2026-05-12 13:18 (3 min ago)
  Status: fail, Elapsed: 12 003 ms
  Selectors miss: ["input[type='file']"]
  DOM diff: ... (truncated, full diff at canary path)
Recent fails (24h): 8/24
Plane status: N (L1) green, H (L3a) red
Canary drift: enum_extension on $.attachments[*].file_kind (soft)
Reproduction: pytest tests/e2e_probe/test_matrix.py -k "chatgpt:T10" -v
```

格式纯文本, 用户审阅后再粘贴. 不含 PII (业务内容已被 redact 标记).

---

## 7. 4 Lane × Target 完整覆盖矩阵 (P5.C.1 起步, P5.C.5 收口)

P5.C.1 启动时的 baseline (browser + desktop 已有, cli + mcp 未接):

| Lane | Target | Tier | Plane | Health 接入 status (P5.C.1 目标) |
|---|---|---|---|---|
| Browser | ChatGPT | S0 | N + H | 🟢 must |
| Browser | Claude.ai | S0 | N + H | 🟢 must |
| Browser | Gemini | S0 | N + H | 🟢 must |
| Browser | Google AI Studio | S1 | N + H | 🟢 must |
| Browser | Perplexity | S1 | N + H | 🟢 must |
| Browser | Copilot/Grok/DeepSeek | S2 | N or H | 🟡 best-effort |
| Browser | HuggingFace/Poe/Kimi/ZhiPu/Mistral/Manus | S3 | H | 🟡 best-effort |
| Browser | M365/Notion/Gmail/Figma (SX) | SX | — | ⚪ no health (scaffolding only) |
| Desktop | Claude Desktop (chat-region) | D0 | N + L3g + M | 🟢 must |
| Desktop | Cursor | D0 | H + N | 🟢 must (P5.B.6) |
| Desktop | Windsurf | D1 | H + N | 🟡 P5.B.6 |
| Desktop | ChatGPT Desktop | D2 | N (Frida fallback Pro) | 🟡 best-effort |
| Desktop | Copilot | D2 | N | 🟡 best-effort |
| CLI | Claude Code | D0 | H1 + N | 🟢 must |
| CLI | Codex CLI | D1 | H1 + N | 🟢 must |
| CLI | Gemini CLI | D1 | H1 + N | 🟢 must |
| MCP | filesystem (reference) | — | M | 🟢 must |
| MCP | git, puppeteer (representative) | — | M | 🟡 best-effort |

P5.C.1 acceptance: Browser 5 个 must + Desktop Claude Desktop + CLI 3 个 + MCP 1 个 reference = 至少 10 个 target 上报 health beacon.

---

## 8. 与 Test Conductor 的关系 (refines `META-PIPELINE-FRAMEWORK.md` §5)

Test Conductor `list_targets()` tool 输出每个 target 的 health summary:

```python
# pce_test_conductor/server.py
async def list_targets() -> list[TargetSummary]:
    matrix = await fetch("GET /api/v1/health/matrix")
    return [
        TargetSummary(
            target_id=...,
            lane=...,
            tier=...,
            health_color=t["color"],          # green/yellow/red/grey
            last_pass_ts=t["last_pass_ts"],
            applicable_cases=...,
        )
        for lane in matrix["lanes"]
        for target_id, t in lane["targets"].items()
    ]
```

任何 lane 没接 health beacon → `health_color="grey"` ("health_unknown") → conductor 拒绝该 lane 进 release matrix.

---

## 9. 演进规则

### 9.1 v1 → v2 演进

健康度 schema 演进沿用 `CaptureEvent v2` 模式:
- 只追加字段, 不删除/重命名
- `meta_json` 是 lane-specific metadata 的逃生口
- 任何破坏性 change 需要 ADR

### 9.2 v2 候选字段 (待 P5.C.5 后评估)

- `slo_target` — 每个 target 的 SLO 阈值 (e.g. 24h pass_rate ≥ 95%)
- `trend_7d` — 7 天趋势分类 (improving / stable / declining)
- `last_canary_drift_severity` — 最近 canary 漂移严重度
- `repair_history[]` — 该 target 历次 broken→fixed 的时间线

### 9.3 v2 不会做的

- 用户行为 telemetry (访问频率 / 使用时长 / 内容偏好) → 永久禁止 (PROJECT.md §7.1)
- 跨用户横向对比 → 永久禁止 (没有云端账号体系)
- Auto-disable lane → 不做; 退化由用户决定

---

## 10. Reference

### 10.1 上游契约
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` §2.3
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` §3.1
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-017-test-conductor-cross-lane-agent-contract.md` §3.2 `list_targets`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\PROJECT.md` §7.1 (local-first)

### 10.2 同形姊妹
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` (browser case)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` (desktop case)

### 10.3 实施落点 (P5.C.1)
- `pce_core/health.py` (待建) — `HealthBeacon` dataclass + `record_beacon()` + `current_health()` + `health_history()`
- `pce_core/db.py` migration 0013 — `health_beacons` 表
- `pce_core/server.py` — 4 个新 endpoints
- `pce_core/dashboard/health.html` + `health.js` — 3 个视图
- `pce_browser_extension_wxt/utils/capture-runtime.ts` — beacon 接入
- `tests/e2e_desktop_ui/drivers/base.py` — beacon 接入
- `pce_cli_wrapper/capture.py` — beacon 接入
- `pce_mcp_proxy/capture.py` — beacon 接入
- `pce_mcp/server.py` — beacon 接入

### 10.4 P5.C.1 测试
- `tests/test_health_beacon.py` (待建) — 至少 8 测试 (CRUD + 聚合 + 4 lane 接入冒烟)

---

## 11. 一句话锚点

**Health-as-data 是 Meta-Pipeline 三件套的第三件. 它不是为了好看的 dashboard, 是为了在项目所有者无法亲自维护时, 系统自己有一双能看到自己哪儿坏了的眼睛 — 这双眼睛通过 nightly probe + auto-issue + Test Conductor 把 broken 在客户报警前 12h 就转化为可执行的修复任务.**
