# SPDX-License-Identifier: Apache-2.0
"""L3g reader for OpenAI Codex CLI session storage.

Codex CLI persists every session as a JSONL file at
``~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``.

Line types:
  session_meta    — session ID, cwd, cli_version, git context
  response_item   — role=user|assistant, content blocks
  turn_context    — model, cwd, approval_policy, sandbox_policy
  event_msg       — user_message, token_count, agent_reasoning

Usage::

    from scripts.harvest.l3g_codex import read_codex_sessions
    for s in read_codex_sessions():
        print(s.session_id, s.model, len(s.turns))
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.harvest.l3g.codex")


@dataclasses.dataclass
class CodexTurn:
    role: str  # user | assistant
    content_text: str
    timestamp: Optional[str] = None


@dataclasses.dataclass
class CodexSession:
    session_id: str
    cwd: Optional[str]
    cli_version: Optional[str]
    model: Optional[str]
    git_repo: Optional[str]
    git_branch: Optional[str]
    originator: Optional[str]  # codex_cli_rs | codex_vscode
    turns: list[CodexTurn] = dataclasses.field(default_factory=list)
    source_path: Optional[Path] = None


def default_codex_sessions_dir() -> Path:
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    return home / ".codex" / "sessions"


def _extract_text(content: list) -> str:
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text = block["text"]
            # Skip environment_context XML blocks
            if text.strip().startswith("<environment_context>"):
                continue
            parts.append(text)
        elif "output" in block:
            parts.append(block["output"])
    return "\n".join(parts)


def parse_session(jsonl_path: Path) -> Optional[CodexSession]:
    """Parse a Codex session JSONL into a CodexSession."""
    if not jsonl_path.is_file():
        return None

    session_id = ""
    cwd = None
    cli_version = None
    model = None
    git_repo = None
    git_branch = None
    originator = None
    turns: list[CodexTurn] = []

    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            line_type = obj.get("type")
            payload = obj.get("payload", {})
            timestamp = obj.get("timestamp")

            if line_type == "session_meta":
                session_id = payload.get("id", "")
                cwd = payload.get("cwd")
                cli_version = payload.get("cli_version")
                originator = payload.get("originator")
                git = payload.get("git") or {}
                git_repo = git.get("repository_url")
                git_branch = git.get("branch")

            elif line_type == "turn_context":
                if payload.get("model"):
                    model = payload["model"]

            elif line_type == "response_item":
                role = payload.get("role")
                content = payload.get("content", [])
                if role in ("user", "assistant") and isinstance(content, list):
                    text = _extract_text(content)
                    if text:
                        turns.append(CodexTurn(
                            role=role,
                            content_text=text,
                            timestamp=timestamp,
                        ))

    if not session_id or not turns:
        return None

    return CodexSession(
        session_id=session_id,
        cwd=cwd,
        cli_version=cli_version,
        model=model,
        git_repo=git_repo,
        git_branch=git_branch,
        originator=originator,
        turns=turns,
        source_path=jsonl_path,
    )


def read_codex_sessions(
    sessions_dir: Optional[Path] = None,
    limit: Optional[int] = None,
) -> list[CodexSession]:
    """Read all Codex CLI sessions from local storage."""
    root = sessions_dir or default_codex_sessions_dir()
    if not root.is_dir():
        return []

    sessions = []
    for jsonl_file in sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        session = parse_session(jsonl_file)
        if session:
            sessions.append(session)
            if limit and len(sessions) >= limit:
                break

    return sessions


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sessions = read_codex_sessions(limit=args.limit)
    if args.json:
        out = [dataclasses.asdict(s) for s in sessions]
        for s in out:
            if s.get("source_path"):
                s["source_path"] = str(s["source_path"])
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Found {len(sessions)} Codex session(s):\n")
        for s in sessions:
            print(f"  session {s.session_id}")
            print(f"    model:   {s.model or '?'}")
            print(f"    cwd:     {s.cwd or '?'}")
            print(f"    version: {s.cli_version or '?'}")
            print(f"    origin:  {s.originator or '?'}")
            print(f"    turns:   {len(s.turns)}")
            for t in s.turns[:3]:
                print(f"      [{t.role:9s}] {repr(t.content_text[:60])}")
            if len(s.turns) > 3:
                print(f"      ... +{len(s.turns) - 3} more")
            print()
