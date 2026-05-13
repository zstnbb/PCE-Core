# SPDX-License-Identifier: Apache-2.0
"""Tests for CopilotChatNormalizer and CursorChatNormalizer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.normalizer.copilot_chat import CopilotChatNormalizer
from pce_core.normalizer.cursor_chat import CursorChatNormalizer

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# CopilotChatNormalizer
# ---------------------------------------------------------------------------


def _load_copilot_state():
    with open(FIXTURES / "copilot_chat_session_state.json", encoding="utf-8") as f:
        return json.load(f)


def test_copilot_basic_extraction():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat",
        path=f"/{state['sessionId']}",
    )
    assert result is not None
    assert result.provider == "github"
    assert result.tool_family == "copilot-chat-l3g"
    assert result.session_key == "1b150f92-f959-4433-9e50-fe0167d7247b"
    assert result.confidence >= 0.9


def test_copilot_message_count():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result is not None
    assert len(result.messages) == 5  # 3 user + 2 assistant (1 incomplete)


def test_copilot_user_text():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result.messages[0].role == "user"
    assert result.messages[0].content_text == "What is 2+2?"


def test_copilot_assistant_text_no_thinking():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assistant = result.messages[1]
    assert assistant.role == "assistant"
    assert assistant.content_text == "4"
    assert "Answering simple math" not in assistant.content_text


def test_copilot_model_propagation():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result.model_name == "copilot/gpt-5-mini"
    for m in result.messages:
        assert m.model_name == "copilot/gpt-5-mini"


def test_copilot_title():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result.title_hint == "Basic math question"


def test_copilot_token_count():
    state = _load_copilot_state()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assistant = result.messages[1]
    assert assistant.token_estimate == 172


def test_copilot_empty_body():
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        "", "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result is None


def test_copilot_invalid_json():
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        "not json", "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result is None


def test_copilot_can_handle():
    norm = CopilotChatNormalizer()
    assert norm.can_handle("github", "local-copilot-chat", "/session-123")
    assert not norm.can_handle("github", "api.github.com", "/chat")
    assert not norm.can_handle("openai", "chatgpt.com", "/chat")


# ---------------------------------------------------------------------------
# CursorChatNormalizer
# ---------------------------------------------------------------------------


def _load_cursor_data():
    with open(FIXTURES / "cursor_chat_session.json", encoding="utf-8") as f:
        return json.load(f)


def test_cursor_basic_extraction():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat",
        path=f"/{data['composer_id']}",
    )
    assert result is not None
    assert result.provider == "cursor"
    assert result.tool_family == "cursor-chat-l3g"
    assert result.session_key == "26c80266-3499-48a0-94fa-fc93ea2a8be1"
    assert result.confidence >= 0.9


def test_cursor_message_count():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result is not None
    assert len(result.messages) == 4  # 2 user + 2 assistant


def test_cursor_role_assignment():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    roles = [m.role for m in result.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_cursor_user_text():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result.messages[0].content_text == "What is 2+2?"
    assert result.messages[2].content_text == "Write me a 1-line Python function that adds two numbers"


def test_cursor_assistant_text():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result.messages[1].content_text == "**2 + 2 = 4**"
    assert "```python" in result.messages[3].content_text


def test_cursor_title():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result.title_hint == "Basic arithmetic question"


def test_cursor_model_name():
    data = _load_cursor_data()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result.model_name == "default"


def test_cursor_empty_bubbles():
    data = {"composer_id": "abc", "bubbles": [], "model_config": {}, "name": "x"}
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result is None


def test_cursor_can_handle():
    norm = CursorChatNormalizer()
    assert norm.can_handle("cursor", "local-cursor-chat", "/composer-123")
    assert not norm.can_handle("cursor", "api2.cursor.sh", "/chat")


# ---------------------------------------------------------------------------
# D03 — multi-turn persistence
# ---------------------------------------------------------------------------


def _load_cursor_multi_turn():
    with open(FIXTURES / "cursor_chat_multi_turn.json", encoding="utf-8") as f:
        return json.load(f)


def _load_copilot_multi_turn():
    with open(FIXTURES / "copilot_chat_multi_turn.json", encoding="utf-8") as f:
        return json.load(f)


def test_cursor_d03_multi_turn():
    """D03: 10-turn conversation preserves all messages + order + same session."""
    data = _load_cursor_multi_turn()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result is not None
    assert len(result.messages) == 20
    assert result.session_key == "multi-turn-test-0001"
    roles = [m.role for m in result.messages]
    for i in range(0, 20, 2):
        assert roles[i] == "user"
        assert roles[i + 1] == "assistant"


def test_copilot_d03_multi_turn():
    """D03: 6-request session preserves all messages + order + same session."""
    state = _load_copilot_multi_turn()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result is not None
    # 6 complete requests (user+asst) + 1 cancelled (user only) + 1 code (user+asst) = 15
    users = [m for m in result.messages if m.role == "user"]
    assistants = [m for m in result.messages if m.role == "assistant"]
    assert len(users) == 8  # all 8 requests have user text
    assert len(assistants) == 7  # 6 normal + 1 code, cancelled has no assistant
    assert result.session_key == "multi-turn-test-copilot-0001"


# ---------------------------------------------------------------------------
# D04 — cancel / incomplete
# ---------------------------------------------------------------------------


def test_cursor_d04_cancel():
    """D04: Empty assistant bubble → no assistant message emitted."""
    data = {
        "composer_id": "cancel-test",
        "name": "Cancelled session",
        "model_config": {"modelName": "default"},
        "bubbles": [
            {"bubble_id": "u1", "composer_id": "cancel-test", "bubble_type": 1,
             "text": "This prompt was cancelled", "context": {}},
            {"bubble_id": "a1", "composer_id": "cancel-test", "bubble_type": 2,
             "text": "", "context": {}},
        ]
    }
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result is not None
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"
    assert result.messages[0].content_text == "This prompt was cancelled"


def test_copilot_d04_cancel():
    """D04: Request with no content response_blocks → user-only message."""
    state = _load_copilot_multi_turn()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    # The cancelled request (index 6) should have user but no assistant
    # Find the cancelled user message
    cancelled_user = None
    for i, m in enumerate(result.messages):
        if m.content_text == "This was cancelled mid-stream":
            cancelled_user = m
            # Next message should NOT be an assistant for this prompt
            if i + 1 < len(result.messages):
                assert result.messages[i + 1].role == "user"  # next is another user
            break
    assert cancelled_user is not None
    assert cancelled_user.role == "user"


# ---------------------------------------------------------------------------
# D05 — model switch
# ---------------------------------------------------------------------------


def test_cursor_d05_model_switch():
    """D05: Different model_config → different model_name."""
    norm = CursorChatNormalizer()
    data1 = {
        "composer_id": "s1", "name": "Session 1",
        "model_config": {"modelName": "claude-sonnet-4-6", "selectedModels": []},
        "bubbles": [
            {"bubble_id": "u1", "composer_id": "s1", "bubble_type": 1, "text": "Hi", "context": {}},
            {"bubble_id": "a1", "composer_id": "s1", "bubble_type": 2, "text": "Hello", "context": {}},
        ]
    }
    data2 = {
        "composer_id": "s2", "name": "Session 2",
        "model_config": {"modelName": "gpt-5", "selectedModels": []},
        "bubbles": [
            {"bubble_id": "u2", "composer_id": "s2", "bubble_type": 1, "text": "Hi", "context": {}},
            {"bubble_id": "a2", "composer_id": "s2", "bubble_type": 2, "text": "Hey", "context": {}},
        ]
    }
    r1 = norm.normalize(json.dumps(data1), "", provider="cursor", host="local-cursor-chat", path="/s1")
    r2 = norm.normalize(json.dumps(data2), "", provider="cursor", host="local-cursor-chat", path="/s2")
    assert r1.model_name == "claude-sonnet-4-6"
    assert r2.model_name == "gpt-5"


# ---------------------------------------------------------------------------
# D07 — code block
# ---------------------------------------------------------------------------


def test_copilot_d07_codeblock():
    """D07: Response with codeblock kind → fenced code in content_text."""
    state = _load_copilot_multi_turn()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    # Last assistant message should have the code block
    code_msg = result.messages[-1]
    assert code_msg.role == "assistant"
    assert '```python' in code_msg.content_text
    assert 'print("Hello, World!")' in code_msg.content_text


# ---------------------------------------------------------------------------
# D10 — error state / robustness
# ---------------------------------------------------------------------------


def test_cursor_d10_malformed_bubbles():
    """D10: Bubbles with missing/null fields → graceful handling."""
    data = {
        "composer_id": "bad-data",
        "name": "Bad session",
        "model_config": {},
        "bubbles": [
            {"bubble_id": "u1", "composer_id": "bad-data", "bubble_type": 1, "text": "Good", "context": {}},
            None,  # null bubble
            {"bubble_id": "a1", "bubble_type": 2},  # missing text
            {"bubble_id": "u2", "composer_id": "bad-data", "bubble_type": 1, "text": "Also good", "context": {}},
        ]
    }
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert result is not None
    assert len(result.messages) == 2  # only the two valid user bubbles


def test_copilot_d10_empty_session():
    """D10: Session with 0 requests → None."""
    state = {
        "version": 3, "sessionId": "empty-session",
        "requests": [], "inputState": {"selectedModel": {"identifier": "copilot/auto"}},
    }
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assert result is None


# ---------------------------------------------------------------------------
# D11 — long-context survival
# ---------------------------------------------------------------------------


def test_cursor_d11_long_context():
    """D11: 10-turn (20 bubble) session → all messages preserved, no drops."""
    data = _load_cursor_multi_turn()
    norm = CursorChatNormalizer()
    result = norm.normalize(
        json.dumps(data), "",
        provider="cursor", host="local-cursor-chat", path="/test",
    )
    assert len(result.messages) == 20
    # Verify content integrity
    assert result.messages[0].content_text == "Question 1: What is 1 + 1?"
    assert result.messages[19].content_text == "20. The answer to 10 + 10 is 20."


def test_copilot_d11_token_preservation():
    """D11: Token counts preserved on assistant messages."""
    state = _load_copilot_multi_turn()
    norm = CopilotChatNormalizer()
    result = norm.normalize(
        json.dumps(state), "",
        provider="github", host="local-copilot-chat", path="/test",
    )
    assistants = [m for m in result.messages if m.role == "assistant"]
    # First 6 assistants have tokens 50, 60, 70, 80, 90, 100
    for i, a in enumerate(assistants[:6]):
        assert a.token_estimate == 50 + i * 10


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed} PASS / {failed} FAIL")
    if failed:
        sys.exit(1)
