"""Unit tests for pce_core.clipboard_monitor – AI conversation detection & parsing."""

import pytest

from pce_core.clipboard_monitor import (
    detect_ai_conversation,
    parse_conversation,
    MIN_TEXT_LENGTH,
    MIN_ROLE_MARKERS,
)


# ── detect_ai_conversation ───────────────────────────────────────────────

class TestDetectAIConversation:
    """Tests for the clipboard AI conversation heuristic."""

    def test_clear_conversation_detected(self):
        text = (
            "User: How do I sort a list in Python?\n\n"
            "Assistant: You can use the `sorted()` function or the `.sort()` method.\n\n"
            "Here's an example:\n"
            "```python\n"
            "my_list = [3, 1, 2]\n"
            "sorted_list = sorted(my_list)\n"
            "```\n\n"
            "1. **`sorted()`** returns a new sorted list\n"
            "2. **`.sort()`** sorts in-place\n\n"
            "User: What about sorting by a custom key?\n\n"
            "Assistant: You can pass a `key` parameter:\n"
            "```python\n"
            "sorted(items, key=lambda x: x.name)\n"
            "```\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is True
        assert score >= 0.5
        assert "role_markers" in reason
        assert "both_roles_present" in reason

    def test_short_text_rejected(self):
        text = "User: Hi\nAssistant: Hello"
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is False
        assert score == 0.0

    def test_empty_text(self):
        is_ai, reason, score = detect_ai_conversation("")
        assert is_ai is False

    def test_none_text(self):
        is_ai, reason, score = detect_ai_conversation(None)
        assert is_ai is False

    def test_normal_text_not_detected(self):
        text = (
            "Meeting notes from today's standup:\n"
            "- Alice is working on the payment integration\n"
            "- Bob fixed the login bug\n"
            "- Charlie will review the PR\n"
            "Next meeting is scheduled for Thursday at 10am.\n"
            "Please make sure to update your JIRA tickets before the end of the day.\n"
            "Additional items for discussion include the Q4 roadmap and hiring plan.\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is False

    def test_code_only_not_detected(self):
        text = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fibonacci(n-1) + fibonacci(n-2)\n\n"
            "# Test the function\n"
            "for i in range(10):\n"
            "    print(fibonacci(i))\n"
            "# Output: 0 1 1 2 3 5 8 13 21 34\n"
            "# This is a simple recursive implementation.\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is False

    def test_human_and_chatgpt_markers(self):
        text = (
            "Human: Explain quantum computing in simple terms.\n\n"
            "ChatGPT: Quantum computing uses quantum bits (qubits) instead of "
            "classical bits. Unlike classical bits that are either 0 or 1, qubits "
            "can exist in a superposition of both states simultaneously.\n\n"
            "Here's a breakdown:\n"
            "1. **Superposition** - qubits can be 0 and 1 at the same time\n"
            "2. **Entanglement** - qubits can be correlated\n"
            "3. **Interference** - quantum states can amplify correct answers\n\n"
            "Human: Can you give me a practical example?\n\n"
            "ChatGPT: Sure! Let me explain with drug discovery...\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is True
        assert "both_roles_present" in reason

    def test_single_role_low_confidence(self):
        text = (
            "Assistant: Here is a long explanation of how databases work.\n"
            "A database is a structured collection of data that is stored and "
            "accessed electronically. Databases are used to store everything from "
            "simple lists to complex data warehouses. They support operations like "
            "CRUD (Create, Read, Update, Delete) and can be relational or non-relational.\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        # Only one role, so lower score
        assert score < 0.7

    def test_ai_patterns_boost_score(self):
        text = (
            "User: Help me write a function\n\n"
            "Assistant: I'd be happy to help! Here's an example:\n"
            "```python\n"
            "def greet(name):\n"
            "    return f'Hello, {name}!'\n"
            "```\n\n"
            "Let me explain the key parts:\n"
            "1. **`def`** defines a function\n"
            "2. **`f'...'`** is an f-string for formatting\n\n"
            "User: Thanks!\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is True
        assert "ai_patterns" in reason

    def test_long_text_bonus(self):
        base = "User: Question\n\nAssistant: " + "x " * 300 + "\n\nUser: Follow up\n\nAssistant: " + "y " * 300
        is_ai, reason, score = detect_ai_conversation(base)
        assert is_ai is True
        # Long text should get a length bonus
        assert score > 0.5

    def test_confidence_capped_at_one(self):
        text = (
            "User: How do I do X?\n\n"
            "Assistant: I'd be happy to help! Here's a step-by-step breakdown:\n"
            "```python\ncode\n```\n"
            "1. **First step**\n"
            "2. **Second step**\n"
            "- **Bullet item**\n"
            "Let me explain further...\n\n"
            "User: What about Y?\n\n"
            "Assistant: Here's an example:\n"
            "```javascript\nmore code\n```\n"
            "User: And Z?\n\n"
            "Assistant: Sure! " + "content " * 500 + "\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert score <= 1.0

    def test_q_and_a_format(self):
        text = (
            "Q: What is the capital of France?\n\n"
            "A: The capital of France is Paris. It is the largest city in France "
            "and serves as the country's major cultural and commercial center. "
            "Paris is known for landmarks like the Eiffel Tower, the Louvre Museum, "
            "and Notre-Dame Cathedral. The city has a population of approximately "
            "2.1 million people within the city limits.\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert "role_markers" in reason


# ── parse_conversation ───────────────────────────────────────────────────

class TestParseConversation:
    """Tests for clipboard text → message list parsing."""

    def test_basic_user_assistant(self):
        text = "User: Hello there\n\nAssistant: Hi! How can I help?"
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert "Hello there" in msgs[0]["content"]
        assert msgs[1]["role"] == "assistant"
        assert "How can I help" in msgs[1]["content"]

    def test_human_chatgpt(self):
        text = "Human: What is AI?\n\nChatGPT: AI stands for Artificial Intelligence."
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_multi_turn(self):
        text = (
            "User: First question\n\n"
            "Assistant: First answer\n\n"
            "User: Second question\n\n"
            "Assistant: Second answer\n"
        )
        msgs = parse_conversation(text)
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[3]["role"] == "assistant"

    def test_unparseable_text_single_block(self):
        text = "This is just random text without any role markers at all."
        msgs = parse_conversation(text)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "unknown"

    def test_empty_content_skipped(self):
        text = "User: \n\nAssistant: Real content here"
        msgs = parse_conversation(text)
        # Empty user content should be skipped
        assert any(m["content"] == "Real content here" for m in msgs)

    def test_claude_marker(self):
        text = "Human: Explain recursion\n\nClaude: Recursion is when a function calls itself."
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert msgs[1]["role"] == "assistant"

    def test_you_bot_markers(self):
        text = "You: What time is it?\n\nBot: I don't have access to the current time."
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_preserves_multiline_content(self):
        text = (
            "User: Write a haiku\n\n"
            "Assistant: Here's a haiku for you:\n"
            "Autumn moonlight—\n"
            "a worm digs silently\n"
            "into the chestnut.\n"
        )
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert "haiku" in msgs[1]["content"]
        assert "Autumn" in msgs[1]["content"]

    def test_q_a_format(self):
        text = "Q: What is 2+2?\n\nA: The answer is 4."
        msgs = parse_conversation(text)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
