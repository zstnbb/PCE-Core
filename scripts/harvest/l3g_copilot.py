# SPDX-License-Identifier: Apache-2.0
"""L3g reader for VS Code GitHub Copilot Chat's local session storage.

Copilot Chat persists every panel session as a single JSONL file under
``%APPDATA%/Code/User/globalStorage/emptyWindowChatSessions/<sessionId>.jsonl``.

The file is **not** straight JSON — it's an append-only operation log
(JSON-patch-style deltas) which must be replayed to reconstruct the final
session state. Each non-empty line is one operation:

  kind=0  (init / snapshot)
    {"kind": 0, "v": <full session state object>}
    Replaces the entire state with ``v``. There's usually exactly one
    of these at the top of the file.

  kind=1  (replace at path)
    {"kind": 1, "k": [<json-path>], "v": <new value>}
    Sets ``state[k[0]][k[1]]...] = v``. Used for individual field updates
    like ``["customTitle"]`` or ``["requests", 1, "result"]``.

  kind=2  (append / insert at path)
    {"kind": 2, "k": [<json-path>], "v": <items>, "i": <optional index>}
    Appends ``v`` to the list at ``k``. If ``i`` is present, inserts at
    index ``i`` (and ``v`` may itself be a list).

After replay, the session state has this shape (Copilot Chat v0.31, May 2026)::

    {
      "version": 3,
      "creationDate": <ms>,
      "initialLocation": "panel" | "inline" | "editor",
      "responderUsername": "GitHub Copilot",
      "sessionId": "<uuid>",
      "hasPendingEdits": bool,
      "requests": [
        {
          "requestId": "request_<uuid>",
          "timestamp": <ms>,
          "agent": {"id": "setup.agent" | "github.copilot.chat", ...},
          "message": {
            "text": "<verbatim user prompt>",
            "parts": [
              {"range": {...}, "editorRange": {...},
               "text": "<chunk>", "kind": "text"},
              ...
            ]
          },
          "variableData": {"variables": [...]},
          "response": [
            {"kind": "thinking",  "value": "..."},     ← chain-of-thought
            {"kind": "markdown",  "value": "..."},     ← assistant text
            {"kind": "codeblock", "language": "py", "code": "..."},
            ...
          ],
          "result": {"timings": {...}, "metadata": {...}},
          "completionTokens": int,
          "elapsedMs": int,
          "followups": [...],
          "modelState": {...},
        },
        ...
      ],
      "customTitle": "<short title>",
      "inputState": {
        "selectedModel": {"identifier": "copilot/gpt-5-mini", ...},
        ...
      },
    }

Usage::

    from scripts.harvest.l3g_copilot import read_copilot_sessions

    for s in read_copilot_sessions():
        print(s.session_id, s.title, len(s.requests))
        for r in s.requests:
            print("  user:", r.user_text[:80])
            print("  asst:", r.assistant_text[:80])

CLI::

    python scripts/harvest/l3g_copilot.py                # summary
    python scripts/harvest/l3g_copilot.py --json         # JSON dump
    python scripts/harvest/l3g_copilot.py --since 1h     # only recently-touched
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("pce.harvest.l3g.copilot")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CopilotRequest:
    """One user-turn / assistant-reply pair in a Copilot Chat session."""

    request_id: str
    timestamp_ms: int
    agent_id: str
    model_identifier: Optional[str]
    user_text: str
    user_parts: list                 # editor-range tagged parts
    response_blocks: list            # [{kind: "thinking|markdown|codeblock", ...}]
    result_metadata: dict
    completion_tokens: Optional[int]
    elapsed_ms: Optional[int]
    followups: list
    raw: dict                        # full raw request JSON

    @property
    def assistant_text(self) -> str:
        """Best-effort plain-text concatenation of the assistant reply."""
        parts = []
        for b in self.response_blocks:
            if not isinstance(b, dict):
                continue
            kind = b.get("kind")
            if kind == "markdown":
                parts.append(b.get("value", ""))
            elif kind == "codeblock":
                lang = b.get("language", "")
                code = b.get("code", "")
                parts.append(f"```{lang}\n{code}\n```")
            elif kind == "thinking":
                # exclude chain-of-thought from the public reply
                continue
        return "\n".join(p for p in parts if p)


@dataclasses.dataclass
class CopilotSession:
    """One Copilot Chat panel session."""

    session_id: str
    title: Optional[str]              # customTitle
    initial_location: str             # "panel" | "inline" | "editor"
    creation_date_ms: int
    responder_username: str           # "GitHub Copilot"
    selected_model: Optional[str]     # "copilot/gpt-5-mini" etc.
    requests: list[CopilotRequest]
    raw_state: dict                   # full replayed state for forensics


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_copilot_sessions_dir() -> Path:
    """Where VS Code stores Copilot's panel chat sessions on Windows."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Code" / "User" / "globalStorage" / "emptyWindowChatSessions"


# ---------------------------------------------------------------------------
# JSONL delta replay
# ---------------------------------------------------------------------------


def _set_path(state: dict, path: list, value: Any) -> None:
    """state[k[0]][k[1]]...] = value, creating dicts/lists on the way."""
    if not path:
        # whole-state replacement is handled by the caller (kind=0)
        return
    cur: Any = state
    for k in path[:-1]:
        if isinstance(cur, list):
            try:
                cur = cur[int(k)]
            except (IndexError, ValueError):
                logger.debug("set_path: bad list index %r at %r", k, path)
                return
        elif isinstance(cur, dict):
            if k not in cur:
                cur[k] = {}
            cur = cur[k]
        else:
            return
    last = path[-1]
    if isinstance(cur, list):
        try:
            i = int(last)
            while len(cur) <= i:
                cur.append(None)
            cur[i] = value
        except ValueError:
            pass
    elif isinstance(cur, dict):
        cur[last] = value


def _append_path(state: dict, path: list, value: Any, index: Optional[int]) -> None:
    """Append (or insert at index) ``value`` to the list at ``path``."""
    if not path:
        return
    cur: Any = state
    for k in path:
        if isinstance(cur, list):
            try:
                cur = cur[int(k)]
            except (IndexError, ValueError):
                return
        elif isinstance(cur, dict):
            if k not in cur:
                cur[k] = []
            cur = cur[k]
        else:
            return
    if not isinstance(cur, list):
        return
    # Copilot's kind=2 ops sometimes pass a single dict, sometimes a list-of-dicts.
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    if index is None:
        cur.extend(items)
    else:
        for i, item in enumerate(items):
            cur.insert(index + i, item)


def replay_session(jsonl_path: Path) -> Optional[dict]:
    """Replay a Copilot session JSONL into its final state dict.

    Returns None if the file is empty or unreadable.
    """
    if not jsonl_path.is_file():
        return None
    text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    state: Optional[dict] = None
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            op = json.loads(line)
        except Exception as exc:
            logger.debug("%s line %d: parse fail %s", jsonl_path.name, i, exc)
            continue
        if not isinstance(op, dict):
            logger.debug("%s line %d: non-dict op (%s)", jsonl_path.name, i, type(op).__name__)
            continue
        kind = op.get("kind")
        if kind == 0:
            state = op.get("v", {}) if isinstance(op.get("v"), dict) else {}
        elif kind == 1 and isinstance(state, dict):
            k = op.get("k") or []
            v = op.get("v")
            _set_path(state, k, v)
        elif kind == 2 and isinstance(state, dict):
            k = op.get("k") or []
            v = op.get("v")
            idx = op.get("i")
            _append_path(state, k, v, idx)
        else:
            logger.debug("%s line %d: unknown op kind=%r", jsonl_path.name, i, kind)
    return state


# ---------------------------------------------------------------------------
# State → typed model
# ---------------------------------------------------------------------------


def _request_from_raw(req: dict) -> CopilotRequest:
    msg = req.get("message") or {}
    agent = req.get("agent") or {}
    # selectedModel is on the session, not the request — leave None here
    return CopilotRequest(
        request_id=req.get("requestId", ""),
        timestamp_ms=int(req.get("timestamp", 0)),
        agent_id=agent.get("id", "") if isinstance(agent, dict) else "",
        model_identifier=None,
        user_text=msg.get("text", "") if isinstance(msg, dict) else "",
        user_parts=msg.get("parts", []) if isinstance(msg, dict) else [],
        response_blocks=req.get("response", []) or [],
        result_metadata=req.get("result", {}) or {},
        completion_tokens=req.get("completionTokens"),
        elapsed_ms=req.get("elapsedMs"),
        followups=req.get("followups", []) or [],
        raw=req,
    )


def parse_session(state: dict, session_id_fallback: str = "") -> CopilotSession:
    input_state = state.get("inputState", {}) or {}
    selected_model = None
    sm = input_state.get("selectedModel")
    if isinstance(sm, dict):
        selected_model = sm.get("identifier")

    requests = [
        _request_from_raw(r)
        for r in state.get("requests", []) or []
        if isinstance(r, dict)
    ]
    # propagate model id onto each request
    for r in requests:
        if r.model_identifier is None:
            r.model_identifier = selected_model

    return CopilotSession(
        session_id=state.get("sessionId", session_id_fallback),
        title=state.get("customTitle"),
        initial_location=state.get("initialLocation", "panel"),
        creation_date_ms=int(state.get("creationDate", 0)),
        responder_username=state.get("responderUsername", "GitHub Copilot"),
        selected_model=selected_model,
        requests=requests,
        raw_state=state,
    )


# ---------------------------------------------------------------------------
# Top-level reader
# ---------------------------------------------------------------------------


def read_copilot_sessions(
    sessions_dir: Optional[Path] = None,
    since_unix: Optional[float] = None,
) -> list[CopilotSession]:
    """Read every Copilot Chat session JSONL and return parsed sessions.

    Args:
        sessions_dir: Where the .jsonl files live. Defaults to the
            Windows VS Code path.
        since_unix: Only include sessions whose file mtime >= this epoch.

    Returns:
        list of :class:`CopilotSession`, newest first.
    """
    sessions_dir = sessions_dir or default_copilot_sessions_dir()
    if not sessions_dir.is_dir():
        return []
    out: list[CopilotSession] = []
    for f in sessions_dir.iterdir():
        if f.suffix not in (".jsonl", ".json"):
            continue
        if since_unix is not None and f.stat().st_mtime < since_unix:
            continue
        state = replay_session(f)
        if not state:
            continue
        session = parse_session(state, session_id_fallback=f.stem)
        out.append(session)
    out.sort(key=lambda s: -s.creation_date_ms)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    if s[-1] in "smhd":
        n = float(s[:-1])
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[s[-1]]
        return time.time() - n * mult
    from datetime import datetime
    return datetime.fromisoformat(s.rstrip("Z")).timestamp()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sessions-dir", type=Path, default=None)
    p.add_argument("--since", type=str, default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-raw", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    since = _parse_since(args.since) if args.since else None
    sessions = read_copilot_sessions(args.sessions_dir, since)[: args.limit]

    if args.json:
        out = []
        for s in sessions:
            out.append({
                "session_id": s.session_id,
                "title": s.title,
                "creation_date_ms": s.creation_date_ms,
                "initial_location": s.initial_location,
                "selected_model": s.selected_model,
                "n_requests": len(s.requests),
                "requests": [
                    {
                        "request_id": r.request_id,
                        "timestamp_ms": r.timestamp_ms,
                        "agent_id": r.agent_id,
                        "model_identifier": r.model_identifier,
                        "user_text": r.user_text,
                        "assistant_text": r.assistant_text,
                        "n_response_blocks": len(r.response_blocks),
                        "completion_tokens": r.completion_tokens,
                        "elapsed_ms": r.elapsed_ms,
                        "n_followups": len(r.followups),
                        **({"raw": r.raw} if args.include_raw else {}),
                    }
                    for r in s.requests
                ],
                **({"raw_state": s.raw_state} if args.include_raw else {}),
            })
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
        return 0

    print(f"Found {len(sessions)} Copilot session(s):")
    for s in sessions:
        print(f"\n  session {s.session_id}")
        print(f"    title:           {s.title!r}")
        print(f"    initialLocation: {s.initial_location}")
        print(f"    model:           {s.selected_model}")
        print(f"    requests:        {len(s.requests)}")
        for i, r in enumerate(s.requests):
            user = (r.user_text or "").replace("\n", " ")[:80]
            asst = r.assistant_text.replace("\n", " ")[:80]
            print(f"      [{i:>2}] user:      {user!r}")
            print(f"           assistant: {asst!r}  ({r.completion_tokens}t, {r.elapsed_ms}ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
