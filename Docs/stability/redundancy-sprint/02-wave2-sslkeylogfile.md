# Wave 2 — A2 SSLKEYLOGFILE 生产链路 (Day 2, ~20h, 2026-05-15 重写)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1
> + `@Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
> §5.2 Phase 5
> + `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §1.0 (V-GREEN-C amendment)
> + `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §1.2.1 (L1+A2=2-leg amendment)
>
> **2026-05-15 重写理由**: owner 提出合规风险审视后, V-GREEN-C 等级被
> 引入, A2 SSLKEYLOGFILE 从"L1 的 corroboration"重定位为**完全独立
> 的 V-GREEN-clean 备用腿**, 用于替代 L1 MITM 的合规风险敞口. 架构
> 从 mitmproxy-addon 改为 **tshark wrap (Arch B)**, 工时 14.5h → ~20h.

---

## 1 · 目标 (2026-05-15 重写)

给所有 **Chromium-based 场景** (5 个 Web Tier-α + 2 个 Desktop AI app +
3 个 Electron IDE + Node CLI) 提供一条**完全独立于 mitmproxy** 的
**V-GREEN-clean** 网络捕获腿, 替代 L1 MITM 的合规风险敞口.

L1 MITM 不删除, 但降级为 V-GREEN-C (主路) + A2 SSLKEYLOGFILE 升级为
V-GREEN-clean (合规替代), 实现"双保险" — 任何场景 ≥2 V-GREEN-clean
leg, 不再单点依赖 L1.

---

## 2 · 设计契约 (2026-05-15 重写)

### 2.1 架构: Arch B — tshark wrap, 独立管线

```
日常 Chrome / Electron AI app
  │ 启动时读 SSLKEYLOGFILE env var
  │
  ├──→ 正常 TLS handshake ───→ 服务器 (服务器看到的是真 Chrome, 不可区分)
  │
  └─→ 顺手写 session keys 到 %LOCALAPPDATA%\pce\keylog.txt

pce_sslkeylog_daemon (新 module)
  │ spawn tshark -i any -f "host <allowlist>" -o tls.keylog_file:<path>
  │                                          -T json -j "http2 http"
  │
  ├──→ tshark 输出 NDJSON (每行一个 HTTP 请求/响应)
  │
  └─→ Python parser: tshark JSON → request/response pair → raw_captures
                                                          ↓
                                  source_id='sslkeylog-default' (新, migration 0015)
                                  pair_id = sha256(client_addr + stream_id + ts)
                                  独立于 mitmproxy 的 source_id='proxy-default'
```

### 2.2 关键点: A2 是独立 leg (V-GREEN-clean), 不是 L1 corroboration

**与原 Wave 2 计划的差别**:
- 原计划: A2 跟 L1 共享 `pair_id` (corroboration on existing row)
- 新计划: A2 **自己生成 pair_id**, 写**自己的** raw_captures 行

理由 (2026-05-15 §1.2.1 amendment): L1+A2 = 2 独立 leg, 不是 1.5.
8 种失败模式里 6 种相互独立 → A2 完全替代 L1 也行, 不应该被绑定.

### 2.3 服务端合规 / 封号风险敞口

A2 SSLKEYLOGFILE **没有** L1 MITM 的可观察 fingerprint 风险:

| 维度 | L1 MITM 的可探测度 | A2 SSLKEYLOGFILE 的可探测度 |
|---|:---:|:---:|
| TLS ClientHello fingerprint (JA3/JA4) | 🔴 Python 库 ≠ BoringSSL | 🟢 真 Chrome handshake |
| HTTP/2 帧顺序 | 🟡 mitm 重组 | 🟢 NIC 抓的就是原顺序 |
| ALPN extension order | 🟡 mitm 跟 Chrome 略有差 | 🟢 真 Chrome |
| Cipher suite order | 🟡 mitm 用 cryptography 默认 | 🟢 真 Chrome |
| 服务端能看到我们的存在吗 | 🔴 看到一个非典型 Chrome | 🟢 看不到, 我们不在请求路径 |

A2 是真正"零封号风险" 的 Chromium 网络栈捕获路径.

---

## 3 · 落地清单

## 3 · 落地清单 (2026-05-15 重写, Arch B tshark wrap)

| 文件 | 内容 | LOC | 工时 |
|---|---|:---:|:---:|
| `pce_sslkeylog/__init__.py` (新模块) | package marker + version | 5 | - |
| `pce_sslkeylog/tshark_wrap.py` (新) | `TsharkRunner`: spawn tshark subprocess with `SSLKEYLOGFILE` env + `-T json -j "http http2"`, stream NDJSON output, restart on crash | ~200 | 5h |
| `pce_sslkeylog/parser.py` (新) | tshark JSON event → `(host, path, direction, body, headers)` tuple; handles HTTP/2 frame reassembly, host filtering by ALLOWED_HOSTS | ~250 | 5h |
| `pce_sslkeylog/capture.py` (新) | 把 parser 输出转换为 `raw_captures` 行: pair_id from (stream_id + client_addr), `source_id='sslkeylog-default'`, dedup via `(host, path, body_hash)` 30s window | ~150 | 3h |
| `pce_sslkeylog/__main__.py` (新) | CLI entry: `python -m pce_sslkeylog --keylog <path> --interface any` | ~50 | 1h |
| `pce_core/db.py` (改) | 加 `SOURCE_SSLKEYLOG = "sslkeylog-default"` 常量 | ~5 | 0.5h |
| `pce_core/migrations/0015_sslkeylog_source.py` (新) | 注册 source_id 进 sources 表, 同 0014 模式 | ~80 | 0.5h |
| `pce_core/cert_wizard/keylog.py` (新) | 设置 / 取消 `SSLKEYLOGFILE` env var 到 `%LOCALAPPDATA%\pce\keylog.txt`; user-level setx | ~100 | 2h |
| `tests/test_sslkeylog_parser.py` (新) | ~15 单测: tshark JSON 解析 / HTTP/2 重组 / host 过滤 / pair_id 生成 / dedup | ~300 | 3h |
| `tests/test_sslkeylog_smoke.py` (新) | 1 个 live test 验证 tshark 装好 + spawn 成功 + 至少 1 行 capture 入库 | ~80 | 1h |
| `Docs/install/PCE_SSLKEYLOG_INSTALL.md` (新) | 一次性 setup 指南: 装 Wireshark, 设 env var, 重启 Chromium app | ~80 | 1h |
| **handoff** `HANDOFF-W2-A2-SSLKEYLOG-LIVE-2026-05-XX.md` | 真 Claude Desktop + 真 Chrome 各 1 session, 验 raw_captures.source_id='sslkeylog-default' 独立行存在 | — | 2h |

**总:** ~20h (从 14.5h 上调).

### 3.1 工时上调原因

原 14.5h 是 mitmproxy corroboration 路径 (Arch A). 新 ~20h 是独立 leg
路径 (Arch B). 多花 5.5h 换 V-GREEN-clean 独立 leg, ROI 高.

### 3.2 用户一次性 setup (合规无感, 跟设系统代理同级别)

1. 装 Wireshark (含 Npcap driver, 一次性, 跟装 mitmproxy CA 同级别 friction)
2. 在 onboarding 里点 "Enable SSLKEYLOGFILE" → PCE 自动:
   - `setx SSLKEYLOGFILE "%LOCALAPPDATA%\pce\keylog.txt"` (用户级环境变量)
   - 创建 keylog.txt 空文件
3. 重启 Chromium-based AI app 一次 (让它们读到新 env var)

之后**永远不用再动**. Chromium 会自动写 keys, tshark daemon 后台扫包,
解密, 入库.

---

## 4 · 关键设计点

### 4.1 NSS Key Log Format

参考: <https://firefox-source-docs.mozilla.org/security/nss/legacy/key_log_format/index.html>

格式 (每行):

```
<Label> <ClientRandom (hex)> <Secret (hex)>

Labels we care about:
- CLIENT_HANDSHAKE_TRAFFIC_SECRET
- SERVER_HANDSHAKE_TRAFFIC_SECRET
- CLIENT_TRAFFIC_SECRET_0
- SERVER_TRAFFIC_SECRET_0
- EXPORTER_SECRET
```

5 labels per session. ADR-018 §3 实测证据: "10 TLS 1.3 sessions × 5 labels".

### 4.2 client_random 作为 join key

mitmproxy flow 里能拿到 `flow.client_conn.tls_extensions` 里的
ClientRandom. 用它去 keylog dict 里查 secrets, 然后:

- 如果 secrets 全 5 条都齐 → `keylog_evidence.completeness = "full"`
- 如果只有 handshake 没 traffic → `"partial_handshake_only"`
- 如果完全找不到 → 不写 meta_json (说明 keylog 没启用或 race)

### 4.3 文件 rotation 处理

`SSLKEYLOGFILE` 是 Chromium 进程**追加写**, 永远不删. 需要:

- 启动时读全文件
- 用 `watchdog` 监听文件 size 增长 → 增量读尾部
- 内存中 `client_random → secrets` LRU 上限 1000 sessions, 防 OOM

### 4.4 隐私

keylog 内容是 **TLS pre-master secret**. 这是高度敏感数据:

- 默认存在 `%LOCALAPPDATA%\pce\keylog.txt` (user 私有目录, 别人读不到)
- redaction: keylog 不进 raw_captures.body (只 in-memory join), 落盘的
  meta_json.keylog 只存 `{completeness: "full", session_count: 1}`
  这种 metadata, 不存 secrets 本身
- 用户可主动清: `pce keylog clear`

---

## 5 · 测试矩阵 (16 单测)

| # | 名称 | 验证 |
|---|---|---|
| 1 | `test_parse_simple_keylog` | 单 session 5 labels 全齐 |
| 2 | `test_parse_multi_session` | 10 session 各 5 labels |
| 3 | `test_parse_partial_handshake` | 只有 handshake, 无 traffic |
| 4 | `test_parse_malformed_line` | 跳过格式错误行不崩 |
| 5 | `test_parse_empty_file` | 空文件返回空 dict |
| 6 | `test_rotation_append_only` | 文件追加新行, 增量读取 |
| 7 | `test_lru_cap_at_1000` | 第 1001 个 session 入, 第 1 个出 |
| 8 | `test_client_random_lookup_hit` | 给定 random 找到 secrets |
| 9 | `test_client_random_lookup_miss` | 找不到返回 None |
| 10 | `test_set_env_var_user_level` | 设置 user 级 env var |
| 11 | `test_set_env_var_idempotent` | 重复设置不报错 |
| 12 | `test_clear_env_var` | 清除 env var |
| 13 | `test_meta_json_serialization` | meta_json.keylog 不含 secrets |
| 14 | `test_completeness_full` | 5 labels 全 → "full" |
| 15 | `test_completeness_partial` | 只 2 labels → "partial_handshake_only" |
| 16 | `test_no_keylog_no_field` | keylog 不可用 → meta_json 无 keylog 键 |

---

## 6 · live test (`tests/test_real_smoke.py` 新增)

```python
def test_a2_keylog_live_corroboration():
    """真 Chromium 端发请求, 验 raw_captures.meta_json.keylog 存在.

    Manual setup:
    1. Set SSLKEYLOGFILE=%LOCALAPPDATA%\pce\keylog.txt
    2. Restart Chrome
    3. Run: pytest tests/test_real_smoke.py::test_a2_keylog_live_corroboration -v
       (skip in CI, run locally)
    """
    if not os.environ.get("PCE_E2E_LIVE"):
        pytest.skip("Live test, set PCE_E2E_LIVE=1 to run")
    # ... see test file
```

---

## 7 · handoff `HANDOFF-W2-A2-SSLKEYLOGFILE-LIVE-2026-05-14.md` 模板

```markdown
---
title: "W2 — A2 SSLKEYLOGFILE Production Path Live"
status: PASS
date: 2026-05-15
session: HH:MM-HH:MM
operator: <name>
predecessor: ADR-018 Phase 5 spec
artifacts:
  - commit <sha for keylog_mode.py + cert_wizard/keylog.py>
  - sqlite: Docs/handoff/_evidence_W2_2026-05-14/p1_a2.db, p2_a2.db
---

## TL;DR
SSLKEYLOGFILE production path delivered. Both P1 Claude Desktop and
P2 ChatGPT Desktop now write `raw_captures.meta_json.keylog =
{completeness: "full", session_count: N}` corroboration.

## Implementation
- pce_proxy/keylog_mode.py (NN LOC)
- pce_core/cert_wizard/keylog.py (NN LOC)
- 16 unit tests GREEN

## Live evidence — P1 Claude Desktop
<table: pair_id | host | meta_json.keylog>

## Live evidence — P2 ChatGPT Desktop
<table>

## REDUNDANCY-AUDIT update
§3 P1 row #3 (A2 column): V-PARTIAL → V-GREEN (corroboration)
§3 P2 row #2 (A2 column): V-PARTIAL → V-GREEN (corroboration)

Note: per §1.3 isolation rules, A2 still counts as 0.5 leg
(shares Chromium net stack with L1). Does NOT lift P1/P2 to ≥3
on its own — but provides forensic-grade evidence channel.
```

---

## 8 · 验收门

```
[ ] 16 单测 GREEN
[ ] 1 live test GREEN (CI 跳过, 本地手测)
[ ] handoff 显示 P1 Claude Desktop + P2 ChatGPT Desktop 各
    ≥ 1 个 raw_captures 行 meta_json 含非空 keylog 块
[ ] REDUNDANCY-AUDIT §3 对应行 A2 列从 V-PARTIAL → V-GREEN
    (注: 因 §1.3 规则, 仅作 corroboration 不计独立 leg)
[ ] CHANGELOG 记录 ADR-018 Phase 5 关闭
[ ] ADR-018 status: 标记 Phase 5 已 Closed
```

完成后进入 [`03-wave3-supervisor-v1.md`](03-wave3-supervisor-v1.md).
