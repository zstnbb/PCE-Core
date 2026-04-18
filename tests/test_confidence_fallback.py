# SPDX-License-Identifier: Apache-2.0
"""Tests for normalizer confidence scoring and try-fallback chain."""

import sys
import os
import json
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.normalizer.base import NormalizedResult, normalize_pair
from pce_core.normalizer.openai import OpenAIChatNormalizer
from pce_core.normalizer.anthropic import AnthropicMessagesNormalizer
from pce_core.normalizer.conversation import ConversationNormalizer
from pce_core.normalizer.registry import get_all_normalizers


# ---------------------------------------------------------------------------
# Confidence scoring tests
# ---------------------------------------------------------------------------

class TestOpenAIConfidence:
    """OpenAI normalizer confidence scoring."""

    def test_high_confidence_full_openai(self):
        """Full OpenAI request+response with model, messages, choices, usage → high confidence."""
        n = OpenAIChatNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            response_body=json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
                "model": "gpt-4",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }),
            provider="openai",
            host="api.openai.com",
            path="/v1/chat/completions",
            created_at=time.time(),
        )
        assert result is not None
        # All signals present: messages(0.25) + model(0.10) + choices(0.20) +
        # usage(0.10) + both_roles(0.15) + known_host(0.10) + exact_path(0.10)
        assert result.confidence >= 0.9, f"Expected >= 0.9, got {result.confidence}"

    def test_medium_confidence_request_only(self):
        """Request with messages but no valid response → medium confidence."""
        n = OpenAIChatNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            response_body="",
            provider="openai",
            host="api.openai.com",
            path="/v1/chat/completions",
            created_at=time.time(),
        )
        assert result is not None
        # No response → no choices, no usage, no assistant role
        assert result.confidence < 0.8, f"Expected < 0.8, got {result.confidence}"

    def test_low_confidence_unknown_host(self):
        """Request from unknown host with messages → lower confidence."""
        n = OpenAIChatNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "messages": [{"role": "user", "content": "hello"}],
            }),
            response_body=json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            }),
            provider="unknown",
            host="some.random.api",
            path="/api/chat",
            created_at=time.time(),
        )
        assert result is not None
        # No model, unknown host, non-standard path
        assert result.confidence < 0.7, f"Expected < 0.7, got {result.confidence}"


class TestAnthropicConfidence:
    """Anthropic normalizer confidence scoring."""

    def test_high_confidence_full_anthropic(self):
        """Full Anthropic request+response with claude model → high confidence."""
        n = AnthropicMessagesNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            response_body=json.dumps({
                "content": [{"type": "text", "text": "Hi!"}],
                "model": "claude-sonnet-4-20250514",
                "role": "assistant",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }),
            provider="anthropic",
            host="api.anthropic.com",
            path="/v1/messages",
            created_at=time.time(),
        )
        assert result is not None
        # messages(0.25) + claude_model(0.15) + content_blocks(0.20) +
        # usage(0.10) + both_roles(0.15) + host(0.10) + path(0.05) = 1.0
        assert result.confidence >= 0.9, f"Expected >= 0.9, got {result.confidence}"

    def test_medium_confidence_non_claude_model(self):
        """Anthropic format but with a non-claude model → lower confidence."""
        n = AnthropicMessagesNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "model": "mistral-large",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            response_body=json.dumps({
                "content": [{"type": "text", "text": "Hi!"}],
                "role": "assistant",
            }),
            provider="anthropic",
            host="api.anthropic.com",
            path="/v1/messages",
        )
        assert result is not None
        # model is not claude → only +0.05 instead of +0.15
        assert result.confidence < 1.0


class TestConversationConfidence:
    """Conversation normalizer confidence scoring."""

    def test_high_confidence_rich_conversation(self):
        """DOM conversation with many messages, known provider, session_id → high confidence."""
        n = ConversationNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "messages": [
                    {"role": "user", "content": "What is machine learning?"},
                    {"role": "assistant", "content": "Machine learning is a subset of AI that enables systems to learn from data..."},
                    {"role": "user", "content": "Can you give an example?"},
                    {"role": "assistant", "content": "Sure! A common example is email spam filtering..."},
                ],
                "conversation_id": "abc-123",
                "title": "ML Discussion",
            }),
            response_body="",
            provider="deepseek",
            host="chat.deepseek.com",
            path="/",
            created_at=time.time(),
        )
        assert result is not None
        # Well-formed msgs(0.25) + both_roles(0.20) + known_provider(0.10) +
        # known_host(0.10) + conv_id(0.10) + title(0.05) + >=4 msgs(0.10) + avg>20(0.10)
        assert result.confidence >= 0.85, f"Expected >= 0.85, got {result.confidence}"

    def test_low_confidence_sparse_conversation(self):
        """Conversation with single short message → low confidence."""
        n = ConversationNormalizer()
        result = n.normalize(
            request_body=json.dumps({
                "messages": [
                    {"role": "user", "content": "hi"},
                ],
            }),
            response_body="",
            provider="unknown",
            host="some.site.com",
            path="/",
        )
        assert result is not None
        assert result.confidence < 0.5, f"Expected < 0.5, got {result.confidence}"


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Test that normalize_pair tries multiple normalizers and picks the best."""

    def test_get_all_normalizers_returns_multiple(self):
        """For a host like chatgpt.com, both OpenAI and Conversation normalizers should match."""
        candidates = get_all_normalizers("openai", "chatgpt.com", "/backend-api/conversation")
        names = [type(n).__name__ for n in candidates]
        # OpenAI matches (host in _COMPATIBLE_HOSTS + path regex),
        # Conversation matches (catch-all)
        assert "OpenAIChatNormalizer" in names
        assert "ConversationNormalizer" in names
        assert len(candidates) >= 2

    def test_best_result_wins(self):
        """When multiple normalizers match, the one with higher confidence wins."""
        # OpenAI-format request → OpenAI normalizer should win over Conversation
        req = {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "body_text_or_json": json.dumps({
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            "model_name": "gpt-4",
            "created_at": time.time(),
        }
        resp = {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "body_text_or_json": json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
                "model": "gpt-4",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }),
        }
        result = normalize_pair(req, resp)
        assert result is not None
        assert result.normalizer_name == "OpenAIChatNormalizer"
        assert result.confidence >= 0.9

    def test_conversation_wins_for_dom_captures(self):
        """DOM-extracted conversation body → Conversation normalizer should produce result."""
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "What is Python?"},
                {"role": "assistant", "content": "Python is a programming language."},
            ],
            "conversation_id": "sess-001",
        })
        req = {
            "provider": "deepseek",
            "host": "chat.deepseek.com",
            "path": "/",
            "body_text_or_json": body,
            "created_at": time.time(),
        }
        resp = dict(req)
        result = normalize_pair(req, resp)
        assert result is not None
        assert len(result.messages) >= 2
        assert result.confidence > 0

    def test_normalizer_name_tagged(self):
        """Result should always have normalizer_name set."""
        req = {
            "provider": "anthropic",
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "body_text_or_json": json.dumps({
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hi"}],
            }),
            "created_at": time.time(),
        }
        resp = {
            "provider": "anthropic",
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "body_text_or_json": json.dumps({
                "content": [{"type": "text", "text": "Hello!"}],
                "role": "assistant",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }),
        }
        result = normalize_pair(req, resp)
        assert result is not None
        assert result.normalizer_name is not None
        assert len(result.normalizer_name) > 0

    def test_no_normalizer_returns_none(self):
        """Completely invalid data → None result."""
        req = {
            "provider": "unknown",
            "host": "example.com",
            "path": "/foo",
            "body_text_or_json": "not json at all",
        }
        result = normalize_pair(req, dict(req))
        assert result is None

    def test_high_confidence_short_circuits(self):
        """When a normalizer returns confidence >= 0.9, we stop immediately."""
        req = {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "body_text_or_json": json.dumps({
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test"}],
            }),
            "created_at": time.time(),
        }
        resp = {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "body_text_or_json": json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                "model": "gpt-4",
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }),
        }
        result = normalize_pair(req, resp)
        assert result is not None
        assert result.confidence >= 0.9
        # Should be OpenAI (first in registry, high confidence → short-circuit)
        assert result.normalizer_name == "OpenAIChatNormalizer"


# ---------------------------------------------------------------------------
# NormalizedResult dataclass tests
# ---------------------------------------------------------------------------

class TestNormalizedResultFields:
    """Test that NormalizedResult has the new fields with correct defaults."""

    def test_default_confidence(self):
        r = NormalizedResult(provider="test", tool_family="test")
        assert r.confidence == 0.5

    def test_default_normalizer_name(self):
        r = NormalizedResult(provider="test", tool_family="test")
        assert r.normalizer_name is None

    def test_custom_confidence(self):
        r = NormalizedResult(provider="test", tool_family="test", confidence=0.95)
        assert r.confidence == 0.95

    def test_custom_normalizer_name(self):
        r = NormalizedResult(
            provider="test", tool_family="test",
            normalizer_name="MyNormalizer",
        )
        assert r.normalizer_name == "MyNormalizer"
