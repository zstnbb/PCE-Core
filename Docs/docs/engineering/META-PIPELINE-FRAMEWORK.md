# Meta-Pipeline Framework — 跨 lane 抓取 / 验证 / 健康度统一框架

> **Status**: canonical (adopted 2026-05-12 via ADR-019).
>
> **Version**: v1.0
>
> **Audience**: Cascade、后续接手 agent、所有 lane 实现者、Pro repo 维护者.
>
> **Authority chain**:
> - 上承策略层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\UNIVERSAL-CAPTURE-STACK-DESIGN.md` (UCS canonical 5 层) + `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` (4 plane × 6+1 type)
> - 上承决议层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md`
> - 上承决策层: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md` + `ADR-017-test-conductor-cross-lane-agent-contract.md`
> - 下指执行层: `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` (P5.C 子阶段)
>
> **本文件不替代**: UCS DESIGN (5 层定义), DESKTOP COGNITIVE FRAMEWORK (4 plane 定义), 各 lane 已有 case 标准 (T-case / D-case / C-case).
>
> **本文件提供**: 跨 lane 公共词汇, 三件套契约的工程级形式化, lane 与 plane / UCS 层的映射表, Test Conductor 接入规范.

---

## 1. 核心心智模型

### 1.1 PCE 是什么 (重申)

PCE 是 **本地 AI 交互捕获基础设施**. 长期产品路径: `记录 → 看见 → 理解 → 干预`. 当前在 "记录 → 看见" 收尾, 准备进入 "理解" 的工程基础.

### 1.2 三个正交轴

PCE 在过去 7 个月先后建立了 3 根认知轴:

```
轴 1 — UCS (Universal Capture Stack) 5 层 / ADR-009
  L0 Kernel · L1 TLS MITM · L2 Frida · L3 App hook (a-h) · L4 UI

轴 2 — Cognitive Framework 4 plane × 6+1 type / DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md
  N (网络) / H (进程) / M (MCP) / U (UI) × Type 1-7

轴 3 — Lane (本框架新增)
  Browser / Desktop / CLI / MCP × verification + health 契约
```

三轴正交, 缺一不可:
- 缺 UCS → 不知道在哪儿插管子
- 缺 Cognitive → 不知道捕获什么
- 缺 Lane → 不知道**怎么持续维护**

### 1.3 Lane 的定义

**Lane** = PCE 能在产品类别上独立运行的一组实施栈, 每条 lane 包含:
1. 一个或多个 UCS 层的实施 (`pce_proxy/`, `pce_browser_extension_wxt/`, `pce_mcp_proxy/`, ...)
2. 一组 case-as-data 标准 (T-case / D-case / C-case / M-case / K-case)
3. 一个 verification runner (`tests/e2e_probe/`, `tests/e2e_desktop_ui/`, ...)
4. 一组 health beacon 接入点

PCE v1.1 共 4 条 lane:

| Lane | 主 UCS 层 | 主 plane | 辅 UCS 层 | 辅 plane | case 命名 | runner |
|---|---|---|---|---|---|---|
| **Browser** | L3a 浏览器扩展 | H | L1 mitmproxy | N | T-case | `tests/e2e_probe/` |
| **Desktop** | L3d CDP / L3g 持久化 | H | L1 / L4b UIA | N / U | D-case (chat) + C-case (cowork) | `tests/e2e_desktop_ui/` |
| **CLI** | L3h CLI wrap | H1 (PATH-priority shim) | L1 | N | K-case (待定义) | `tests/e2e_l3h/` |
| **MCP** | L3f MCP middleware | M | (none) | — | M-case (待定义) | `tests/e2e_mcpb/` (实质待建) |

L0 / L2 / L3b / L4b 在 Pro repo 实现, 但通过 manifest 接入 Test Conductor (见 §6.4).

---

## 2. 三件套契约 (canonical)

任何 lane 进入 PCE 主线必须实现以下 3 件契约. 缺一就是 "scaffolding only", 不进 release matrix.

### 2.1 Capture-as-Data (`CaptureEvent v2`)

**已存在**, 不变. 定义于 `@f:\INVENTION\You.Inc\PCE Core\pce_core\capture_event.py`.

强约束:
- 所有 lane 必须 POST `/api/v1/captures/v2`
- schema 只追加, 不删除/重命名 (per ADR-010)
- `source_type` enum 涵盖所有 lane 的来源 (browser_extension / mcp_proxy / cli_wrapper / local_persistence / pce_mcp / 等)
- `layer_meta` 字段是 lane-specific metadata 的逃生口 (无需 schema migration)

### 2.2 Verification-as-Data (Case Standard)

**部分存在**, P5.C.0 显式化. 定义形式:

每个 lane 必须有一份 `<LANE>-CASE-EXECUTION-STANDARD.md` + 一份机器可读 Python dataclass.

| Lane | Markdown standard | Machine-readable | case 命名 | 状态 |
|---|---|---|---|---|
| **Browser** | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` | `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\execution_standard.py` | T00–T20 | ✅ 已存在 |
| **Desktop chat** | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` | `tests/e2e_desktop_ui/execution_standard.py` (P5.C.0 待建) | D00–D22 | 🟡 markdown ✅, dataclass 待建 |
| **Desktop cowork** | `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.B | 同上 | C00–C16 | 🟡 markdown ✅, dataclass 待建 |
| **CLI** | (待建) | (待建) | K-case | ⬜ P5.C.5+ |
| **MCP** | (待建) | (待建) | M-case | ⬜ P5.C.5+ |

每个 case 必须包含四元组 (沿用 `PCE-PROBE-E2E-EXECUTION-STANDARD.md` §"Global Pass Bar" 定义):

```python
@dataclass(frozen=True)
class CaseStandard:
    case_id: str        # T01 / D01 / C01 / ...
    name: str           # basic_chat / streaming_complete / ...
    capture: str        # 抓取层做什么算 PASS
    storage: str        # 存储层做什么算 PASS
    render: str         # 渲染层做什么算 PASS (即使 dashboard 还没做也要写预期合约)
    pass_gate: str      # 三段都过才算 PASS, 任一缺失算 GAP
    allowed_skip: tuple[str, ...]  # 可接受的 SKIP 原因
    strict_gap_on_skip: bool       # SKIP 是否强制视为 GAP
```

**禁止**: lane-specific case 标准没有四元组. 只有四元组齐才能被 Test Conductor 的 `list_cases()` 接受.

### 2.3 Health-as-Data (`HealthBeacon v1`)

**P5.C.1 新增**. schema:

```python
@dataclass
class HealthBeacon:
    lane: Literal["browser", "desktop", "cli", "mcp"]
    layer: str          # "L1" / "L3a" / "L3d" / "L3f" / "L3g" / "L3h" / ...
    target: str         # "chatgpt" / "claude_desktop" / "claude_code" / ...
    case_id: str | None # "T01" / "D03" / None for non-case-bound beacon
    status: Literal["pass", "fail", "skip", "degraded", "infra_error"]
    ts: float           # unix epoch seconds
    elapsed_ms: int | None
    meta_json: str      # lane-specific metadata as JSON string
    dom_selector_hits: dict[str, int] | None  # selector → 命中次数 (browser/desktop only)
    # 不包含: PII, content body, 用户 token, host 路径详情 (这些在 capture-as-data)
```

接入方式:

```python
from pce_core.health import emit_beacon
emit_beacon(
    lane="browser",
    layer="L3a",
    target="chatgpt",
    case_id="T01",
    status="pass",
    elapsed_ms=2_341,
    meta={"site_set": "first5", "selectors_used": [...]},
)
```

写入 SQLite `health_beacons` 表 (migration 0013, P5.C.1). dashboard `/dashboard/health` 聚合显示.

强约束:
- 4 lane 必须接入 (P5.C.1 释放门)
- 不上报到外部服务器 (per PROJECT.md §7.1 + ADR-019 §5.3)
- beacon 数据不含敏感信息 (与 raw_captures 分离)

---

## 3. Lane × UCS 层映射矩阵

为了让任何新 target 进入视野时能 5 分钟内被定位, 这是 v1.1 的完整矩阵:

| Lane | UCS 层 | 主 plane | 当前实施 (path) | OSS / Pro | case set | 健康度接入 |
|---|---|---|---|---|---|---|
| Browser | L1 (TLS MITM) | N | `pce_proxy/addon.py` | OSS | T01-T20 (网络通道) | P5.C.1 |
| Browser | L3a (扩展) | H | `pce_browser_extension_wxt/` | OSS | T01-T20 (DOM 通道) | P5.C.1 |
| Desktop | L1 (TLS MITM) | N | `pce_proxy/` (复用) | OSS | D01-D11 (网络通道) | P5.C.1 |
| Desktop | L3d (CDP launcher) | H | `pce_app/` (Squirrel/macOS) | OSS | D01-D22 (Cursor/Windsurf) | P5.C.1 |
| Desktop | L3f (MCP middleware) | M | `pce_mcp_proxy/` | OSS | D08, C00-C16 | P5.C.1 |
| Desktop | L3g (持久化) | N (磁盘) | `pce_persistence_watcher/` | OSS | C03, C09-C16 | P5.C.1 |
| Desktop | L4a (Clipboard) | U | `pce_core/clipboard_monitor.py` | OSS | (待 case-化) | P5.C.5+ |
| Desktop | L0 (Kernel) | N | `pce_agent_kernel/` (Pro) | **Pro** | (待 case-化) | manifest 接入 |
| Desktop | L2 (Frida) | N | `pce_agent_frida/` (Pro) | **Pro** | (待 case-化, P6) | manifest 接入 |
| Desktop | L3b (Electron preload) | H | `pce_agent_electron/` (Pro) | **Pro (LOCKED H4)** | (deprecated) | — |
| Desktop | L4b (UIA / AX) | U | `pce_agent_ax/` (Pro) | **Pro** | (待 case-化, P6) | manifest 接入 |
| CLI | L3h (CLI wrap) | H1 | `pce_cli_wrapper/` | OSS | K-case 待建 | P5.C.1 |
| CLI | L1 (TLS MITM) | N | `pce_proxy/` (复用) | OSS | K-case 待建 | P5.C.1 |
| MCP | L3f (MCP middleware) | M | `pce_mcp_proxy/` (复用 from desktop) | OSS | M-case 待建 | P5.C.1 |
| MCP | (姿态 A) | M | `pce_mcp/` server | OSS | (PCE 自身作 MCP server) | P5.C.1 |

**应用此矩阵的方式**: 当一个新产品进入视野 (如 "Cline 是 D0 还是 D1?"), 走以下流程:

1. 用 Cognitive Framework 找它的 plane / type (Cline = Type 2 IDE-class MCP-aware → plane H + M)
2. 用 SITE-TIER-MATRIX / DESKTOP-PRODUCT-MATRIX 评估其 tier (persona alignment)
3. 用本矩阵找现有 lane 是否能复用 (Cline = Desktop lane via L3d + L3f)
4. 如果能复用 → 增加 manifest, 接入 Test Conductor `list_targets`
5. 如果不能复用 → 走 ADR 流程提案新 lane 或新 UCS 层

---

## 4. AdapterContract — 跨 lane 适配器抽象

### 4.1 现状 — 各 lane 已有的雏形

| Lane | 雏形 base class | 路径 |
|---|---|---|
| Browser | `BaseProbeSiteAdapter` | `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py` |
| Desktop | `DesktopDriver` ABC | `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\base.py` |
| CLI | (无显式 base, 散落 in `pce_cli_wrapper/`) | — |
| MCP | (无显式 base, 散落 in `pce_mcp_proxy/`) | — |

### 4.2 Canonical 接口 (formalised)

每个 adapter / driver 必须实现以下 5 组方法:

```python
class AdapterContract(ABC):
    """跨 lane 适配器统一契约."""

    # 静态属性 (manifest 化, P5.C.4 抽 YAML)
    name: str                    # "chatgpt" / "claude_desktop" / "claude_code"
    lane: Literal[...]           # "browser" / "desktop" / "cli" / "mcp"
    tier: Literal["S0","S1","S2","S3","SX","D0","D1","D2"]
    primary_layer: str           # "L3a" / "L3d" / "L3h" / "L3f"
    fallback_layers: tuple[str, ...]
    plane: Literal["N","H","M","U"]

    # 1. 生命周期
    @abstractmethod
    def setup(self) -> None: ...
    @abstractmethod
    def teardown(self) -> None: ...

    # 2. 健康检查
    @abstractmethod
    def health_check(self) -> HealthBeacon: ...

    # 3. Case 执行
    @abstractmethod
    def list_cases(self) -> list[CaseStandard]: ...
    @abstractmethod
    def run_case(self, case_id: str, mode: Literal["live","replay"]) -> RunResult: ...

    # 4. Selector / Locator (browser/desktop only, lane-specific)
    @abstractmethod
    def get_selectors(self) -> dict[str, list[str]]: ...
    """Returns multi-fallback selector lists, e.g. {"send_button": [..., ...]}"""

    # 5. Canary
    @abstractmethod
    def get_canary_payload(self, case_id: str) -> dict | None: ...
    """Returns the JSON payload to be schema-snapshotted by Test Conductor."""
```

### 4.3 实施路径 (P5.C.4)

P5.C.4 不强制重写所有 adapter — 只把 ChatGPT / Claude / Gemini 三个 S0 站点 (browser lane) 抽到 YAML 化 + 显式实现 `AdapterContract`. 其他站点 carry forward 现有 `BaseProbeSiteAdapter` 直到自然 refactor.

桌面 lane 的 `DesktopDriver` 在 P6 才统一到 `AdapterContract` (P6 也是 UIA / AX selector 抽 YAML 的窗口).

---

## 5. Test Conductor 接入规范 (refines ADR-017)

### 5.1 Conductor 的位置

```
┌──────────────────────────────────────────────────────────┐
│  Tier A — Agent Surface                                  │
│  pce_test_conductor/                                     │
│  · 8 MCP tool                                            │
│  · FailureKind 9 值 enum                                 │
│  · canary schema 仓                                      │
│  · patch templates                                       │
└────────────────┬─────────────────────────────────────────┘
                 │ MCP stdio JSON-RPC 2.0
        ┌────────┴────────┐
        ▼                 ▼
┌──────────────┐   ┌──────────────┐
│ Browser Lane │   │ Desktop Lane │   (CLI Lane / MCP Lane 同形)
│ T-case       │   │ D-case+C-case│
└──────┬───────┘   └──────┬───────┘
       │                  │
       └────────┬─────────┘
                ▼
       ┌──────────────────┐
       │ pce_core/health  │ ← 三件套之一
       │ · health_beacons │
       │ · /api/v1/health │
       └──────────────────┘
                │
                ▼
       ┌──────────────────┐
       │ capture_verifier │ ← HTTP closed-loop
       │ (single shared)  │
       └──────────────────┘
```

### 5.2 Lane 注册 (manifest 形式)

每条 lane 在 `pce_test_conductor/targets/<id>.yaml`:

```yaml
# 例: pce_test_conductor/targets/browser_chatgpt.yaml
target_id: browser_chatgpt
lane: browser
tier: S0
plane: [N, H]      # 多 plane (multi-channel redundancy)
primary_layer: L3a
fallback_layers: [L1]
adapter_class: tests.e2e_probe.sites.chatgpt.ChatGPTAdapter
runner:
  type: pytest
  args:
    - tests/e2e_probe/test_matrix.py
    - -k
    - chatgpt
case_standard_module: tests.e2e_probe.execution_standard
canary_dir: pce_test_conductor/canaries/browser_chatgpt/
health_beacon_filter:
  lane: browser
  target: chatgpt
ownership:
  codeowner: "@zstnbb"
  fallback_owner: "@zstnbb"
```

```yaml
# 例: pce_test_conductor/targets/desktop_claude_chat.yaml
target_id: desktop_claude_chat
lane: desktop
tier: D0
plane: [N, M]
primary_layer: L3g    # 持久化为主
fallback_layers: [L1, L3f]
adapter_class: tests.e2e_desktop_ui.drivers.claude_desktop.ClaudeDesktopDriver
runner:
  type: pytest
  args: [tests/e2e_desktop_ui/cases]
  marker_filter: p1_chat
case_standard_module: tests.e2e_desktop_ui.execution_standard
canary_dir: pce_test_conductor/canaries/desktop_claude_chat/
health_beacon_filter:
  lane: desktop
  target: claude_desktop
ownership:
  codeowner: "@zstnbb"
  fallback_owner: "@zstnbb"
```

### 5.3 Pro lane 接入

Pro repo `pce-pro` 通过同形 manifest 接入 conductor, 但 manifest 文件落在 OSS repo (因为 conductor 是 OSS):

```yaml
# pce_test_conductor/targets/desktop_chatgpt_pinned.yaml (P6 之后)
target_id: desktop_chatgpt_pinned
lane: desktop
tier: D2
plane: [N]
primary_layer: L2     # Frida (Pro)
fallback_layers: []
adapter_class: pce_pro.agent_frida.adapters.chatgpt_desktop.ChatGPTDesktopFridaAdapter
adapter_provider: pro    # ← 显式标记 Pro
# OSS conductor 调用 Pro adapter 时, Pro 包必须 installed (用户买了订阅)
runner:
  type: pytest
  args: [tests-pro/e2e_pinned/]
ownership:
  codeowner: "@zstnbb"
  fallback_owner: "@pce-pro-maintainer"
```

**注意**: 这不违反 ADR-010 import direction (OSS 不 import Pro), 因为 manifest 是 metadata, conductor 是动态 dispatch (`importlib`), 在 Pro 包未安装时 OSS 这个 target 会显示 `infra_error: pro_module_unavailable`.

---

## 6. 三件套之外的辅助契约 (推荐, 不强制)

### 6.1 Replay Fixture

每个 case 在 first PASS 后保存 raw payload 到 `pce_test_conductor/fixtures/<target>/<case>/`. Conductor `run_case(mode="replay")` 用 fixture 跑 → live 30s vs replay 0.5s.

应用场景: 调试 normalizer 时不需要重新驱动 UI.

### 6.2 Selector Drift Watcher (ADR-011 G3 落地)

每个 browser adapter 的 `get_canary_payload` 返回 DOM root snapshot. Conductor 自动 schema 化 + diff. 任何 `selector_changed_pending_review` 事件 → 自动开 PR.

### 6.3 LLM-Assisted Repair (ADR-011 G9 落地, ADR-017 §3.5 形式)

`propose_patch` 调用 LLM:

```python
def propose_patch(run_id: str) -> list[PatchProposal]:
    run = get_run(run_id)
    failure = run.failure
    
    if failure.kind == "UI_SELECTOR_MISS":
        # 输入: failed selector + DOM diff vs canary
        # 输出: 候选新 selector list with confidence
        return llm_suggest_selectors(...)
    elif failure.kind == "SCHEMA_DRIFT":
        # 输入: 旧 schema + 新 payload + normalizer 代码片段
        # 输出: unified diff for normalizer
        return llm_suggest_normalizer_patch(...)
    elif failure.kind == "CONTENT_BLOCK_UNKNOWN":
        # 输入: 新发现的 content_block.type + 现有 type 处理逻辑
        # 输出: 添加新 elif 分支的 unified diff
        return llm_suggest_block_handler(...)
    # ...
```

LLM 用户 opt-in (Anthropic API key via env var). 默认走 Claude Sonnet (经验上对代码 patch 任务质量好), 用户可换 OpenAI / 自部署模型.

**禁止**: conductor 自动应用 patch. 永远是 patch-as-data, agent / 人工 review.

---

## 7. 与既有架构的整合点

### 7.1 与 UCS 5 层的关系

UCS 给 PCE 定义了 "在哪儿插管子" 的物理位置. Meta-Pipeline 不改 UCS, 只是把 UCS 层在 lane × case 维度上**做账**. 一个 UCS 层 (如 L1 mitmproxy) 同时服务多个 lane (browser + desktop + cli) — 这在过去是隐式的, 现在通过 manifest 显式化.

### 7.2 与 Cognitive Framework 4 plane 的关系

Cognitive Framework 给桌面端定义了 "捕获面" 概念. Meta-Pipeline 把它扩展到所有 lane: 任何 D0/S0 target 必须 ≥2 plane (契约 B). 浏览器侧的 N/H 双通道 (L1 + L3a) 也是这个原则的早期实例.

### 7.3 与 Open Core boundary 的关系

ADR-010 的 import direction 不变. Meta-Pipeline 在跨 OSS/Pro 时通过两个机制保持边界:

- **Manifest dispatch**: conductor 用 `importlib` 动态加载 adapter, Pro 模块未装时显示 `infra_error`
- **CaptureEvent v2 HTTP**: Pro adapter 写入数据走 `POST /api/v1/captures/v2` (网络层, 非 in-process)

### 7.4 与 PROJECT.md 阶段划分的关系

P5.C 是 P5.B 与 P6 之间的新阶段. `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\PROJECT.md` §11 加 P5.C 行 (P3 后续动作清单).

---

## 8. 反模式 (来自决议 §4)

任何 PR / commit 触及以下任一行为 → CI fail 或 review 拒收:

| 反模式 | 检测 |
|---|---|
| 临时诊断脚本散根目录 | gitignore + pre-commit hook 拦截 `.diag_*.py` / `.tmp_*.py` 等 |
| 单 plane 部署 D0/S0 | conductor `release_check` tool 在 release CI fail |
| selector 写在代码里 | P5.C.4 之后 lint 规则 + PR template 强制勾选 |
| 跳过 health beacon | conductor `list_targets` 标记 `health_unknown` + dashboard 标黄 |
| 跳过 verification standard | conductor `list_cases` 返回空 → `release_check` fail |
| Pro 模块跳过 manifest | conductor 显示 `unregistered_pro_module` warning |
| AI auto-fix 直接 push | patches-as-data 强制原则, conductor 不实现 apply |
| 把维护负担留给 owner | 有 auto-issue 但无 CODEOWNERS → CI warn |
| dashboard 完全延后 | 健康度 dashboard 是 P5.C.1 必交付, 不能延后 |
| 等所有 lane 做完再标准化 | 已经在 P5.C 反过来 — 先冻结契约再做 lane |

---

## 9. 演进与版本

### 9.1 v1.0 (本文件, 2026-05-12)

- 4 lane (Browser/Desktop/CLI/MCP) × 三件套契约定型
- AdapterContract 5 组方法
- HealthBeacon v1 schema
- Test Conductor manifest 规范

### 9.2 v1.1 (P5.C 完成后, 预计 ~2026-08)

- 新增 mobile lane (如果 P7 启动)
- HealthBeacon v2 (加 trend / SLO 字段)
- AdapterContract 演进 (合并 desktop UIA selector 抽象)

### 9.3 v2.0 (P8 Capture Supervisor 之后)

- Supervisor 取代 manual lane 切换
- Conductor + Supervisor 合并为 "Self-Healing Pipeline"

### 9.4 演进规则

- 三件套契约 (capture / verification / health) 永远只追加, 不删除/重命名
- 任何破坏 backward-compat 的修改需要 ADR
- HealthBeacon schema 演进沿用 CaptureEvent v2 的 `meta_json` 逃生口模式

---

## 10. 引用清单

### 10.1 上游必读

- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\PROJECT.md` — 项目边界
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\UNIVERSAL-CAPTURE-STACK-DESIGN.md` — UCS 5 层
- `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` — 4 plane × 6+1 type
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\ARCHITECTURE.md` — 架构 (v0.4)

### 10.2 决议 / ADR

- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\decisions\2026-05-12-meta-pipeline-and-maintenance-strategy.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-017-test-conductor-cross-lane-agent-contract.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-011-probe-remote-agent-autonomous-testing.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-010-open-core-module-boundary.md`

### 10.3 Standard

- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` — T-case (browser)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-D-CASE-EXECUTION-STANDARD.md` — D-case (desktop chat)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.B — C-case (desktop cowork)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md` — Health beacon 矩阵契约
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\SITE-TIER-MATRIX.md` — 浏览器 tier (frozen)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` — 桌面 tier (active)

### 10.4 Handoff (active)

- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-META-PIPELINE-KICKOFF-2026-05-12.md` — P5.C 入口
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-IDE-DESKTOP-KICKOFF.md` — P5.B carry-on (cowork + code 余项)

### 10.5 实施雏形

- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py` — Browser adapter base class
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\base.py` — Desktop driver base class
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\execution_standard.py` — Browser case standard dataclass
- `@f:\INVENTION\You.Inc\PCE Core\pce_core\capture_event.py` — CaptureEvent v2 (capture-as-data)
- `pce_core/health.py` — HealthBeacon (P5.C.1 待建)
- `pce_test_conductor/` — Test Conductor (P5.C.2 待建)

---

## 11. 一句话锚点

**Meta-Pipeline = capture-as-data + verification-as-data + health-as-data 在 4 个 lane 上的统一契约. 三件套不齐, 这个 lane 不算被纳入 PCE 主线; 三件套齐, 任何 agent / 贡献者 / Pro 模块都能像插件一样接入或扩展 — 这是项目所有者无法主体维护时, 系统能继续生存的物理前提.**
