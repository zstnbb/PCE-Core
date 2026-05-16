# W1 Live Evidence Snapshots — 2026-05-14 (STRICT MODE)

> 上承: `@Docs/stability/redundancy-sprint/01-wave1-live-evidence.md` §3
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.1
>
> 本目录存放 P5.D.1 Wave 1 live evidence 的 SQLite 快照. 每个任务
> (W1-T1 ~ W1-T11) 留 1 个 `.db` 文件作为不可变证据.

---

## 文件清单 (target)

| 文件 | 来自任务 | 描述 |
|---|---|---|
| `w1_t1.db` | W1-T1 | F4 P1 Claude Desktop / L3f MCP-proxy live |
| `w1_t2.db` | W1-T2 | F4 P1 Claude Desktop / L3f `.mcpb` posture A |
| `w1_t3.db` | W1-T3 | F6 P6 Claude Code CLI / L3h |
| `w1_t4.db` | W1-T4 | F1 Gemini Web / L1 |
| `w1_t5.db` | W1-T5 | F1 GAS / L1 |
| `w1_t6.db` | W1-T6 | F1 Grok Web / L1 |
| `w1_t7.db` | W1-T7 | F5 P4 Windsurf / L3f MCP live |
| `w1_t8.db` | W1-T8 | F6 P7 Codex CLI / L3h |
| `w1_t9.db` | W1-T9 | F6 P7 Codex CLI / L3g local persistence |
| `w1_t10.db` | W1-T10 | F6 P8 Gemini CLI / L3h |
| `w1_t11.db` | W1-T11 | F6 P8 Gemini CLI / L3g local persistence |

---

## 命名规范

- 文件命名: `w1_t<N>.db` (小写 + 下划线)
- 大小: 通常 < 5 MB (只含 task 期间产生的行); 超过 50MB 应该分析为何

---

## 隐私与脱敏

⚠️ **这些 .db 文件可能含 raw_captures.body_text_or_json 的 user prompt
内容**. 在 commit 前:

1. 确认 prompt 是固定的 "What is 2+2?" 等无敏感信息
2. 确认 redaction 已对 headers / api keys 生效
   (`pce_proxy/redact.py` redact_headers_json)
3. 确认无 cookie / authorization token 落库

提交时如果担心, 用 `pce_core.export.export_anonymized` 先过一遍.

---

## 为什么需要 SQLite snapshot

REDUNDANCY-AUDIT-MATRIX §1 V-GREEN 严格定义要求**可重现证据**. 单纯
handoff 文本叙述不够 — 任何后来 agent 可以打开这些 .db 验证:

```powershell
sqlite3 Docs/handoff/_evidence_W1_2026-05-14/w1_t1.db
> SELECT source_id, COUNT(*) FROM raw_captures WHERE source_id='mcp-proxy-default' GROUP BY source_id;
```

---

## gitignore 策略

- 默认: 这些 `.db` 文件**进 git** (作为证据)
- 例外: 单个文件 > 50 MB → 使用 git LFS 或分析为何过大
- 极端例外: 含敏感数据 → 用 `signed-manifest.json` 加签名后只 commit
  manifest, .db 文件本身放在团队私有存储

`signed-manifest.json` 格式 (如使用):

```json
{
  "task_id": "W1-T1",
  "db_sha256": "<sha>",
  "row_summary": {
    "raw_captures": <count>,
    "mcp_proxy_default_rows": <count>
  },
  "signed_at": "2026-05-14T...",
  "signed_by": "<operator>",
  "external_storage_url": "<optional s3 / nas path>"
}
```

---

## Cleanup policy

冲刺关闭后 6 个月, 这些证据可以归档到 `_archive/` 子目录 (压缩 +
manifest 保留, .db 移除). 在归档前必须确认 REDUNDANCY-AUDIT §3 引用
的 handoff 不再需要 .db 重现.
