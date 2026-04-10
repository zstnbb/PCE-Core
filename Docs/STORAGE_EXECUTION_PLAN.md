# PCE 存储层强化执行计划

> 基准：2026-04-10 | 采集端已锁定（30/33 E2E pass），进入"存"的强化阶段

---

## 0. 当前状态评估

### 已完成的数据流

```
浏览器扩展                         PCE Core
┌──────────────┐                ┌──────────────────────────────────────────┐
│ DOM提取       │──conversation──▶│                                          │
│ (generic.js   │                │  /api/v1/captures                        │
│  chatgpt.js   │                │       │                                  │
│  deepseek.js) │                │       ▼                                  │
│              │                │  raw_captures (L0, 只追加)                │
│ 网络拦截      │──network_     │       │                                  │
│ (network_     │  intercept───▶│       ▼                                  │
│  interceptor) │                │  Normalizer Pipeline                     │
└──────────────┘                │  ├─ OpenAIChatNormalizer                  │
                                │  ├─ AnthropicMessagesNormalizer           │
                                │  └─ ConversationNormalizer (catch-all)    │
                                │       │                                  │
                                │       ▼                                  │
                                │  sessions + messages (L1, 结构化)         │
                                │       │                                  │
                                │       ▼                                  │
                                │  Query API → Dashboard                   │
                                └──────────────────────────────────────────┘
```

### 已有的表结构

| 表 | 角色 | 记录规模 |
|----|------|---------|
| `raw_captures` | L0 不可变事实层 | 高频，每次交互产生多条 |
| `sessions` | L1 会话索引 | 按对话粒度，1 session = 1 chat thread |
| `messages` | L1 消息序列 | 每条 user/assistant 发言 |
| `sources` | 来源注册 | proxy / browser_ext / mcp |
| `custom_domains` | 动态域名白名单 | 用户自定义 |

### 已有的 API

| 端点 | 功能 |
|------|------|
| `POST /api/v1/captures` | 写入 + 自动归一化 |
| `GET /api/v1/captures` | 原始捕获查询 |
| `GET /api/v1/sessions` | 会话列表（支持 provider 过滤） |
| `GET /api/v1/sessions/{id}/messages` | 会话消息 |
| `GET /api/v1/stats` | 总体统计 |
| `GET /api/v1/capture-health` | 通道健康度 |

### 已知不足

1. **L1 质量不均** — DeepSeek 产 9 条消息（含重复），有些 provider 的 content_text 残留 raw JSON
2. **无全文搜索** — 用户无法搜索历史对话
3. **Session 不合并** — 同一对话跨页面刷新可能产生多个 session
4. **无标签/分类** — session 只有 provider + title_hint，无 topic/language/intent 标签
5. **无导出能力** — 数据只能通过 API 查看，不能导出为 markdown/json
6. **无数据生命周期管理** — raw_captures 无限增长，无归档/压缩策略

---

## 1. 执行路线总览

```
Phase 1  L1 质量审计与修复          ← 3 天    ← 【先做这个】
Phase 2  全文搜索                   ← 2 天
Phase 3  Session 智能管理           ← 3 天
Phase 4  数据导出 + 生命周期        ← 2 天
Phase 5  L2 知识抽取（探索性）       ← 5 天
                                    ────────
                                    总计 ~15 天
```

---

## Phase 1：L1 质量审计与修复（3 天）

> 目标：确保 messages 表中的数据在所有 11 个 provider 上都干净、完整、可用。

### 1.1 编写诊断脚本 `tools/audit_l1_quality.py`

**产出物**：一个可重复执行的审计脚本，输出每个 provider 的数据质量报告。

**检查维度**：

| # | 检查项 | SQL/逻辑 | 严重级 |
|---|--------|----------|--------|
| Q1 | content_text 残留 raw JSON | `content_text LIKE '{%' AND content_text LIKE '%parts%'` | HIGH |
| Q2 | content_text 为空或过短 | `LENGTH(content_text) < 5` | HIGH |
| Q3 | 缺少 user 角色消息 | session 中无 role='user' | HIGH |
| Q4 | 缺少 assistant 角色消息 | session 中无 role='assistant' | HIGH |
| Q5 | 消息重复（同 session 内） | `_message_hash` 冲突计数 | MEDIUM |
| Q6 | session 无 title_hint | `title_hint IS NULL OR title_hint = ''` | LOW |
| Q7 | session 无 session_key | `session_key IS NULL` | LOW |
| Q8 | content_json 应有但缺失 | content_text 含 `[Image]` 等标记但 content_json 为空 | MEDIUM |
| Q9 | token_estimate 缺失 | `token_estimate IS NULL` | LOW |
| Q10 | 孤儿 session（0 消息） | `message_count = 0` | MEDIUM |

**输出格式**：

```
=== PCE L1 Quality Audit ===

Provider: openai (chatgpt)
  Sessions: 15 | Messages: 47
  Q1 raw JSON residue:  0 ✓
  Q2 empty content:     0 ✓
  Q3 no user msg:       0 ✓
  Q4 no assistant msg:  0 ✓
  Q5 duplicates:        2 ⚠  (session abc123: 2 dupes)
  ...
  Overall: 95% clean

Provider: moonshot (kimi)
  Sessions: 3 | Messages: 6
  Q1 raw JSON residue:  0 ✓
  ...
```

**文件位置**：`tools/audit_l1_quality.py`

### 1.2 修复发现的问题

根据审计结果，逐 provider 修复。常见修复模式：

- **raw JSON 残留** → 增强 `_clean_content()` 在 `conversation.py` 中的模式匹配
- **缺少 user 消息** → 检查对应 content script 的 DOM 选择器（类似 Kimi 的修复）
- **消息重复** → 增强 `_message_hash()` 或 `_normalize_message_text_for_dedup()`
- **token 缺失** → 添加 `_estimate_tokens()` 函数（简单的 word count × 1.3）

### 1.3 添加回归测试

- 为每种发现的 bad case 添加 unit test 到 `tests/test_normalizer_quality.py`
- 确保修复不会引入新问题

### 1.4 编写一次性修复迁移脚本

对已入库的脏数据，编写 `tools/fix_l1_data.py` 做一次性清洗：
- 重新跑 normalizer 对有问题的 session 进行重建
- 清理空 session
- 补充缺失的 token_estimate

### 验收标准

- [ ] 审计脚本 100% 覆盖所有 11 个 provider
- [ ] 所有 HIGH 级问题修复，MEDIUM 级 > 90% 修复
- [ ] 回归测试全绿
- [ ] 修复迁移脚本可安全重复执行

---

## Phase 2：全文搜索（2 天）

> 目标：用户能搜索自己所有 AI 对话的历史内容。

### 2.1 SQLite FTS5 虚拟表

```sql
-- Schema addition in db.py
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content_text,
    content='messages',
    content_rowid='rowid',
    tokenize='unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content_text)
    VALUES (new.rowid, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content_text)
    VALUES ('delete', old.rowid, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content_text)
    VALUES ('delete', old.rowid, old.content_text);
    INSERT INTO messages_fts(rowid, content_text)
    VALUES (new.rowid, new.content_text);
END;
```

### 2.2 搜索 API

```python
# 新端点：GET /api/v1/search?q=xxx&provider=openai&limit=20

@app.get("/api/v1/search")
def search_messages(
    q: str = Query(..., min_length=2),
    provider: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
):
    """Full-text search across all message content."""
    # Returns: list of {message, session, highlight_snippet}
```

**db.py 新增函数**：

```python
def search_messages(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """FTS5 search on messages, returns messages + parent session info."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT m.*, s.provider, s.title_hint, s.started_at as session_started,
                   snippet(messages_fts, 0, '<mark>', '</mark>', '...', 32) as snippet
            FROM messages_fts
            JOIN messages m ON m.rowid = messages_fts.rowid
            JOIN sessions s ON m.session_id = s.id
            WHERE messages_fts MATCH ?
        """
        params = [query]
        if provider:
            sql += " AND s.provider = ?"
            params.append(provider)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

### 2.3 FTS 数据回填

已有数据需要一次性灌入 FTS 索引：

```python
# tools/rebuild_fts.py
def rebuild_fts():
    conn = get_connection()
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    conn.commit()
```

### 2.4 Dashboard 搜索 UI

在 `dashboard/app.js` 中添加搜索框组件，调用 `/api/v1/search`，展示高亮结果。

### 验收标准

- [ ] `GET /api/v1/search?q=hello` 能在 < 50ms 内返回结果
- [ ] 搜索结果包含 snippet 高亮和所属 session 信息
- [ ] Dashboard 搜索框可用
- [ ] 新消息自动进入 FTS 索引（trigger 机制）

---

## Phase 3：Session 智能管理（3 天）

> 目标：Session 粒度合理、元数据丰富、查询维度多样。

### 3.1 Session 合并

**问题**：同一对话页面刷新后，DOM 提取产生新 capture → `session_hint` 相同但 `pair_id` 不同 → 可能创建新 session。

**当前机制**：`_find_existing_session()` 通过 `session_key` 查找已有 session，存在则复用。这一机制已基本工作，但有边缘 case：
- `session_key` 为 None 时每次创建新 session
- 不同 direction (conversation vs network_intercept) 可能产出不同 session_key

**修复方案**：

```python
# pipeline.py — 增强 session_key 提取
def _derive_session_key(capture_row: dict, result: NormalizedResult) -> Optional[str]:
    """多层回退的 session_key 推导：
    1. result.session_key（normalizer 产出）
    2. capture_row.session_hint（浏览器扩展 URL 中提取的 conversation ID）
    3. 基于 provider + title_hint 的模糊匹配（时间窗口内）
    """
```

### 3.2 Session 元数据增强

给 sessions 表添加新字段（通过 ALTER TABLE 迁移）：

```sql
ALTER TABLE sessions ADD COLUMN language TEXT;        -- 'zh', 'en', 'mixed'
ALTER TABLE sessions ADD COLUMN topic_tags TEXT;       -- JSON array: ["coding", "math"]
ALTER TABLE sessions ADD COLUMN total_tokens INTEGER;  -- sum of message token_estimates
ALTER TABLE sessions ADD COLUMN model_names TEXT;       -- JSON array of distinct models used
```

**自动标注逻辑** — `pce_core/tagger.py`（新文件）：

```python
def auto_tag_session(session_id: str):
    """基于消息内容自动标注 session 的 language 和 topic_tags。"""
    messages = query_messages(session_id)

    # Language detection (简单规则，不依赖外部库)
    language = _detect_language(messages)

    # Topic tagging (关键词匹配，轻量级)
    topics = _extract_topics(messages)

    # Token sum
    total_tokens = sum(m.get('token_estimate', 0) or 0 for m in messages)

    # Model names
    model_names = list(set(m['model_name'] for m in messages if m.get('model_name')))
```

**language 检测规则**（无需外部依赖）：

```python
def _detect_language(messages: list[dict]) -> str:
    all_text = ' '.join(m.get('content_text', '') or '' for m in messages)
    # Count CJK characters vs Latin
    cjk = sum(1 for c in all_text if '\u4e00' <= c <= '\u9fff')
    latin = sum(1 for c in all_text if c.isascii() and c.isalpha())
    if cjk > latin * 0.3:
        return 'zh' if cjk > latin else 'mixed'
    return 'en'
```

**topic 提取规则**（关键词映射）：

```python
_TOPIC_KEYWORDS = {
    'coding': ['code', 'function', 'class', 'bug', 'error', 'python', 'javascript', '代码', '编程', '函数'],
    'writing': ['write', 'essay', 'article', 'blog', '写作', '文章', '论文'],
    'math': ['equation', 'calculate', 'formula', 'proof', '计算', '公式', '数学'],
    'analysis': ['analyze', 'data', 'chart', 'trend', '分析', '数据', '趋势'],
    'translation': ['translate', 'translation', '翻译', '英译中', '中译英'],
    'creative': ['story', 'poem', 'creative', '故事', '诗', '创作'],
    'qa': ['what is', 'how to', 'explain', 'why', '什么是', '怎么', '为什么'],
}
```

### 3.3 Session 查询增强

扩展 `GET /api/v1/sessions` 支持更多过滤维度：

```python
@app.get("/api/v1/sessions")
def list_sessions(
    last: int = Query(20, ge=1, le=500),
    provider: Optional[str] = None,
    language: Optional[str] = None,        # NEW
    topic: Optional[str] = None,           # NEW
    since: Optional[float] = None,         # NEW: timestamp
    until: Optional[float] = None,         # NEW: timestamp
    min_messages: Optional[int] = None,    # NEW: filter out noise sessions
    q: Optional[str] = None,              # NEW: title search
):
```

### 验收标准

- [ ] 同一对话的 DOM 重复提取不会创建新 session
- [ ] 每个 session 有 language / topic_tags / total_tokens
- [ ] API 支持多维度过滤查询
- [ ] 自动标注脚本可对历史数据批量运行

---

## Phase 4：数据导出 + 生命周期（2 天）

> 目标：用户能导出数据，系统能管理数据增长。

### 4.1 导出 API

```python
# GET /api/v1/export/session/{session_id}?format=markdown
# GET /api/v1/export/sessions?provider=openai&since=xxx&format=jsonl

@app.get("/api/v1/export/session/{session_id}")
def export_session(session_id: str, format: str = "markdown"):
    """Export a single session as markdown or JSON."""
```

**Markdown 格式**：

```markdown
# ChatGPT: How to use Python decorators
> Provider: openai | Model: gpt-4o | Date: 2026-04-10 | Messages: 5

## User
How do I use Python decorators?

## Assistant
Python decorators are functions that modify...

## User
Can you show me an example with arguments?

## Assistant
Sure! Here's an example...
```

**JSONL 格式**（每行一个 message，方便大数据处理）：

```jsonl
{"session_id":"abc","role":"user","content":"How do I...","ts":1775829459}
{"session_id":"abc","role":"assistant","content":"Python decorators...","ts":1775829470}
```

### 4.2 批量导出

```python
# GET /api/v1/export/all?format=jsonl&since=xxx
# → 流式返回所有 session 和 messages

@app.get("/api/v1/export/all")
async def export_all(format: str = "jsonl", since: Optional[float] = None):
    """Stream all sessions and messages as JSONL."""
    # Use StreamingResponse for large datasets
```

### 4.3 数据生命周期管理

**raw_captures 归档策略**：

```python
# tools/archive_old_captures.py
def archive_captures(older_than_days: int = 90):
    """将超过 N 天的 raw_captures 导出到 gzip 文件并从主库删除。
    L1 (sessions/messages) 保留不动。
    """
    cutoff = time.time() - older_than_days * 86400
    # 1. Export to archive file
    # 2. Verify archive integrity
    # 3. DELETE FROM raw_captures WHERE created_at < cutoff
    # 4. VACUUM
```

**存储空间监控** — 在 `/api/v1/stats` 中添加：

```python
{
    "storage": {
        "db_size_mb": 45.2,
        "raw_captures_count": 12340,
        "sessions_count": 156,
        "messages_count": 2890,
        "oldest_capture_days": 45
    }
}
```

### 验收标准

- [ ] 单 session 可导出为 markdown / json
- [ ] 批量导出为 JSONL 流式输出
- [ ] 归档脚本可安全执行（导出 → 验证 → 删除 → VACUUM）
- [ ] Dashboard 显示存储使用量

---

## Phase 5：L2 知识抽取（探索性，5 天）

> 目标：从对话历史中提取结构化知识，为 PCE 连续性模型奠基。

### 5.1 知识单元定义

```sql
CREATE TABLE IF NOT EXISTS knowledge_units (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    source_session_id TEXT NOT NULL,
    source_message_id TEXT,
    category        TEXT NOT NULL,   -- 'fact', 'preference', 'skill', 'decision', 'goal'
    content         TEXT NOT NULL,   -- 知识内容（自然语言）
    confidence      REAL DEFAULT 0.5,
    tags            TEXT,            -- JSON array
    last_confirmed  REAL,            -- 最近被验证的时间
    contradicted_by TEXT,            -- 如果被推翻，指向新的 knowledge_unit id
    FOREIGN KEY (source_session_id) REFERENCES sessions(id)
);
```

### 5.2 知识类别

| 类别 | 示例 | 提取方法 |
|------|------|---------|
| **fact** | "用户是前端开发者" | 从 user 消息中的自我描述提取 |
| **preference** | "偏好 Python 而非 Java" | 从反复使用的技术栈推断 |
| **skill** | "擅长 React + TypeScript" | 从问题类型和深度推断 |
| **decision** | "项目选用 SQLite 而非 PostgreSQL" | 从决策讨论中提取 |
| **goal** | "在做一个 AI 对话记录工具" | 从反复出现的主题推断 |

### 5.3 提取策略

**Phase 5a：基于规则的轻量提取**（不依赖外部 LLM）

```python
# pce_core/knowledge/extractor.py

class RuleBasedExtractor:
    """从对话文本中用正则和模式匹配提取知识单元。"""

    def extract_from_session(self, session_id: str) -> list[KnowledgeUnit]:
        messages = query_messages(session_id)
        units = []
        units.extend(self._extract_tech_preferences(messages))
        units.extend(self._extract_self_descriptions(messages))
        units.extend(self._extract_project_context(messages))
        return units
```

**Phase 5b：LLM 辅助提取**（可选，需要用户配置 API key）

```python
class LLMExtractor:
    """用 LLM 从对话中提取结构化知识。"""

    PROMPT = """从以下对话中提取关于用户的关键信息。
    输出格式：JSON array of {category, content, confidence}
    类别：fact / preference / skill / decision / goal
    对话：
    {conversation}"""
```

### 5.4 知识查询 API

```python
@app.get("/api/v1/knowledge")
def list_knowledge(
    category: Optional[str] = None,
    tag: Optional[str] = None,
    min_confidence: float = 0.5,
    limit: int = 50,
):
    """List extracted knowledge units."""

@app.get("/api/v1/knowledge/profile")
def user_profile():
    """Aggregate knowledge into a user profile summary."""
    # Returns: {tech_stack, languages, interests, active_projects, ...}
```

### 验收标准

- [ ] knowledge_units 表建立
- [ ] 规则提取器能从现有数据中提取 ≥ 10 个有意义的知识单元
- [ ] 知识查询 API 可用
- [ ] （可选）LLM 提取器可配置

---

## 文件修改清单

### Phase 1
| 文件 | 操作 | 说明 |
|------|------|------|
| `tools/audit_l1_quality.py` | **新建** | L1 数据质量审计脚本 |
| `tools/fix_l1_data.py` | **新建** | 一次性数据清洗迁移 |
| `pce_core/normalizer/conversation.py` | 修改 | 增强 `_clean_content()` |
| `pce_core/normalizer/pipeline.py` | 修改 | 增强 `_message_hash()` + token 估算 |
| `tests/test_normalizer_quality.py` | **新建** | 质量回归测试 |

### Phase 2
| 文件 | 操作 | 说明 |
|------|------|------|
| `pce_core/db.py` | 修改 | FTS5 schema + triggers + `search_messages()` |
| `pce_core/server.py` | 修改 | `GET /api/v1/search` 端点 |
| `pce_core/models.py` | 修改 | `SearchResult` Pydantic model |
| `pce_core/dashboard/app.js` | 修改 | 搜索 UI 组件 |
| `tools/rebuild_fts.py` | **新建** | FTS 索引回填 |

### Phase 3
| 文件 | 操作 | 说明 |
|------|------|------|
| `pce_core/db.py` | 修改 | sessions 新字段迁移 + 查询增强 |
| `pce_core/tagger.py` | **新建** | 自动标注（language / topic） |
| `pce_core/normalizer/pipeline.py` | 修改 | session_key 推导增强 |
| `pce_core/server.py` | 修改 | sessions 查询新参数 |
| `tools/backfill_tags.py` | **新建** | 历史数据标注回填 |

### Phase 4
| 文件 | 操作 | 说明 |
|------|------|------|
| `pce_core/exporter.py` | **新建** | Markdown / JSONL 导出逻辑 |
| `pce_core/server.py` | 修改 | 导出 API 端点 |
| `tools/archive_old_captures.py` | **新建** | raw_captures 归档 |

### Phase 5
| 文件 | 操作 | 说明 |
|------|------|------|
| `pce_core/db.py` | 修改 | knowledge_units 表 |
| `pce_core/knowledge/extractor.py` | **新建** | 知识提取器 |
| `pce_core/knowledge/profile.py` | **新建** | 用户画像聚合 |
| `pce_core/server.py` | 修改 | 知识查询 API |

---

## 依赖关系

```
Phase 1 ──▶ Phase 2（FTS 依赖干净的 content_text）
Phase 1 ──▶ Phase 3（标注依赖正确的 messages）
Phase 1 ──▶ Phase 5（知识提取依赖高质量 L1）
Phase 2 ──╳ Phase 3（互相独立，可并行）
Phase 3 ──▶ Phase 5（知识提取使用 session tags 作为上下文）
Phase 4 ──╳ 其他（完全独立）
```

**推荐执行顺序**：`1 → 2 → 3 → 4 → 5`

---

## 风险与缓解

| 风险 | 级别 | 缓解 |
|------|------|------|
| FTS5 在大数据量下性能退化 | LOW | SQLite FTS5 百万级高效，且数据量有限 |
| Session 合并误判（不同对话合为一个） | MEDIUM | 保守策略：仅 session_key 完全匹配时合并 |
| 知识提取准确率低 | HIGH | Phase 5a 仅规则提取，设 confidence 阈值 |
| ALTER TABLE 迁移风险 | LOW | SQLite ALTER TABLE ADD COLUMN 安全，不重建表 |
| raw_captures 归档丢数据 | MEDIUM | 归档前验证 checksum，保留 30 天回滚窗口 |

---

## 下一步

**立即可以开始的是 Phase 1.1：编写 `tools/audit_l1_quality.py`**

这个脚本不修改任何数据、不改任何代码，只是读库生成报告。执行后根据报告决定后续修复优先级。
