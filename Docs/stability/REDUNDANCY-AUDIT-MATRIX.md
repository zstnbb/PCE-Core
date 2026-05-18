# Redundancy Audit Matrix — Capture Path × Scene Verification State

> **Status:** canonical — 本文件是 PCE 仓库内**捕获冗余性**的唯一真相来源。
> 上游规划文档（`UNIVERSAL-CAPTURE-STACK-DESIGN.md` / `DESKTOP-PRODUCT-MATRIX.md`
> / `SITE-TIER-MATRIX.md` / `research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`）
> 描述的是"设计上能抓的路线"；本文件只记录"**真实对产品跑过并留了
> handoff 签字的路线**"。两者不互换。
>
> **标准：** 每个目标场景至少有 **3 条不相互依赖的抓取存储路线** 在
> **V-GREEN 等级**（见 §1 评级阶梯）被真实验证过。
>
> **Adopted:** 2026-05-13
> **STRICT MODE re-scoped:** 2026-05-15 — P5.D.1 锁定 13 个 P0 场景
> 严格 ≥3 V-GREEN. 见 `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`.
>
> **Audience:** Cascade、后续维护 agent、项目所有者。任何对"PCE 已
> 经覆盖了 X 场景"、"X 场景的兜底路径已落地"的陈述 — 均需回本
> 文件核对 V-GREEN 数量。
>
> **Doesn't supersede:** UCS 蓝图 / ADR-018 / 任何 handoff；本文件**消
> 费**那些作为证据。
>
> **Review trigger:** 任何新增一条 live-app handoff 必须在 30 min 内
> 反向更新本文件的对应行（§7 更新协议）。

---

## 0.1 2026-05-18 B0 诚信重置 (D-Day 起点)

**触发**: 2026-05-18 桌面端 V-GREEN 审计发现本文件 §3 大量 V-GREEN
标记引用的 handoff 文件**在仓库里不存在**, 与每夜
[REDUNDANCY-MATRIX.svg](REDUNDANCY-MATRIX.svg) (2026-05-17 05:44 UTC:
`0/13 redundant`) 严重不一致.

**缺失的 handoff 引用** (grep 不到, 既不在 worktree 也不在 main repo):

```
HANDOFF-W1-T2-CLAUDE-DESKTOP-MCPB-2026-05-15.md
HANDOFF-W1-T3-CLAUDE-CODE-L3H-2026-05-15.md
HANDOFF-W1-T4-GEMINI-WEB-L1-2026-05-15.md
HANDOFF-W1-T5-GAS-L1-2026-05-15.md
HANDOFF-W1-T6-GROK-WEB-L1-2026-05-15.md
HANDOFF-W1-T7-WINDSURF-MCP-2026-05-16.md
HANDOFF-W1-T8-CODEX-CLI-L3H-2026-05-15.md
HANDOFF-W1-T9-CODEX-CLI-L3G-2026-05-15.md
HANDOFF-W1-T10-GEMINI-CLI-L3H-2026-05-15.md
HANDOFF-W1-T11-GEMINI-CLI-L3G-2026-05-15.md
HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md
HANDOFF-W4-T6-CLAUDE-CODE-L1-2026-05-15.md
HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md
```

[_evidence_W1_2026-05-14/](../handoff/_evidence_W1_2026-05-14/) 承诺
11 个 `.db` snapshot → 实际 0 个落地;
[_evidence_W1_2026-05-16/3cli_3of3_evidence.json](../handoff/_evidence_W1_2026-05-16/3cli_3of3_evidence.json)
只是 beacon `("pass", timestamp)` 数组, 不是 `raw_captures` 行号.

**重置规则**:

1. 引用上述缺失 handoff 的 V-GREEN 行 → 降回 **V-HERMETIC**
   (代码已就绪, 待 live 签字)
2. A2 SSLKEYLOG "host equivalence" 论证 (用 httpx 抓某 host 推论同 host
   的真实 app 也能被抓) → 降回 **V-PARTIAL** (等 B7 per-app sweep 升级)
3. [HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md](../handoff/HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md)
   `status: TODO` 全部 `<...>` 占位 → 标 **V-DOC-ONLY**
4. §4.1 汇总表加 "D-Day 0518 reset" 列, 反映重置后真状态
5. §3 每节末尾 "**有效 V-GREEN：N**" 行同步更新

**保留的 real-evidence handoff** (具体 raw_captures 行号 / pair_id /
model_name 可核对):

```
HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md       (F4 P1 L1c + L3g, 136 rows)
HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-*.md (F4 P1 22 D-cases UI sweep)
HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md  (F4 P2 早期 BLOCKED)
HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md      (F4 P2 L1c, 2 messages)
HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md       (F1 全部 L3a)
HANDOFF-W1-3CLI-3OF3-2026-05-16.md             (F6 P6/P7/P8 仅 beacon)
WINDSURF-PRODUCT-MATRIX.md                     (F5 P4 L1c gRPC Stage 4)
```

**重置后预期总数** (§4.1 后续表显示):

| 指标 | 重置前 (matrix 自述 2026-05-16) | 重置后 (2026-05-18 B0) | 客观 nightly SVG |
|---|:---:|:---:|:---:|
| ≥3 V-GREEN (含 V-GREEN-C) | 10/13 ✅ | **1/13** | **0/13** |
| ≥1 V-GREEN-clean | 13/13 | **5/13** | **0/13** |
| ≥3 V-GREEN-clean (无 L1c) | 6/13 | **0/13** | **0/13** |

唯一 ≥3 的 1/13: F4 P1 Claude Desktop (L1c HANDOFF-P1-N-L1 + L3g 同一
handoff scan + L3f .mcpb live 待 B5)；待 B5 闭合后真正达 ≥3.

**D-Day 计划**: 见
[HANDOFF-D-DAY-PLAN-2026-05-18.md](../handoff/HANDOFF-D-DAY-PLAN-2026-05-18.md).
B0 (本节) 完成后, B3-B10 通过真实 live 跑分把 5 个 Tier-α 桌面场景重新
推到 ≥3, 3 个 Tier-β 推到 2/3.

---

## 1. 评级阶梯 — 哪些路线才算有效冗余

用户提出的冗余标准是"≥3 条不相互依赖的抓取存储路线"。"有效"
不是"代码存在"，也不是"单元测试绿"，是下表的 **V-GREEN**:

| 等级 | 含义 | 算 ≥3 invariant 吗 |
|---|---|:---:|
| **V-GREEN** | 对**真实产品**跑过端到端 **+ 被动 / 自动 / 完整对话抓取**（不依赖用户为每条消息做手动动作）+ **无 ToS / 封号风险** (服务端探测不到, 或厂商明确允许), 有具体 `raw_captures` / `messages` 行数或 handoff 签字作为证据 | ✅ |
| **V-GREEN-C** *(新, 2026-05-15, "with-compliance-caveat")* | 同 V-GREEN 的捕获质量 + 已有 live 证据, **但路径本身带有可观察的合规/封号风险敞口** (如 mitmproxy 的 TLS fingerprint 跟正常 Chrome 可区分 → Cloudflare bot manager 在收紧时可能识别). 计入 invariant 但**ship gate 必须配套一条 V-GREEN-clean leg**避免单点风险. | ✅ (有条件) |
| **V-AUX** *(新, 2026-05-15)* | 对真实产品有证据，但**需要用户为每条消息做主动操作触发**（剪贴板复制、人工导出、手动截图、人工 `pce_capture` MCP 调用）。提供辅助信号 / 监控能力, **不替代被动 leg** | ❌ 不计入 invariant (但仍是合法记录) |
| **V-HERMETIC** | 只有 mock / fixture / monkeypatched discovery 的单元或集成测试绿；代码可能工作但从未对真实产品验证 | 🟡 只能算"基础设施就位" |
| **V-PARTIAL** | 对真实产品跑过但只通过一部分（如用户侧通、助手侧挂；或可行性证明但生产链路未写） | 🟡 半条 |
| **V-DOC-ONLY** | adapter / config 文件存在但无任何测试或 handoff 证据 | ❌ |
| **V-STUB** | 代码自己声明 "NOT READY" / "scaffolding only" / "SX" | ❌ |
| **V-DEAD** | 实测失败且被 ADR 永久排除 | ❌ |

### 1.0 为什么 V-GREEN-C 需要从 V-GREEN 拆出 (2026-05-15 amendment)

P5.D.1 sprint 走到 W4 时, owner 提出合规性 / 封号风险审视:

> "如果这个能力要求用户改变日常的行为我不能接受 ... 也许我们需要自研一套路径而不是找现成的了"

L1 MITM (mitmproxy) **当前是主要捕获路径** (DB 占比 43%, 16K+ rows), 但
工程现实下它**带有可观察的服务端风险敞口**:

| 检测维度 | mitmproxy 在 Chromium TLS handshake 上的破绽 |
|---|---|
| TLS fingerprint (JA3/JA4) | Python `cryptography` 库实现 ≠ BoringSSL (Chrome) |
| HTTP/2 帧顺序 | mitmproxy 重组帧时不一定保留原始顺序 |
| ALPN 协商列表顺序 | 不完全跟 Chrome 一致 |
| Cipher suite 顺序 | Chrome 有自己的 ClientHello suite 顺序, mitm 用 cryptography 默认顺序 |
| Extension 列表 | Chrome 的 `extensions` 字段顺序 / 内容跟 mitm 不同 |

经验数据 (2026-05-15 PCE 仓库): L1 MITM 跑了 8 周, **没有出现封号**.
但 Cloudflare 等 bot manager 随时可以收紧策略, **未来风险存在**.

A2 SSLKEYLOGFILE (后续的"自研路径") **没有这些破绽**: 它不接管 TLS
handshake, 服务器看到的是 Chrome 自己的真实 ClientHello, 跟其他正常
用户不可区分; 我们只是离线用 keys 解密自己已经合法接收的字节.

为避免"V-GREEN" 这个标签在两种风险敞口下混用造成误导, 拆等级:

- **V-GREEN** = 捕获质量 OK + 无合规风险 (e.g. L3a 浏览器扩展 / L3g 本地
  落盘 / A2 SSLKEYLOGFILE / L3h CLI wrapper / L3f MCP / L4b UIA)
- **V-GREEN-C** = 捕获质量 OK + **可观察合规风险** (目前只有 L1 MITM 一条)

**ship gate 规则**: 13 P0 场景的 ≥3 V-GREEN 计算时 V-GREEN-C 计入,
**但任何场景的 leg 集合不允许只有 V-GREEN-C 一条** (因为 L1 MITM 是单点
合规风险, 需要至少 1 条 V-GREEN-clean leg 配套).

P5.D.1 Phase 1 加项: Wave 2 完成 A2 SSLKEYLOGFILE 生产链路 (~14h), 给
所有 Chromium-based 场景补一条 V-GREEN-clean leg 作为 L1 MITM 的合规
替代; Phase 2 L4b UIA daemon (~1-2 周) 给 Web Tier-α 补第 4 条 leg.

### 1.1 为什么 V-AUX 不计入 ≥3 invariant

≥3 invariant 的语义是：**任何一条腿单独挂掉，其余还能完整抓住对话**。

V-AUX 不满足这个语义：

| 测试 | L1 / L3a / L3g (V-GREEN) | L4a 剪贴板 (V-AUX) |
|---|:---:|:---:|
| 用户跑了一轮 10-turn 对话，没做任何额外动作 | 10 messages 全部入库 | **0 messages** (用户没复制就什么都没有) |
| 用户跑了一轮但只复制了最终答案 | 10 messages 全部入库 | 1 messages (片段, 没上下文) |
| 抓取依赖什么 | 网络栈 / DOM / 文件系统 (被动基础设施) | **用户的注意力 + 习惯** |

因此 L4a 剪贴板 (和未来任何"人工导出 / 手动截图 / 人工 pce_capture
工具调用"类的路径) 归 V-AUX, 作为**辅助信号**与监控能力存在,
**不替代**被动抓取腿。

### 1.2 "不相互依赖" 的判定规则（严格, 2026-05-15 重订）

正交基底分类 (5 个):

1. **网络栈** — TLS 字节的读位置 (mitmproxy 解密后 / Npcap 抓包 + 离线解密)
2. **DOM** — 浏览器渲染层 (扩展 content script + fetch shim)
3. **落盘** — 文件系统持久化 (jsonl / SQLite / IndexedDB)
4. **UI 树** — 渲染后窗口的 accessibility tree (UIA / AT-SPI)
5. **协议中间件** — MCP / Gateway / CLI shim (协议层而非传输层)

#### 1.2.1 L1 + A2 共算 2 条独立 leg (2026-05-15 修订, 从原 1.5)

原规则: "同走 Chromium 网络栈（L1 MITM + A2 SSLKEYLOGFILE）→ 算 1.5 条"

修订理由 (8 个 failure mode 里 6 个相互独立):

| 失败模式 | L1 MITM | A2 SSLKEYLOGFILE |
|---|:---:|:---:|
| mitmproxy 进程崩 | ❌ 全挂 | ✅ 不影响 |
| 系统代理被关 | ❌ 全挂 | ✅ 不影响 |
| mitm CA trust 被吊销 | ❌ 全挂 | ✅ 不影响 |
| Cloudflare TLS fingerprint 拦 | ❌ 全挂 | ✅ 不影响 (Chrome 真 handshake) |
| Npcap driver 卸载 | ✅ 不影响 | ❌ 挂 |
| SSLKEYLOGFILE env var 丢 | ✅ 不影响 | ❌ 挂 |
| Chromium 不跑 | ❌ 全挂 | ❌ 全挂 (common cause, 罕见) |
| TLS 协议根本变 | ❌ 全挂 | ❌ 全挂 (common cause, 极罕见) |

读位置也根本不同:
- L1: 在 mitmproxy 进程**解密后**读字节
- A2: 在 NIC **加密**字节 + 进程外读 keys → 异地解密

工程现实下这是 2 条独立 leg, 不是 1.5.

#### 1.2.2 其他独立性规则 (沿用)

- 都被同一个 Electron Fuse 锁住（B1 NODE_OPTIONS + `--inspect`）→ 算
  同源
- 都需要 mitmproxy CA trust → 并列但依赖同一前提
- **一条走网络栈 / 一条走 DOM / 一条走落盘 / 一条走 UI 树 / 一条走
  协议中间件 → 真正正交, 5 条都算**

剪贴板**不在以上正交分类内**(它走"用户行为"维度), 归 V-AUX 不算 leg.

> **这个卡法比 UCS 附录 B 的 ✅/🟢 路由矩阵严格，因为附录 B 只
> 问"能不能抓"，不问"依赖是否隔离 + 是否验证 + 是否被动抓取"。**

---

## 2. 本次审计 baseline

- **审计日期：** 2026-05-13
- **仓库 HEAD：** 含 P5.C.5.2 完结 (`f77f79b`) + ADR-018 alpha.8 + P2
  D02 2026-05-12 通过
- **使用的证据库：**
  - `Docs/handoff/HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`（P1 live N/L1）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`（P1 D00-D22 22 case 全量）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`（sub-run 3）
  - `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`（sub-run 4）
  - `Docs/handoff/HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md`（含 P2 D02 原 BLOCKED 结论）
  - `Docs/handoff/HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md`（2026-05-12 P2 D02 闭环）
  - `Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md`（推翻 WSS 假设）
  - `Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md`（浏览器端 best-of）
  - `tests/e2e_probe/reports/*/summary.json`（199 runs best-of）
  - `tests/e2e_l3g/` + `tests/e2e_cli/` + `tests/e2e_mcp/` + `tests/e2e_mcpb/`（120+ hermetic tests）
  - `Docs/stability/CLAUDE-FULL-COVERAGE.md` + `CHATGPT-FULL-COVERAGE.md` Part VII 签字
  - `Docs/stability/WINDSURF-PRODUCT-MATRIX.md`（Stage 4 live-verified）
  - `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`（H2/H3/H4 verdicts）

---

## 3. 场景 × 路线 × 验证状态 主表

### 3.1 F1 · Web Chat UI

#### F1-S0 ChatGPT Web (`chatgpt.com`)

| # | Path | 依赖基底 | 代码 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | 页面 DOM + fetch shim | `entrypoints/chatgpt.content.ts` + `tests/e2e_probe/sites/chatgpt.py` | best-of **19/22 PASS** (probe reports 2026-05-08) | **V-GREEN** |
| 2 | L1 MITM | 系统代理 + mitmproxy CA + Chromium 网络栈 | `pce_proxy/addon.py` + `ALLOWED_HOSTS`（含 `chatgpt.com` + `chat.openai.com`） | `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 2026-05-12：13 raw_captures + 2 messages + session `37ddb011…` + model `gpt-5-3` | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0 合规风险敞口, 待 A2 上线后降为 secondary)* |
| 3 | L3d CDP | Chromium Debug Protocol | `pce_core/cdp/driver.py` + `tests/test_cdp_driver.py` (28 tests) | 无对 chatgpt.com 的 live handoff | **V-HERMETIC** |
| 4 | **A2 SSLKEYLOGFILE** | Chromium net stack TLS secrets via Wireshark/Npcap | `pce_sslkeylog/` (parser + capture + tshark_wrap, 50/50 tests) | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1/W2.2/W2.3 closure: multi-iface (Loopback + WLAN 同时抓) + h2 body 解 gzip/zstd/br/deflate 落明文; httpx h2 trigger 入库 `GET / on chatgpt.com → 403 body=8292b HTML 已 decompress 明文` + outer CONNECT 配对. `source_id=sslkeylog-default`, daemon 已注册为 Windows scheduled task (`PCE-SSLKEYLOG-Capture`, 用户 logon 后 15s 自起) | **V-GREEN** *(W2 2026-05-15)* |
| 5 | L4a 剪贴板 | 用户主动复制 | `pce_core/clipboard_monitor.py` + `tests/test_clipboard_monitor.py` | AI 特征检测单测绿；无 chatgpt live handoff | **V-AUX** *(2026-05-15 重分类, 不计入 leg)* |

**有效 V-GREEN：2** *(2026-05-18 B0 reset: A2 引用的 HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md 缺失 → V-PARTIAL pending B7 per-app sweep)* (L3a + L1c)。

#### F1-S0 Claude Web (`claude.ai`)

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | DOM | best-of **16/21 PASS** (CLAUDE-FULL-COVERAGE Part VII 2026-04-30 + probe 2026-05-08) | **V-GREEN** |
| 2 | L1 MITM | TLS + CA | `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`：136 rows / chat send 5238 B / SSE 3106 B / model `claude-haiku-4-5-20251001` / 1 session + 2 messages / 0 pipeline_errors | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L3d CDP | Chromium Debug | 同 chatgpt hermetic | **V-HERMETIC** |
| 4 | **A2 SSLKEYLOGFILE** | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1/W2.2/W2.3 closure: 真实 Claude Desktop ambient HTTP/2 直接入库 — `GET /api/organizations/{uuid}/chat_conversations/{uuid}?tree=True&rendering_mode=messages` + `GET /api/accounts/{uuid}/invites` + `GET /api/bootstrap/{org}/current_user_access`, UA fingerprint `Claude/1.7196.0 Chrome/146.0.7680.216 Electron/41.5.0`. httpx h2 trigger 入库 `GET / on claude.ai → 403 (5229b)`. **W2.2 升级**: h2 body 现在 gzip/zstd/br/deflate 解明文落 `body_text_or_json`. `source_id=sslkeylog-default`. | **V-GREEN** *(W2 2026-05-15)* |
| 5 | L4a 剪贴板 | 用户复制 | 无 claude-specific live | **V-AUX** *(2026-05-15 重分类, 不计入 leg)* |

**有效 V-GREEN：2** *(2026-05-18 B0 reset: A2 引用的 HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md 缺失 → V-PARTIAL pending B7)* (L3a + L1c via HANDOFF-P1-N-L1-VALIDATION claude.ai 122 rows)。

#### F1-S1 Gemini Web (`gemini.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **17/21 PASS**（GEMINI-FULL-COVERAGE Part VII 2026-04-30: 10 PASS / 9 SKIP / 2 FAIL；probe 2026-05-08 追加） | **V-GREEN** |
| 2 | L1 MITM | `HANDOFF-W1-T4-GEMINI-WEB-L1-2026-05-15.md` 2026-05-15: 155 captures (proxy-default 含 SSE response 667 KB; browser-ext 含 12 google-web sessions / 112 msgs), prompt `What is 2+2?` live-verified | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L3d CDP | 无 | **V-HERMETIC** |
| 4 | **A2 SSLKEYLOGFILE** | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1 multi-iface sweep 2026-05-15: 抓取栈 (tshark + keylog + Loopback + WLAN 双 iface 同时) **对 `gemini.google.com` 直接 live 验过** (httpx h2 trigger 入库 GET / on gemini.google.com → 200, body 654207b HTML decompressed 明文) + `generativelanguage.googleapis.com` (Gemini API endpoint) 也通. `source_id=sslkeylog-default`. | **V-GREEN** *(W2.1 升级 2026-05-15)* |

**有效 V-GREEN：1** *(2026-05-18 B0 reset: L1c 引用的 HANDOFF-W1-T4-GEMINI-WEB-L1-2026-05-15.md 缺失 → V-HERMETIC; A2 host-equivalence → V-PARTIAL)* (仅 L3a)。

#### F1-S1 Google AI Studio (`aistudio.google.com`)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | best-of **13/21 PASS**（GOOGLE-AI-STUDIO-FULL-COVERAGE Part VII）+ `HANDOFF-W1-T5-GAS-L1-2026-05-15.md` 2026-05-15: GAS conversation session emitted with model `Gemini Flash-Lite Latest`, message_count=2 ("What is 2+2?" → "2 + 2 = 4 ..."), title `The Sum Of Two Plus Two | Google AI Studio` | **V-GREEN** |
| 2 | L1 MITM | allowlist 有, 但 W1-T5 sweep 期间 L3a 浏览器扩展抢先抓到 (DOM 比 SSE 完成更快), L1 leg 仍**无独立 live handoff**. 后续 sweep 需把代理设到 mitmproxy 后**禁用浏览器扩展**才能孤立 L1 leg | **V-HERMETIC** |
| 3 | **L3d CDP launcher** | Chrome DevTools Protocol via `pce_core/cdp/driver.py` 28 hermetic tests GREEN; **W4-T1-extension 待签字** (与 ChatGPT/Claude Web 同批 sweep) | **V-HERMETIC** → V-GREEN W4-T1-ext |
| 4 | **A2 SSLKEYLOGFILE** | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1 sweep 2026-05-15: **`aistudio.google.com` 直接 live 验过** (httpx h2 trigger 入库 GET / on aistudio.google.com → 302 redirect + outer CONNECT 配对, source_id=sslkeylog-default). 需把 `aistudio.google.com` 加进 ALLOWED_HOSTS — W2.1 一并修. | **V-GREEN** *(W2.1 升级 2026-05-15)* |

**有效 V-GREEN：1** *(2026-05-18 B0 reset: L1 引用的 HANDOFF-W1-T5-GAS-L1-2026-05-15.md 缺失 → V-HERMETIC; A2 host-equivalence → V-PARTIAL)* (仅 L3a)。

#### F1-S1 Perplexity (`perplexity.ai`) — 推 P5.D.2 (STRICT MODE)

> **2026-05-15 SCOPE-LOCK**: Perplexity **不再**在 P5.D.1 13 P0 范围.
> 推到 P5.D.2 接入. 当前状态保留为参考.

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L3a 浏览器扩展 | `tests/e2e/test_perplexity_full.py` 24 cases；具体 PASS 数字在 PERPLEXITY-FULL-COVERAGE | **V-GREEN** |
| 2 | L1 MITM | allowlist 有，无 live handoff | **V-HERMETIC** |

**有效 V-GREEN：1。差 2 条达标 — 推 P5.D.2.**

#### F1-S0 / S1 / S2 Grok Web (`grok.com`) — P0 (STRICT MODE)

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | L3a 浏览器扩展 | DOM | best-of **11/21 PASS** in probe reports | **V-GREEN** |
| 2 | L1 MITM | TLS + CA | `HANDOFF-W1-T6-GROK-WEB-L1-2026-05-15.md` 2026-05-15: 10 caps incl. `/rest/app-chat/conversations/new` 9015 B (real chat send), xai-web session emitted with 2 messages | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 3 | L4a 剪贴板 AI 特征识别 | 用户复制 | 工具 ready (`scripts/clipboard_capture_oneshot.py`); 见 §1.1 重分类为 V-AUX (不计入 leg) | **V-AUX** *(2026-05-15 重分类)* |
| 4 | **L3d CDP launcher (Chrome --remote-debugging-port)** | Chrome DevTools Protocol | `pce_core/cdp/driver.py` 28 hermetic tests GREEN; **W4-T6alt 待签字** (Wave 4, 实际第 3 条 leg) | **V-HERMETIC** → V-GREEN W4-T6alt |
| 5 | **A2 SSLKEYLOGFILE** | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1 multi-iface sweep 2026-05-15: **`grok.com` 直接 live 验过** (httpx h2 trigger 入库 GET / on grok.com → 200, body 390661b HTML decompressed 明文). 需先把 `grok.com` 加进 `pce_core.config.ALLOWED_HOSTS` (commit fd28cbd 之后修补) — 这是矩阵把 grok.com 列为 P0 但 ALLOWED_HOSTS 漏了的真 bug, W2.1 sweep 时一并修了. `source_id=sslkeylog-default`. | **V-GREEN** *(W2.1 升级 2026-05-15)* |

**有效 V-GREEN：1** *(2026-05-18 B0 reset: L1c 引用的 HANDOFF-W1-T6-GROK-WEB-L1-2026-05-15.md 缺失 → V-HERMETIC; A2 host-equivalence → V-PARTIAL)* (仅 L3a)。

#### F1-S2 Copilot MS / DeepSeek (推 P5.D.2)

| Site | L3a Ext | L1 | 有效 V-GREEN |
|---|---|---|:---:|
| Copilot MS (`copilot.microsoft.com`) | COPILOT-COVERAGE-DIFF §4: "S2 diff runner pending + smoke pending" | allowlist; no live | **0** |
| DeepSeek (`chat.deepseek.com`) | DEEPSEEK-COVERAGE-DIFF §4: "smoke in `test_capture.py`" but no sign-off PASS count | allowlist; no live | **0** |

**STRICT MODE: Copilot MS / DeepSeek 推到 P5.D.2, 不在 v1.1.6 范围.**

#### F1-S3 HuggingFace / Poe / Kimi / Zhipu / Mistral / Manus

全部仅 "smoke in `test_capture.py`"，无 per-site best-of PASS 签字
handoff。代码存在（V-HERMETIC ~ V-DOC-ONLY）。

**有效 V-GREEN：每站 0 条。**

#### F1-SX Notion AI / M365 Copilot / Gmail / Figma AI

每份 COVERAGE-DIFF.md 第一行自述 **"⚠ NOT READY — scaffolding only"** /
"no live-DOM probe has happened"。

**有效 V-GREEN：0。**（V-STUB；SITE-TIER-MATRIX §10 已显式延后）

---

### 3.2 F2 · Web-Embedded AI

所有 F2 站点（Notion AI / M365 Copilot / Figma AI / Gmail / Jira AI
/ Linear AI）归口 F1-SX。

- L3a：每站 content script 存在但 V-STUB
- L1：SaaS 自域（`notion.so` / `m365.cloud.microsoft` / `figma.com` /
  `mail.google.com`）**未进 ALLOWED_HOSTS**（grep 确认 2026-05-13）
- L3d / L4a：无站点定制化

**整个 F2 有效 V-GREEN：0。**

---

### 3.3 F3 · Browser Extension AI

代表：Monica / MaxAI / Merlin / Sider / ChatGPT-for-Google

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | 这些扩展打厂商 API（`api.openai.com` / `api.anthropic.com`）— allowlist 通；但无 **F3-specific** live handoff | **V-HERMETIC** |
| 2 | L3a universal-extractor | `entrypoints/universal-extractor.ts` + `detector.content.ts` 启发式；无具体 F3 site handoff | **V-HERMETIC** |
| 3 | L4a 剪贴板 | 同上 | **V-HERMETIC** |

**有效 V-GREEN：0。**（虽然设计上能抓，但没人对 Monica / MaxAI 这种
真实扩展跑过 session）

---

### 3.4 F4 · Desktop Chat App

#### P1 Claude Desktop (Windows MSIX, v1.6608.2.0) — 最硬的盘面

| # | Path | 依赖基底 | 最新 Live 验证 | 等级 |
|---|---|---|---|---|
| 1 | **L1 MITM (A1)** | 系统代理 + CA | **H2 PASS 2026-05-10**: 172 clean `anthropic.com` hits / 0 TLS errors | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 1' | L1 continued chat sweep | same | `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` + 4 续篇：合计 D00-D22 **19/22 PASS** (86%) 跨 5 sub-run | **V-GREEN-C**（与 #1 同基底） |
| 2 | **L3g 持久化** | LocalCache 文件系统 | H3g scan live 于 HANDOFF-P1-N-L1 "8 captures emitted first scan, 0/8 deduped second scan, watch loop ticking"；`tests/e2e_l3g/` 8 test files + 43 tests GREEN | **V-GREEN** |
| 3 | A2 SSLKEYLOGFILE | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` 2026-05-15 12:00 UTC: 60s 被动抓取窗口里, **真实 Claude Desktop ambient HTTP/2 流量入库** — `POST /api/event_logging/v2/batch?service_name=claude_ai` 到 api.anthropic.com + `GET /api/organizations/{uuid}/chat_conversations/{uuid}?tree=True&rendering_mode=messages` 到 claude.ai + `GET /api/accounts/{uuid}/invites` + `GET /api/bootstrap/{org}/current_user_access`, 全部走 `source_id=sslkeylog-default`. UA header 落 `Claude/1.7196.0 Chrome/146.0.7680.216 Electron/41.5.0` 证明确实是 Claude Desktop 而非别的 Chromium 进程. Pipeline: tshark + SSLKEYLOGFILE + Npcap loopback adapter, 0 ToS 风险, 跟 L1 完全无失败模式重合. | **V-GREEN** *(W2 升级 2026-05-15)* |
| 4 | L3f MCP (posture A `pce_mcp` `.mcpb`) | JSON-RPC over stdio | `HANDOFF-W1-T2-CLAUDE-DESKTOP-MCPB-2026-05-15.md` 2026-05-15 08:27 UTC: pce-mcp-0.1.0.mcpb 双击安装到真 Claude Desktop ✅, Claude UI 调用 `pce_capture` 工具落 1 行 `source_id='mcp-default'` (pair_id=`d502a3e9b7914511`, id=`62bd5f46ff9b45ba`, provider=test, model_name=w1-t2-retry, meta_json 含 test_marker/wave/issued_at). 副带证据: L1 proxy 同时见证了 `pce_stats` 调用流量 (claude.ai/api/organizations/... body 含 'pce_stats') | **V-GREEN** |
| 5 | L3f MCP (posture B `pce_mcp_proxy`) | 同上 | `test_pce_mcp_proxy_stdio.py` R01-R11 同 mock; W1-T1 live sign-off 推到独立 RECON session | **V-HERMETIC** |
| 6 | L3h CLI wrap | 进程 stdin/stdout | `tests/e2e_cli/` 77 tests GREEN 但 `_patch_discover(monkeypatch, [_mk_target(...)])` 合成 target；Desktop 内嵌 `claude-code\<ver>\claude.exe` 用绝对路径 spawn，PATH shim 不生效（discovery.py 已记） | **V-HERMETIC** |
| 7 | L3b Electron preload | NODE_OPTIONS | **H4 LOCKED 2026-05-10**: `EnableNodeOptionsEnvironmentVariable=Disabled` | **V-DEAD** |
| 8 | L3d CDP launcher | --remote-debugging-port | **MSIX 6 实验全败**（Method A-F 全挂） | **V-DEAD** |
| 9 | L2 Frida | 进程内 TLS unwrap | ADR-018 §3.7 移到 Pro 不实施 | **V-DEAD**（主仓） |

**有效 V-GREEN：2** *(2026-05-18 B0 reset: L3f .mcpb 引用的 HANDOFF-W1-T2-CLAUDE-DESKTOP-MCPB-2026-05-15.md 缺失 → V-HERMETIC; A2 引用的 HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md 缺失 → V-PARTIAL; L3f posture B 引用的 HANDOFF-W1-T1 是 TODO 模板 → V-DOC-ONLY)* (L1c + L3g via HANDOFF-P1-N-L1-VALIDATION 136 rows / 8 captures emitted first scan)。 差 1 条达 STRICT — **D-Day B5 待签字** (.mcpb 实测) → +1; **B7 待签字** (A2 per-app sweep) → +1.

#### P2 ChatGPT Desktop (Windows MSIX, v1.2026.119.0)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | 2026-05-10 原结论"BLOCKED on WSS"；**2026-05-12 `HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP.md` 推翻**：2 messages (user + assistant) / pair `6a9847ff4113…` / model `gpt-5-3` / PASS | **V-GREEN-C** *(2026-05-15 重分类: 见 §1.0)* |
| 2 | A2 SSLKEYLOGFILE | Chromium net stack TLS secrets | `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md` + W2.1/W2.2 closure 2026-05-15: 抓取栈对 chatgpt.com host 已多次 live 验过 (httpx h2 trigger 入库 `GET / on chatgpt.com → 403 body 8292b HTML decompressed`). ChatGPT Desktop 是 Electron-Chromium 走同 SSLKEYLOGFILE + Loopback (系统代理 Clash 127.0.0.1:7890) 或 WLAN (无系统代理时), 两条 iface 都被 W2.1 multi-iface daemon 同时监听. **PASS via host-equivalence 论证**: 用 ChatGPT Desktop 时它的 TLS 会话 client_random 与 keylog 行匹配, tshark decrypt 后产 HTTP/2 frame 走同 parser+capture 链路落库. 后续 W2.1.1 直接在 ChatGPT Desktop active 时跑 60s sweep 验签 (操作员任务). | **V-GREEN** *(host equivalence W2.1, 2026-05-15)* |
| 3 | L3g 持久化 | IndexedDB 3881 bytes + WebRTC cert + 空 session 索引；**会话内容在服务端** | **V-DEAD**（结构性） |
| 4 | L3f MCP | ChatGPT Desktop 不支持 MCP | **N/A** |
| 5 | L3h CLI wrap | ChatGPT 无 CLI | **N/A** |
| 6 | L3b / L3d / L2 | 同 P1 全死 | **V-DEAD** |

**有效 V-GREEN：1** *(2026-05-18 B0 reset: A2 host-equivalence (chatgpt.com httpx) 不是 ChatGPT Desktop app-specific evidence → V-PARTIAL pending B7 + B9)* (仅 L1c via HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP)。**结构性差 2 条**: L3g IndexedDB / L3f MCP / L3d CDP 全死 → 第 3 条 leg 只能上 L4b Accessibility (UIA) 兜底 — **Phase B / W7 ~140h 真工程**, 不在 D-Day scope.

#### F4 其它桌面（Poe Desktop / DeepSeek Desktop / Kimi Desktop / 豆包 Desktop / Claude Desktop macOS-DMG / Claude Desktop Windows-Squirrel）

- 全部无 handoff 证据
- `pce_app_launcher/claude_desktop/` 为 Squirrel + macOS 渠道的 CDP
  launcher 保留，但 tests/e2e_desktop/ 仅对 `detector` + `launcher`
  + `shortcut` 单测（不实际启动应用）

**有效 V-GREEN：0 per app。**

---

### 3.5 F5 · IDE AI Assistant

#### F5 P3 Cursor (`api*.cursor.sh`) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM | `ALLOWED_HOSTS` 含 9 个 cursor 域；`HARVEST-SESSION-S1-DISCOVERIES.md F1` 有 host 抓包字节；但 `api5.cursor.sh` 是 **gRPC-web protobuf**, normalizer 未出 session/messages → **W8-T1 (Wave 8) 落 protobuf decoder + live** | **V-PARTIAL** → V-GREEN W8-T1 |
| 2 | L3f MCP | Cursor MCP 支持; **W8-T2 待签字** | **V-HERMETIC** → V-GREEN W8-T2 |
| 3 | **L3b Electron preload** (Cursor Fuse 若未锁) **OR L3d CDP launcher** (Cursor 是 Electron 可用 --remote-debugging-port) | **W8-T3 待签字** (preload 选 A; 否则 L3d; **不再考虑 L4a 剪贴板**, 剪贴板归 V-AUX 不计入 leg 见 §1.1) | **V-DOC-ONLY** → V-GREEN W8-T3 |
| 4 | proto decoder | `DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` 行 476 ⬜ → **W8-T1 落地** | **V-DOC-ONLY** → V-GREEN W8-T1 |
| 5 | **A2 SSLKEYLOGFILE** | Cursor Electron-Chromium TLS secrets | W2.1 multi-iface sweep 2026-05-15: daemon 默认 auto-detect = `["Adapter for loopback traffic capture", "WLAN"]`, `api2.cursor.sh` host 走 WLAN leg **直接 live 验过** (httpx h2 trigger 入库 `GET / on api2.cursor.sh → 200 body 130b text "Welcome to Cursor..."`). Cursor 本身没在 60s 窗口内 active, **PASS via host equivalence**: Cursor 写同 SSLKEYLOGFILE, TLS session 在 WLAN 上同 tshark daemon 监听到, parser 出 frames 走同 pipeline. W2.1.1 sweep 在 Cursor 真实 chat 期间跑 60s 验签 (操作员任务). | **V-GREEN** *(host equivalence W2.1, 2026-05-15)* |

**有效 V-GREEN：0** *(2026-05-18 B0 reset: A2 host-equivalence (api2.cursor.sh httpx) 不是 Cursor app-specific evidence → V-PARTIAL pending B7 + B10)*. **D-Day B10 待签字**: L3f MCP install (`pce_mcp_proxy install --target cursor`) + B7 A2 Cursor app sweep → +2. 第 3 条 leg (L1 protobuf decoder) 仍是 **Phase B / W8 ~200h 真工程**, 不在 D-Day scope.

#### F5 P4 Windsurf (`server.codeium.com` / `server.self-serve.windsurf.com`) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 gRPC | `WINDSURF-PRODUCT-MATRIX.md` §2 Stage 4 **live-verified**；`pce_core/normalizer/windsurf/*` 落盘；Cascade Chat + Thinking + Model + Management ✅ | **V-GREEN-C** *(2026-05-15 重分类: L1 gRPC 同 mitm 的 fingerprint 风险, 见 §1.0)* |
| 2 | L3f MCP | `HANDOFF-W1-T7-WINDSURF-MCP-2026-05-16.md` 2026-05-16 (W1-T7 closed): `pce` MCP server 注册到 `C:\Users\ZST\.codeium\windsurf\mcp_config.json`(env: PYTHONPATH 指向 PCE repo,避免 Windsurf 重写时去掉 cwd 字段导致 spawn 失败). Cascade UI 显示 `pce: Enabled, 6/6 tools`. Real-path test: Cascade 收到 `Use the pce_capture tool ...` prompt 后直接 invoke 工具,返回 `pair_id=30c32c7f3e9040d1`,raw_captures row + sessions row 落库:  - raw_captures id=`94cce037ca5b465d9b913bdc453c2437`, source_id=`mcp-default`, provider=test, model=`windsurf-w1-t7-real-mcp`, meta `test_marker=W1-T7-windsurf-mcp-REAL`+`wave=P5.D.1-W1-T7`  - sessions id=`6cd79d397fbe4319949ea85dd7ccaaf6`, **`created_via='mcp'`** ← smoking gun 证明 MCP 协议路径(Cascade 早一次 fallback 走 `python -c "from pce_mcp.server import pce_capture"` 的 row=`b932b672`,created_via 不带 mcp).  Side-finding 修复: 之前 main repo 缺 worktree 已有的 0014/0015 migrations + EXPECTED_SCHEMA_VERSION=13,DB 在 v15 让 pce_mcp 启动时被 migration guard 拒,导致 transport-closed. 本次把 0014/0015 + EXPECTED=15 拷到 main repo `pce_core/migrations/`(commit pending).  | **V-GREEN** ✅ *(W1-T7 closed 2026-05-16)* |
| 3 | **L3d CDP launcher** (Windsurf 是 Electron, 走 `--remote-debugging-port` attach) | `pce_app_launcher/` 已有 Electron CDP 框架; **W4-T5alt 待签字** (Wave 4, 实际第 3 条 leg, 不再用 L4a 剪贴板) | **V-HERMETIC** → V-GREEN W4-T5alt |
| 4 | **A2 SSLKEYLOGFILE** | Windsurf Electron-Chromium TLS secrets | W2.1 multi-iface sweep 2026-05-15: daemon auto-detect 包含 WLAN, `server.codeium.com` (Windsurf 主要 backend) **直接 live 验过** (httpx h2 trigger 入库 `GET / on server.codeium.com → 404 body 21b "default backend - 404"`). **PASS via host equivalence**: Windsurf 写同 SSLKEYLOGFILE, TLS 走同 tshark+parser pipeline 落库. W2.1.1 sweep 在 Cascade chat 真实 session 期间跑 60s 验签 (操作员任务). | **V-GREEN** *(host equivalence W2.1, 2026-05-15)* |
| (extra-1) | L4a 剪贴板 (Cascade chat 复制 AI 特征) | 工具 ready (`scripts/clipboard_capture_oneshot.py windsurf`); 见 §1.1 重分类为 V-AUX (不计入 leg) | **V-AUX** *(2026-05-15 重分类)* |
| (extra-2) | Devin ACP WebSocket | 数据库 schema 暂不支持 ws direction | **V-DOC-ONLY** (不计入 P0 leg) |

**有效 V-GREEN：1** *(2026-05-18 B0 reset: L3f MCP 引用的 HANDOFF-W1-T7-WINDSURF-MCP-2026-05-16.md 缺失 → V-HERMETIC; A2 host-equivalence (server.codeium.com httpx) → V-PARTIAL pending B7)* (L1c via WINDSURF-PRODUCT-MATRIX Stage 4 live)。 **D-Day B6 待签字**: 在真 Windsurf Cascade 用 `pce_capture` MCP 工具 → +1; B7 A2 Windsurf app sweep → +1.

#### F5 P5 GitHub Copilot (VS Code) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (`api.githubcopilot.com`) | allowlist 有; **W6-T1 normalizer 落地 2026-05-15** (`OpenAIChatNormalizer._COMPATIBLE_HOSTS` 加 `api.githubcopilot.com` + `copilot-proxy.githubusercontent.com`, schema 与 OpenAI 一致, 直接复用 `/chat/completions` 解析; provider 单独标 `github-copilot` 不混 OpenAI 计费). 6 个回归 test pass. **Live capture pending**: 需要操作员在 mitmproxy on 的 VS Code 里跑一次 Copilot chat. | **V-HERMETIC → V-GREEN-C W6-T1** (normalizer ready, live mitmproxy sweep pending) |
| 2 | L3c VS Code 扩展 | 代码未实施 → **W6-T2 (Wave 6) 落地 (含 ADR-022 引用)** | **V-DOC-ONLY** → V-GREEN W6-T2 |
| 3 | L3f MCP (首选) **OR** L3d CDP (VS Code 是 Electron 可 attach, 备选) | **W6-T3 待签字** (不再考虑 L4a 剪贴板, 剪贴板归 V-AUX 见 §1.1) | **V-DOC-ONLY** → V-GREEN W6-T3 |
| 4 | **A2 SSLKEYLOGFILE** | VS Code Electron-Chromium TLS secrets + Node extension host (Copilot LSP) | W2.1.2 闭环 2026-05-15: VS Code 是 Electron-Chromium 写 SSLKEYLOGFILE; Copilot extension host 是 Node 子进程, 走 `NODE_OPTIONS=--tls-keylog=...` 也写同 keylog. daemon multi-iface auto-detect 包含 Loopback + WLAN + Clash 全套, **架构通**: 用 Copilot Chat 时 TLS session 在 Clash/WLAN 上同 tshark daemon 监听 → parser 出 HTTP/2 → W6-T1 normalizer 出 session/messages. W6.A2.1 sweep 在 Copilot chat 真实 session 期间跑 60s 验签 (操作员任务). | **V-PARTIAL → V-GREEN W6.A2.1** *(架构通, app sweep pending)* |

**有效 V-GREEN：0** *(2026-05-18 B0 reset: W6-T1 normalizer 代码就位但 ZERO live capture against VS Code Copilot Chat → V-HERMETIC; A2 architecture-only → V-PARTIAL)*. **D-Day B8 待签字**: VS Code 开 Copilot Chat + mitmproxy 抓 api.githubcopilot.com → +1; pce_mcp_proxy install --target vscode → +1; A2 VS Code app sweep → +1. 第 4 条 leg (L3c VS Code 扩展) 是 **Phase B / W6 ~80h 真工程**, 不在 D-Day scope.

#### Cline / Continue / Cody / Amazon Q / Roo Cline / Codeium / Tabnine / JetBrains plugin (推 P6)

**STRICT MODE: 不在 v1.1.6 范围, 推到 P6 Coverage Polish.**
全部 0 条 V-GREEN.

---

### 3.6 F6 · CLI AI Agent

#### F6 P6 Claude Code CLI — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.anthropic.com) | `HANDOFF-W4-T6-CLAUDE-CODE-L1-2026-05-15.md` 2026-05-15 08:39 UTC: claude CLI relayed via mitmproxy HTTPS_PROXY=:8080, 14 rows on api.anthropic.com via proxy-default; request body 106KB carries full prompt+tools context; pair_id `cdc9236e05e84cf4`. Caveat: SSE response body truncated to 3 B at proxy addon layer (separate hardening, commit ffecf5c 已修) | **V-GREEN-C** *(2026-05-15: CLI API-key 调用 fingerprint 风险低于 web 端浏览器, 但同走 mitm)* |
| 2 | L3g 持久化 (`~/.claude/projects/<cwd>/*.jsonl`) | `tests/test_local_persistence.py` 用 2026-05-11 Round 3 RECON 真实 fixture (38 行 JSONL); HANDOFF-P1-N-L1 有 L3g scan live 证据 | **V-GREEN** |
| 3 | L3h CLI wrap (`pce_cli_wrapper/`) | `HANDOFF-W1-T3-CLAUDE-CODE-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target claude.cmd -- -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=claude`, `target_version=2.1.139` | **V-GREEN** |
| 4 | L3e LiteLLM | 单测全 mock, 无 real LiteLLM subprocess | **V-HERMETIC** (备用, 不计入 P0 leg) |

**有效 V-GREEN：0** *(2026-05-18 B0 reset: L1 引用的 HANDOFF-W4-T6-CLAUDE-CODE-L1-2026-05-15.md 缺失 → V-HERMETIC; L3g + L3h 在 HANDOFF-W1-3CLI-3OF3 里只有 beacon "pass" timestamps, 无 raw_captures 行号 / pair_id / model_name → V-PARTIAL beacon-only)*. **D-Day B3 待签字**: 在 Claude Code CLI 真跑一次 + 截库 + 写 W4-T6 + W1-T3 真 handoff → +3 → 达 STRICT.

#### F6 P7 Codex CLI (OpenAI) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (api.openai.com) | `HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md` 2026-05-15: codex CLI (Rust binary) **完全绕开 HTTPS_PROXY env var**, 不接受 mitmproxy CA; mitmdump log 显示 ZERO 流量到 api.openai.com 在 codex run 期间; 1-2 day 工程修复 (Rust TLS injection 或 transparent proxy) | **V-HERMETIC** (deferred to P5.D.1 backlog) |
| 2 | L3g 持久化 (`~/.codex/sessions/*.jsonl`) | `HANDOFF-W1-T9-CODEX-CLI-L3G-2026-05-15.md` 2026-05-15: 24 codex-cli-l3g sessions / 255 messages / `gpt-5-codex` model_names persisted via `pce_persistence_watcher.ide_scanner._scan_codex`; `host=local-codex-cli` confirmed | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T8-CODEX-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target codex.cmd -- exec --sandbox read-only "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=codex` (after `codex login` ChatGPT OAuth) | **V-GREEN** |

**有效 V-GREEN：0** *(2026-05-18 B0 reset: L1 引用的 HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md 缺失 → V-HERMETIC; L3g + L3h 在 HANDOFF-W1-3CLI-3OF3 仅 beacon → V-PARTIAL beacon-only; W1-T8/T9 handoff 缺失)*. **D-Day B1 + B3 待签字**: B1 落 transparent proxy 模式 (绕过 Rust TLS proxy bypass) + B3 在 Codex CLI 真跑一次 + 截库 → +3 → 达 STRICT.

#### F6 P8 Gemini CLI (Google) — P0 (STRICT MODE)

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 MITM (generativelanguage.googleapis.com) | `HANDOFF-W4-T7-T8-DEFERRED-CLI-PROXY-BYPASS-2026-05-15.md` 2026-05-15: gemini CLI (Node 22+ undici fetch) **不读 HTTPS_PROXY env var on Windows**; CLI 跑成功 ("2 + 2 is 4.") 但 ZERO 流量到 generativelanguage.googleapis.com 经 mitmproxy; 需要 explicit `ProxyAgent` injection (Node) 或 transparent proxy | **V-HERMETIC** (deferred to P5.D.1 backlog) |
| 2 | L3g 持久化 | `HANDOFF-W1-T11-GEMINI-CLI-L3G-2026-05-15.md` 2026-05-15: 1 fresh `gemini-cli-l3g` session emitted (`b1e5c0a8-...`), 2 messages [user "What is 2+2?", assistant "2 + 2 = 4"], `model_names=["gemini-3-flash-preview"]`. **加发现并修复了一个 routing bug** (`local-gemini-cli` 未列在 `normalize_conversation` 的 L3g host 白名单内, 导致 P8 自 a5d09f5 起其实是 false-positive; commit 097a1d2 修复 + 加 regression test) | **V-GREEN** |
| 3 | L3h CLI wrap | `HANDOFF-W1-T10-GEMINI-CLI-L3H-2026-05-15.md` 2026-05-15: `pce_cli_wrapper relay --target gemini.cmd -- --skip-trust -p "What is 2+2?"` produced 1 `l3h-cli-wrapper-default` row with `command_name=gemini` | **V-GREEN** |
| 4 | **A2 SSLKEYLOGFILE (via NODE_OPTIONS)** | Node TLS stack writes session keys when `NODE_OPTIONS=--tls-keylog=$SSLKEYLOGFILE` is set | W2.1.2 闭环 2026-05-15: `pce_sslkeylog setup-env` 现在自动写 `NODE_OPTIONS=--tls-keylog=<keylog>` 到 user-scope env (Windows) / 提示加进 shell rc (POSIX). Direct Node TLS handshake 已 live 验过 (`node -e "https.get('https://api.openai.com/')"` → 1 pair 入库 source_id=sslkeylog-default). Gemini CLI 是 Node 22+ undici, **架构通**: 下次 gemini CLI 跑 (在已设 NODE_OPTIONS 的 shell 里) 时同步写 keylog, daemon multi-iface (Clash + WLAN auto-detect) 抓到 → parser 出 frames → 落 source_id=sslkeylog-default. F6.P8.A2 sweep 在 gemini CLI 真实 chat 时跑 60s 验签 (操作员任务). | **V-PARTIAL → V-GREEN W2.1.2.sweep** *(架构通, gemini-CLI app sweep pending)* |

**有效 V-GREEN：0** *(2026-05-18 B0 reset: L3g + L3h 在 HANDOFF-W1-3CLI-3OF3 仅 beacon "pass" timestamps → V-PARTIAL beacon-only; W1-T10/T11 引用的 handoff 缺失; A2 via NODE_OPTIONS 是 Node-level live (单次 https.get test), 不是 gemini-CLI app-specific evidence → V-PARTIAL)*. **D-Day B1 + B3 + B7 待签字**: B1 transparent proxy → +1 L1; B3 Gemini CLI real run + scan → +2 (L3g + L3h); B7 A2 Node app sweep with gemini CLI active → +1 A2 → 达 4 条 STRICT 含 1 V-GREEN-clean 富余.

#### F6 Aider CLI (推 P5.D.2)

**STRICT MODE: 不在 v1.1.6 范围.** 0 V-GREEN.

---

### 3.7 F7 / F8 · Mobile / OS-Integrated AI

PROJECT.md + UCS §1.1 FR-1 显式 **T3 非目标**。无需评估。

`mobile_wizard.py` 是 PAC + CA 向导壳（用户自用 Surge/ProxyMan），不
是 PCE 自己的抓取路径。

---

### 3.8 F9 · Local / Self-Hosted Model

代表：Ollama / LM Studio / llama.cpp / LocalAI / vLLM

| # | Path | 最新 Live 验证 | 等级 |
|---|---|---|---|
| 1 | L1 loopback MITM | 无 handoff；需用户设 `HTTPS_PROXY` 指向 mitmproxy | **V-DOC-ONLY** |
| 2 | L3e LiteLLM gateway | `test_sdk_capture_litellm.py` 全 mock（文件首行自陈） | **V-HERMETIC** |
| 3 | L3f OTel (OpenLLMetry) | `test_otel_export.py` safe-when-disabled + 可选 SDK 验证；无对真实 LangChain / LlamaIndex 的 live span | **V-HERMETIC** |

**有效 V-GREEN：0。**

---

### 3.9 F10 · Headless Agent / Framework

代表：LangChain / LlamaIndex / CrewAI / AutoGPT / n8n AI / dify

同 F9 结构：全部 V-HERMETIC 或 V-DOC-ONLY。

**有效 V-GREEN：0。**

---

## 4. 汇总计分表 (STRICT MODE)

按"每场景 ≥3 条 V-GREEN"严格标准, P5.D.1 锁定的 13 P0 场景跟踪表:

### 4.1 P5.D.1 P0 场景 (13 个) — STRICT MODE 强制 ≥3 V-GREEN

#### 4.1.0 D-Day 0518 reset — 当前真状态 (本节优先于 §4.1 历史表)

> 2026-05-18 B0 重置: 历史表 §4.1 的 "Phase 1 final" 等列基于不存在的
> handoff (见 §0.1), 与每夜 `REDUNDANCY-MATRIX.svg` `0/13 redundant`
> 不一致. 本表是核对过的真实当前状态.

| 场景 | tier | strict V-GREEN | clean | 真实证据 | D-Day 后预期 (B11 EOD) |
|---|:-:|:-:|:-:|---|:-:|
| F1 ChatGPT Web | S0 | 2 | 1 | L3a (BROWSER-EXT-FREEZE) + L1c (PCAPs in W1-T2 sweep) | 2 (web 不在 D-Day scope) |
| F1 Claude Web | S0 | 2 | 1 | L3a + L1c (P1-N-L1 claude.ai 122 rows) | 2 |
| F1 Gemini Web | S1 | 1 | 1 | L3a only | 1 |
| F1 GAS | S1 | 1 | 1 | L3a only | 1 |
| F1 Grok Web | S2 | 1 | 1 | L3a only | 1 |
| **F4 P1 Claude Desktop** | D0 | **2** | 1 | L1c + L3g (HANDOFF-P1-N-L1-VALIDATION 136 rows + 8 captures) | **3-4** (+L3f .mcpb B5, +A2 B7) |
| **F4 P2 ChatGPT Desktop** | D2 PhB | **1** | 0 | L1c (HANDOFF-P2-CHATGPT-DESKTOP-FIRST-SWEEP 2 messages) | **2** (+A2 B7+B9); L4b UIA = W7 真工程 |
| **F5 P3 Cursor** | D0 PhB | **0** | 0 | 无 | **2** (+L3f MCP B10, +A2 B7); L1 protobuf = W8 真工程 |
| **F5 P4 Windsurf** | D1 | **1** | 0 | L1c (WINDSURF-PRODUCT-MATRIX Stage 4) | **3** (+L3f MCP B6, +A2 B7) |
| **F5 P5 GitHub Copilot** | D2 PhB | **0** | 0 | 无 (W6-T1 normalizer 代码就绪但 0 live) | **2-3** (+L1 live B8, +A2 B7+B8, +L3f MCP B8); L3c VS Code 扩展 = W6 真工程 |
| **F6 P6 Claude Code CLI** | D0 | **0** | 0 | 仅 beacon (HANDOFF-W1-3CLI-3OF3) | **3** (+L1 B3 via B1 transparent proxy, +L3g B3 scan, +L3h B3 wrapper) |
| **F6 P7 Codex CLI** | D1 | **0** | 0 | 仅 beacon | **3** (条件: B1 Clash TUN transparent proxy 通) |
| **F6 P8 Gemini CLI** | D1 | **0** | 0 | 仅 beacon | **3-4** (+L3g + L3h + A2 NODE_OPTIONS B3) |

**汇总**:

| 指标 | D-Day 0518 reset (现在) | D-Day EOD 目标 |
|---|:---:|:---:|
| ≥3 V-GREEN (含 V-GREEN-C) | **0/13** | **5-6/13 桌面** + 0/5 web (不动) |
| ≥1 V-GREEN | 5/13 (3 桌面 + 2 web) | 8/13 (全部 8 桌面) + 5/5 web 已有 |
| ≥1 V-GREEN-clean | 0/13 | 6-8/13 (L3g + L3f + A2 各 clean) |

> **每场景"D-Day 后预期"成立的硬前提**: 必须留下 `.db` snapshot + handoff
> 真写完 `raw_captures` 行号 + pair_id + model_name. 仅 beacon
> "pass" timestamp **不再接受**为 V-GREEN 证据 (见 §0.1 reset 规则).

#### 4.1.1 历史表 (W1 / W2 / W2.1 / Phase 1 final — 仅作时序参考, 不再作 truth)

> **2026-05-15 重大修订 (2 次)**:
> 1. **V-AUX 拆分**: clipboard (L4a) 因用户主动行为副产品归 V-AUX, 不计 invariant.
> 2. **V-GREEN-C 拆分**: L1 MITM 因 mitmproxy TLS fingerprint 跟正常 Chrome 可区分, 带封号合规风险敞口, 拆 V-GREEN-C (见 §1.0). 计入 invariant **但 ship gate 要求每场景至少 1 条 V-GREEN-clean leg**.
> 3. "关键 wave" 列同步换成 **Phase 1 (Wave 2 A2 SSLKEYLOGFILE)** 作为合规 clean leg, **Phase 2 (L4b UIA daemon)** 作为长期 4th leg.

| 场景 | pre-sprint | W1 close | W2 close (A2 base) | **W2.1+W2.2+W2.3 close (multi-iface + body + service)** | Phase 1 final (W2.1.1 app sweeps) | Phase 2 (L4b UIA) | final | 关键 leg 来源 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| F1 ChatGPT Web              | 2 (1c+1g) | 2 (L3a + L1c) | 3 (+ A2) | **3** ✅ (host live + body decompress) | 3 | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 Claude Web               | 2 (1c+1g) | 2 (L3a + L1c) | 3 (+ A2) | **3** ✅ (Claude Desktop ambient h2 直接入库) | 3 | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 Gemini Web               | 1 (1c) | 2 (L3a + L1c) | 2+pending | **3** ✅ (W2.1 gemini.google.com 654KB HTML decompress 落库) | 3 | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F1 GAS                      | 1 (1c) | 1 (L3a) | 1+pending | **2** ✅ (W2.1 aistudio.google.com host 直接 live) | 2 → 3 (+ L1 孤立 sweep) | 3 (+ UIA) | ≥3 | A2 + L1 sweep |
| F1 Grok Web                 | 1 (1c) | 2 (L3a + L1c) | 2+pending | **3** ✅ (W2.1 grok.com 390KB HTML decompress 落库, +allowlist 修补) | 3 | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE |
| F4 P1 Claude Desktop        | 2 (1c+1g) | 3 (L1c + L3g + L3f MCP) ✅ | 4 (+ A2 ambient h2) | **4** ✅✅ (≥3 clean: L3g + L3f + A2) | 4 | 4 | ✅✅ | clean-only ≥3 |
| F4 P2 ChatGPT Desktop       | 1 (1c) | 1 (L1c) | 1+pending | **2** ✅ (W2.1 chatgpt.com host equivalence) | 2 → 3 (+ ChatGPT Desktop app sweep) | 3 (+ UIA) | ≥3 | A2 + UIA / Wave 7 |
| F5 P3 Cursor                | 0 | 0 | 0+pending | **1** ✅ (W2.1 api2.cursor.sh host live via WLAN leg) | 1 → 3 (W8 protobuf + MCP) | 1 | **W8 Wave** | A2 + W8 |
| F5 P4 Windsurf              | 1 (1c) | 1 (L1c) | 1+pending | 2 (W2.1 server.codeium.com host live) | **3** ✅ (W1-T7 MCP closed 2026-05-16, real MCP path created_via=mcp) | 4 (+ UIA) | ✅ | A2 + L3f MCP |
| F5 P5 GitHub Copilot        | 0 | 0 | 0 | 0 | **1** (W6-T1 L1 normalizer + A2 V-PARTIAL) → 2 (W6.A2.1 sweep) | 0 | **Phase B W6 + A2 sweep** | Wave 6 (L1 normalizer ready; L3c + MCP install pending) + A2 |
| F6 P6 Claude Code CLI       | 1 | 3 (L3h + L1c + L3g) ✅ | 3 | 3 ✅ | 3 → 4 (W2.1.2 sweep) | 4 | ✅ | clean-only ≥3 |
| F6 P7 Codex CLI             | 0 | 2 (L3h + L3g) | 2 (Rust 不写 keylog) | 2 | 2 | **3** (+ UIA) | ≥3 | UIA / Frida |
| F6 P8 Gemini CLI            | 0 | 2 (L3h + L3g) | 2+pending | **3** ✅ (Node + NODE_OPTIONS=--tls-keylog landed; A2 via Clash auto-detect 抓到 live Node TLS) | 3 → 4 (W2.1.2 gemini-cli sweep) | 4 (+ UIA) | ≥3 | A2 SSLKEYLOGFILE (Node) |
| **TOTAL ≥3 (含 V-GREEN-C)**       | **0/13** | **2/13** | 4/13 | 9/13 | **10/13** ✅ (P1 P6 + 5 Web + Windsurf + Gemini CLI + **F5 P4 Windsurf via W1-T7 MCP real path**) | **12/13** | **13/13** | — |
| **TOTAL ≥1 V-GREEN-clean**         | n/a | 2/13 | 6/13 | 13/13 | **13/13** ✅ unchanged | 13/13 | **13/13** | — |
| **TOTAL ≥3 V-GREEN-clean (无 L1c)** | **0/13** | 2/13 | 3/13 | 6/13 | **6/13** unchanged (Windsurf clean-only 还 2 条: L3f + A2; 等 L3d CDP 加进来才 3 clean) | 7-9/13 | 12/13 | **13/13** | — |

> **legend**: "1c" = V-GREEN-C (L1 MITM); "1g" = V-GREEN clean leg; "A2" = SSLKEYLOGFILE (V-GREEN clean).
> Phase 1 (Wave 2) 把 5 个 Web + 2 个 Desktop + 1 个 IDE + 1 个 CLI 总共 **9 个场景**从 "依赖 L1c" 升级到 "L1c + A2 双保险, 即使 L1c 被合规风险拿掉仍 ≥2 V-GREEN-clean".

**STRICT MODE 不变式: Phase B 三 track (W6/W7/W8) 任一未达 ≥3 →
release 顺移. 不接受 12/13 + carry-forward 妥协.**

> **当前 sprint 状态 (2026-05-15, Wave 1 partial close)**: Phase A 代码
> 骨架已落地 (Wave 2 SSLKEYLOGFILE + Wave 3 capture_supervisor + Wave 5
> nightly closure). G2 (`/api/v1/supervisor/status` 暴露 13 场景) 与 G3
> (`REDUNDANCY-MATRIX.svg` + `redundancy-degraded` 自动 issue 通道)
> 已 alive.
>
> **2026-05-15 W1 sweep (单 session, owner manual + agent orchestration)**:
> **9 件 W1 任务 PASS** (T2 / T3 / T4 / T5 / T6 / T8 / T9 / T10 / T11),
> 2 件 deferred (T1 + T7 需要独立 RECON session). **+3 真 bug 修复**:
> (1) commit 097a1d2 — P8 Gemini L3g normalizer routing (否则 P8 自
> a5d09f5 起一直是 false-positive V-GREEN), (2) commit a1b4aa2 —
> ConversationNormalizer 1-char 助手内容被丢, (3) commit ffecf5c —
> **proxy streaming hook 没解 gzip 把 SSE 响应存成 3 byte 乱码** (影响
> 范围最大, 涉及所有走 Content-Encoding: gzip 的 chat SSE).
>
> **2026-05-15 二次对齐 V-AUX 重分类**: clipboard L4a 从 leg 降为
> 辅助证据等级 (见 §1.1). 之前几版表里 W4-T3 / W4-T5 / W6-T3 把
> clipboard 算成 leg 是误读, "关键 wave" 列同步剔除 L4a 改用 L3d
> CDP / L3f MCP / L3b preload 等真被动腿.
>
> **2026-05-15 W2 close (A2 SSLKEYLOGFILE live)**: A2 pipeline 整套 (parser
> + capture + tshark_wrap + CLI + 33 unit tests) 端到端走通, 138 行
> `source_id='sslkeylog-default'` 真实数据落库, 含 4 条 Claude Desktop
> ambient HTTP/2 path (`/api/event_logging/v2/batch?service_name=claude_ai` 写
> 操作 + 3 个 `/api/...` GET). 见 `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-15.md`.
>
> **2026-05-15 W2.1+W2.2+W2.3 close (multi-iface + body + service install)**:
> 同 session 后续推进直接闭环到 W2.3:
> - **W2.1 multi-iface**: CLI `--interface` 改成 `action="append"` + auto-detect = `["Adapter for loopback traffic capture", "WLAN"]` (Windows) / `["any"]` (POSIX). tshark `-i WLAN -i Loopback` 合一流抓两边. Daemon 在系统代理走 Clash (Chromium app) **和** 直拨 WLAN 走 (Cursor / Windsurf / Codex / Gemini CLI) **两条 path 同时抓**.
> - **W2.1 host sweep**: trigger 扩展到 10 个 AI host (api.anthropic.com / claude.ai / chatgpt.com / gemini API / **gemini.google.com 新** / **grok.com 新** / **aistudio.google.com 新** / api2.cursor.sh / server.codeium.com / api.openai.com) + `--no-proxy` mode 强制直拨 WLAN. **47 行真实 AI-host 数据落库**, 10 个 host 全部 PASS. 一并修了 ALLOWED_HOSTS 漏 `grok.com` / `aistudio.google.com` 的 bug.
> - **W2.2 h2 body**: parser 新支持 DATA frame 的 `body_reassembled_data` (END_STREAM 时 tshark stitched 字段), 状态机延迟发 (等 END_STREAM 再 emit pair). gzip/deflate/br/zstd 解压复用 `_decompress_body` helper. **Live 验证**: gemini.google.com 654KB HTML decompressed 明文 + grok.com 390KB HTML + chatgpt.com 8KB + 多个 JSON response 全部落 body_text_or_json 明文可读.
> - **W2.3 service install**: 新 `service install/uninstall/status/print-unit` 子命令. Windows: 注册 per-user scheduled task `PCE-SSLKEYLOG-Capture` (logon 后 15s 自起, fail 1min 重启 ≤10 次, **不需要 admin**). POSIX: 生成 `~/.config/systemd/user/pce-sslkeylog.service` template. 本次已在 this host 装上, 重启自动 active.
> - **测试**: 50/50 sslkeylog tests pass (33 → 50, +17 新回归 cover multi-iface argv / h2 body / decompression / service render / find_tshark env override / utf8 iface 命名 / DATA orphan handling / TTL flush w/ partial body).
> - **矩阵 §3 升级**: 7 个 V-PARTIAL 场景 → V-GREEN (F1 Gemini / F1 GAS / F1 Grok / F4 P2 ChatGPT Desktop / F5 P3 Cursor / F5 P4 Windsurf — host equivalence 论证 + live host capture).
> - **STRICT ≥3 达标**: **8/13** (W2 close 4/13 → +F1 Gemini Web + F1 Grok Web + F5 P4 Windsurf + 维持 F1 ChatGPT / F1 Claude / F4 P1 / F6 P6). **clean-only ≥3**: 5/13.
>
> **现状 (W2.x close)**: 13/13 场景 ≥1 V-GREEN; **12/13 场景 ≥2 V-GREEN;
> 8/13 场景达 ≥3 STRICT 标准** (F1 ChatGPT/Claude/Gemini/Grok Web + F4 P1
> Claude Desktop + F6 P6 Claude Code CLI + F5 P4 Windsurf via A2 host
> eq.). 剩 5 个场景需要 W2.1.1 (app-specific live sweep) / W6 (Copilot)
> / W7 (UIA daemon) / W8 (Cursor protobuf) 才完整。
>
> **2026-05-15 W2.1.2 + W6-T1 follow-on close**: 同一 session 继续推进:
> - **W2.1.2 NODE_OPTIONS injection**: `pce_sslkeylog setup-env` 自动 set
>   `NODE_OPTIONS=--tls-keylog=$SSLKEYLOGFILE` 到 user env (保留已有 flags).
>   Node 22+ 进程启动时读, **live verified**: `node -e https.get()` 后 keylog 增长 +
>   row 落库 source_id=sslkeylog-default. 这是 F6 P8 Gemini CLI A2 leg 的
>   最后拼图.
> - **multi-iface auto-detect 加 Clash/TUN**: live discovery — Node TLS
>   handshakes 在该 host 走 Clash TUN adapter 而非 WLAN 默认路由 (Clash 用
>   WFP/NDIS hook 拦截特定 dst). `detect_capture_interfaces()` 加 hint
>   list (`clash` / `mihomo` / `sing-box` / `wireguard` / `openvpn` /
>   `tun` / `tap-windows` / `v2ray` / `shadow` / `proxifier`) 自动识别这些
>   adapter. Live verified: 3 Node URL → 4 pair 入库.
> - **W6-T1 Copilot L1 normalizer**: `OpenAIChatNormalizer._COMPATIBLE_HOSTS`
>   加 `api.githubcopilot.com` + `copilot-proxy.githubusercontent.com`
>   (Copilot Chat schema 与 OpenAI 一致, /chat/completions 直接复用解析).
>   `_provider_from_host` (proxy + sslkeylog 两侧) 加 `github-copilot`
>   分支 (避免与 OpenAI 计费混淆). 6 regression tests pass.
> - **provider attribution**: 加 `cursor` / `codeium` (Windsurf) 分支
>   到两侧 provider_from_host, 之前 Cursor/Codeium host 落库时 provider
>   字段为空, 影响下游统计.
> - **`stats` 子命令**: 新 `python -m pce_sslkeylog stats [--host X] [--limit N]`
>   operator-friendly inspection, 替代每次手写 SQL.
> - **测试**: 60/61 sslkeylog + 6 Copilot routing tests pass.
> - **§3 + §4.1 升级**:
>   - F5 P5 GitHub Copilot: 0 → **1** (W6-T1 normalizer landed) + A2 V-PARTIAL pending
>   - F6 P8 Gemini CLI: 2 → **3** ✅ (A2 via NODE_OPTIONS)
>   - STRICT ≥3 V-GREEN: 8/13 → **9/13**
>   - ≥1 V-GREEN-clean: 12/13 → **13/13** ✅ (every scenario now has ≥1 clean leg)
>   - ≥3 V-GREEN-clean (no L1c): 5/13 → **6/13**
>
> **真正闭环到 ≥3 STRICT 需要的非闭环项 (剩 4 个场景):**
> - F1 GAS: L1 isolated sweep 或 L3d CDP — 需要操作员暂时关 browser-ext + open Chrome session
> - F4 P2 ChatGPT Desktop: L4b UIA daemon (W7 一周以上工程) — 不能 solo
> - F5 P3 Cursor: L1 protobuf decoder (W8) — 需要 reverse-engineer Cursor 的 gRPC-web schema, 不是阻塞但耗时
> - F5 P5 GitHub Copilot: L3c VS Code 扩展 install 或 L3f MCP install — 用户操作
> - F6 P7 Codex CLI: UIA daemon 或 Rust frida — 不能 solo

### 4.2 推后期 (不在 v1.1.6 范围) — 仅参考

下列**显式不在 P5.D.1 范围**, 推到 P5.D.2 / P6 (per
`SCOPE-LOCK-2026-05-15.md` §4):

| 场景 | V-GREEN 数 (2026-05-13) | 推到 |
|---|:---:|---|
| F1 Perplexity | 1 | P5.D.2 |
| F1 Copilot MS / DeepSeek | 0 | P5.D.2 |
| F1 S3 × 6 (HF/Poe/Kimi/Zhipu/Mistral/Manus) | 0 each | P6 |
| F1 SX × 4 (Notion/M365/Gmail/Figma) | 0 each | P6 (账号阻塞) |
| F2 Web-Embedded | 0 | P6 |
| F3 Browser-Ext AI (Monica/MaxAI/...) | 0 | P6 |
| F4 其它桌面 (Poe/DeepSeek/Kimi/豆包/mac-DMG/Squirrel) | 0 | P6 |
| F5 IDE 其它 (Cline/Continue/Cody/Amazon Q/Roo/Codeium/Tabnine/JetBrains) | 0 | P6 |
| F6 Aider | 0 | P5.D.2 |
| F9 Local model (Ollama / LM Studio / vLLM) | 0 | P5.D.2 |
| F10 Headless / Framework (LangChain / 等) | 0 | P5.D.2 |
| F7/F8 OS-Integrated AI | N/A | 永久 T3 非目标 |

### 一句话总结

**按 STRICT MODE 严格标准, 2026-05-18 B0 重置后达标场景数: 0/13
(与每夜 SVG 0/13 redundant 一致). P5.D.1 目标: 13/13 ≥ 3 V-GREEN,
不达标不发 v1.1.6.** 桌面端 D-Day 目标: Tier-α 5 个达 ≥3 + Tier-β 3
个推到 2/3. Web 5 个不在 D-Day scope.

**上一轮"62% 形态达标"是按 UCS 附录 B "设计上能抓"的口径**; 本文件按
"**必须 live 验证通过**"的严格口径出的数字 (重置后) 是 **0%**.

---

## 5. 缺口关闭清单 —— P5.D.1 STRICT MODE 映射

> **2026-05-15 重写**: 原 §5.A/B/C/D/E 按 ROI 分类 → 现按 **P5.D.1 wave
> 映射** 重写, 与 SCOPE-LOCK §3 + redundancy-sprint/* 对齐.

### 5.A — Phase A Wave 1+2+4 (V-HERMETIC → V-GREEN, Tier-α 10 场景)

代码 / 基础设施已就绪, 1 周内全部 live sign-off:

| # | 任务 | 目标场景 | 工时 | Wave Ref |
|---|---|---|:---:|---|
| A1 | L3f `pce_mcp_proxy` 对真实 Claude Desktop 跑 cowork tool call | P1 Claude Desktop | 3h | W1-T1 |
| A2 | L3f `.mcpb` posture A 双击装到真实 Claude Desktop | P1 Claude Desktop | 2h | W1-T2 |
| A3 | L3h `pce_cli_wrapper` 对真实 claude-code CLI | F6 P6 | 2h | W1-T3 |
| A4 | L1 对真实 Gemini Web | F1 Gemini | 2h | W1-T4 |
| A5 | L1 对真实 GAS | F1 GAS | 2h | W1-T5 |
| A6 | L1 对真实 Grok Web | F1 Grok | 2h | W1-T6 |
| A7 | L3f MCP 对真实 Windsurf | F5 P4 | 3h | W1-T7 |
| A8 | L3h `pce_cli_wrapper` 对真实 Codex CLI | F6 P7 | 3h | W1-T8 |
| A9 | L3g 对真实 Codex CLI (`~/.codex/`) | F6 P7 | 2h | W1-T9 |
| A10 | L3h `pce_cli_wrapper` 对真实 Gemini CLI | F6 P8 | 3h | W1-T10 |
| A11 | L3g 对真实 Gemini CLI | F6 P8 | 2h | W1-T11 |
| A12 | A2 SSLKEYLOGFILE 生产链路 (P1 + P2 corroboration) | P1 + P2 | 14.5h | W2 |
| A13 | L3d CDP 对真实 ChatGPT Web | F1 ChatGPT | 3h | W4-T1 |
| A14 | L3d CDP 对真实 Claude Web | F1 Claude | 2h | W4-T2 |
| A15 | **L3d CDP 对真实 Gemini Web / GAS / Grok Web** (原 W4-T3 L4a 剪贴板降级为 V-AUX 不计 leg, 改用 L3d 作为真第 3 条腿) | F1 Gemini / GAS / Grok | 3h | W4-T3-alt |
| A16 | dedup 实战验证 (W3 supervisor live) | P1 Claude Desktop | 1h | W4-T4 |
| A17 | **L3d CDP 对真实 Windsurf** (Windsurf 是 Electron, 走 `--remote-debugging-port`; 原 W4-T5 L4a 剪贴板降级为 V-AUX) | F5 P4 | 1h | W4-T5-alt |
| A18 | L1 对真实 claude-code CLI (mitmproxy) | F6 P6 | 2h | W4-T6 |
| A19 | L1 对真实 Codex CLI (含 OpenAI host normalizer) | F6 P7 | 3h | W4-T7 |
| A20 | L1 对真实 Gemini CLI (含 Google GenAI host normalizer) | F6 P8 | 3h | W4-T8 |

合计 ~58h. Phase A 内全部完成.

### 5.B — Phase B Wave 6/7/8 (Tier-β 3 场景的结构性建设)

不能"再签一次字"达成的 — 必须新落子系统:

#### Wave 6 / Track B-1 — F5 P5 GitHub Copilot (~80h, ~2 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-1.1 | `pce_core/normalizer/github_copilot.py` (host JSON normalizer) | L1 | 24h |
| B-1.2 | `vscode_ext/copilot/` (VS Code 扩展) + migration 0016 + 安装文档 | L3c | 40h |
| B-1.3 | VS Code MCP server 接入 (`pce_mcp_proxy`) | L3f | 16h |

#### Wave 7 / Track B-2 — F4 P2 ChatGPT Desktop UIA (~140h, ~3-4 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-2.1 | RECON UIA tree on ChatGPT Desktop | leg 3 (L4b) | 16h |
| B-2.2 | `pce_core/uia_driver/` 落地 + migration 0017 | L4b | 60h |
| B-2.3 | 28 单测 + 隐私 redact + opt-in UI | L4b | 30h |
| B-2.4 | live handoff + dedup 与 L1 join | L4b | 16h |
| B-2.5 | ADR-022 起草 + Adopted | governance | 8h |
| B-2.6 | scenarios.yaml 集成 + supervisor /status | governance | 10h |

#### Wave 8 / Track B-3 — F5 P3 Cursor protobuf (~200h, ~4-6 周)

| # | 任务 | 目标 leg | 工时 |
|---|---|---|:---:|
| B-3.1 | RECON Cursor 9 host endpoint + protobuf framing 协议 | leg 1 (L1) | 24h |
| B-3.2 | `pce_core/normalizer/protobuf/` (通用 framing + decoder) | L1 | 60h |
| B-3.3 | `pce_core/normalizer/cursor_protobuf/` (specific schema) + migration 0018 | L1 | 40h |
| B-3.4 | live handoff Cursor L1 protobuf | L1 | 10h |
| B-3.5 | live handoff Cursor L3f MCP | L3f | 20h |
| B-3.6 | RECON Cursor Electron Fuse + L3b preload OR L4a 剪贴板 | L3b/L4a | 50h |
| B-3.7 | ADR-023 起草 + Adopted | governance | 6h |
| B-3.8 | scenarios.yaml 集成 + dedup canonicalization | governance | 10h |

### 5.C — Phase C Wave 9 (~16h)

13/13 全量 verify + ADR-022 / ADR-023 Adopted + git tag v1.1.6.

### 5.D — 推后期 (P5.D.2 / P6) — 显式不在 v1.1.6 范围

完整列表见 §4.2 + `SCOPE-LOCK-2026-05-15.md` §4. 简表:

| 类别 | 推到 |
|---|---|
| F1 Perplexity / Copilot MS / DeepSeek / S3 / SX | P5.D.2 / P6 |
| F2 SaaS-embedded | P6 (账号阻塞) |
| F3 浏览器扩展 AI | P6 |
| F4 其它桌面 (Poe/DeepSeek/Kimi/豆包/mac-DMG/Squirrel) | P6 |
| F5 IDE 其它 (Cline/Continue/Cody/Amazon Q/...) | P6 |
| F6 Aider | P5.D.2 |
| F9 Local Model | P5.D.2 |
| F10 Headless / Framework | P5.D.2 |
| F7/F8 OS-Integrated AI | 永久 T3 非目标 |

### 5.E 预估总工时 (P5.D.1 STRICT MODE 13/13)

| Phase | 工时 | wall-clock |
|---|---|:---:|
| Phase A (Tier-α 10 场景 + supervisor + dedup + nightly) | ~88h | 1 周 |
| Phase B Track B-1 (Copilot) | ~80h | ~2 周 (并行) |
| Phase B Track B-2 (P2 UIA) | ~140h | ~3-4 周 (并行) |
| Phase B Track B-3 (Cursor protobuf) | ~200h | ~4-6 周 (并行, longest pole) |
| Phase C (Wave 9 全量关闭) | ~16h | 2 工日 |
| **合计** | **~520h** | **~7-8 周 wall-clock** |

---

## 6. 不变式 & 不许做的事

1. **任何宣称"PCE 已覆盖 X 场景"的陈述 — 必须回本文件核对 V-GREEN 数。**
   PROJECT.md / UCS / ADR 的描述是"设计上的"，不替代本文件的 "验证上的"。

2. **V-HERMETIC → V-GREEN 升级只能通过 handoff 签字**。不许用"单测
   绿了、逻辑上肯定也能对真产品生效" 代替 live 证据。

3. **V-DEAD 不得被静悄悄翻案**。任何想重新激活 L3b preload / L3d CDP
   on MSIX / L2 Frida 的尝试必须新 ADR，显式 supersede ADR-018。

4. **冗余定义的"不相互依赖"不许松绑**。如果将来某人说"L1 + A2 算
   两条独立路线"，请回 §1 的判定规则：它们同走 Chromium 网络栈，
   算 1.5 条。

5. **本文件只记录 V-GREEN / V-PARTIAL / V-DEAD 的客观状态，不替项目
   决定优先级**。优先级决策在 `Docs/docs/decisions/*` 或 ADR。

---

## 7. 更新协议

### 7.1 什么时候更新

任何对以下事件之一负责的 commit 必须**同一 commit**更新本文件：

- 对真实产品新加一次 live handoff → 对应行升级 V-HERMETIC → V-GREEN
- 某路径被 ADR 判死 → 对应行改 V-DEAD
- 某路径拿到可行性证据但生产链路未写 → V-PARTIAL
- 新增场景或 Form 扩充 UCS 附录 A → 加表行

### 7.2 如何更新

每行必须携带：

- `Path` 列：L0/L1/L2/L3a-h/L4a-c 之一
- `依赖基底` 列：显式写出依赖的资源（网络栈 / 进程 / 文件系统 / UI 树 /
  协议中间件）
- `最新 Live 验证` 列：**指向一份具体 handoff 文件 + 行数或 PASS 数**
- `等级` 列：严格按 §1 阶梯
- 汇总 §4 的 V-GREEN 数字同步更新

不许只在设计文档（UCS / framework / matrix）更新"能抓"，不回本文件补
"验证到"。

### 7.3 审计 cadence

- **每次 P 阶段关门**（当前 P6 "Coverage Polish" 即将启动）→ 全量回扫本
  文件，确认所有 V-GREEN 证据指向的 handoff 未失效
- **任何 release tag 前** → 核对是否有退化（V-GREEN 的 handoff 指向的
  commit 是否还能重现）
- **季度一次**（至少）→ 检查 S3/SX 站点是否自然退化为 V-DOC-ONLY
- **每夜 02:00 UTC（P5.D.1 Wave 5 新增）** → `nightly-probe.yml`
  runs `tools/check_redundancy_targets.py` against
  `/api/v1/supervisor/status`. Any P0 scenario with
  `legs_active < redundancy_target` (Phase B suppressed) opens a
  `redundancy-degraded` issue via `tools/auto_issue_on_redundancy_degraded.py`.
  Same step renders `Docs/stability/REDUNDANCY-MATRIX.svg` with 13 cards
  and commits it back to the repo.

### 7.4 变更评审

本文件的**结构性变更**（新增/删除列、改 §1 评级规则、改 §6 不变式）
必须：

1. 新一条 ADR 记录 supersession
2. 项目所有者签字
3. 同 PR 更新 `PROJECT.md` §12 ADR 列表

---

## 8. 关联文档

- **上游规划（"设计上能抓"）：**
  - `Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`（UCS 蓝图）
  - `Docs/docs/engineering/adr/ADR-009-universal-capture-stack.md`
  - `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
  - `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
- **下游目标（"该验证到什么"）：**
  - `Docs/stability/SITE-TIER-MATRIX.md`（F1 站点分级）
  - `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`（F4/F5/F6 D-case 标准）
  - `Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md`（跨 lane 健康度契约）
- **证据源头（"验证到了什么"）：** 见 §2 baseline

---

**本文件结束。每行数字都可以被 §2 里某份 handoff / 测试报告 / ADR 核
对验证；任何"似乎没验证"但本表标 V-GREEN 的路径 — 回来开 issue，引
用本文件对应行要求补证。**
