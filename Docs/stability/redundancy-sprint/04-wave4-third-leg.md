# Wave 4 — Third Leg per Tier-α Scenario (Day 4, ~18h, STRICT MODE)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.1
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.1
>
> **目标**: 把 Tier-α 10 个场景里仍处 1-2 V-GREEN 的, 每个补到 3.
> Phase A mid-gate: Tier-α 10 全 ≥3 V-GREEN, 进入 Phase B.

---

## 1 · 任务矩阵 (8 任务覆盖 Tier-α 10 场景)

| 任务 ID | 场景 | leg 上的方案 | 工时 | 成功证据 |
|---|---|---|:---:|---|
| **W4-T1** | F1 ChatGPT Web | L3d CDP live | 3h | Chrome `--remote-debugging-port=9222` + CDP driver 抓 1 条 chat → `raw_captures.source_id='cdp-embedded'` ≥ 1 行 |
| **W4-T2** | F1 Claude Web | L3d CDP live | 2h | 同 T1, 对 claude.ai |
| **W4-T3** | F1 Gemini / GAS / Grok | L4a 剪贴板 AI 特征识别 | 总 3h | 3 站各复制 1 段对话 → `clipboard_monitor` 触发 → `source_id='clipboard-monitor-default'` (migration 0014) + AI 特征 detect=True |
| **W4-T4** | F4 P1 Claude Desktop | 已在 W1-T1/T2 完成 → 复核 §3 dedup 真起作用 | 1h | 触发 1 条 chat → 三 leg 都报 → dedup 后只有 1 主行 + deduped_by=["L3g","L3f_mcp_proxy"] |
| **W4-T5** | F5 P4 Windsurf | L4a 剪贴板 AI 特征识别 (cascade chat 复制) | 1h | Windsurf cascade chat 复制 1 段 → `source_id='clipboard-monitor-default'` + meta_json.subsystem=`windsurf` |
| **W4-T6** | F6 P6 Claude Code CLI | L1 host live (api.anthropic.com 经 mitmproxy) | 2h | 真 claude-code CLI 跑 1 prompt 经过 mitm → `raw_captures.host='api.anthropic.com'` + `source_id='proxy-default'` + meta_json.cli_kind=`claude-code` |
| **W4-T7** | F6 P7 Codex CLI | L1 host live (api.openai.com 经 mitmproxy) | 3h | 真 codex CLI 跑 1 prompt 经过 mitm → `raw_captures.host='api.openai.com'` + `source_id='proxy-default'` + meta_json.cli_kind=`codex`; 含 OpenAI host normalizer 落 messages 行 |
| **W4-T8** | F6 P8 Gemini CLI | L1 host live (generativelanguage.googleapis.com 经 mitmproxy) | 3h | 真 gemini CLI 跑 1 prompt 经过 mitm → `raw_captures.host='generativelanguage.googleapis.com'` + `source_id='proxy-default'` + meta_json.cli_kind=`gemini`; 含 Google GenAI host normalizer 落 messages 行 |

**总:** 18h, 单人 ~1.5 工日, 3 人并行 ~0.6 工日.

> **不在 Wave 4 范围 (推 Phase B Wave 6/7/8)**:
> - F4 P2 ChatGPT Desktop 第 3 条 (L4b UIA) → Wave 7
> - F5 P3 Cursor 全部 3 条 → Wave 8
> - F5 P5 GitHub Copilot 全部 3 条 → Wave 6

---

## 2 · W4-T1 / W4-T2 — L3d CDP Live (5h)

### 2.1 前置

- `pce_core/cdp/driver.py` 已存在 (28 hermetic tests GREEN)
- 但**从未对 chatgpt.com / claude.ai live sign-off**

### 2.2 SOP

```
1. 杀掉所有 Chrome 进程 (taskkill /F /IM chrome.exe)
2. 启动 pce_core server
3. 启动 Chrome with debug port:
   "C:\Program Files\Google\Chrome\Application\chrome.exe"
     --remote-debugging-port=9222
     --user-data-dir="%TEMP%\pce_cdp_test_<task>"
   (用独立 user-data-dir 避免污染日常 profile)
4. 启动 pce_core CDP driver:
   python -m pce_core.cdp.driver --port 9222 --site chatgpt
5. 在 Chrome 里登录 chatgpt.com (CDP 不会拦登录)
6. 发 1 prompt: "What is 2+2?"
7. 等 5s
8. 检查 raw_captures:
   SELECT * FROM raw_captures
   WHERE source_id='cdp-embedded'
     AND created_at > <baseline>
9. handoff 模板填空
```

### 2.3 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W4-R1 | CDP driver 在用户 Chrome 上失败 (旧实例) | SOP 第 1 步 kill 全部 chrome |
| W4-R2 | site adapter 没识别 chatgpt 的特定网络模式 | fallback: 用通用 Network domain listener, 后期 P6 收紧 |

### 2.4 验收

- W4-T1: `raw_captures.source_id='cdp-embedded'` ≥ 1 行 + host 含 chatgpt.com
- W4-T2: 同上, host 含 claude.ai

---

## 3 · W4-T3 / W4-T5 — L3d CDP launcher (third leg for F1 Gemini/GAS/Grok + F5 P4 Windsurf, 4h)

> **2026-05-15 重写 (V-AUX 重分类)**: 原 W4-T3 / W4-T5 用 L4a 剪贴板.
> 剪贴板因为是用户主动行为副产品 (见 `REDUNDANCY-AUDIT-MATRIX.md` §1.1)
> 重分类为 V-AUX **不计入 ≥3 invariant**. 这一节改用 **L3d CDP launcher**
> 作为真正的第 3 条被动 leg (Chrome + Electron 都可以走
> `--remote-debugging-port` attach). 剪贴板路径作为辅助证据保留 (
> `scripts/clipboard_capture_oneshot.py`), 但不再签 V-GREEN.

### 3.1 前置依赖: clipboard source migration (保留, 但仅 V-AUX)

`migration 0014_clipboard_source.py` (commit 69bb1d2) 已落地, 注册了
`source_id='clipboard-monitor-default'`. 剪贴板捕获走这个 source,
但**只算 V-AUX** (辅助证据 + 监控), 不计入 leg invariant.

### 3.2 W4-T3-alt SOP (L3d CDP, 3 站, ~1h 每站)

```
1. 启动 pce_core server
2. taskkill /F /IM chrome.exe (清掉用户当前所有 Chrome 实例)
3. 启动 Chrome with debug port (独立 user-data-dir 避免污染日常 profile):
   "C:\Program Files\Google\Chrome\Application\chrome.exe" `
     --remote-debugging-port=9222 `
     --user-data-dir="%TEMP%\pce_cdp_test_gemini"
4. 启动 PCE CDP driver: python -m pce_core.cdp.driver --port 9222
   (driver 监听 9222, 自动 attach 到打开的 tabs)
5. 在 Chrome 里登录 gemini.google.com (CDP 不会拦登录态)
6. 发 1 prompt: "What is 2+2?"
7. 等 5s
8. 验:
   SELECT * FROM raw_captures
   WHERE source_id='cdp-embedded' AND host='gemini.google.com'
     AND created_at > <baseline>
9. 重复 step 3-8 for aistudio.google.com (--user-data-dir 用 _gas)
10. 重复 step 3-8 for grok.com (--user-data-dir 用 _grok)
11. handoff 填空; 3 站合并成一份 W4-T3-alt handoff
```

### 3.3 W4-T5-alt SOP (L3d CDP attach Windsurf Electron, 1h)

Windsurf 是 Electron app, 走 Electron 的 `--remote-debugging-port` flag
能 attach CDP. 详见 `pce_app_launcher/` 已有的 Electron CDP 框架.

```
1. 关掉所有 Windsurf 实例
2. 用 --remote-debugging-port=9223 启动 Windsurf:
   "C:\Users\<u>\AppData\Local\Programs\Windsurf\Windsurf.exe" --remote-debugging-port=9223
3. PCE CDP driver attach: python -m pce_core.cdp.driver --port 9223
4. 在 Windsurf Cascade 发 1 prompt: "What is 2+2?"
5. 等 5s
6. 验:
   SELECT * FROM raw_captures
   WHERE source_id='cdp-embedded'
     AND created_at > <baseline>
   期望 host 字段 = `windsurf.com` / `codeium.com` / 类似 Cascade upstream
7. handoff 填空
```

### 3.4 验收

- W4-T3-alt: Gemini / GAS / Grok 各 ≥ 1 行 `source_id='cdp-embedded'`,
  host 字段含对应域名, ≥ 1 messages row 关联到该站 session
- W4-T5-alt: Windsurf ≥ 1 行 `source_id='cdp-embedded'`, meta_json 含
  electron 来源标识

### 3.5 V-AUX clipboard tasks (保留为附加项, 不计 leg)

可选: 用户跑 W4-T3-alt / W4-T5-alt 时, 顺手对每个站复制 1 段 assistant
输出, 用 `python scripts/clipboard_capture_oneshot.py <tag>` 跑一次,
作为**辅助证据 + clipboard pipeline 健康度检查**. 但**不计入 ≥3 leg
invariant**, 不写 V-GREEN handoff.

---

## 4 · W4-T4 — Dedup 实战验证 (1h)

### 4.1 目的

W3 的 dedup 单测全是合成数据. W4-T4 是**真实环境的 end-to-end 验证**:
3 条腿同时跑, 看 dedup 是否真的把它们合并到 1 主行. **这是 G2 唯一的
live 证据点, 不可省**.

### 4.2 SOP

```
1. 启动 pce_core server
2. 启动:
   - pce_proxy (L1)
   - pce_persistence_watcher (L3g)
   - pce_mcp_proxy (L3f)
3. 启动 Claude Desktop
4. 发 1 个 cowork tool call (e.g. ask Claude to read a local file)
5. 等 10s
6. 检查 raw_captures (应只有 1 主行):
   SELECT * FROM raw_captures
   WHERE pair_id LIKE '%<recent>%'
   ORDER BY created_at
7. 检查 deduped_by 字段:
   SELECT pair_id, source_id, deduped_by FROM raw_captures
   WHERE created_at > <baseline>
   
   期望:
   pair_id      | source_id (primary)         | deduped_by
   -------------+------------------------------+------------------------
   abc123       | mcp-proxy-default            | ["L1_mitm","L3g..."]
   (or some other order based on which leg arrives first)
8. handoff 填空
```

### 4.3 验收

- 同一 pair_id 只有 1 主行
- deduped_by 数组含其他 2 个 source
- /api/v1/supervisor/scenario/f4_p1_claude_desktop 返回 status=redundant

---

## 5 · W4-T6 / W4-T7 / W4-T8 — CLI L1 host live (8h)

### 5.1 前置: 三家 host normalizer 落地

| CLI | host | normalizer | 状态 |
|---|---|---|---|
| Claude Code | api.anthropic.com | `pce_core/normalizer/anthropic.py` | ✅ 已存在 |
| Codex | api.openai.com (`/v1/chat/completions` `/v1/responses`) | `pce_core/normalizer/openai.py` | ⏳ Wave 4 新增 ~150 LOC |
| Gemini | generativelanguage.googleapis.com (`v1beta/models/...:generateContent`) | `pce_core/normalizer/google_genai.py` | ⏳ Wave 4 新增 ~150 LOC |

### 5.2 SOP (per CLI)

```
1. 启动 pce_proxy on 8080
2. 设系统代理 → 127.0.0.1:8080; 让 CLI 走 mitmproxy CA
   (export HTTPS_PROXY=http://127.0.0.1:8080 + 信任 CA)
3. 跑 1 prompt: <CLI> "What is 2+2?"
4. 等 5s
5. 检查 raw_captures:
   SELECT host, source_id, length(body_text_or_json) AS body_len
   FROM raw_captures
   WHERE host IN ('api.openai.com', 'api.anthropic.com',
                  'generativelanguage.googleapis.com')
     AND created_at > <baseline>
6. 检查 messages 行 (host normalizer 已落):
   SELECT role, model_name, length(content_text) AS content_len
   FROM messages
   WHERE created_at > <baseline>
7. handoff 填空; 还原系统代理
```

### 5.3 验收

- W4-T6: claude-code → api.anthropic.com ≥ 1 raw_capture pair + 1 messages
  (anthropic.py 已存在, 此任务只验 live)
- W4-T7: codex → api.openai.com ≥ 1 pair + 1 messages 行 (openai.py 新增)
- W4-T8: gemini-cli → google generative ≥ 1 pair + 1 messages
  (google_genai.py 新增)
- 三 CLI meta_json.cli_kind 正确标记

---

## 6 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W4-R1 | CDP launcher 用户 Chrome 旧实例 | SOP 强制 kill |
| W4-R2 | clipboard 假阳 (复制非 AI 内容也触发) | ai_signal_score < 0.6 时不写 raw_captures, 只 log |
| W4-R3 | dedup window 30s 不够 (3 leg 慢的那条 > 30s 才报) | scenarios.yaml 加 `dedup_window_s` 字段, P0 场景默认 30s, 可调到 120s |
| W4-R4 | Codex / Gemini CLI 首次 normalizer 不工作 | 任务前先用 fixtures 跑 unit 测; live 只验对接 |
| W4-R5 | CLI 不尊重系统代理 (走 native HTTPS 客户端不读 env var) | fallback: 用 mitmproxy 透明模式 (PAC 文件) 或重新走 CLI native flag (--proxy) |

---

## 7 · 验收门 (Wave 4 结束 = Phase A mid-gate)

```
[ ] 8 份 handoff PASS
[ ] REDUNDANCY-AUDIT-MATRIX §3 升级矩阵 (2026-05-15 V-AUX 重分类后):
    - F1 ChatGPT Web:          2 → 3 ✅ (W4-T1 L3d CDP)
    - F1 Claude Web:           2 → 3 ✅ (W4-T2 L3d CDP)
    - F1 Gemini Web:           2 → 3 ✅ (W1-T4 L1 + W4-T3-alt L3d CDP)
    - F1 GAS:                  1 → 3 ✅ (L1 isolated sweep + W4-T3-alt L3d CDP)
    - F1 Grok Web:             2 → 3 ✅ (W1-T6 L1 + W4-T3-alt L3d CDP)
    - F4 P1 Claude Desktop:    3 ✅ 已锁
    - F5 P4 Windsurf:          1 → 3 ✅ (W1-T7 MCP + W4-T5-alt L3d CDP + L1 已有)
    - F6 P6 Claude Code CLI:   3 ✅ 已锁 (W1-T3 L3h + W4-T6 L1 + L3g)
    - F6 P7 Codex CLI:         2 → 3 ✅ (W1-T8 L3h + W1-T9 L3g + W4-T7 L1 待 Rust proxy fix)
    - F6 P8 Gemini CLI:        2 → 3 ✅ (W1-T10 L3h + W1-T11 L3g + W4-T8 L1 待 Node undici fix)
    [V-AUX 附加项, 不算 ≥3 invariant] clipboard 3 web + windsurf 各 ≥1 行
[ ] G1 Tier-α 10/10 ≥ 3 V-GREEN ✅ (Phase A mid-gate)
[ ] migration 0014 通过 (clipboard source 注册)
[ ] migration 0015 通过 (codex+gemini source 注册, 如适用)
[ ] dedup 实战在 W4-T4 已验
[ ] G2 /api/v1/supervisor/status 13 场景里 Tier-α 10 显 redundant
[ ] G2 Tier-β 3 场景显 impaired/minimal (待 Phase B 关闭)
```

完成后进入 [`05-wave5-nightly-closure.md`](05-wave5-nightly-closure.md) (Phase A 关闭 + Phase B kickoff).
