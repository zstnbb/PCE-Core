# SPDX-License-Identifier: Apache-2.0
"""PCE Session Auto-Tagger

Automatically tags sessions with language, topics, total_tokens, and model_names
based on message content analysis. No external dependencies required.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .db import get_connection, query_messages

logger = logging.getLogger("pce.tagger")

# ---------------------------------------------------------------------------
# Topic keyword mapping
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "coding": [
        "code", "function", "class", "bug", "error", "python", "javascript",
        "typescript", "rust", "java", "html", "css", "sql", "api", "debug",
        "compile", "runtime", "algorithm", "variable", "loop", "array",
        "代码", "编程", "函数", "变量", "调试", "报错",
    ],
    "writing": [
        "write", "essay", "article", "blog", "draft", "paragraph", "proofread",
        "grammar", "rewrite", "summarize", "summary",
        "写作", "文章", "论文", "摘要", "润色", "改写",
    ],
    "math": [
        "equation", "calculate", "formula", "proof", "integral", "derivative",
        "matrix", "probability", "statistics", "algebra",
        "计算", "公式", "数学", "方程", "概率", "统计",
    ],
    "analysis": [
        "analyze", "data", "chart", "trend", "insight", "compare", "evaluate",
        "metrics", "benchmark", "report",
        "分析", "数据", "趋势", "对比", "评估",
    ],
    "translation": [
        "translate", "translation", "interpreter",
        "翻译", "英译中", "中译英", "日译",
    ],
    "creative": [
        "story", "poem", "creative", "fiction", "novel", "character", "plot",
        "故事", "诗", "创作", "小说",
    ],
    "explanation": [
        "what is", "how to", "explain", "why does", "how does", "tell me about",
        "什么是", "怎么", "为什么", "解释", "介绍",
    ],
}


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def _detect_language(messages: list[dict]) -> str:
    """Detect dominant language from message content.

    Returns 'zh', 'en', 'ja', 'ko', or 'mixed'.
    """
    all_text = " ".join(m.get("content_text", "") or "" for m in messages)
    if not all_text.strip():
        return "en"

    cjk_zh = sum(1 for c in all_text if '\u4e00' <= c <= '\u9fff')
    cjk_ja = sum(1 for c in all_text if '\u3040' <= c <= '\u30ff')
    cjk_ko = sum(1 for c in all_text if '\uac00' <= c <= '\ud7af')
    latin = sum(1 for c in all_text if c.isascii() and c.isalpha())

    total_cjk = cjk_zh + cjk_ja + cjk_ko
    total = total_cjk + latin
    if total == 0:
        return "en"

    if total_cjk > total * 0.3:
        if cjk_ja > cjk_zh and cjk_ja > cjk_ko:
            return "ja"
        if cjk_ko > cjk_zh:
            return "ko"
        if total_cjk > total * 0.6:
            return "zh"
        return "mixed"
    return "en"


def _extract_topics(messages: list[dict]) -> list[str]:
    """Extract topic tags from message content using keyword matching."""
    all_text = " ".join(m.get("content_text", "") or "" for m in messages).lower()
    if not all_text.strip():
        return []

    topics = []
    for topic, keywords in _TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in all_text)
        if score >= 2:
            topics.append(topic)

    return sorted(topics)


# ---------------------------------------------------------------------------
# Main tagger
# ---------------------------------------------------------------------------

def tag_session(
    session_id: str,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Auto-tag a single session. Returns the computed tags."""
    messages = query_messages(session_id, db_path=db_path)
    if not messages:
        return {}

    language = _detect_language(messages)
    topics = _extract_topics(messages)
    total_tokens = sum(m.get("token_estimate", 0) or 0 for m in messages)
    model_names = sorted(set(
        m["model_name"] for m in messages if m.get("model_name")
    ))

    tags = {
        "language": language,
        "topic_tags": json.dumps(topics) if topics else None,
        "total_tokens": total_tokens,
        "model_names": json.dumps(model_names) if model_names else None,
    }

    # Write to DB
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE sessions
               SET language = ?, topic_tags = ?, total_tokens = ?, model_names = ?
               WHERE id = ?""",
            (tags["language"], tags["topic_tags"], tags["total_tokens"],
             tags["model_names"], session_id),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to tag session %s", session_id)
    finally:
        conn.close()

    return tags


def tag_all_sessions(
    *,
    force: bool = False,
    db_path: Optional[Path] = None,
) -> int:
    """Tag all sessions. If force=False, only tags sessions without language set."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if force:
            rows = conn.execute("SELECT id FROM sessions").fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE language IS NULL"
            ).fetchall()
    finally:
        conn.close()

    count = 0
    for row in rows:
        tag_session(row["id"], db_path=db_path)
        count += 1

    return count
