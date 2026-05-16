# Wave 6 — GitHub Copilot 3-leg Build (Phase B Track-1, ~80h, ~2 周)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.2
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.2
> + `@Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.5 (P5 详情)
>
> **目标**: F5 P5 GitHub Copilot 0 → 3 V-GREEN. Phase B 三 track 中
> 最短 pole, ~2 周 wall-clock 单人. 与 Wave 7 / Wave 8 完全并行.
>
> **Track owner**: TBD (单人专职; 不可与 Wave 7/8 共用人)

---

## 1 · 现状

`REDUNDANCY-AUDIT-MATRIX.md` §3.5:

| # | Path | 当前 | 缺口 |
|---|---|---|---|
| 1 | L1 MITM (`api.githubcopilot.com`) | V-HERMETIC (allowlist 已加, 无 live + 无 normalizer) | normalizer + live |
| 2 | L3c VS Code 扩展 | V-DOC-ONLY (代码未实施) | 全实施 |
| 3 | L3f MCP 或 L4a 剪贴板 | 未配置 | 实施 |

**0 V-GREEN, 缺 3 条腿全部从 0 起.**

---

## 2 · 三条腿落地

### 2.1 Leg 1: L1 host JSON normalizer (~3 天)

`api.githubcopilot.com` 走 HTTPS REST (非 protobuf, 不像 Cursor).
现有 `pce_proxy/` allowlist 已含, 缺 normalizer.

| 文件 | 内容 | LOC |
|---|---|:---:|
| `pce_core/normalizer/github_copilot.py` (新) | 解析 `/v1/chat/completions` 风格请求/响应 → messages | ~200 |
| `tests/test_normalizer_github_copilot.py` (新) | 12 单测: streaming SSE / function-calling / inline completion / model_name 提取 | ~250 |
| `pce_core/normalizer/__init__.py` (改) | 注册新 normalizer + host pattern | ~5 |

handoff: `HANDOFF-W6-T1-COPILOT-L1-LIVE-2026-05-19.md` —
真 VS Code Copilot session 经 mitmproxy → ≥ 1 messages 行 +
model_name 非空.

**工时 24h** (含 endpoint RECON 4h + normalizer 14h + 12 单测 4h + live 2h).

### 2.2 Leg 2: L3c VS Code Extension (~1 周)

VS Code extension API 让我们旁挂一个 listener 在 Copilot 提示前后,
捕本地 prompt + completion. 与 L1 不同基底 (extension host vs HTTPS).

| 文件 | 内容 | LOC |
|---|---|:---:|
| `vscode_ext/copilot/package.json` (新) | extension manifest, activate on `onLanguage:*` + Copilot extension API hook | ~50 |
| `vscode_ext/copilot/src/extension.ts` (新) | 注册 inline completion provider listener; intercept `vscode.lm.*` API | ~300 |
| `vscode_ext/copilot/src/pce_client.ts` (新) | POST 到 `pce_core` ingest API with `source_id='vscode-copilot-default'` | ~80 |
| `vscode_ext/copilot/src/redact.ts` (新) | 复用 OSS redact 规则 | ~50 |
| `vscode_ext/copilot/tests/extension.test.ts` (新) | 8 单测: vscode.lm hook / event order / disable on absent / redact | ~200 |
| `pce_core/migrations/0016_vscode_copilot_source.py` (新) | 注册 `source_id='vscode-copilot-default'` | ~60 |
| `pce_core/db.py` (改) | 加 `SOURCE_VSCODE_COPILOT` 常量 | ~3 |
| `Docs/install/PCE_VSCODE_COPILOT_INSTALL.md` (新) | 用户安装指南 | — |

handoff: `HANDOFF-W6-T2-COPILOT-L3C-LIVE-2026-05-19.md` —
真 VS Code 装 ext + 触发 1 inline completion → ≥ 1 raw_capture 行
`source_id='vscode-copilot-default'`.

**工时 40h** (含 RECON VS Code lm API 8h + extension 实现 16h + 测试 8h
+ 真机 live 4h + 文档 4h).

### 2.3 Leg 3: L3f MCP 或 L4a 剪贴板 (~2 天)

VS Code 1.103+ 起原生支持 MCP (即用户可在 settings.json 配 MCP servers).
我们让 `pce_mcp_proxy` 作为一条 MCP server 装到 VS Code, 捕 Copilot
chat tool calls.

**首选: L3f MCP** (与 L1/L3c basis 不同, 真独立)
**备选: L4a 剪贴板** (如果 Copilot chat 不暴露 MCP API)

| 文件 | 内容 | LOC |
|---|---|:---:|
| `Docs/install/PCE_VSCODE_MCP_INSTALL.md` (新) | settings.json 配置示例 | — |
| `tests/e2e_mcp/test_vscode_copilot_mcp_live.py` (新) | live test scaffold | ~100 |

handoff: `HANDOFF-W6-T3-COPILOT-MCP-LIVE-2026-05-19.md` —
真 VS Code 配 MCP + 触发 1 tool call → ≥ 1 raw_capture 行
`source_id='mcp-proxy-default'` + meta_json.subsystem=`vscode`.

**工时 16h** (含 VS Code MCP 接入 RECON 4h + live 4h + L4a fallback 4h
+ 文档 4h).

---

## 3 · scenarios.yaml 已含 (Wave 3 §3.2)

```yaml
- id: f5_p5_github_copilot
  legs:
    - source: L1_mitm
      independent_basis: copilot_https_api
    - source: L3c_vscode_ext
      independent_basis: vscode_extension_host
    - source: L3f_mcp_proxy
      independent_basis: mcp_jsonrpc
  redundancy_target: 3
```

如果 Leg 3 走 L4a 备选, 改成:

```yaml
    - source: L4a_clipboard
      independent_basis: user_action
```

---

## 4 · 测试矩阵

- L1 normalizer: 12 单测 (test_normalizer_github_copilot.py)
- L3c extension: 8 单测 (jest / vitest in vscode-test)
- L3f live: 1 e2e (handoff signed)

总: 20 新单测 + 3 handoff.

---

## 5 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W6-R1 | Copilot 协议带 obfuscation 不经 HTTPS REST | RECON 优先确认; fallback: 仅靠 L3c + L4a 凑 2 条, 第 3 条用 L3f, 4 周变 5 周 |
| W6-R2 | VS Code extension API 限制不能 hook lm.* | fallback: 用 onDidChangeTextDocument + diff inference (准确度低) |
| W6-R3 | VS Code Copilot 不接受外部 MCP server | fallback: L4a 剪贴板; meta_json.ai_signal_score 阈值调严 |
| W6-R4 | live test 需 GitHub Copilot 付费账号 | 提前 24h 让 owner 准备 |

---

## 6 · 验收门 (Wave 6 关闭)

```
[ ] HANDOFF-W6-T1-COPILOT-L1-LIVE PASS
[ ] HANDOFF-W6-T2-COPILOT-L3C-LIVE PASS
[ ] HANDOFF-W6-T3-COPILOT-MCP-LIVE (或 L4a) PASS
[ ] REDUNDANCY-AUDIT §3 P5 GitHub Copilot 3 行 V-GREEN
[ ] /api/v1/supervisor/scenario/f5_p5_github_copilot status=redundant
[ ] migration 0016 通过 + 20 新单测 GREEN
[ ] HANDOFF-W6-CLOSING-2026-05-19.md 写好 (可与 W7/W8 closing 合并)
```

完成后并入 [`09-wave9-final-closure.md`](09-wave9-final-closure.md) 全量关闭.
