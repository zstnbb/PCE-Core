"""PCE Core – Clipboard monitor for AI conversation capture.

Monitors the system clipboard for text that looks like AI conversations.
When detected, stores it as a raw capture with direction='clipboard'.

This is an experimental/optional feature.
"""

import hashlib
import json
import logging
import re
import threading
import time
from typing import Optional

from .config import DB_PATH
from .db import SOURCE_BROWSER_EXT, init_db, insert_capture, new_pair_id

logger = logging.getLogger("pce.clipboard")

# ---------------------------------------------------------------------------
# AI conversation heuristics
# ---------------------------------------------------------------------------

# Role markers that suggest AI conversation text
ROLE_PATTERNS = [
    re.compile(r"^(User|Human|You|Me|Q)\s*[:：]", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^(Assistant|AI|Bot|A|ChatGPT|Claude|Gemini|Copilot|GPT-4|GPT)\s*[:：]", re.MULTILINE | re.IGNORECASE),
]

# Patterns that strongly suggest AI-generated text
AI_CONTENT_PATTERNS = [
    re.compile(r"```\w*\n", re.MULTILINE),        # Fenced code blocks
    re.compile(r"^\d+\.\s+\*\*", re.MULTILINE),   # Numbered bold list items
    re.compile(r"^[-*]\s+\*\*", re.MULTILINE),     # Bullet bold list items
    re.compile(r"I('d| would) be happy to help", re.IGNORECASE),
    re.compile(r"Here('s| is) (a|an|the) (step|example|summary|breakdown)", re.IGNORECASE),
    re.compile(r"Let me (explain|help|break)", re.IGNORECASE),
]

# Minimum text length to consider
MIN_TEXT_LENGTH = 100
# Minimum role markers to consider as conversation
MIN_ROLE_MARKERS = 2


def detect_ai_conversation(text: str) -> tuple[bool, str, float]:
    """Detect if clipboard text looks like an AI conversation.

    Returns (is_ai, reason, confidence_score).
    confidence_score: 0.0-1.0
    """
    if not text or len(text) < MIN_TEXT_LENGTH:
        return False, "", 0.0

    score = 0.0
    reasons = []

    # Check for role markers
    role_count = 0
    user_found = False
    assistant_found = False

    for pattern in ROLE_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            role_count += len(matches)
            marker = matches[0].lower() if isinstance(matches[0], str) else matches[0]
            if marker in ("user", "human", "you", "me", "q"):
                user_found = True
            else:
                assistant_found = True

    if role_count >= MIN_ROLE_MARKERS:
        score += 0.4
        reasons.append(f"role_markers:{role_count}")

    if user_found and assistant_found:
        score += 0.3
        reasons.append("both_roles_present")

    # Check for AI content patterns
    ai_pattern_count = 0
    for pattern in AI_CONTENT_PATTERNS:
        if pattern.search(text):
            ai_pattern_count += 1

    if ai_pattern_count >= 2:
        score += 0.2
        reasons.append(f"ai_patterns:{ai_pattern_count}")
    elif ai_pattern_count >= 1:
        score += 0.1
        reasons.append(f"ai_patterns:{ai_pattern_count}")

    # Code blocks boost
    code_blocks = text.count("```")
    if code_blocks >= 2:
        score += 0.1
        reasons.append(f"code_blocks:{code_blocks // 2}")

    # Length bonus (longer text is more likely to be a real conversation)
    if len(text) > 500:
        score += 0.05
    if len(text) > 2000:
        score += 0.05

    is_ai = score >= 0.5
    reason = ", ".join(reasons)

    return is_ai, reason, min(score, 1.0)


def parse_conversation(text: str) -> list[dict]:
    """Best-effort parse of clipboard text into messages.

    Tries to split on role markers like "User:", "Assistant:", etc.
    """
    messages = []

    # Try splitting on role patterns
    # Combine all role markers into one regex
    split_pattern = re.compile(
        r"^(User|Human|You|Me|Q|Assistant|AI|Bot|ChatGPT|Claude|Gemini|Copilot|GPT-4|GPT|A)\s*[:：]\s*",
        re.MULTILINE | re.IGNORECASE,
    )

    parts = split_pattern.split(text)

    if len(parts) >= 3:
        # parts[0] is text before first marker (usually empty)
        # parts[1] is the role, parts[2] is the content, etc.
        i = 1
        while i < len(parts) - 1:
            role_text = parts[i].strip().lower()
            content = parts[i + 1].strip()

            if role_text in ("user", "human", "you", "me", "q"):
                role = "user"
            else:
                role = "assistant"

            if content:
                messages.append({"role": role, "content": content})
            i += 2
    else:
        # Can't parse into messages, store as single block
        messages.append({"role": "unknown", "content": text.strip()})

    return messages


# ---------------------------------------------------------------------------
# Clipboard monitor thread
# ---------------------------------------------------------------------------

class ClipboardMonitor:
    """Monitors clipboard for AI conversation content."""

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_hash: str = ""
        self._capture_count: int = 0

    def start(self):
        """Start monitoring clipboard in a background thread."""
        if self._running:
            return
        self._running = True
        init_db()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Clipboard monitor started (poll every %.1fs)", self.poll_interval)

    def stop(self):
        """Stop the clipboard monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Clipboard monitor stopped (%d captures)", self._capture_count)

    @property
    def capture_count(self) -> int:
        return self._capture_count

    def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                text = self._get_clipboard_text()
                if text:
                    text_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
                    if text_hash != self._last_hash:
                        self._last_hash = text_hash
                        self._check_and_capture(text)
            except Exception:
                logger.debug("Clipboard read error (non-fatal)")

            time.sleep(self.poll_interval)

    def _get_clipboard_text(self) -> Optional[str]:
        """Read text from system clipboard (cross-platform)."""
        try:
            import platform
            system = platform.system()

            if system == "Windows":
                import ctypes
                CF_UNICODETEXT = 13
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32

                if not user32.OpenClipboard(0):
                    return None
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if not handle:
                        return None
                    kernel32.GlobalLock.restype = ctypes.c_wchar_p
                    text = kernel32.GlobalLock(handle)
                    kernel32.GlobalUnlock(handle)
                    return text
                finally:
                    user32.CloseClipboard()

            elif system == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=2,
                )
                return result.stdout if result.returncode == 0 else None

            elif system == "Linux":
                import subprocess
                try:
                    result = subprocess.run(
                        ["xclip", "-selection", "clipboard", "-o"],
                        capture_output=True, text=True, timeout=2,
                    )
                    return result.stdout if result.returncode == 0 else None
                except FileNotFoundError:
                    result = subprocess.run(
                        ["xsel", "--clipboard", "--output"],
                        capture_output=True, text=True, timeout=2,
                    )
                    return result.stdout if result.returncode == 0 else None

        except Exception:
            return None

        return None

    def _check_and_capture(self, text: str):
        """Check if text is an AI conversation and capture it."""
        is_ai, reason, confidence = detect_ai_conversation(text)
        if not is_ai:
            return

        messages = parse_conversation(text)
        pair_id = new_pair_id()

        body = json.dumps({
            "messages": messages,
            "total_messages": len(messages),
            "raw_text_length": len(text),
            "detection_reason": reason,
            "confidence": confidence,
        }, ensure_ascii=False)

        capture_id = insert_capture(
            direction="clipboard",
            pair_id=pair_id,
            host="clipboard",
            path="",
            method="",
            provider="clipboard",
            body_text_or_json=body,
            body_format="json",
            source_id=SOURCE_BROWSER_EXT,
            meta_json=json.dumps({
                "capture_source": "clipboard_monitor",
                "confidence": confidence,
                "reason": reason,
                "message_count": len(messages),
            }),
        )

        if capture_id:
            self._capture_count += 1
            logger.info(
                "Clipboard capture: %d messages, confidence=%.2f, reason=%s",
                len(messages), confidence, reason,
            )
