# ADR-017: 跨 lane 测试编排与 Agent 可调用契约（Test Conductor）

- Status: Adopted (drafted 2026-05-09; MVP shipped 2026-05-12 in P5.C.2 — `pce_test_conductor/` 8 MCP tools + 9 FailureKind + canary store + 3 patch templates)
- Date: 2026-05-09
- Phase: P5.B.4（proposed；follows ADR-016 Phase 3 收尾）
- Related: ADR-009（UCS canonical 层枚举）、ADR-010（Open Core 边界）、ADR-011（PCE Probe 远程化与 agent 自治测试通道，本 ADR 接续其 local 部分；远程 VPS 部分仍延期）、ADR-013（OSS 归属论据）、ADR-015（UCS L3f）、ADR-016（CDP launcher + `.mcpb` 打包）
- Supersedes: 无（首份本主题 ADR）
- Refines: ADR-011 §"已登记的 9 个 gap" 中 **G3**（watcher / DOM baseline）与 **G9**（LLM auto-fix proposal）— 本 ADR 把 "agent 侧实现，插件不投资" 进一步细化为 "**patches as data，agent 用自有 edit 工具落地**"，并在 conductor 层提供 patch 生成 + 重跑验证的合规 surface

---

## 1. 决议（一句话）

**新建 `pce_test_conductor/`（OSS, Apache-2.0）作为跨 lane 测试编排层与 Agent 可调用 MCP 契约**：以 Adapter / Case-as-Data / Verifier 三层契约为骨架，把现有 `pce_probe/`（browser lane, P5.A 落地）与新建 `tests/e2e_desktop/`（desktop lane via L3d CDP, ADR-016 Phase 3）统一到同一组 8 个 MCP tool 之下；引入失败本体枚举（`FailureKind`）、JSON Schema canary 快照、模板化补丁建议，**但补丁的物理应用由调用方 agent（如 Cascade）通过其自有 edit/apply_patch 工具完成**，conductor 只负责生成 patch data 与 verify-by-rerun，与 ADR-011 G9 "agent 侧实现，插件不投资" 完全一致。

---

## 2. 背景：为什么需要一层 Conductor

### 2.1 现有可复用资产（必须继承，不能推翻）

- **Browser lane**: `pce_probe/` (P5.A 落地) — WS 22 个动词、matrix runner、`summary.json` / `triage --json` 机器可读输出、14 站 × 21 case 笛卡尔积、`Docs/testing/PCE-PROBE-AGENT-LOOP.md` runbook
- **Browser autopilot 补充**: `tests/e2e/test_chatgpt_full.py` + `tests/e2e/sites/base.py BaseSiteAdapter`（8 个动词）+ `tests/e2e/test_claude_full.py` 等 5 套 full-fidelity 矩阵（T01–T20 级别）
- **Desktop lane（新建中）**: ADR-016 Phase 3 落地的 `pce_app_launcher/` + `tests/e2e_desktop/` (目前 stub Playwright)
- **Verifier**: `tests/e2e/capture_verifier.py` — HTTP closed-loop、token round-trip、`wait_for_session_matching` 等 6 个 wait 助手
- **Reports**: `tests/e2e/reports/<target>/<ts>/{summary.json, T*.json}`

### 2.2 触发本 ADR 的 4 个新事实（2026-05-09）

1. **ADR-016 引入 desktop lane**：Claude Desktop / Cursor / Windsurf 三个新 target 全部进测试矩阵，但目前各自独立的入口。**没有共享的 Adapter / Case / Verifier 抽象**，三个 target 等于三套并行流水线，浪费工程并且让 cross-lane 比较（如"同一段对话的 H 面 vs M 面捕获是否一致"）做不了。
2. **桌面 H 面更新打穿比浏览器更隐蔽**：ADR-016 §6 已列 4 个 open question（CDP thinking 可见性、`.mcpb` directory review、macOS 快捷方式、CDP launcher 多窗口）。Anthropic 一次小版本就可能改 response schema 或加新 `content_block.type`，**不主动发现就只能等用户报障**。需要 schema canary 主动巡检。
3. **ADR-011 G3 / G9 契约缺失**：ADR-011 锁了 3 条铁律但只把 G1–G9 登记成 known-gaps，没给实施契约。其中 G3（DOM baseline watcher）与 G9（LLM auto-fix）**与本 ADR 范围正面相关**——本 ADR 在 conductor 层把它们细化到 PR-ready 程度，但仍尊重 G9 "agent 侧实现，插件不投资" 的边界。
4. **MCP 已成 agent 的一等接口**：ADR-016 把 `pce_mcp/` 打成 `.mcpb` 接进 Claude Desktop。conductor 复用同一协议（stdio JSON-RPC 2.0）暴露测试能力，意味着 **Cascade / Claude Desktop / Claude Code 任一 LLM agent 都可直接调**——零额外集成成本。

### 2.3 不做 Conductor 会发生什么（reductio ad absurdum）

- 每个新 target 复制粘贴一份 `test_xxx_full.py`，到 v1.2 时有 6+ 套（chatgpt / claude_web / claude_desktop / cursor / windsurf / copilot），任何 verifier 改进要改 6 处
- Schema 漂移要等 e2e_probe / autopilot 偶发跑到才暴露（ADR-011 自己点出"best-of 历史最长间隔 2 周"）
- Cascade 想跑测试只能 shell out `pytest` + 解析 stdout，等于把工具能力用绳子捆死在终端
- ADR-011 G3 watcher 永远停留在"概念已锁、实现没人写"的状态（已 deferred 6 个月）

---

## 3. 决议内容

### 3.1 三层契约（骨架，继承现有 pce_probe / autopilot 模式）

```
┌─────────────────────────────────────────────────────────┐
│  Tier A · Agent Surface (新, OSS)                       │
│  pce_test_conductor/                                    │
│  ├── __main__.py     # MCP stdio server entry           │
│  ├── server.py       # 8 个 tool 实现                   │
│  ├── targets/<id>.yaml  # 每 target 一份 manifest       │
│  ├── canaries/<id>/<case>.schema.json   # 见 §3.4       │
│  ├── classifier.py   # FailureKind 推断                 │
│  ├── patches/        # 见 §3.5 模板                     │
│  └── replay.py       # 离线 fixture 回放                │
└────────────────┬────────────────────────────────────────┘
                 │ MCP stdio JSON-RPC 2.0
        ┌────────┴────────┐
        ▼                 ▼
┌──────────────┐   ┌──────────────┐
│ Browser Lane │   │ Desktop Lane │
│ (现有)       │   │ (新, mirror) │
│              │   │              │
│ pce_probe/   │   │ tests/       │
│ tests/e2e/   │   │ e2e_desktop/ │
│ ├ sites/     │   │ ├ apps/      │
│ │ base.py    │   │ │ base.py    │
│ │ chatgpt.py │   │ │ claude_    │
│ │ claude.py  │   │ │ desktop.py │
│ │ ...        │   │ │ cursor.py  │
│ └ test_*_    │   │ └ test_*_    │
│   full.py    │   │   full.py    │
└──────┬───────┘   └──────┬───────┘
       │                  │
       └────────┬─────────┘
                ▼
       ┌────────────────┐
       │ Tier C · 共享  │
       │ capture_       │
       │ verifier.py    │
       │ (单文件，HTTP) │
       └────────────────┘
```

**关键约束**：
- conductor **不重写**任何现有 case 或 site adapter
- conductor 通过**调用 pytest collection API + subprocess** 跑现有 `tests/e2e/test_*_full.py`、`tests/e2e_desktop/test_*_full.py`（与 `pce_probe/` 矩阵 runner 同形）
- conductor 添加的是"上层编排"——发现 / 触发 / 收集报告 / 失败分类 / 补丁建议——而不是替换底层执行

### 3.2 八个 MCP tool 契约

每个 tool 的 input/output 是 JSON-serialisable，不允许返回 Python 对象引用。

```
┌──────────────────────┬──────────────────────────────────────┐
│ Tool                  │ 用途                                  │
├──────────────────────┼──────────────────────────────────────┤
│ list_targets()        │ 枚举可用 target (browser+desktop)    │
│ list_cases(target)    │ 列出该 target 的所有 case            │
│ run_case(target,case, │ 执行单 case，return run_id+evidence  │
│   mode="live"|"replay"│ replay 走 fixture，0.5s vs live 30s  │
│ get_run(run_id)       │ 拿历史 run 详情                       │
│ diff_canary(target,   │ JSON Schema diff vs 最近 known-good  │
│   case)               │                                      │
│ classify_failure(     │ 把 run.error 映射到 FailureKind      │
│   run_id)             │ enum + 字段路径 + severity           │
│ propose_patch(run_id) │ 返回 list[PatchProposal] (diff 数据) │
│                       │ 不应用！见 §3.5                       │
│ verify_patch(target,  │ 假设外部 agent 已落 diff，重跑 case  │
│   case)               │ 返回新 run 结果                       │
└──────────────────────┴──────────────────────────────────────┘
```

**典型返回 schema**（举 `run_case` + `propose_patch` 两例）：

```json
// run_case →
{
  "run_id": "20260509-225432-claude_desktop-T03",
  "target": "claude_desktop",
  "case_id": "T03",
  "status": "fail",
  "elapsed_s": 28.4,
  "started_at": 1746823472.1,
  "evidence": {
    "captures_path": "...reports/.../captures.json",
    "messages_path": "...reports/.../messages.json",
    "screenshot_path": "...png",
    "raw_payload_path": "...json"
  },
  "failure": {
    "kind": "CONTENT_BLOCK_UNKNOWN",
    "field_path": "$.content[*].type",
    "expected_set": ["text", "tool_use", "thinking"],
    "actual_set": ["text", "tool_use", "thinking", "server_tool_use"],
    "severity": "soft",
    "first_seen_payload": "reports/.../body.json"
  }
}

// propose_patch →
{
  "run_id": "20260509-225432-claude_desktop-T03",
  "proposals": [
    {
      "patch_id": "p-001",
      "kind": "add_content_block_type",
      "files": ["pce_core/normalizer/anthropic.py"],
      "unified_diff": "--- a/...\n+++ b/...\n@@ -395,6 +395,12 @@\n+        elif btype == \"server_tool_use\":\n+            ...\n",
      "rationale": "新 type='server_tool_use' 在 _extract_rich_blocks 的 elif 链中没有 handler；按 'tool_use' 同型扩展 attachments={type:'tool_call', ...}",
      "confidence": 0.85,
      "test_targets": ["claude_desktop:T03", "claude_web:T03"]
    }
  ]
}
```

### 3.3 失败本体（`FailureKind` enum）

闭集 9 个值。每个值配套强制字段。

```
LOGIN_WALL          // adapter 检测到登录页 → required_field: site_url
UI_SELECTOR_MISS    // browser only：DOM 元素找不到 → selector, fallback_tried
NETWORK_NOISE_MISS  // 抓包到了但被 noise filter 过滤 → host, path
SCHEMA_DRIFT        // payload 字段消失/类型变 → field_path, expected_type, actual_type
URL_PATTERN_DRIFT   // 端点路径变 → old_path, new_path_candidate
CONTENT_BLOCK_UNKNOWN  // §3.2 例 → field_path, expected_set, actual_set, severity
RACE_TIMEOUT        // verifier 等不到 → waited_s, last_seen_count
INFRA               // PCE Core 没起 / cdp port 占用 → component, hint
UNKNOWN             // classifier 无法归类，必须人工 triage
```

`severity` 字段:
- `hard` = 现有字段消失/类型变、URL 路径已替换 → 不修代码就坏
- `soft` = 加了新字段、新 enum 值 → 不修也能跑，但漏抓新内容
- `info` = 只是数量/时间漂移 → 可能是真 bug 也可能是测试 brittleness

### 3.4 Canary schema 快照

**位置**: `pce_test_conductor/canaries/<target>/<case_id>_<endpoint>.schema.json`

**生成方式**:
1. 任意一次 `run_case` 成功后，conductor 自动用 [genson](https://pypi.org/project/genson/) 从 `evidence.raw_payload_path` 推断 JSON Schema
2. 若该路径已有 schema → 走 `merge`（genson 支持），把新观察到的 type/required 合并进去
3. 若 merged schema 与原 schema 字符串等价 → 不写盘
4. 否则写盘 + 触发一个 `selector_changed_pending_review` 事件（**不污染 fail 计数**，per ADR-011 铁律 2）

**diff 算法**: `diff_canary(target, case)` 调用 `jsonschema-diff` lib，输出三类条目：
- `added_property` (severity=soft)
- `removed_property` (severity=hard)
- `changed_type` (severity=hard)
- `enum_extension` (severity=soft, 当 enum 列表只新增不移除)

**git 存档**: canaries 目录走 git 提交（与 ADR-011 G3 "DOM baseline 存档在仓" 铁律对齐）。任何 schema 变更需要 PR review。

### 3.5 补丁模板（patches as DATA，不是 action）

`pce_test_conductor/patches/` 下放 3 类模板（v1 范围）：

| 模板 ID | 触发 FailureKind | 输出 diff 范围 |
|---|---|---|
| `add_content_block_type` | `CONTENT_BLOCK_UNKNOWN` (soft) | `pce_core/normalizer/<provider>.py` 的 elif 链追加 |
| `add_url_path` | `URL_PATTERN_DRIFT` (hard) | `pce_core/normalizer/<provider>.py` 的 `_PATHS` set 追加 |
| `widen_schema_field` | `SCHEMA_DRIFT` (soft) | `pce_core/models.py` 加 `Optional[...]` |

**关键约束（与 ADR-011 G9 对齐）**：

> conductor **永远不**调用 `edit_file` / `apply_patch` 类副作用工具。`propose_patch` 返回的 `unified_diff` 是**字符串数据**；调用方 agent（Cascade / Claude Code / 任意 LLM）拿到后**用其自有 edit 工具落到磁盘**，然后调用 `verify_patch(target, case)` 让 conductor 重跑验证。

这条约束让 conductor 保持 **read + classify-only**，与 ADR-011 铁律一致：
- conductor 不需要写权限到代码库
- conductor 不需要 git 操作
- conductor 不需要分支管理
- 全部 mutation 落到 agent 的 audit log（agent 端有自己的 trace），conductor 端只记录"我建议了什么 + 验证结果"

### 3.6 Browser lane 改造（最小入侵）

**不动 `pce_probe/` 内部**。只在 conductor 中做：
- `pce_test_conductor/lanes/browser.py` —— `run_case` 时 subprocess `python -m pce_probe matrix --case=T03 --site=chatgpt --json-out=...`
- 收集 `summary.json` + `triage --json` 输出，重塑成 conductor 的 `run_case` 返回格式
- `tests/e2e/test_chatgpt_full.py` 等 5 个 full 矩阵作为补充：通过 pytest plugin (`-p pce_test_conductor.pytest_collector`) 暴露 case 列表

`pce_probe/` 与 `tests/e2e/test_*_full.py` 仍可独立跑（开发者本机不需要装 conductor）。conductor 是**附加层**，不是替代层。

### 3.7 Desktop lane 新建（mirror browser）

```
tests/e2e_desktop/
├── conftest.py                  # pce_app_launcher 起 app + Playwright connect_over_cdp
├── capture_verifier.py          # 引用 tests.e2e.capture_verifier 同一文件（symlink 或 import）
├── apps/
│   ├── __init__.py
│   ├── base.py                  # BaseAppAdapter（8 动词 mirror BaseSiteAdapter）
│   ├── claude_desktop.py
│   ├── cursor.py                # P5.B.4 后期
│   └── windsurf.py              # P5.B.4 后期
├── test_claude_desktop_full.py  # T01–T16（mirror chatgpt_full）
└── reports/<app>/<ts>/
```

`BaseAppAdapter` 8 个动词与 `BaseSiteAdapter` **同名同语义**（仅驱动从 Selenium 换 Playwright over CDP）：

```python
class BaseAppAdapter:
    name: str
    cdp_endpoint: str = "http://127.0.0.1:9222"

    def launch(self) -> LauncherHandle: ...           # 起 app（用 pce_app_launcher）
    def attach(self, cdp_url: str) -> Page: ...       # connect_over_cdp
    def send_message(self, page, msg) -> bool: ...
    def wait_for_response(self, page) -> bool: ...
    def upload_paths(self, page, paths, kind) -> bool: ...
    def trigger_manual_capture(self, page) -> None: ...
    def take_screenshot(self, page, suffix) -> str: ...
    def run_test(self, page) -> AppResult: ...
```

### 3.8 OSS / Pro 归属

`pce_test_conductor/` 与 `tests/e2e_desktop/` 全部归 **OSS（Apache-2.0）**，论据与 ADR-013 / ADR-015 / ADR-016 完全平行：

1. 测试编排能力直接关系到"用户能不能信任 PCE 抓得全"，paywall 化等于卖"我们的代码不会偷偷退化"——这是基础信任承诺，不可商品化
2. 与捕获主路径耦合度极低（只读 PCE Core HTTP API），不依赖任何 closed-source 资产
3. Pro 路径上的"fleet 测试编排 / 多租户 dashboard / 长期 trend 分析"可以在 OSS conductor 之上构筑，不污染本包

### 3.9 与 ADR-011 的关系（critical disambiguation）

| ADR-011 范围 | 本 ADR 是否覆盖 | 说明 |
|---|---|---|
| G1 远程 host + HMAC | ❌ 不覆盖 | 仍延期；conductor 走 **local stdio MCP**，不走 HTTP，不需要远程化 |
| G2 PCE Core HTTP probe 路由 | ❌ 不覆盖 | 仍延期 |
| **G3 watcher / DOM baseline** | ✅ 部分细化 | conductor §3.4 canary store 是 G3 的 schema 维度对应物。**DOM baseline 维度仍 deferred**（pce_probe 里 DOM 部分由 ADR-011 G3 接管，本 ADR 不替它做） |
| G4 Dockerfile / Xvfb | ❌ 不覆盖 | 仍延期 |
| G5 login_state export/import | ❌ 不覆盖 | 仍延期 |
| G6 login_health watcher | ❌ 不覆盖 | 仍延期 |
| G7 调度器 cron / systemd | ❌ 不覆盖 | 仍延期 |
| G8 多租户 | ❌ 不覆盖 | ADR-011 已说"不在 v1.x 投资" |
| **G9 LLM auto-fix proposal** | ✅ 接续细化 | 本 ADR §3.5 把 "patches as data" 落地到 PR-ready 契约，但**保持 ADR-011 stance：apply 在 agent 侧** |

ADR-011 三条铁律全部**仍然成立**且本 ADR 不挑战：
- 铁律 1 (远程 → auth/TLS)：本 ADR 走 stdio MCP，**根本不远程化**，铁律 1 不适用
- 铁律 2 (watcher 独立于矩阵 run、不污染 fail 计数)：§3.4 canary 触发 `selector_changed_pending_review` 事件，**不计入 fail**，符合
- 铁律 3 (登录态可移植性优先于自动登录)：本 ADR 不动登录态领域

### 3.10 测试义务

任何对 `pce_test_conductor/` 的修改 PR 必须：
- 引用本 ADR 编号 `ADR-017`
- `tests/test_pce_test_conductor.py` 全绿（最少覆盖：8 tool 的 stdio JSON-RPC 契约、FailureKind 9 值的 classifier 单测、3 个 patch 模板的 diff 生成）
- 不退化已有 `pce_probe/` 24 个测试 + `tests/e2e/test_chatgpt_full.py` 等 5 套 full 矩阵的 pass-rate
- 新增 FailureKind 必须扩 enum 而非塞 UNKNOWN
- 新增 patch 模板必须配套 1+ 真实 fixture 测试

---

## 4. 决议依据

### 4.1 为什么不直接扩 `pce_probe/` 而新建 conductor

`pce_probe/` 的设计中心是 **WS 动词 + 矩阵 runner + 站点适配器**，专为 browser autopilot 优化（22 个动词大部分是 DOM 操作）。把 desktop lane 塞进去会：
- 污染 `pce_probe/sites/` 抽象（site ≠ app；CSS selector ≠ CDP target）
- 强行让 desktop 走 22 动词框架，但 desktop 实际只需 8 个（CDP 不需要 stealth、不需要 file input fallback、不需要 cookie 管理）
- 阻碍 `pce_probe/` 自身按 ADR-011 G3 / G7 演进

**conductor 取舍**：作为更高一层的**编排层**——下面的 lane 各自演进（browser lane 继续按 ADR-011 路线、desktop lane 按 ADR-016 路线），conductor 只暴露统一的 case-as-data + tool 契约给 agent。这与 Linux kernel 的 VFS 层抽象哲学相同。

### 4.2 为什么 patch 是 data 而不是 action（与 ADR-011 G9 对齐）

ADR-011 line 66 明文："G9 Selector 变更 → adapter patch 的 LLM auto-fix proposal | agent 侧实现，**插件不投资**"。

本 ADR 完全 honor 这条 stance，理由细化为：
1. **审计责任**：patch 应用是 mutation，每次 mutation 必须可追溯到具体 agent + 具体 prompt 上下文。conductor 作为工具不该承担这个审计责任，它只做"建议"
2. **权限边界**：conductor 是 OSS daemon，安装到任何用户机器上即获得读 PCE Core 数据 + 跑测试的权限。再多一份"改 PCE 自身代码"的权限是过度授权（principle of least privilege）
3. **agent 多样性**：Cascade / Claude Code / Cursor / Windsurf 各自有不同的 edit 工具 + 不同的回滚机制。conductor 写一份 universal apply 不可能比每个 agent 自家工具更好
4. **`verify_patch` 已足够闭环**：agent 落 diff 后 → 调 `verify_patch(target, case)` → conductor 重跑 case + 给 status。这个 round-trip 已构成完整的 propose-apply-verify 循环，不需要 conductor 自己改代码

### 4.3 为什么 canary 用 JSON Schema 而不是 raw fixture diff

- raw fixture diff 噪声大：随机 ID / 时间戳 / 用户 token 都会触发 false positive
- JSON Schema 抽象到字段层：只关心"有没有这个 field、type 是什么、enum 是哪些值"，对 value 不敏感
- genson + jsonschema-diff 是成熟 lib：不需要自己写差异引擎
- 与 OpenAPI / OTel schema 生态一致：未来若 conductor 接 OTel 也是 JSON Schema 思维

### 4.4 为什么 conductor 走 MCP stdio 而不是 HTTP REST

- ADR-016 已把 `pce_mcp/` 打成 `.mcpb`，复用同一协议让"agent 调测试"和"agent 调捕获工具"是同形操作，认知负担为 0
- stdio 不需要端口管理、不需要 auth（per-process 隔离）、不需要 TLS（本机 process pipe 加密无意义）
- ADR-011 铁律 1 严禁裸 0.0.0.0 HTTP；conductor 选 stdio 直接绕过这条铁律的负担
- MCP spec 是开放标准（[modelcontextprotocol.io](https://modelcontextprotocol.io)），其他 LLM agent 也接进来零成本

### 4.5 为什么不复用 OpenAPI 自动生成 conductor 客户端

OpenAPI 的客户端生成器（如 `openapi-generator`）会输出大量 boilerplate + 它自己的 type system 假设，而 conductor 的 8 个 tool 的输入输出加起来不到 200 行 schema。手写 dataclass + 一份 JSON schema 文档比 OpenAPI 链路更轻、更易 review、更不依赖外部生态。

### 4.6 为什么 v1 只 3 个 patch 模板

模板要做到 confidence ≥ 0.8 才有用——少量精准的模板 > 大量粗糙的模板。3 个模板（add content block / add URL / widen schema）已覆盖**ADR-016 §6 列出的 4 个 open question 中 3 个**的失败模式。其余失败（`LOGIN_WALL` / `INFRA` / `RACE_TIMEOUT`）本质上是配置/环境问题，模板化没有 ROI；对人 / agent 友好的 `agent_hint` 字符串足够。

---

## 5. 影响清单

### 5.1 文档同步项（与本 ADR 同 commit 一并落）

- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`
  - §7 ADR 表追加 ADR-017
  - §10.1 unlock 矩阵新增 "测试编排 (conductor)" 列，初始空白；P4.D.1 落地后填 ✅
  - §10.5 (新建) "测试编排义务"：所有新 target adapter 必须**同 PR**注册到 `pce_test_conductor/targets/<id>.yaml`
- `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
  - §8.2 sign-off 检查清单加 "ADR-017 conductor MVP 跑通 T01–T05"
  - §9 风险登记表加 "DR-08 桌面 H 面更新打穿（mitigation: ADR-017 §3.4 canary）"
- `Docs/docs/PROJECT.md`
  - §12 ADR 列表追加 ADR-017
  - §UCS 段落补一句 "测试编排见 ADR-017"
- `Docs/testing/PCE-PROBE-AGENT-LOOP.md`
  - 文末加附录 "Appendix: 跨 lane via conductor"，说明本 runbook 仍是 browser-only loop，跨 lane 走 conductor MCP
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
  - §7 总图表追加 "测试编排" 列，每行填 conductor case ID 或留空

### 5.2 代码实施（Phase 4.D 序列，本 ADR 是 kickoff 决议）

无新代码——本 ADR 仅锁定契约，实施分 6 个 phase：

- **P4.D.1**: `pce_test_conductor/` MVP（read-only：`list_targets / list_cases / run_case`）+ browser lane wrap → `tests/test_pce_test_conductor.py` 单测 + 1 个真机 ChatGPT T01
- **P4.D.2**: `tests/e2e_desktop/` + `BaseAppAdapter` + Claude Desktop T01–T05
- **P4.D.3**: canary store + `diff_canary` + `classify_failure`
- **P4.D.4**: 3 个 patch 模板 + `propose_patch` + `verify_patch`
- **P4.D.5**: 文档与 audit log（agent 侧 audit 不在本 ADR 范围）
- **P4.D.6**: Cursor + Windsurf adapter（依赖 ADR-016 P5.B.3 落地）

每 phase 一个独立 commit + tag，从 `v1.1.0-alpha.7-conductor` 开始。

### 5.3 不影响

- `pce_probe/` 内部（22 动词 / matrix / triage 全部不动）
- `tests/e2e/test_*_full.py` 5 套现有矩阵（独立可跑，conductor 仅作为附加层）
- ADR-011 三铁律（全部仍然成立）
- ADR-016 Phase 1–3 已交付物（conductor 是 Phase 4 新增）
- 捕获主路径 / UCS 蓝图 / Open Core 边界

### 5.4 后续 follow-up（不在本 commit 范围）

- ADR-011 G3 watcher 的 DOM baseline 维度（与本 ADR canary 维度独立，仍归 ADR-011）
- ADR-011 G1/G2/G4–G7 远程化（仍延期到 P5.B "Server deployment"）
- 第 4 个 patch 模板：`update_selector`（browser-only，需要 DOM baseline 配合，等 G3 落地后再立项）
- conductor 自身的 health endpoint（`get_canary` / `get_classifier_stats`，提供运维可见性）

---

## 6. Open Questions · 留给 P4.D.1 启动后

1. **Replay 模式的 fixture 来源**：`run_case(mode="replay")` 用上次 live 的 raw_payload，还是用 git-tracked 的 golden fixture？前者方便，后者可重现。建议先 live-cache 后期可加 golden 维度
2. **Multi-run aggregation**：ADR-011 G3 的 watcher 需要"过去 7 天 schema diff 趋势"。conductor 是否提供 `get_canary_history(target, case, days=7)`？v1 不做，等 G3 落地一起设计
3. **跨 lane 一致性断言**：同一段对话同时被 browser lane（claude_web）和 desktop lane（claude_desktop）抓到，conductor 是否提供 `compare_runs(run_id_a, run_id_b)` 验证内容等价？v1 不做，先把单 lane 跑稳
4. **Conductor 自身的 `.mcpb` 打包**：未来用户能否一键安装 `pce_test_conductor.mcpb` 到 Claude Desktop 让 Claude 自检？技术上可行，但分发策略需要谨慎（测试工具流入 end-user 的 Claude 不一定是好事）。留作 v1.2+ 决议
5. **patch 模板的版本演进**：模板自身可能 ABI 变更（如 `pce_core/normalizer/anthropic.py` 重构），如何让 6 个月前生成的 `propose_patch` 仍能 apply？建议每个 patch 带 `requires_pce_version` 字段，apply 前 verify
6. **agent audit log 跨进程合流**：Cascade / Claude Code 各自有 trace，conductor 如何统一收集？v1 不做，每 agent 各自负责；conductor 只在自己端 `audit.jsonl` 记录"被谁的 run_id 调过"

任何处理上述 open question 的 PR 必须新写 ADR 引用本 ADR 作为前置。

---

*End of ADR-017.*
