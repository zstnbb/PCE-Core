# SPDX-License-Identifier: Apache-2.0
"""L3g reader for Gemini CLI local chat sessions.

Gemini CLI stores chat history as JSONL files at:
    ~/.gemini/tmp/<project>/chats/session-<timestamp>-<id>.jsonl

Each file contains:
- Line 1: session metadata {sessionId, projectHash, startTime, kind}
- Subsequent lines: messages or $set updates
  - User: {id, timestamp, type:"user", content:[{text:"..."}]}
  - Gemini: {id, timestamp, type:"gemini", content:"...", thoughts:[...],
             tokens:{input,output,cached,thoughts,tool,total}, model:"..."}
  - Update: {"$set":{lastUpdated:"..."}}

Usage:
    python scripts/harvest/l3g_gemini.py [--limit N] [--json]
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GeminiMessage:
    id: str
    timestamp: str
    role: str  # "user" or "assistant"
    content: str
    model: Optional[str] = None
    thoughts: list[dict] = field(default_factory=list)
    tokens: Optional[dict] = None


@dataclass
class GeminiSession:
    session_id: str
    project_hash: str
    start_time: str
    kind: str
    messages: list[GeminiMessage] = field(default_factory=list)
    model: Optional[str] = None
    file_path: Optional[str] = None


def _extract_user_text(content) -> str:
    """Extract text from user content (list of parts or string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content)


def read_session(jsonl_path: Path) -> Optional[GeminiSession]:
    """Parse a Gemini CLI JSONL session file."""
    session = None
    messages: list[GeminiMessage] = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip $set updates
            if "$set" in obj:
                continue

            # Session metadata (first line)
            if "sessionId" in obj and "kind" in obj:
                session = GeminiSession(
                    session_id=obj["sessionId"],
                    project_hash=obj.get("projectHash", ""),
                    start_time=obj.get("startTime", ""),
                    kind=obj.get("kind", "main"),
                    file_path=str(jsonl_path),
                )
                continue

            # User message
            if obj.get("type") == "user":
                text = _extract_user_text(obj.get("content", ""))
                if text:
                    messages.append(GeminiMessage(
                        id=obj.get("id", ""),
                        timestamp=obj.get("timestamp", ""),
                        role="user",
                        content=text,
                    ))
                continue

            # Gemini response
            if obj.get("type") == "gemini":
                content = obj.get("content", "")
                if isinstance(content, list):
                    content = _extract_user_text(content)
                model = obj.get("model")
                messages.append(GeminiMessage(
                    id=obj.get("id", ""),
                    timestamp=obj.get("timestamp", ""),
                    role="assistant",
                    content=content,
                    model=model,
                    thoughts=obj.get("thoughts", []),
                    tokens=obj.get("tokens"),
                ))
                if model and session:
                    session.model = model
                continue

    if session is None:
        return None

    session.messages = messages
    return session


def discover_sessions(gemini_dir: Optional[Path] = None) -> list[Path]:
    """Find all Gemini CLI JSONL session files."""
    if gemini_dir is None:
        gemini_dir = Path(os.path.expanduser("~/.gemini"))

    if not gemini_dir.exists():
        return []

    # Search in tmp/<project>/chats/ and also history/ directories
    sessions = []
    for chats_dir in gemini_dir.rglob("chats"):
        for jsonl in chats_dir.glob("session-*.jsonl"):
            if jsonl.stat().st_size > 0:
                sessions.append(jsonl)

    return sorted(sessions, key=lambda p: p.stat().st_mtime, reverse=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Read Gemini CLI local sessions")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gemini-dir", type=str, default=None)
    args = parser.parse_args()

    gemini_dir = Path(args.gemini_dir) if args.gemini_dir else None
    files = discover_sessions(gemini_dir)

    print(f"Found {len(files)} Gemini CLI session files", file=sys.stderr)

    for jsonl_path in files[:args.limit]:
        session = read_session(jsonl_path)
        if session is None:
            continue

        if args.json:
            print(json.dumps({
                "session_id": session.session_id,
                "model": session.model,
                "start_time": session.start_time,
                "messages": [
                    {"role": m.role, "content": m.content[:200], "model": m.model}
                    for m in session.messages
                ],
            }, ensure_ascii=False))
        else:
            print(f"\n  session {session.session_id}")
            print(f"    model: {session.model}")
            print(f"    start: {session.start_time}")
            print(f"    messages: {len(session.messages)}")
            for m in session.messages:
                text = m.content[:80].replace("\n", " ")
                print(f"      [{m.role:9s}] {text}")


if __name__ == "__main__":
    main()
